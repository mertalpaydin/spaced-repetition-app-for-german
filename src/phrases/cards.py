"""Pick the sentences that become cards, and say which ones the deck wants
glossed next.

Only sentences with an Azure or Gemini gloss qualify (CLAUDE.md rule 9). A
unit's cards cover distinct surface forms first (``wartet auf``, ``wartete
auf``, ``warte … auf``, ``gewartet auf``), then fill to the cap by length.
"""

import hashlib
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from src.contracts import GapSpan, PhraseCard, PhraseUnit
from src.phrases.occurrences import Occurrence

TRUSTED_GLOSS_SOURCES: frozenset[str] = frozenset({"azure", "gemini"})
GLOSS_LENGTH_RATIO: tuple[float, float] = (0.4, 2.5)


@dataclass(frozen=True)
class Gloss:
    english: str
    source: str


@dataclass(frozen=True)
class WantedCarrier:
    unit_id: str
    corpus_source: str
    line_id: str
    text: str


@dataclass
class CardSelection:
    cards: list[PhraseCard]
    wanted: list[WantedCarrier]
    stats: dict[str, int] = field(default_factory=dict)


def card_id_for(unit_id: str, sentence: str) -> str:
    return hashlib.sha1(f"{unit_id}\n{sentence}".encode()).hexdigest()[:12]


def gloss_is_sane(german: str, english: str) -> bool:
    if not english.strip() or "\n" in english:
        return False
    ratio = len(english) / max(len(german), 1)
    return GLOSS_LENGTH_RATIO[0] <= ratio <= GLOSS_LENGTH_RATIO[1]


def _score(occ: Occurrence, *, taken_forms: set[str]) -> float:
    """Lower is better: short, plain sentences first, a new surface form
    before a repeat."""
    score = float(len(occ.text))
    words = occ.text.split()
    score += 15.0 * sum(1 for w in words[1:] if w[:1].isupper() and w.isalpha() and len(w) > 3)
    score += 10.0 * sum(1 for w in words if any(ch.isdigit() for ch in w))
    if occ.needs_context:
        score += 20.0
    if occ.form_key in taken_forms:
        score += 25.0
    return score


def _to_card(unit: PhraseUnit, occ: Occurrence, gloss: Gloss) -> PhraseCard:
    gaps = [
        GapSpan(start=start, end=end, answer=surface, token_index=index)
        for (start, end), surface, index in zip(
            occ.spans, occ.surfaces, occ.token_indices, strict=True
        )
    ]
    return PhraseCard(
        card_id=card_id_for(unit.unit_id, occ.text),
        unit_id=unit.unit_id,
        kind=unit.kind,
        sentence_de=occ.text,
        gloss_en=gloss.english,
        gloss_source=gloss.source,  # type: ignore[arg-type]
        gaps=gaps,
        answers=[g.answer for g in gaps],
        form_key=occ.form_key,
        corpus_source=occ.corpus_source,  # type: ignore[arg-type]
        corpus_line_id=occ.line_id,
        needs_context=occ.needs_context,
    )


def select_cards(
    units: Iterable[PhraseUnit],
    occurrences_by_unit: Mapping[str, list[Occurrence]],
    glosses: Mapping[str, Gloss],
    *,
    validate: Callable[[str], bool],
    k: int = 6,
    k_trivial: int = 2,
    wanted_per_unit: int = 10,
    max_validations: int = 3000,
) -> CardSelection:
    """``occurrences_by_unit`` maps ``unit_id`` to that unit's occurrences.
    ``glosses`` maps sentence text to its stored gloss (any source; the trust
    rule is applied here). ``validate`` is the carrier validator, injected."""
    cards: list[PhraseCard] = []
    wanted: list[WantedCarrier] = []
    validation_cache: dict[str, bool] = {}
    validations = 0
    stats: dict[str, int] = defaultdict(int)

    def is_valid(text: str) -> bool:
        nonlocal validations
        if text not in validation_cache:
            validation_cache[text] = validate(text)
            validations += 1
        return validation_cache[text]

    for unit in units:
        cap = k_trivial if unit.trivial else k
        occurrences = occurrences_by_unit.get(unit.unit_id, [])
        # One occurrence per sentence: a sentence hosting the unit twice is
        # ambiguous as a card.
        per_sentence: dict[str, list[Occurrence]] = defaultdict(list)
        for occ in occurrences:
            per_sentence[occ.text].append(occ)
        glossed: list[tuple[Occurrence, Gloss]] = []
        unglossed: list[Occurrence] = []
        for text, group in per_sentence.items():
            if len(group) != 1:
                stats["ambiguous_sentence"] += 1
                continue
            occ = group[0]
            gloss = glosses.get(text)
            if gloss is None or gloss.source not in TRUSTED_GLOSS_SOURCES:
                unglossed.append(occ)
                continue
            if not gloss_is_sane(text, gloss.english):
                stats["gloss_insane"] += 1
                continue
            glossed.append((occ, gloss))

        chosen: list[PhraseCard] = []
        taken_forms: set[str] = set()
        remaining = list(glossed)
        while remaining and len(chosen) < cap:
            remaining.sort(key=lambda pair: _score(pair[0], taken_forms=taken_forms))
            occ, gloss = remaining.pop(0)
            if not is_valid(occ.text):
                stats["carrier_rejected"] += 1
                continue
            chosen.append(_to_card(unit, occ, gloss))
            taken_forms.add(occ.form_key)
        cards.extend(chosen)
        if len(chosen) < cap and unglossed:
            unglossed.sort(key=lambda o: len(o.text))
            for occ in unglossed[:wanted_per_unit]:
                if validations >= max_validations:
                    stats["wanted_unvalidated"] += 1
                    wanted.append(
                        WantedCarrier(unit.unit_id, occ.corpus_source, occ.line_id, occ.text)
                    )
                    continue
                if is_valid(occ.text):
                    wanted.append(
                        WantedCarrier(unit.unit_id, occ.corpus_source, occ.line_id, occ.text)
                    )
    stats["cards"] = len(cards)
    stats["wanted"] = len(wanted)
    stats["validations"] = validations
    return CardSelection(cards=cards, wanted=wanted, stats=dict(stats))


def with_card_counts(units: Iterable[PhraseUnit], cards: Iterable[PhraseCard]) -> list[PhraseUnit]:
    counts: dict[str, int] = defaultdict(int)
    for card in cards:
        counts[card.unit_id] += 1
    return [unit.model_copy(update={"card_count": counts.get(unit.unit_id, 0)}) for unit in units]
