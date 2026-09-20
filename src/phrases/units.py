"""Occurrences in, units out: thresholds, association, rank, trivial flag.

Detection is generous on purpose; every decision about what is a unit lives
here so it can be re-tuned from ``report.json`` without re-parsing the corpus.
The association measure abstains where counts are sparse (TODO item 4 of the
grammar trainer, kept): an unattested pair of two rare words is evidence of
nothing.
"""

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.contracts import UNIT_ID_PREFIX, WORD_KINDS, Case, PhraseUnit
from src.lexicon.lemmatizer import SEPARABLE_PREFIXES, normalise
from src.lexicon.vocabulary import VocabularyStore
from src.phrases import paradigms, verb_government
from src.phrases.carrier_validation import _load_dictionary
from src.phrases.curated import CuratedLists
from src.phrases.mining import LemmaCounts
from src.phrases.mining.collocations import _STOP_ADJECTIVES
from src.phrases.mining.common import CONTRACTED_PREPS
from src.phrases.occurrences import Occurrence

DEFAULT_VOCAB_PATH = Path("data/fixtures/corpus/vocab_levels.json")

#: The corpora whose register is everyday speech rather than news or web prose.
EVERYDAY_SOURCES: frozenset[str] = frozenset({"tatoeba", "opensubtitles_2018"})
#: Ranking weight per corpus; everyday corpora count tenfold (owner's
#: decision, 2026-09-11). Unlisted corpora weigh one.
SOURCE_WEIGHTS: dict[str, float] = dict.fromkeys(EVERYDAY_SOURCES, 10.0)

_CEFR_RANK: dict[str, int] = {"A1": 1, "A2": 2, "B1": 3, "B2": 4}
#: Parts that carry no vocabulary of their own: the reflexive pronoun and
#: the prepositions of verb-preposition units and Funktionsverbgefuege.
_FUNCTION_PARTS: frozenset[str] = frozenset(
    {
        "sich",
        "an",
        "auf",
        "aus",
        "bei",
        "durch",
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
        "zur",
        "zum",
        "im",
        "am",
        "ins",
        "als",
    }
)
_KIND_ORDER: dict[str, int] = {
    "noun": -6,
    "verb": -5,
    "adjective": -4,
    "adverb": -3,
    "adj_verb": -2,
    "expression": -1,
    "verb_prep": 0,
    "reflexive_verb": 1,
    "separable_verb": 2,
    "noun_verb": 3,
    "adj_noun": 4,
    "connector": 5,
    "two_part_connector": 6,
    "idiom": 7,
}

_REFLEXIVE_HAND_LISTS: frozenset[str] = (
    paradigms.ACCUSATIVE_ONLY_REFLEXIVE_VERBS
    | paradigms.DATIVE_REFLEXIVE_VERBS_NO_OBJECT
    | paradigms.DATIVE_REFLEXIVE_VERBS_WITH_OBJECT
)


@dataclass(frozen=True)
class Thresholds:
    min_count: int = 5
    vp_min_ratio: float = 0.05
    vp_min_lift: float = 3.0
    rv_min_share: float = 0.3
    #: A ``sich V prep`` unit replaces ``sich V`` when it covers this share.
    reflexive_prep_share: float = 0.6
    colloc_min_count: int = 5
    colloc_min_g2: float = 15.13
    colloc_min_lift: float = 5.0
    colloc_min_lemma_count: int = 20
    colloc_cap_per_verb: int = 8
    #: An adjective-noun pair is taught inside its preposition ("in
    #: sicherer Entfernung", three gaps) when one preposition governs at
    #: least this share of its sentences; otherwise as the bare pair.
    prep_share: float = 0.8
    colloc_cap_per_noun: int = 4
    adj_cap_per_noun: int = 2
    #: Free adjective-noun combinations ("alter Mann") pass the collocation
    #: bar on Tatoeba's repetitive sentences; the review flagged them as not
    #: worth a card, so adjective-noun pairs need stronger evidence. Raised
    #: hard on 2026-09-20: the adjective and the noun are now units in their
    #: own right, which makes most of the pairs redundant, so only the set
    #: phrases stay ("die goldene Regel", "kuenstliche Intelligenz").
    adj_min_count: int = 40
    adj_min_lift: float = 25.0
    case_majority: float = 0.75
    trivial_rank_max: int = 300
    #: A mined collocation must also occur in everyday registers (Tatoeba,
    #: subtitles) this often. The web and news corpora carry boilerplate pairs
    #: ("personenbezogene Daten", "erneuerbare Energie") that are real
    #: collocations and useless to a learner; everyday evidence is what
    #: separates "gute Idee" from them.
    colloc_min_everyday: int = 3
    #: Single words. ``word_min_count`` is the sentence count a word needs to
    #: be worth teaching; 100 over 4M sentences leaves about 9,400 nouns,
    #: verbs and adjectives (measured 2026-09-20). ``word_share`` is the
    #: proportion of the ranking given to single words: the owner's choice of
    #: 2026-09-20 is one interleaved ranking rather than words first, because
    #: a word is always at least as frequent as any phrase containing it and
    #: would otherwise fill the first two thousand ranks.
    word_min_count: int = 100
    word_share: float = 0.6
    #: Adjective + verb, as for the other collocations but stricter, and the
    #: modifier must be used as an adjective somewhere: the tagger calls a
    #: predicative adjective an adverb, so "oft kommen" reaches the detector
    #: too.
    adj_verb_min_count: int = 8
    adj_verb_min_lift: float = 8.0
    adj_verb_min_adjective_use: int = 50
    adj_verb_cap_per_verb: int = 6
    #: An "adjective" is a word the tagger calls one in at least this share
    #: of its sentences. Predicative adjectives are tagged ADV, so the bar is
    #: low: measured 2026-09-20, "gut" 42%, "schnell" 18%, "ernst" 19%, while
    #: "endlich" is 0.7% and "sofort", "kaum", "gern" are 0%.
    adjective_min_share: float = 0.05


@dataclass
class UnitStats:
    kind: str
    key: str
    parts: Counter[tuple[str, ...]] = field(default_factory=Counter)
    sentences: set[tuple[str, str]] = field(default_factory=set)
    count_by_source: Counter[str] = field(default_factory=Counter)
    case_tally: Counter[str] = field(default_factory=Counter)
    pron_case_tally: Counter[str] = field(default_factory=Counter)
    surface_tally: Counter[str] = field(default_factory=Counter)
    #: Noun-verb units only: the noun as written. The citation form uses
    #: the commonest one, so "Angaben machen" is not cited as "Angabe machen"
    #: and "Vertrauen gewinnen" keeps its capital.
    noun_surfaces: Counter[str] = field(default_factory=Counter)
    #: Adjective-noun units only: nominative surfaces with the article that
    #: precedes them, so the citation form reads "ein guter Zweck", not the
    #: oblique "guten Zweck" the corpus shows most often.
    nominative_surfaces: Counter[str] = field(default_factory=Counter)
    #: Adjective-noun units only: the pair with the preposition that governs
    #: it, for units that live inside one ("in sicherer Entfernung", "mit
    #: offenen Armen") and never occur in the nominative.
    prep_phrases: Counter[str] = field(default_factory=Counter)
    form_keys: Counter[str] = field(default_factory=Counter)
    discontinuous: int = 0
    fused: int = 0
    #: Noun units only: the gender the corpus shows, for the article in the
    #: citation form ("die Frage"). The gap stays the noun alone.
    gender_tally: Counter[str] = field(default_factory=Counter)
    #: Single-word kinds only. Their occurrences are capped at parse time
    #: (``mining.words``), so the sentence set understates them; the counts
    #: file holds the true corpus figures and these two fields carry them.
    corpus_count: int = 0
    corpus_count_by_source: Counter[str] = field(default_factory=Counter)

    @property
    def count(self) -> int:
        return self.corpus_count or len(self.sentences)

    @property
    def best_parts(self) -> tuple[str, ...]:
        return self.parts.most_common(1)[0][0]


_TRANSLITERATE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def slug(key: str) -> str:
    """ASCII id fragment: umlauts transliterated, everything else collapsed
    to underscores. ``"sich interessieren für"`` -> ``"sich_interessieren_fuer"``."""
    text = key.lower().translate(_TRANSLITERATE)
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


def unit_id_for(kind: str, key: str) -> str:
    return f"{UNIT_ID_PREFIX[kind]}:{slug(key)}"


def log_likelihood(pair: int, a: int, b: int, n: int) -> float:
    """Dunning's G² over the 2x2 contingency of ``a`` and ``b`` co-occurring
    in ``pair`` of ``n`` sentences."""

    def term(observed: int, expected: float) -> float:
        if observed == 0 or expected <= 0:
            return 0.0
        return observed * math.log(observed / expected)

    o11, o12, o21 = pair, a - pair, b - pair
    o22 = n - a - b + pair
    if min(o11, o12, o21, o22) < 0 or n <= 0:
        return 0.0
    e11 = a * b / n
    e12 = a * (n - b) / n
    e21 = (n - a) * b / n
    e22 = (n - a) * (n - b) / n
    return 2 * (term(o11, e11) + term(o12, e12) + term(o21, e21) + term(o22, e22))


def lift(pair: int, a: int, b: int, n: int) -> float:
    if a == 0 or b == 0 or n == 0:
        return 0.0
    return (pair / n) / ((a / n) * (b / n))


#: Prepositions that never mark a verb's complement: "ohne" anywhere, and
#: "durch" on a participle, where it is the passive agent ("wird durch ...
#: geregelt"). Review step 1 flagged eight such "V durch" units in the top 700.
_ADJUNCT_PREPS: frozenset[str] = frozenset({"ohne"})
_PASSIVE_AGENT_PREPS: frozenset[str] = frozenset({"durch"})

_VERB_INDEX: dict[str, int] = {
    "verb_prep": 0,
    "reflexive_verb": 1,
    "separable_verb": 0,
    "noun_verb": -1,
    "adj_verb": -1,
    "verb": 0,
}


#: Strong participles the tagger leaves as their own lemma, beyond the
#: paradigm table. Keyed without "ge" so prefixed forms resolve through one
#: lookup: "eingefangen" -> "fangen" -> "einfangen", "unterzogen" ->
#: "ziehen" -> "unterziehen", "entschlossen" -> "schließen".
_STRONG_STEMS: dict[str, str] = {
    "bissen": "beißen",
    "blasen": "blasen",
    "bogen": "biegen",
    "boten": "bieten",
    "bunden": "binden",
    "drungen": "dringen",
    "fangen": "fangen",
    "flossen": "fließen",
    "froren": "frieren",
    "gangen": "gehen",
    "glichen": "gleichen",
    "glitten": "gleiten",
    "gossen": "gießen",
    "graben": "graben",
    "griffen": "greifen",
    "hoben": "heben",
    "klungen": "klingen",
    "kniffen": "kneifen",
    "krochen": "kriechen",
    "liehen": "leihen",
    "litten": "leiden",
    "logen": "lügen",
    "messen": "messen",
    "mieden": "meiden",
    "pfiffen": "pfeifen",
    "raten": "raten",
    "rieben": "reiben",
    "rissen": "reißen",
    "ritten": "reiten",
    "rochen": "riechen",
    "rungen": "ringen",
    "sandt": "senden",
    "schieden": "scheiden",
    "schlichen": "schleichen",
    "schliffen": "schleifen",
    "schmissen": "schmeißen",
    "schnitten": "schneiden",
    "schoben": "schieben",
    "schossen": "schießen",
    "schritten": "schreiten",
    "schrien": "schreien",
    "schwiegen": "schweigen",
    "schwollen": "schwellen",
    "schworen": "schwören",
    "schwunden": "schwinden",
    "sotten": "sieden",
    "spien": "speien",
    "stochen": "stechen",
    "stohlen": "stehlen",
    "stritten": "streiten",
    "strichen": "streichen",
    "sunken": "sinken",
    "trieben": "treiben",
    "troffen": "treffen",
    "trogen": "trügen",
    "wandt": "wenden",
    "wichen": "weichen",
    "wiesen": "weisen",
    "wogen": "wiegen",
    "wunden": "winden",
    "zogen": "ziehen",
    "zwungen": "zwingen",
}
_STRONG_STEMS.update(
    {
        (k[2:] if k.startswith("ge") else k): v
        for k, v in paradigms.PARTICIPLE_II_TO_INFINITIVE.items()
        if k.startswith("ge") and k.endswith("n")
    }
)
# "gelegen" stripped of "ge" is "legen", which is the infinitive of another
# verb; keeping it would turn every "anlegen" into "anliegen".
_STRONG_STEMS.pop("legen", None)
_ALL_PREFIXES: tuple[str, ...] = tuple(
    sorted(
        set(SEPARABLE_PREFIXES)
        | set(paradigms._INSEPARABLE_PREFIXES)
        | {"über", "unter", "um", "durch", "wider", "hinter", "wieder"},
        key=len,
        reverse=True,
    )
)


#: Verbs whose infinitive begins with "ge": the one shape the participle
#: tables would otherwise misread ("geraten" is not the participle of "raten").
_GE_INFINITIVES: frozenset[str] = frozenset(
    {
        "geraten",
        "gefallen",
        "gehören",
        "gelingen",
        "genießen",
        "geschehen",
        "gestehen",
        "gewinnen",
        "gebären",
        "gedeihen",
        "gelangen",
        "gewähren",
        "gewöhnen",
        "gebrauchen",
        "gefrieren",
        "genügen",
        "gestalten",
        "gelten",
        "gehen",
        "geben",
        "gedenken",
        "gefährden",
        "gehorchen",
    }
)


def _infinitive_of_preterite(part: str, dictionary: frozenset[str] | None) -> str | None:
    """The infinitive behind a preterite string left as lemma: strong
    ("ankamen" -> "ankommen") through the stem table under any prefix,
    weak ("füllten" -> "füllen") gated on the dictionary."""
    prefix = ""
    rest = part
    for _ in range(3):
        for ending in ("en", "st", "t", "e", ""):
            stem = rest[: len(rest) - len(ending)] if ending else rest
            if stem in paradigms.PRAETERITUM_STEMS:
                return prefix + paradigms.PRAETERITUM_STEMS[stem]
        hit = next(
            (p for p in _ALL_PREFIXES if rest.startswith(p) and len(rest) > len(p) + 2), None
        )
        if hit is None or hit == "ge":
            break
        prefix += hit
        rest = rest[len(hit) :]
    if part.endswith("ten") and len(part) > 5:
        candidate = part[:-3] + "en"
        if dictionary is not None and normalise(candidate) in dictionary:
            return candidate
    return None


def _infinitive_of_participle(part: str, dictionary: frozenset[str] | None) -> str | None:
    """The infinitive behind a Partizip II string, or ``None``. Strong forms
    resolve through ``_STRONG_STEMS`` under any prefix; weak forms
    ("angepasst", "angefordert") drop "ge" and swap "-t" for "-en"/"-n",
    gated on the dictionary."""
    if part in paradigms.PARTICIPLE_II_TO_INFINITIVE:
        return paradigms.PARTICIPLE_II_TO_INFINITIVE[part]
    prefix = ""
    rest = part
    while True:
        if rest in _STRONG_STEMS:
            return prefix + _STRONG_STEMS[rest]
        hit = next(
            (p for p in _ALL_PREFIXES if rest.startswith(p) and len(rest) > len(p) + 2), None
        )
        if hit is None:
            break
        if hit == "ge":
            rest = rest[2:]
            continue
        prefix += hit
        rest = rest[len(hit) :]
    if part.endswith("t"):
        core = part[len(prefix) :] if prefix and part.startswith(prefix) else part
        core = core[2:] if core.startswith("ge") else core
        stem = core[:-1]
        candidate = prefix + stem + ("n" if stem.endswith(("er", "el")) else "en")
        if dictionary is None or normalise(candidate) in dictionary:
            return candidate
    return None


#: Verb pairs the tagger confuses because one's finite stem looks like the
#: other's: "legt ... an" lemmatised as "anliegen", "fuhr ... vor" as
#: "vorführen". Keyed by (lemma base, surface stem prefix) -> right base.
_STEM_CONFUSIONS: dict[tuple[str, str], str] = {
    ("liegen", "leg"): "legen",
    ("legen", "lieg"): "liegen",
    ("legen", "lag"): "liegen",
    ("führen", "fahr"): "fahren",
    ("führen", "fähr"): "fahren",
    ("führen", "fuhr"): "fahren",
    ("fahren", "führ"): "führen",
    ("sitzen", "setz"): "setzen",
    ("setzen", "sitz"): "sitzen",
    ("setzen", "saß"): "sitzen",
}


def _fix_stem_confusion(verb: str, surfaces: list[str]) -> str:
    for (base, stem), right in _STEM_CONFUSIONS.items():
        if not verb.endswith(base):
            continue
        prefix = verb[: -len(base)]
        for s in surfaces:
            low = s.lower()
            if low.startswith(prefix + stem) or (prefix and low.startswith(stem)):
                return prefix + right
    return verb


def canonical_verb(
    verb: str,
    surfaces: list[str],
    form_key: str,
    dictionary: frozenset[str] | None,
    infinitives: Mapping[str, object] | None = None,
) -> str | None:
    verb = _fix_stem_confusion(verb, surfaces)
    """The infinitive a mined verb part stands for, or ``None`` when the part
    is not one the deck can cite.

    Two tagger slips reach here. A participle left as its own lemma
    (form ``Part`` and the surface equals the part) is fine when the string
    is also the infinitive ("erhalten", "bekommen"); otherwise ("gelitten",
    "angepasst") it maps through the participle tables or is dropped. A
    zu-infinitive left as lemma ("sich einzubringen") loses its ``zu``.
    """
    lowered = {s.lower() for s in surfaces}
    # A separated verb's lemma is particle + verb surface ("wuchsen … auf").
    lowered.add("".join(s.lower() for s in reversed(surfaces)))
    # The Goethe list also lists some participles ("geschrien", "gestritten"),
    # so a listed string counts as an infinitive only when the participle
    # tables do not resolve it to a different verb.
    known_infinitive = verb in _GE_INFINITIVES or (
        infinitives is not None
        and normalise(verb) in infinitives
        and verb.endswith("n")
        and _infinitive_of_participle(verb, dictionary) in {None, verb}
    )
    # A present plural surface is the infinitive itself ("sie haften"); when
    # the tagger's lemma disagrees ("hafen") the surface wins.
    if (
        form_key.startswith("Fin|Pres")
        and ("|1|Plur" in form_key or "|3|Plur" in form_key)
        and not form_key.endswith("discontinuous")
    ):
        for s in surfaces:
            low = s.lower()
            # Only the verb's own surface, one letter off ("haften" for
            # "hafen"): a particle or preposition surface ("zusammen", "zu",
            # "offen") is not the verb (step 4 finding).
            if (
                low != verb.lower()
                and low.endswith("en")
                and len(low) > 3
                and abs(len(low) - len(verb)) <= 1
                and low[:3] == verb[:3]
                and low not in {"sein", "haben", "werden"}
                and dictionary is not None
                and normalise(low) in dictionary
                and not known_infinitive
            ):
                return low
    # The surface is the lemma: right for an infinitive ("wir kommen",
    # "hat erhalten"), a tagger slip for a participle, a preterite or a
    # zu-infinitive, whatever VerbForm the tagger claims ("hat eingestochen"
    # comes back as Inf).
    if verb.lower() in lowered and not known_infinitive:
        if form_key.startswith("Fin|Past") or not verb.endswith("n"):
            mapped = _infinitive_of_preterite(verb, dictionary) or (
                _infinitive_of_participle(verb, dictionary) if not verb.endswith("n") else None
            )
            if mapped is None:
                return None
            verb = mapped
        else:
            participle = _infinitive_of_participle(verb, dictionary)
            preterite = _infinitive_of_preterite(verb, dictionary)
            if participle not in {None, verb}:
                verb = participle
            elif preterite is not None and preterite != verb:
                verb = preterite
    if form_key.startswith("Inf"):
        for prefix in sorted(SEPARABLE_PREFIXES, key=len, reverse=True):
            if verb.startswith(prefix + "zu"):
                rest = verb[len(prefix) + 2 :]
                if rest and (dictionary is None or normalise(prefix + rest) in dictionary):
                    verb = prefix + rest
                break
    return verb


#: Mirrors ``parse.separable_particle``'s adverb and prepositional-phrase
#: rules on an already-parsed occurrence, so a fix there takes effect on
#: the stored parse without re-parsing five million sentences.
_ADVERB_PARTICLES: frozenset[str] = frozenset({"wieder", "weiter", "zurück"})
_PRONOMINAL_ADVERB_PREFIXES: tuple[str, ...] = ("da", "dar", "wo", "wor", "hier")
_PP_OBJECT_NEXT = re.compile(r"\s*(?::|„|“|http|\"\w|[A-ZÄÖÜ]\w|\d)")


def _junk_particle(occ: Occurrence, dictionary: frozenset[str] | None) -> bool:
    if occ.kind != "separable_verb" or not occ.form_key.endswith("discontinuous"):
        return False
    if len(occ.surfaces) != 2 or len(occ.spans) != 2:
        return False
    particle = occ.surfaces[-1].lower()
    lowered_text = occ.text.lower()
    # "es gibt ... raus" is existential "es gibt" with an adverb, and
    # "..., nicht wahr?" is a tag question; neither is a separable verb.
    if occ.surfaces[0].lower() in {"gibt", "gab", "gebe", "geben", "gäbe"} and (
        "es gibt" in lowered_text or "gibt es" in lowered_text or "gab es" in lowered_text
    ):
        return True
    if particle == "wahr" and "nicht wahr" in lowered_text:
        return True
    fused_is_word = dictionary is None or normalise(occ.unit_key) in dictionary
    if (
        particle not in SEPARABLE_PREFIXES
        and particle not in paradigms.KNOWN_PARTICLES
        and not fused_is_word
    ):
        return True
    if particle.startswith(_PRONOMINAL_ADVERB_PREFIXES) and particle != "dazu":
        if not fused_is_word:
            return True
    if particle in _ADVERB_PARTICLES and not fused_is_word:
        return True
    tail = occ.text[occ.spans[-1][1] :]
    return _PP_OBJECT_NEXT.match(tail) is not None


def with_governing_preposition(occ: Occurrence) -> Occurrence:
    """An adjective-noun pair right after a preposition becomes a
    three-token occurrence ("in sicherer Entfernung"): the preposition is
    the hardest part of the phrase and must be a gap, not a giveaway.
    Which variant becomes the unit is decided in ``UnitBuilder.build``
    (``_settle_prepositional``); the cards stage folds back the loser."""
    if len(occ.parts) != 2 or len(occ.spans) != 2:
        return occ
    start = occ.spans[0][0]
    head = occ.text[:start]
    if not head.endswith(" "):
        return occ
    before = head.rstrip().split(" ")[-1] if head.strip() else ""
    clean = before.lower().strip('"„“(')
    if clean not in _PHRASE_PREPS or before != clean:
        return occ
    prep_start = len(head.rstrip()) - len(before)
    if occ.text[prep_start : prep_start + len(before)] != before:
        return occ
    bare = CONTRACTED_PREPS.get(clean, clean)
    return occ.model_copy(
        update={
            "unit_key": f"{bare} {occ.unit_key}",
            "parts": [bare, *occ.parts],
            "token_indices": [occ.token_indices[0] - 1, *occ.token_indices],
            "spans": [(prep_start, prep_start + len(before)), *occ.spans],
            "surfaces": [before, *occ.surfaces],
        }
    )


def without_governing_preposition(occ: Occurrence) -> Occurrence:
    """The inverse, for the cards stage when the bare pair won."""
    if occ.kind != "adj_noun" or len(occ.parts) != 3:
        return occ
    return occ.model_copy(
        update={
            "unit_key": " ".join(p.lower() for p in occ.parts[1:]),
            "parts": list(occ.parts[1:]),
            "token_indices": list(occ.token_indices[1:]),
            "spans": list(occ.spans[1:]),
            "surfaces": list(occ.surfaces[1:]),
        }
    )


def canonical_occurrence(
    occ: Occurrence,
    dictionary: frozenset[str] | None,
    infinitives: Mapping[str, object] | None = None,
) -> Occurrence | None:
    """Rewrite an occurrence's key to its citation form, or drop it."""
    if occ.kind == "adj_noun":
        if occ.parts[-2].lower() in _STOP_ADJECTIVES:
            return None
        return with_governing_preposition(occ)
    index = _VERB_INDEX.get(occ.kind)
    if index is None:
        return occ
    if _junk_particle(occ, dictionary):
        return None
    parts = list(occ.parts)
    if occ.kind in {"verb_prep", "reflexive_verb"} and len(parts) >= 2:
        prep = parts[-1].lower()
        if prep in _ADJUNCT_PREPS:
            return None
        if prep in _PASSIVE_AGENT_PREPS and occ.form_key.startswith("Part"):
            return None
    verb_surface = next((s for s in occ.surfaces if s.lower() == parts[index].lower()), None)
    if verb_surface is not None and verb_surface[:1].isupper() and occ.spans[0][0] > 0:
        # "im Hafen in": a noun the tagger lemmatised as a verb.
        return None
    verb = canonical_verb(parts[index], occ.surfaces, occ.form_key, dictionary, infinitives)
    if verb is None:
        return None
    if verb == parts[index]:
        return occ
    parts[index] = verb
    key = " ".join(p.lower() for p in parts)
    return occ.model_copy(update={"parts": parts, "unit_key": key})


_ARTICLES: frozenset[str] = frozenset({"der", "die", "das", "ein", "eine"})
_PHRASE_PREPS: frozenset[str] = frozenset(
    {
        "in",
        "im",
        "an",
        "am",
        "auf",
        "mit",
        "bei",
        "beim",
        "unter",
        "aus",
        "vor",
        "nach",
        "zu",
        "zur",
        "zum",
        "von",
        "vom",
        "für",
        "über",
        "ohne",
        "durch",
    }
)


def _adj_noun_surface(occ: Occurrence) -> str:
    """The pair as written, adjective lower-cased: a sentence-initial
    "Heftiger Regen" is not a citation form."""
    adj, *rest = occ.surfaces
    return " ".join([adj[:1].lower() + adj[1:], *rest])


def _word_before(occ: Occurrence) -> str:
    before = occ.text[: occ.spans[0][0]].split()
    return before[-1].lower().strip('"„“(') if before else ""


def _nominative_phrase(occ: Occurrence) -> str:
    phrase = _adj_noun_surface(occ)
    before = _word_before(occ)
    if before in _ARTICLES:
        return f"{before} {phrase}"
    return phrase


def _merge_counts(dst: UnitStats, src: UnitStats) -> None:
    """Fold ``src``'s evidence into ``dst`` (everything but ``parts``, which
    would let the loser's shape win ``best_parts``)."""
    dst.sentences |= src.sentences
    dst.count_by_source += src.count_by_source
    dst.case_tally += src.case_tally
    dst.pron_case_tally += src.pron_case_tally
    dst.surface_tally += src.surface_tally
    dst.form_keys += src.form_keys
    dst.noun_surfaces += src.noun_surfaces
    dst.nominative_surfaces += src.nominative_surfaces
    dst.prep_phrases += src.prep_phrases


_WORD_ARTICLE: dict[str, str] = {"Masc": "der", "Fem": "die", "Neut": "das"}


def interleave_by_share[T](words: list[T], phrases: list[T], share: float) -> list[T]:
    """One ranking out of two, in the given proportion.

    A single word is always at least as frequent as any phrase containing it,
    so a straight frequency sort puts every word before almost every phrase.
    The owner's choice of 2026-09-20 is to interleave: with ``share`` 0.6 the
    ranking runs six words to four phrases, each group keeping its own
    frequency order.
    """
    share = min(max(share, 0.0), 1.0)
    if not words or share <= 0.0:
        return list(phrases)
    if not phrases or share >= 1.0:
        return list(words)
    keyed: list[tuple[float, int, T]] = []
    keyed += [(i / share, 0, item) for i, item in enumerate(words)]
    keyed += [(j / (1.0 - share), 1, item) for j, item in enumerate(phrases)]
    keyed.sort(key=lambda row: (row[0], row[1]))
    return [item for _, _, item in keyed]


def _settle_prepositional(stats: dict[tuple[str, str], UnitStats], share: float) -> None:
    """One unit per adjective-noun pair: the prepositional variant when a
    single preposition governs at least ``share`` of the pair's sentences,
    else the bare pair. The loser's sentences count for the winner's rank;
    its occurrences become the winner's cards only when they carry the
    winner's tokens (the cards stage folds prepositional occurrences back
    to a bare winner, never the reverse)."""
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for kind, key in list(stats):
        if kind != "adj_noun":
            continue
        pair = " ".join(key.split()[-2:])
        groups[pair].append((kind, key))
    for pair, idents in groups.items():
        if len(idents) == 1 and idents[0][1] == pair:
            continue
        total = sum(stats[i].count for i in idents)
        preps = [i for i in idents if i[1] != pair]
        best = max(preps, key=lambda i: stats[i].count) if preps else None
        if best is not None and total and stats[best].count / total >= share:
            winner = best
        else:
            winner = ("adj_noun", pair)
            if winner not in stats:
                stats[winner] = UnitStats(kind="adj_noun", key=pair)
                stats[winner].parts[tuple(pair.split())] += 0
        for ident in idents:
            if ident == winner:
                continue
            _merge_counts(stats[winner], stats[ident])
            if not stats[winner].parts or sum(stats[winner].parts.values()) == 0:
                # A bare winner that never occurred bare: cite it from the
                # prepositional shape's adjective and noun.
                stats[winner].parts[tuple(stats[ident].best_parts[-2:])] += 1
            del stats[ident]


def aggregate(
    occurrences: Iterable[Occurrence],
    dictionary: frozenset[str] | None = None,
    infinitives: Mapping[str, object] | None = None,
) -> dict[tuple[str, str], UnitStats]:
    stats: dict[tuple[str, str], UnitStats] = {}
    for raw in occurrences:
        canonical = canonical_occurrence(raw, dictionary, infinitives)
        if canonical is None:
            continue
        occ = canonical
        entry = stats.get((occ.kind, occ.unit_key))
        if entry is None:
            entry = UnitStats(kind=occ.kind, key=occ.unit_key)
            stats[(occ.kind, occ.unit_key)] = entry
        if occ.sentence_key in entry.sentences:
            continue
        entry.sentences.add(occ.sentence_key)
        entry.count_by_source[occ.corpus_source] += 1
        entry.parts[tuple(occ.parts)] += 1
        entry.surface_tally[" ".join(occ.surfaces)] += 1
        if occ.kind == "adj_noun" and len(occ.parts) == 2:
            if occ.form_key.startswith("Nom|"):
                entry.nominative_surfaces[_nominative_phrase(occ)] += 1
        if occ.kind == "noun":
            gender = occ.evidence.get("gender", "")
            if gender:
                entry.gender_tally[gender] += 1
        if occ.kind == "noun_verb" and len(occ.parts) == 2:
            stem = normalise(occ.parts[0])[:4]
            for surface in occ.surfaces:
                if normalise(surface).startswith(stem):
                    entry.noun_surfaces[surface] += 1
                    break
        entry.form_keys[occ.form_key] += 1
        if occ.case is not None:
            entry.case_tally[occ.case] += 1
        pron_case = occ.evidence.get("pron_case")
        if pron_case:
            entry.pron_case_tally[pron_case] += 1
        if occ.form_key.endswith("discontinuous"):
            entry.discontinuous += 1
        elif occ.form_key.endswith("fused"):
            entry.fused += 1
    return stats


def _majority_case(tally: Counter[str], share: float) -> Case | None:
    total = sum(tally.values())
    if total == 0:
        return None
    case, count = tally.most_common(1)[0]
    if count / total >= share and case in {"Akk", "Dat", "Gen"}:
        return case  # type: ignore[return-value]
    return None


def _base_prep_counts(counts: LemmaCounts) -> Counter[str]:
    merged: Counter[str] = Counter()
    for prep, count in counts.prepositions.items():
        merged[CONTRACTED_PREPS.get(prep, prep)] += count
    return merged


@dataclass
class _Decision:
    accept: bool
    reason: str = ""
    case: Case | None = None
    cefr: str | None = None
    gloss_en: str | None = None
    display: str | None = None
    source: str = "mined"
    trivial: bool = False
    trivial_reason: str | None = None
    score: float = 0.0
    also_accepted: tuple[str, ...] = ()
    #: Set when the decision settles a kind the detector could not
    #: (``adjective`` against ``adverb``); otherwise the stats' kind stands.
    kind: str | None = None


class UnitBuilder:
    def __init__(
        self,
        counts: LemmaCounts,
        curated: CuratedLists,
        thresholds: Thresholds | None = None,
        vocabulary: VocabularyStore | None = None,
        frequency_ranks: dict[str, int] | None = None,
        dictionary: frozenset[str] | None = None,
    ) -> None:
        self.counts = counts
        self.curated = curated
        self.t = thresholds or Thresholds()
        self.vocabulary = vocabulary or VocabularyStore.load(DEFAULT_VOCAB_PATH)
        self.frequency_ranks = (
            frequency_ranks if frequency_ranks is not None else self.vocabulary._frequency_ranks
        )
        self.prep_counts = _base_prep_counts(counts)
        self.vp_seeds = {s.key: s for s in curated.verb_prep_seeds}
        self.colloc_seeds = {s.key: s for s in curated.collocation_seeds}
        self.connectors = {c.key: c for c in curated.connectors}
        self.idioms = {i.key: i for i in curated.idioms}
        self.stoplist = set(curated.trivial_stoplist)
        self.excluded = set(curated.exclude)
        self.dictionary = dictionary if dictionary is not None else _load_dictionary()
        self.overrides = {o.key: o for o in curated.overrides}
        self.report: dict[str, Any] = {
            "rejected": Counter(),
            "seed_disagreements": [],
            "zero_hit_curated": [],
            "top_by_kind": {},
        }
        #: Components of an accepted collocation, kept even when they fall
        #: below the word threshold, so "Frage", "stellen" and "eine Frage
        #: stellen" are all units (owner, 2026-09-20).
        self.forced_words: set[str] = set()

    # -- per-kind decisions ---------------------------------------------------

    def _cefr_for(self, lemma: str) -> str | None:
        return self.vocabulary.get_level(lemma)

    def _unit_cefr(self, s: UnitStats, d: _Decision) -> str | None:
        """A mined unit is as hard as its hardest content word, and a word
        the Goethe lists do not know is B2: "nachweisen" is not A1 because
        "weisen" is. Review step 1 flagged 41 such labels in the top 700.
        (A register signal from the everyday corpora was tried and dropped:
        with four news and web sources it moved "stattfinden" to B2.)"""
        if d.source != "mined" or s.kind in {"connector", "two_part_connector", "idiom"}:
            return d.cefr
        verb_index = _VERB_INDEX.get(s.kind)
        verb_part = s.best_parts[verb_index] if verb_index is not None else None
        levels: list[str | None] = []
        for part in s.best_parts:
            if part.lower() in _FUNCTION_PARTS:
                continue
            if part == verb_part:
                # Direct hit only: ``get_level`` would strip the separable prefix
                # and score "nachweisen" as "weisen", A1.
                levels.append(self.vocabulary.vocab.get(normalise(part)))
            else:
                levels.append(self._cefr_for(part))
        level = max((lv for lv in levels if lv), key=_CEFR_RANK.__getitem__, default=None)
        if any(lv is None for lv in levels):
            level = "B2"
        return level

    def _decide_verb_prep(self, s: UnitStats, stats: dict[tuple[str, str], UnitStats]) -> _Decision:
        verb, prep = s.best_parts[0], s.best_parts[-1]
        seed = self.vp_seeds.get(s.key)
        n = self.counts.sentences
        verb_count = self.counts.verbs.get(verb, 0)
        prep_count = self.prep_counts.get(prep, 0)
        ratio = s.count / verb_count if verb_count else 0.0
        lift_value = lift(s.count, verb_count, prep_count, n)
        corpus_case = _majority_case(s.case_tally, self.t.case_majority)
        if seed is not None:
            if seed.case is not None and corpus_case is not None and seed.case != corpus_case:
                self.report["seed_disagreements"].append(
                    {"unit": s.key, "seed_case": seed.case, "corpus_case": corpus_case}
                )
            return _Decision(
                True,
                case=seed.case or corpus_case,
                cefr=seed.cefr or self._cefr_for(verb),
                gloss_en=seed.gloss_en,
                source="mined+curated" if s.count else "curated",
                score=lift_value,
            )
        # A verb whose preposition use is mostly reflexive is taught as the
        # reflexive unit instead.
        reflexive = stats.get(("reflexive_verb", f"sich {s.key}"))
        if (
            reflexive is not None
            and s.count
            and reflexive.count / s.count >= self.t.reflexive_prep_share
        ):
            return _Decision(False, "mostly_reflexive")
        if s.count < self.t.min_count:
            return _Decision(False, "count")
        if ratio < self.t.vp_min_ratio:
            return _Decision(False, "ratio")
        if lift_value < self.t.vp_min_lift:
            return _Decision(False, "lift")
        return _Decision(True, case=corpus_case, cefr=self._cefr_for(verb), score=lift_value)

    def _decide_reflexive(self, s: UnitStats, stats: dict[tuple[str, str], UnitStats]) -> _Decision:
        parts = s.best_parts
        verb = parts[1]
        has_prep = len(parts) == 3
        seed = self.vp_seeds.get(s.key)
        bare = stats.get(("reflexive_verb", f"sich {verb}"))
        bare_count = bare.count if bare is not None else 0
        # Pronoun case: the corpus tally first, the government lexicon second.
        case: Case | None = None
        tally_total = sum(s.pron_case_tally.values())
        if tally_total >= self.t.min_count:
            case = _majority_case(s.pron_case_tally, 0.9)
        if case is None:
            verdict = verb_government.reflexive_verdict(verb)
            case = {"Dat": "Dat", "Acc": "Akk"}.get(verdict or "")  # type: ignore[assignment]
        if has_prep:
            prep_case = _majority_case(s.case_tally, self.t.case_majority)
            if seed is not None:
                if seed.case is not None and prep_case is not None and seed.case != prep_case:
                    self.report["seed_disagreements"].append(
                        {"unit": s.key, "seed_case": seed.case, "corpus_case": prep_case}
                    )
                return _Decision(
                    True,
                    case=seed.case or prep_case,
                    cefr=seed.cefr or self._cefr_for(verb),
                    gloss_en=seed.gloss_en,
                    source="mined+curated" if s.count else "curated",
                    score=float(s.count),
                )
            if s.count < self.t.min_count:
                return _Decision(False, "count")
            if bare_count and s.count / bare_count < self.t.reflexive_prep_share:
                return _Decision(False, "prep_share")
            return _Decision(True, case=prep_case, cefr=self._cefr_for(verb), score=float(s.count))
        # Bare ``sich V``: dropped when a prepositional unit took it over.
        for (kind, key), other in stats.items():
            if kind == "reflexive_verb" and key.startswith(f"sich {verb} ") and other.count:
                if key in self.vp_seeds or (
                    other.count >= self.t.min_count
                    and other.count / s.count >= self.t.reflexive_prep_share
                ):
                    return _Decision(False, "taken_by_prep_unit")
        # "sich einmischen +Akk" misreads: the accusative is the pronoun's own
        # case, not an object the verb governs. Only a dative pronoun
        # ("sich etwas vorstellen": mir, dir) is worth showing.
        if case == "Akk":
            case = None
        verb_count = self.counts.verbs.get(verb, 0)
        share = s.count / verb_count if verb_count else 0.0
        if verb in _REFLEXIVE_HAND_LISTS and s.count >= 1:
            return _Decision(True, case=case, cefr=self._cefr_for(verb), score=share)
        if s.count < self.t.min_count:
            return _Decision(False, "count")
        if share < self.t.rv_min_share:
            return _Decision(False, "share")
        return _Decision(True, case=case, cefr=self._cefr_for(verb), score=share)

    def _decide_separable(self, s: UnitStats) -> _Decision:
        if s.discontinuous == 0:
            return _Decision(False, "fused_only")
        if s.count < self.t.min_count:
            return _Decision(False, "count")
        return _Decision(True, cefr=self._cefr_for(s.key), score=float(s.count))

    def _decide_collocation(self, s: UnitStats) -> _Decision:
        parts = s.best_parts
        seed = self.colloc_seeds.get(s.key)
        if s.kind == "noun_verb":
            noun_lemma = parts[-2].lower()
            verb_lemma = parts[-1]
            a, b = self.counts.nouns.get(noun_lemma, 0), self.counts.verbs.get(verb_lemma, 0)
            head = verb_lemma
        else:
            a = self.counts.adjectives.get(parts[-2].lower(), 0)
            b = self.counts.nouns.get(parts[-1].lower(), 0)
            head = parts[-1].lower()
        n = self.counts.sentences
        g2 = log_likelihood(s.count, a, b, n)
        lift_value = lift(s.count, a, b, n)
        if seed is not None:
            return _Decision(
                True,
                cefr=seed.cefr or self._cefr_for(head),
                gloss_en=seed.gloss_en,
                display=seed.display,
                source="mined+curated" if s.count else "curated",
                score=g2,
            )
        min_count = self.t.adj_min_count if s.kind == "adj_noun" else self.t.colloc_min_count
        min_lift = self.t.adj_min_lift if s.kind == "adj_noun" else self.t.colloc_min_lift
        if s.count < min_count:
            return _Decision(False, "count")
        # A mined collocation is taught only when its noun is on the Goethe
        # A1-B1 lists or in the B2 frequency band. This is what keeps the news
        # corpus's "Täter festnehmen" out while "Frage stellen" stays in: the
        # word lists are the learner's vocabulary, the corpus is not.
        if min(a, b) < self.t.colloc_min_lemma_count:
            return _Decision(False, "sparse")
        noun = parts[-2].lower() if s.kind == "noun_verb" else parts[-1].lower()
        if self._cefr_for(noun) is None:
            return _Decision(False, "noun_not_in_wordlist")
        everyday = sum(s.count_by_source.get(src, 0) for src in EVERYDAY_SOURCES)
        if self.counts.sentences_by_source and everyday < self.t.colloc_min_everyday:
            return _Decision(False, "no_everyday_evidence")
        if g2 < self.t.colloc_min_g2:
            return _Decision(False, "g2")
        if lift_value < min_lift:
            return _Decision(False, "lift")
        return _Decision(True, cefr=self._cefr_for(head), score=g2)

    def _decide_word(self, s: UnitStats) -> _Decision:
        """A single word. The frequency comes from the counts file, not from
        the capped occurrence tally, and the final kind is settled here: the
        tagger files a predicative adjective under ADV, so the detector emits
        both as ``adjective`` and the corpus decides which it mostly is."""
        lemma = s.key
        if s.count < self.t.word_min_count and lemma not in self.forced_words:
            return _Decision(False, "count")
        if lemma in self.connectors or lemma in self.stoplist:
            return _Decision(False, "curated_connector")
        kind = s.kind
        if kind == "adjective":
            kind = self._modifier_kind(lemma)
        return _Decision(True, cefr=self._cefr_for(lemma), score=float(s.count), kind=kind)

    def _modifier_kind(self, lemma: str) -> str:
        """Adjective or adverb, from how the corpus tags the word.

        The tagger calls a predicative adjective an adverb, so a comparison
        of the two counts files "gut" (42% adjective) under the adverbs. A
        true adverb is a different animal: "endlich" is tagged adjective in
        0.7% of its sentences, "sofort" and "kaum" in none. A small share of
        adjective uses is therefore enough to call it an adjective.
        """
        adjective_uses = self.counts.adjectives.get(lemma, 0)
        adverb_uses = self.counts.adverbs.get(lemma, 0)
        total = adjective_uses + adverb_uses
        if not total:
            return "adjective"
        if adjective_uses >= max(20, self.t.adjective_min_share * total):
            return "adjective"
        return "adverb"

    def _decide_adj_verb(self, s: UnitStats) -> _Decision:
        parts = s.best_parts
        if len(parts) < 2:
            return _Decision(False, "parts")
        adjective, verb = parts[-2].lower(), parts[-1]
        seed = self.colloc_seeds.get(s.key)
        if seed is not None:
            return _Decision(True, cefr=seed.cefr, gloss_en=seed.gloss_en, score=float(s.count))
        if s.count < self.t.adj_verb_min_count:
            return _Decision(False, "count")
        # The modifier has to be an adjective somewhere, or every adverb that
        # happens to modify a verb ("oft kommen") becomes a collocation.
        if self.counts.adjectives.get(adjective, 0) < self.t.adj_verb_min_adjective_use:
            return _Decision(False, "not_an_adjective")
        a = self.counts.adjectives.get(adjective, 0)
        b = self.counts.verbs.get(verb, 0)
        n = self.counts.sentences
        if min(a, b) < self.t.colloc_min_lemma_count:
            return _Decision(False, "sparse")
        everyday = sum(s.count_by_source.get(src, 0) for src in EVERYDAY_SOURCES)
        if self.counts.sentences_by_source and everyday < self.t.colloc_min_everyday:
            return _Decision(False, "no_everyday_evidence")
        g2 = log_likelihood(s.count, a, b, n)
        if g2 < self.t.colloc_min_g2:
            return _Decision(False, "g2")
        if lift(s.count, a, b, n) < self.t.adj_verb_min_lift:
            return _Decision(False, "lift")
        return _Decision(True, cefr=self._cefr_for(verb), score=g2)

    def _decide_curated(self, s: UnitStats) -> _Decision:
        if s.kind in {"connector", "two_part_connector"}:
            spec = self.connectors.get(s.key)
            if spec is None:
                return _Decision(False, "unknown_connector")
            return _Decision(
                True,
                cefr=spec.cefr,
                gloss_en=spec.gloss_en,
                also_accepted=tuple(spec.also_accepted),
                display=spec.display,
                source="curated",
                trivial=spec.trivial,
                trivial_reason="curated" if spec.trivial else None,
                score=float(s.count),
            )
        idiom = self.idioms.get(s.key)
        if idiom is None:
            return _Decision(False, "unknown_idiom")
        return _Decision(
            True,
            cefr=idiom.cefr,
            gloss_en=idiom.gloss_en,
            display=idiom.display,
            source="curated",
            score=float(s.count),
        )

    # -- assembly ---------------------------------------------------------------

    def _display(self, s: UnitStats, decision: _Decision) -> str:
        if decision.display:
            return decision.display
        if s.kind in WORD_KINDS:
            return self._word_display(s)
        parts = s.best_parts
        if s.kind in {"verb_prep", "reflexive_verb", "separable_verb"}:
            return " ".join(parts)
        if s.kind == "noun_verb":
            if s.noun_surfaces and len(parts) == 2:
                return f"{s.noun_surfaces.most_common(1)[0][0]} {parts[1]}"
            return " ".join(parts)
        if s.kind == "adj_noun":
            if len(parts) == 3 and s.surface_tally:
                prep, _, rest = s.surface_tally.most_common(1)[0][0].partition(" ")
                adj, _, noun = rest.partition(" ")
                return f"{prep.lower()} {adj[:1].lower()}{adj[1:]} {noun}"
            if s.nominative_surfaces:
                return s.nominative_surfaces.most_common(1)[0][0]
            if s.surface_tally:
                adj, _, noun = s.surface_tally.most_common(1)[0][0].partition(" ")
                return f"{adj[:1].lower()}{adj[1:]} {noun}"
            return " ".join(parts)
        return s.key

    def _apply_corpus_counts(self, stats: dict[tuple[str, str], UnitStats]) -> None:
        """Single-word frequency comes from the counts file, which saw every
        sentence, not from the occurrence tally, which is capped."""
        for (kind, key), s in stats.items():
            if kind not in WORD_KINDS:
                continue
            by_source = self.counts.word_by_source.get(f"{kind}:{key}")
            if by_source:
                s.corpus_count_by_source = Counter(by_source)
                s.corpus_count = sum(by_source.values())
            else:
                role = self.counts.role_counts(kind) if hasattr(self.counts, "role_counts") else {}
                s.corpus_count = role.get(key, 0)

    def _trivial(self, s: UnitStats, decision: _Decision) -> tuple[bool, str | None]:
        if decision.trivial:
            return True, decision.trivial_reason
        if s.key in self.stoplist:
            return True, "stoplist"
        if " " not in s.key and (s.kind in {"connector", "idiom"} or s.kind in WORD_KINDS):
            rank = self.frequency_ranks.get(normalise(s.key))
            if rank is not None and rank <= self.t.trivial_rank_max:
                return True, f"rank<={self.t.trivial_rank_max}"
        return False, None

    def _word_display(self, s: UnitStats) -> str:
        """The citation form of a single word. A noun carries its article so
        the gender is taught alongside it (owner, 2026-09-20); the gap in the
        sentence stays the noun alone."""
        lemma = s.key
        if s.kind != "noun":
            return lemma
        noun = lemma[:1].upper() + lemma[1:]
        gender = s.gender_tally.most_common(1)
        article = _WORD_ARTICLE.get(gender[0][0]) if gender else None
        return f"{article} {noun}" if article else noun

    def _apply_caps(
        self,
        accepted: dict[tuple[str, str], tuple[UnitStats, _Decision]],
        excluded: list[UnitStats] | None = None,
    ) -> None:
        """Cap collocations per verb and per noun. A unit a reviewer excluded
        still occupies its slot: otherwise every exclusion pulls the next,
        weaker candidate into the deck ("Buch durchlesen" after "Buch
        lesen"), and the review never converges (step 3 finding)."""
        by_verb: dict[str, list[tuple[str, str]]] = defaultdict(list)
        by_noun: dict[str, list[tuple[str, str]]] = defaultdict(list)
        by_noun_adj: dict[str, list[tuple[str, str]]] = defaultdict(list)
        by_verb_adj: dict[str, list[tuple[str, str]]] = defaultdict(list)
        taken: dict[str, Counter[str]] = {
            "verb": Counter(),
            "noun": Counter(),
            "noun_adj": Counter(),
            "verb_adj": Counter(),
        }
        for s in excluded or []:
            if s.kind == "noun_verb" and len(s.best_parts) >= 2:
                taken["verb"][s.best_parts[-1]] += 1
                taken["noun"][s.best_parts[-2].lower()] += 1
            elif s.kind == "adj_noun" and len(s.best_parts) >= 2:
                taken["noun_adj"][s.best_parts[-1].lower()] += 1
            elif s.kind == "adj_verb" and len(s.best_parts) >= 2:
                taken["verb_adj"][s.best_parts[-1]] += 1
        for ident, (s, d) in accepted.items():
            if d.source != "mined":
                continue
            if s.kind == "noun_verb":
                by_verb[s.best_parts[-1]].append(ident)
                by_noun[s.best_parts[-2].lower()].append(ident)
            elif s.kind == "adj_noun":
                by_noun_adj[s.best_parts[-1].lower()].append(ident)
            elif s.kind == "adj_verb":
                by_verb_adj[s.best_parts[-1]].append(ident)

        def trim(
            groups: dict[str, list[tuple[str, str]]],
            cap: int,
            reason: str,
            occupied: Counter[str],
        ) -> None:
            for head, group in groups.items():
                members = [ident for ident in group if ident in accepted]
                free = max(cap - occupied[head], 0)
                if len(members) <= free:
                    continue
                members.sort(key=lambda ident: -accepted[ident][1].score)
                for ident in members[free:]:
                    if ident in accepted:
                        del accepted[ident]
                        self.report["rejected"][reason] += 1

        trim(by_verb, self.t.colloc_cap_per_verb, "cap_per_verb", taken["verb"])
        trim(by_noun, self.t.colloc_cap_per_noun, "cap_per_noun", taken["noun"])
        trim(by_noun_adj, self.t.adj_cap_per_noun, "cap_adj_per_noun", taken["noun_adj"])
        trim(by_verb_adj, self.t.adj_verb_cap_per_verb, "cap_adj_per_verb", taken["verb_adj"])

    def _ensure_curated_present(self, stats: dict[tuple[str, str], UnitStats]) -> None:
        """Curated entries with no corpus hit still become (card-less) units,
        ranked last, and are listed in the report."""
        for spec in self.curated.connectors:
            if (spec.kind, spec.key) not in stats:
                stats[(spec.kind, spec.key)] = UnitStats(kind=spec.kind, key=spec.key)
                stats[(spec.kind, spec.key)].parts[(spec.key,)] += 0
                self.report["zero_hit_curated"].append(spec.key)
        for idiom in self.curated.idioms:
            if ("idiom", idiom.key) not in stats:
                stats[("idiom", idiom.key)] = UnitStats(kind="idiom", key=idiom.key)
                stats[("idiom", idiom.key)].parts[(idiom.key,)] += 0
                self.report["zero_hit_curated"].append(idiom.key)
        for seed in self.curated.verb_prep_seeds:
            kind = "reflexive_verb" if seed.reflexive else "verb_prep"
            if (kind, seed.key) not in stats:
                stats[(kind, seed.key)] = UnitStats(kind=kind, key=seed.key)
                parts = (["sich"] if seed.reflexive else []) + [seed.verb, seed.prep]
                stats[(kind, seed.key)].parts[tuple(parts)] += 0
                self.report["zero_hit_curated"].append(seed.key)
        for colloc in self.curated.collocation_seeds:
            if (colloc.kind, colloc.key) not in stats:
                stats[(colloc.kind, colloc.key)] = UnitStats(kind=colloc.kind, key=colloc.key)
                stats[(colloc.kind, colloc.key)].parts[tuple(colloc.key.split())] += 0
                self.report["zero_hit_curated"].append(colloc.key)

    def build(self, occurrences: Iterable[Occurrence]) -> list[PhraseUnit]:
        stats = aggregate(occurrences, self.dictionary, self.vocabulary.vocab)
        _settle_prepositional(stats, self.t.prep_share)
        self._ensure_curated_present(stats)
        self._apply_corpus_counts(stats)
        accepted: dict[tuple[str, str], tuple[UnitStats, _Decision]] = {}
        excluded_stats: list[UnitStats] = []
        # Phrases first, so the words a collocation needs are known before the
        # word threshold is applied: "eine Frage stellen" is only worth
        # teaching beside "Frage" and "stellen" (owner, 2026-09-20).
        word_idents = [ident for ident, s in stats.items() if s.kind in WORD_KINDS]
        phrase_idents = [ident for ident, s in stats.items() if s.kind not in WORD_KINDS]
        for ident in phrase_idents + word_idents:
            s = stats[ident]
            if s.key in self.excluded:
                self.report["rejected"][f"{s.kind}:excluded"] += 1
                excluded_stats.append(s)
                continue
            if s.kind in WORD_KINDS:
                decision = self._decide_word(s)
            elif s.kind == "verb_prep":
                decision = self._decide_verb_prep(s, stats)
            elif s.kind == "reflexive_verb":
                decision = self._decide_reflexive(s, stats)
            elif s.kind == "separable_verb":
                decision = self._decide_separable(s)
            elif s.kind == "adj_verb":
                decision = self._decide_adj_verb(s)
            elif s.kind in {"noun_verb", "adj_noun"}:
                decision = self._decide_collocation(s)
            else:
                decision = self._decide_curated(s)
            if not decision.accept:
                self.report["rejected"][f"{s.kind}:{decision.reason}"] += 1
                continue
            accepted[ident] = (s, decision)
            if s.kind in {"noun_verb", "adj_verb"}:
                self.forced_words.update(part.lower() for part in s.best_parts)
        self._apply_caps(accepted, excluded_stats)

        def frequency_key(
            item: tuple[tuple[str, str], tuple[UnitStats, _Decision]],
        ) -> tuple[float, int, int, str]:
            stat = item[1][0]
            return (-self._per_million(stat), -stat.count, _KIND_ORDER[stat.kind], stat.key)

        rows = list(accepted.items())
        words = sorted((r for r in rows if r[1][0].kind in WORD_KINDS), key=frequency_key)
        phrases = sorted((r for r in rows if r[1][0].kind not in WORD_KINDS), key=frequency_key)
        ordered = interleave_by_share(words, phrases, self.t.word_share)
        units: list[PhraseUnit] = []
        seen_ids: set[str] = set()
        for rank, (_, (s, d)) in enumerate(ordered, start=1):
            kind = d.kind or s.kind
            unit_id = unit_id_for(kind, s.key)
            if unit_id in seen_ids:
                self.report["rejected"]["duplicate_id"] += 1
                continue
            seen_ids.add(unit_id)
            trivial, reason = self._trivial(s, d)
            override = self.overrides.get(s.key)
            units.append(
                PhraseUnit(
                    unit_id=unit_id,
                    kind=kind,  # type: ignore[arg-type]
                    lemma_key=s.key,
                    parts=list(s.best_parts),
                    display_de=(override.display if override and override.display else None)
                    or self._display(s, d),
                    case=(override.case if override and override.case else d.case),
                    cefr=(override.cefr if override and override.cefr else self._unit_cefr(s, d)),  # type: ignore[arg-type]
                    gloss_en=(override.gloss_en if override and override.gloss_en else d.gloss_en),
                    sentence_count=s.count,
                    count_by_source=dict(sorted(s.count_by_source.items())),
                    per_million=round(self._per_million(s), 3),
                    rank=rank,
                    also_accepted=list(d.also_accepted),
                    trivial=trivial,
                    trivial_reason=reason,
                    source=d.source,  # type: ignore[arg-type]
                    card_count=0,
                )
            )
        self.report["rejected"] = dict(self.report["rejected"])
        by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for unit in units:
            if len(by_kind[unit.kind]) < 200:
                by_kind[unit.kind].append(
                    {"unit": unit.lemma_key, "count": unit.sentence_count, "case": unit.case}
                )
        self.report["top_by_kind"] = dict(by_kind)
        self.report["unit_count"] = len(units)
        self.report["kinds"] = dict(Counter(u.kind for u in units))
        self.report["wordlist_coverage"] = self._wordlist_coverage(units)
        return units

    def _per_million(self, s: UnitStats) -> float:
        """Weighted mean over the corpora of sentences-per-million in that
        corpus. Each corpus gets one vote, the everyday ones (Tatoeba,
        subtitles) ten: with equal votes four news and web corpora put
        "jedoch" and "zudem" at ranks 9 and 13, which the first learner
        found too hard too early (they are B1/B2 on the Goethe lists); with
        this weight they sit at 27 and 42 behind "anrufen" and "zu Hause"."""
        totals = self.counts.sentences_by_source
        if not totals:
            return float(s.count)
        weighted = 0.0
        weight_sum = 0.0
        by_source = s.corpus_count_by_source or s.count_by_source
        for source, n in totals.items():
            if not n:
                continue
            weight = SOURCE_WEIGHTS.get(source, 1.0)
            weighted += weight * 1e6 * by_source.get(source, 0) / n
            weight_sum += weight
        return weighted / weight_sum if weight_sum else 0.0

    def _head_lemma(self, unit: PhraseUnit) -> str:
        if unit.kind in WORD_KINDS:
            return unit.lemma_key
        if unit.kind in {"verb_prep", "separable_verb"}:
            return unit.parts[0]
        if unit.kind == "reflexive_verb":
            return unit.parts[1]
        if unit.kind == "noun_verb":
            return unit.parts[-2].lower()
        if unit.kind == "adj_noun":
            return unit.parts[-1].lower()
        return unit.lemma_key

    def _wordlist_coverage(self, units: list[PhraseUnit]) -> dict[str, Any]:
        """Every unit's head lemma against the Goethe A1-B1 lists and the B2
        band (``data/fixtures/corpus/vocab_levels.json``). Units whose head is
        on no list are listed per kind for review."""
        by_level: Counter[str] = Counter()
        missing: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for unit in units:
            level = unit.cefr or self._cefr_for(self._head_lemma(unit))
            by_level[level or "none"] += 1
            if level is None and len(missing[unit.kind]) < 200:
                missing[unit.kind].append(
                    {"unit": unit.lemma_key, "rank": unit.rank, "count": unit.sentence_count}
                )
        return {"by_level": dict(by_level), "not_in_wordlist": dict(missing)}
