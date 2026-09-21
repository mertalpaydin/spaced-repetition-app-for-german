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

from src.contracts import WORD_KINDS, GapSpan, PhraseCard, PhraseUnit
from src.phrases.occurrences import Occurrence

TRUSTED_GLOSS_SOURCES: frozenset[str] = frozenset({"azure", "gemini"})
GLOSS_LENGTH_RATIO: tuple[float, float] = (0.4, 2.5)
#: Score penalty for a sentence with no gloss yet: a glossed sentence of the
#: same form wins, an un-glossed short one still beats a glossed long one.
UNGLOSSED_PENALTY: float = 30.0
#: Score penalty by corpus. Tatoeba sentences are written for learners and
#: reviewed by people; news prose is clean; the web, the 2011 mixed corpus
#: (OCR artefacts, old spelling) and subtitle cues (fragments, dialogue
#: ellipsis) are last resorts. Review step 1, 2026-09-09: 1 finding in 4
#: Tatoeba cards against 60 in 859 subtitle cards.
SOURCE_PENALTY: dict[str, float] = {
    "tatoeba": 0.0,
    "leipzig_news_2025": 15.0,
    "leipzig_news_2024": 15.0,
    "leipzig_web_2021": 25.0,
    "leipzig_mixed_2011": 35.0,
    "opensubtitles_2018": 40.0,
}
#: Tokens of the pre-1996 orthography and OCR damage that the validator lets
#: through; a sentence carrying one never becomes a card.
_OLD_SPELLING = re.compile(
    r"\b(daß|muß|mußte|mußten|müßte|müßten|läßt|ließ|paßt|paßte|ißt|faßt|faßte|haßt"
    r"|schluß|fluß|bißchen|gewußt|wußte|wußten|küßt|küßte|mißt|blaß|naß|kraß|Schluß|Fluß"
    r"|Bißchen|Genuß|Anschluß|Einfluß|Prozeß|Kongreß|Kompromiß|Streß|Schloß)\b"
)
_MOJIBAKE = re.compile("Ã.|â€|Â|�")
_BROKEN_HYPHEN = re.compile(r"[a-zäöüß]-[a-zäöüß]")


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


#: A sentence that opens with one of these and never reaches a main clause
#: ("Weil er mich eingeladen hat.") is a fragment the validator lets through.
_SUBORDINATORS: frozenset[str] = frozenset(
    {
        "sodass",
        "weil",
        "dass",
        "obwohl",
        "wenn",
        "als",
        "ob",
        "damit",
        "nachdem",
        "bevor",
        "während",
        "sobald",
        "solange",
        "falls",
        "sofern",
        "indem",
        "seitdem",
        "bis",
    }
)
_QUOTE_CHARS = '"\u201e\u201c\u201d\u00ab\u00bb'
_WORD = re.compile(r"[\w\u00e4\u00f6\u00fc\u00c4\u00d6\u00dc\u00df]+")


def _reflexive_next_to_verb(occ: Occurrence) -> bool:
    """``Es stellt sich heraus``: the sentence shows the reflexive verb,
    which is its own unit, not the plain one the card would teach.

    The window started as the two words after the verb, grew to both sides
    when single words became units, and is now the whole sentence, because
    each round of the review of 2026-09-21 turned up carriers the previous
    window missed: "wo es sich befindet", then "wir beschaeftigen uns mit",
    then "Ich habe mich auf die Pruefung vorbereitet", where the pronoun is
    five words from the participle.
    """
    start, end = occ.spans[0]
    following = [w.lower() for w in _WORD.findall(occ.text[end:])[:2]]
    preceding = [w.lower() for w in _WORD.findall(occ.text[:start])[-2:]]
    beside = [*following, *preceding]
    words = [w.lower() for w in _WORD.findall(occ.text)]
    # "sich" anywhere in the sentence: a participle or an infinitive at the
    # end sits far from its pronoun ("Ich habe mich auf die Pruefung
    # vorbereitet"), and the two-word window missed those. A carrier dropped
    # in error costs nothing, since the unit has others; a reflexive carrier
    # kept teaches the wrong unit.
    if "sich" in beside or "sich" in words:
        return True
    # "mich", "uns" and "euch" are plain objects as often as reflexives
    # ("Er ruft uns an"), so they count only when the sentence also carries
    # the subject they would have to agree with: "wir ... uns", "ich ...
    # mich". Without this the deck kept drawing reflexive carriers for
    # "beschaeftigen" and "interessieren", whose reflexive share (0.31 and
    # 0.46) sits under the bar that makes a verb a reflexive unit, and
    # lowering that bar would take "aendern", "vorstellen" and 75 more
    # ordinary verbs with it (measured 2026-09-21).
    return any(
        subject in words and any(pronoun in words for pronoun in pronouns)
        for subject, pronouns in _AGREEING_REFLEXIVES.items()
    )


#: Subject pronoun to the reflexive pronouns that agree with it, accusative
#: and dative. The dative ones were added on 2026-09-21, when the review of
#: ranks 1000 to 2000 found "sich etwas merken" and "sich etwas ueberlegen"
#: taught as plain verbs: those verbs take "mir" and "dir", which the
#: accusative-only rule never looked for.
_AGREEING_REFLEXIVES: dict[str, tuple[str, ...]] = {
    "ich": ("mich", "mir"),
    "du": ("dich", "dir"),
    "wir": ("uns",),
    "ihr": ("euch",),
}


_COMPLEMENT_PREPS: frozenset[str] = frozenset(
    {
        "an",
        "auf",
        "aus",
        "bei",
        "für",
        "gegen",
        "in",
        "mit",
        "nach",
        "über",
        "um",
        "unter",
        "von",
        "vor",
        "zu",
    }
)
_COMPLEMENT_RE = re.compile(r"\s(\w+)(?:\s\+(?:Akk|Dat|Gen))?$")
_CONTRACTIONS: dict[str, tuple[str, ...]] = {
    "an": ("am", "ans"),
    "auf": ("aufs",),
    "bei": ("beim",),
    "für": ("fürs",),
    "in": ("im", "ins"),
    "um": ("ums",),
    "über": ("übers",),
    "von": ("vom",),
    "zu": ("zum", "zur"),
}


#: Kinds whose display never hides a governing preposition the parts miss.
#: A new kind left out of this set has its display scanned by
#: ``_COMPLEMENT_RE`` and can acquire a spurious gap.
_NO_COMPLEMENT_KINDS: frozenset[str] = WORD_KINDS | {
    "verb_prep",
    "reflexive_verb",
    "connector",
    "two_part_connector",
    "expression",
}


def complement_preposition(unit: PhraseUnit) -> str | None:
    """The governing preposition a reviewer put into a unit's display that
    the mined parts do not carry ("Wert legen auf +Akk", "sich zubewegen auf
    +Akk"). Verb-preposition and reflexive units already gap theirs."""
    if unit.kind in _NO_COMPLEMENT_KINDS:
        return None
    m = _COMPLEMENT_RE.search(unit.display_de)
    if m is None:
        return None
    prep = m.group(1).lower()
    # An idiom's parts are one string ("ab und zu"); its own last word is
    # not a complement.
    own_words = {w.lower() for part in unit.parts for w in part.split()}
    if prep not in _COMPLEMENT_PREPS or prep in own_words:
        return None
    return prep


def with_complement_gap(occ: Occurrence, prep: str) -> Occurrence | None:
    """Add the complement preposition as one more gap when the sentence has
    exactly one candidate token for it outside the existing gaps (its bare
    or contracted form); otherwise the sentence does not instantiate the
    complement unambiguously and yields no card."""
    forms = (prep, *_CONTRACTIONS.get(prep, ()))
    pattern = re.compile(
        r"(?<![\wäöüÄÖÜß])(" + "|".join(re.escape(f) for f in forms) + r")(?![\wäöüÄÖÜß])",
        re.IGNORECASE,
    )
    hits = [
        m for m in pattern.finditer(occ.text) if not any(s <= m.start() < e for s, e in occ.spans)
    ]
    if len(hits) != 1:
        return None
    m = hits[0]
    spans = sorted([*occ.spans, (m.start(), m.end())])
    surfaces_by_span = dict(zip(occ.spans, occ.surfaces, strict=True))
    surfaces_by_span[(m.start(), m.end())] = m.group(0)
    # Token index: the number of whitespace-separated tokens before the hit,
    # which is what the existing indices count for spaCy's tokenisation of
    # these sentences closely enough for a gap ordering.
    before = len(occ.text[: m.start()].split())
    indices_by_span = dict(zip(occ.spans, occ.token_indices, strict=True))
    indices_by_span[(m.start(), m.end())] = before
    return occ.model_copy(
        update={
            "spans": spans,
            "surfaces": [surfaces_by_span[sp] for sp in spans],
            "token_indices": [indices_by_span[sp] for sp in spans],
        }
    )


def _prepositional_reading(occ: Occurrence, governed: frozenset[str]) -> bool:
    """A plain-verb carrier that really shows a verb the deck teaches with a
    preposition.

    "sorgen" and "sorgen fuer" are both units, and the plain one kept
    drawing sentences that use the prepositional one, so the card taught the
    wrong unit with the preposition outside the gap. The same held for
    "leiden unter", "denken an" and "entscheiden ueber" (review,
    2026-09-21). ``governed`` is what the deck itself teaches for this
    lemma, so the rule cannot invent a government the deck does not have.

    The preposition has to follow the verb and stay close to it: "Ich denke
    an dich" is the prepositional unit, while "An diesem Tag denke ich viel"
    is not.
    """
    if not governed:
        return False
    after = [w.lower() for w in _WORD.findall(occ.text[occ.spans[0][1] :])[:4]]
    pronominal: set[str] = set()
    for prep in governed:
        pronominal |= _PRONOMINAL_FORMS.get(prep, frozenset())
    if (set(governed) | pronominal) & set(after):
        return True
    # The pronominal form also comes before the verb, in a verb-final clause
    # or when it is fronted: "darauf achten", "darueber verfuegen", which
    # cost nine cards at ranks 1000 to 2000. Only the pronominal one is
    # checked backwards: a bare preposition before a verb is usually its own
    # phrase ("Auf dem Tisch liegt das Buch"), while "darauf" can only be
    # the government (review, 2026-09-21).
    before = [w.lower() for w in _WORD.findall(occ.text[: occ.spans[0][0]])[-6:]]
    return bool(pronominal & set(before))


def _pronominal(prep: str) -> frozenset[str]:
    """``fuer`` -> ``dafuer``, ``wofuer``; ``auf`` -> ``darauf``, ``worauf``.

    A preposition that governs a verb often appears as its pronominal
    adverb instead ("dafuer sorgen", "darauf warten"), and the review of
    2026-09-21 found the plain-verb cards drawing exactly those sentences
    after the first version of this rule shipped.
    """
    stem = "dar" if prep[0] in "aeiouäöü" else "da"
    ask = "wor" if prep[0] in "aeiouäöü" else "wo"
    return frozenset({stem + prep, ask + prep})


_PRONOMINAL_FORMS: dict[str, frozenset[str]] = {
    prep: _pronominal(prep) for prep in _COMPLEMENT_PREPS
}


def unsuitable_reason(occ: Occurrence, governed_preps: frozenset[str] = frozenset()) -> str | None:
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
    text = occ.text
    if text.endswith(("...", "…")) or text.lstrip().startswith(("-", "–", "—")):
        return "dialogue_fragment"
    if _MOJIBAKE.search(text):
        return "mojibake"
    if "http" in text.lower() or "www." in text.lower():
        return "url"
    if _OLD_SPELLING.search(text):
        return "old_spelling"
    if _BROKEN_HYPHEN.search(text) and "e-mail" not in text.lower():
        return "broken_hyphen"
    if occ.kind in {"separable_verb", "verb"} and _reflexive_next_to_verb(occ):
        return "reflexive_reading"
    if occ.kind == "verb" and _prepositional_reading(occ, governed_preps):
        return "prepositional_reading"
    first = _WORD.findall(occ.text.lower())[:1]
    if first and first[0] in _SUBORDINATORS and "," not in occ.text:
        return "subordinate_fragment"
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
    score += SOURCE_PENALTY.get(occ.corpus_source, 30.0)
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
        corpus_source=occ.corpus_source,
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
    # What the deck teaches as a verb plus preposition, per verb lemma, so a
    # plain-verb card never shows the prepositional unit instead.
    governed: dict[str, frozenset[str]] = {}
    units = list(units)
    for unit in units:
        if unit.kind == "verb_prep" and len(unit.parts) >= 2:
            verb, *preps = unit.parts
            governed[verb] = governed.get(verb, frozenset()) | {p.lower() for p in preps}

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
        complement = complement_preposition(unit)
        if complement is not None:
            occurrences = [
                o for o in (with_complement_gap(occ, complement) for occ in occurrences) if o
            ]
            stats["complement_missing"] += len(occurrences_by_unit.get(unit.unit_id, [])) - len(
                occurrences
            )
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
            reason = unsuitable_reason(group[0], governed.get(unit.lemma_key, frozenset()))
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
