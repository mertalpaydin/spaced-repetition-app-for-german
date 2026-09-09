"""Pick the sentences that become cards, and say which ones still need a gloss.

Cards come from the whole corpus: the best sentences for a unit are chosen
first, and the English is fetched afterwards (owner's instruction,
2026-09-08). A sentence with an Azure or Gemini gloss wins a tie, so the
cards a client can show today are as many as possible; every chosen sentence
without one goes to ``wanted_carriers.txt`` for the monthly Azure job. Tatoeba's
own English is never used (CLAUDE.md rule 9).

A unit's cards cover distinct surface forms first (``wartet auf``, ``wartete
auf``, ``warte … auf``, ``gewartet auf``), then fill to the cap by length.
"""

import hashlib
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from src.contracts import GapSpan, PhraseCard, PhraseUnit
from src.phrases.occurrences import Occurrence

TRUSTED_GLOSS_SOURCES: frozenset[str] = frozenset({"azure", "gemini"})
GLOSS_LENGTH_RATIO: tuple[float, float] = (0.4, 2.5)
#: Score penalty for a sentence with no gloss yet: a glossed sentence of the
#: same form wins, an un-glossed short one still beats a glossed long one.
UNGLOSSED_PENALTY: float = 30.0


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


def usable_gloss(text: str, gloss: Gloss | None) -> Gloss | None:
    if gloss is None or gloss.source not in TRUSTED_GLOSS_SOURCES:
        return None
    if not gloss_is_sane(text, gloss.english):
        return None
    return gloss


_QUOTE_CHARS = '"\u201e\u201c\u201d\u00ab\u00bb'
_WORD = re.compile(r"[\w\u00e4\u00f6\u00fc\u00c4\u00d6\u00dc\u00df]+")


def unsuitable_reason(occ: Occurrence) -> str | None:
    """Sentence-level reasons a correct occurrence still makes a bad card.

    Found by the phase 1 review: reported-speech Konjunktiv I in the
    sentence (the learner types the indicative and is marked wrong), an
    answer token repeated elsewhere in the sentence (the gap is given away),
    and a stray quotation mark from the source.
    """
    if occ.evidence.get("k1_sentence") == "true" or "K1" in occ.form_key:
        return "konjunktiv_i"
    if sum(occ.text.count(ch) for ch in _QUOTE_CHARS) % 2 == 1:
        return "unbalanced_quotes"
    words = _WORD.findall(occ.text.lower())
    gap_words = [w.lower() for w in occ.surfaces]
    for word in set(gap_words):
        if len(word) > 2 and words.count(word) > gap_words.count(word):
            return "answer_leak"
    return None


def _score(occ: Occurrence, *, taken_forms: set[str], glossed: bool) -> float:
    """Lower is better: short, plain sentences first, a new surface form
    before a repeat, a glossed sentence before an un-glossed twin."""
    score = float(len(occ.text))
    words = occ.text.split()
    score += 15.0 * sum(1 for w in words[1:] if w[:1].isupper() and w.isalpha() and len(w) > 3)
    score += 10.0 * sum(1 for w in words if any(ch.isdigit() for ch in w))
    if occ.needs_context:
        score += 20.0
    if occ.form_key in taken_forms:
        score += 25.0
    if not glossed:
        score += UNGLOSSED_PENALTY
    return score


def _to_card(unit: PhraseUnit, occ: Occurrence, gloss: Gloss | None) -> PhraseCard:
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
        gloss_en=gloss.english if gloss else None,
        gloss_source=gloss.source if gloss else None,  # type: ignore[arg-type]
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
    max_validations: int | None = None,
    excluded_card_ids: frozenset[str] = frozenset(),
) -> CardSelection:
    """``occurrences_by_unit`` maps ``unit_id`` to that unit's occurrences.
    ``glosses`` maps sentence text to its stored gloss (any source; the trust
    rule is applied here). ``validate`` is the carrier validator, injected.
    ``max_validations`` bounds the validator calls; past it, remaining units
    get no cards this run and the stats say so."""
    cards: list[PhraseCard] = []
    wanted: list[WantedCarrier] = []
    validation_cache: dict[str, bool] = {}
    validations = 0
    stats: dict[str, int] = defaultdict(int)

    def is_valid(text: str) -> bool | None:
        nonlocal validations
        if text in validation_cache:
            return validation_cache[text]
        if max_validations is not None and validations >= max_validations:
            return None
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
        candidates: list[tuple[Occurrence, Gloss | None]] = []
        for text, group in per_sentence.items():
            if len(group) != 1:
                stats["ambiguous_sentence"] += 1
                continue
            if card_id_for(unit.unit_id, text) in excluded_card_ids:
                stats["excluded_by_review"] += 1
                continue
            reason = unsuitable_reason(group[0])
            if reason is not None:
                stats[reason] += 1
                continue
            candidates.append((group[0], usable_gloss(text, glosses.get(text))))

        chosen: list[PhraseCard] = []
        taken_forms: set[str] = set()
        remaining = candidates
        budget_hit = False
        while remaining and len(chosen) < cap:
            remaining.sort(
                key=lambda pair: _score(
                    pair[0], taken_forms=taken_forms, glossed=pair[1] is not None
                )
            )
            occ, gloss = remaining.pop(0)
            verdict = is_valid(occ.text)
            if verdict is None:
                budget_hit = True
                break
            if not verdict:
                stats["carrier_rejected"] += 1
                continue
            chosen.append(_to_card(unit, occ, gloss))
            taken_forms.add(occ.form_key)
            if gloss is None:
                wanted.append(WantedCarrier(unit.unit_id, occ.corpus_source, occ.line_id, occ.text))
        if budget_hit:
            stats["units_past_validation_budget"] += 1
        cards.extend(chosen)
    stats["cards"] = len(cards)
    stats["glossed_cards"] = sum(1 for c in cards if c.gloss_en is not None)
    stats["wanted"] = len(wanted)
    stats["validations"] = validations
    return CardSelection(cards=cards, wanted=wanted, stats=dict(stats))


def with_card_counts(units: Iterable[PhraseUnit], cards: Iterable[PhraseCard]) -> list[PhraseUnit]:
    counts: dict[str, int] = defaultdict(int)
    glossed: dict[str, int] = defaultdict(int)
    for card in cards:
        counts[card.unit_id] += 1
        if card.gloss_en is not None:
            glossed[card.unit_id] += 1
    return [
        unit.model_copy(
            update={
                "card_count": counts.get(unit.unit_id, 0),
                "glossed_card_count": glossed.get(unit.unit_id, 0),
            }
        )
        for unit in units
    ]
