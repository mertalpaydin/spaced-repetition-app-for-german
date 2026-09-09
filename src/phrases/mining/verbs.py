"""Verb-headed units: verb + preposition, reflexive verbs, separable verbs.

All three share the same anchor, the lexical verb a dependent's head chain
leads to (``parse.lexical_verb``), so they live in one module and run in one
pass per sentence.
"""

from src.lexicon.lemmatizer import SEPARABLE_PREFIXES, normalise
from src.phrases import paradigms
from src.phrases.mining.common import (
    REFLEXIVE_FORMS,
    base_preposition,
    is_word,
    make_occurrence,
    prep_case,
)
from src.phrases.occurrences import Occurrence
from src.phrases.parse import (
    ParsedSentence,
    ParsedToken,
    form_key,
    is_sentence_initial,
    lexical_verb,
    separable_particle,
    verb_lemma_key,
)

#: (Person, Number) a non-``sich`` reflexive form requires of the subject.
_PRONOUN_AGREEMENT: dict[str, tuple[str, str]] = {
    "mich": ("1", "Sing"),
    "mir": ("1", "Sing"),
    "dich": ("2", "Sing"),
    "dir": ("2", "Sing"),
    "uns": ("1", "Plur"),
    "euch": ("2", "Plur"),
}
_PRONOUN_CASE: dict[str, str] = {"mich": "Akk", "dich": "Akk", "mir": "Dat", "dir": "Dat"}

#: Prepositions that are never a verb's complement when they attach as
#: ``mo``: temporal and quantitative frames.
_NEVER_COMPLEMENT: frozenset[str] = frozenset(
    {
        "seit",
        "während",
        "trotz",
        "wegen",
        "statt",
        "anstatt",
        "laut",
        "ab",
        "je",
        "pro",
        "per",
        "via",
        "binnen",
        "innerhalb",
        "außerhalb",
        "gemäß",
        "zwecks",
        "mangels",
        "dank",
    }
)


#: Nouns that make a prepositional phrase a time or measure frame rather
#: than a verb complement (``am Samstag stattfinden`` is not ``stattfinden an``).
TEMPORAL_NOUNS: frozenset[str] = frozenset(
    {
        "tag",
        "woche",
        "monat",
        "jahr",
        "stunde",
        "minute",
        "sekunde",
        "uhr",
        "zeit",
        "mal",
        "morgen",
        "vormittag",
        "mittag",
        "nachmittag",
        "abend",
        "nacht",
        "wochenende",
        "montag",
        "dienstag",
        "mittwoch",
        "donnerstag",
        "freitag",
        "samstag",
        "sonnabend",
        "sonntag",
        "januar",
        "februar",
        "märz",
        "april",
        "mai",
        "juni",
        "juli",
        "august",
        "september",
        "oktober",
        "november",
        "dezember",
        "beginn",
        "anfang",
        "ende",
        "jahrhundert",
        "jahrzehnt",
        "sommer",
        "winter",
        "herbst",
        "frühling",
        "frühjahr",
        "saison",
        "moment",
        "augenblick",
        "weile",
        "zeitpunkt",
        "termin",
        "datum",
        "feiertag",
        "weihnachten",
        "ostern",
        "silvester",
        "prozent",
        "euro",
        "dollar",
        "meter",
        "kilometer",
        "grad",
        "million",
        "milliarde",
        "ort",
        "stelle",
        "unfallort",
        "unfallstelle",
        "tatort",
        "einsatzort",
        "stadt",
        "haus",
        "hause",
        "straße",
        "platz",
        "rand",
        "seite",
        "boden",
        "wand",
        "tisch",
        "bett",
    }
)


def _is_time_or_measure_frame(sentence: ParsedSentence, prep: ParsedToken) -> bool:
    for child in sentence.children(prep.i):
        if child.dep != "nk":
            continue
        if any(ch.isdigit() for ch in child.text):
            return True
        if child.lemma.lower() in TEMPORAL_NOUNS or child.lower in TEMPORAL_NOUNS:
            return True
        if child.pos == "PROPN":
            return True
    return False


def _has_reflexive_dependent(sentence: ParsedSentence, verb: ParsedToken) -> bool:
    return any(
        c.pos == "PRON" and c.lower in REFLEXIVE_FORMS and c.morph.get("Reflex") == "Yes"
        for c in sentence.children(verb.i)
    )


def _finite_ancestor(sentence: ParsedSentence, token: ParsedToken) -> ParsedToken:
    current = token
    for _ in range(4):
        head = sentence.tokens[current.head]
        if head.i == current.i or head.morph.get("VerbForm") == "Fin":
            return head if head.morph.get("VerbForm") == "Fin" else current
        current = head
    return current


def _subject_of(sentence: ParsedSentence, verb: ParsedToken) -> ParsedToken | None:
    finite = _finite_ancestor(sentence, verb)
    for candidate in (verb, finite):
        subject = sentence.child_with_dep(candidate.i, "sb")
        if subject is not None:
            return subject
    return None


def _verb_tokens(sentence: ParsedSentence, verb: ParsedToken) -> list[ParsedToken]:
    particle = separable_particle(sentence, verb)
    return [verb, particle] if particle is not None else [verb]


def detect_verb_prep(sentence: ParsedSentence, *, source: str, line_id: str) -> list[Occurrence]:
    """Verb + preposition on verbs that are not used reflexively here; the
    reflexive detector owns those ("sich setzen auf" is not "setzen auf")."""
    return [
        occ
        for occ in verb_prep_candidates(sentence, source=source, line_id=line_id)
        if occ.evidence.get("reflexive") != "true"
    ]


def verb_prep_candidates(
    sentence: ParsedSentence, *, source: str, line_id: str
) -> list[Occurrence]:
    """Every verb + preposition pair, reflexive verbs included and flagged."""
    found: list[Occurrence] = []
    for prep in sentence.tokens:
        if prep.pos != "ADP" or prep.dep not in {"op", "mo"} or not is_word(prep):
            continue
        base = base_preposition(prep)
        if base in _NEVER_COMPLEMENT:
            continue
        # A preposition with no noun phrase under it is a particle or a
        # postposition, not a complement.
        if not any(c.dep == "nk" for c in sentence.children(prep.i)):
            continue
        if _is_time_or_measure_frame(sentence, prep):
            continue
        verb = lexical_verb(sentence, prep.i)
        if verb is None or verb.pos != "VERB":
            continue
        reflexive = _has_reflexive_dependent(sentence, verb)
        vkey = verb_lemma_key(sentence, verb)
        if not vkey:
            continue
        found.append(
            make_occurrence(
                kind="verb_prep",
                unit_key=f"{vkey} {base}",
                parts=[vkey, base],
                tokens=[*_verb_tokens(sentence, verb), prep],
                sentence=sentence,
                corpus_source=source,
                line_id=line_id,
                form_key=form_key(verb),
                # "als" is a comparative particle: the noun after it agrees
                # with its referent and carries no governed case.
                case=None if base == "als" else prep_case(sentence, prep),
                evidence={
                    "dep": prep.dep,
                    "verb_i": str(verb.i),
                    "prep_i": str(prep.i),
                    "reflexive": "true" if reflexive else "false",
                },
            )
        )
    return found


def detect_reflexive(
    sentence: ParsedSentence,
    verb_preps: list[Occurrence],
    *,
    source: str,
    line_id: str,
) -> list[Occurrence]:
    """``sich V`` for every reflexive pronoun bound to a lexical verb, plus a
    merged ``sich V prep`` for every verb+preposition match on the same verb,
    so the units stage can decide which of the two the corpus supports."""
    found: list[Occurrence] = []
    for pron in sentence.tokens:
        if pron.pos != "PRON" or pron.lower not in REFLEXIVE_FORMS:
            continue
        if pron.dep not in {"oa", "da", "mo", "sb"} or (pron.dep == "mo" and pron.lower != "sich"):
            continue
        if pron.dep == "sb":
            continue
        verb = lexical_verb(sentence, pron.i)
        if verb is None or verb.pos != "VERB":
            continue
        if pron.lower != "sich" and pron.morph.get("Reflex") != "Yes":
            required = _PRONOUN_AGREEMENT[pron.lower]
            subject = _subject_of(sentence, verb)
            if subject is None:
                continue
            if (subject.morph.get("Person"), subject.morph.get("Number")) != required:
                continue
        vkey = verb_lemma_key(sentence, verb)
        if not vkey:
            continue
        evidence = {"pron": pron.lower, "pron_case": _PRONOUN_CASE.get(pron.lower, "")}
        verb_tokens = _verb_tokens(sentence, verb)
        found.append(
            make_occurrence(
                kind="reflexive_verb",
                unit_key=f"sich {vkey}",
                parts=["sich", vkey],
                tokens=[*verb_tokens, pron],
                sentence=sentence,
                corpus_source=source,
                line_id=line_id,
                form_key=form_key(verb),
                evidence=evidence,
            )
        )
        for vp in verb_preps:
            if vp.evidence.get("verb_i") != str(verb.i):
                continue
            prep = sentence.tokens[int(vp.evidence["prep_i"])]
            found.append(
                make_occurrence(
                    kind="reflexive_verb",
                    unit_key=f"sich {vp.unit_key}",
                    parts=["sich", *vp.parts],
                    tokens=[*verb_tokens, pron, prep],
                    sentence=sentence,
                    corpus_source=source,
                    line_id=line_id,
                    form_key=vp.form_key,
                    case=vp.case,
                    evidence={**evidence, "with_prep": vp.parts[-1]},
                )
            )
    return found


def _fused_separable_key(verb: ParsedToken, dictionary: frozenset[str] | None) -> str | None:
    lemma = verb.lemma.lower()
    if verb.morph.get("VerbForm") not in {"Inf", "Part"} and verb.dep not in {"oc", "re", "cj"}:
        return None
    # A participle used predicatively ("das ist ausgezeichnet") is an adjective.
    if verb.dep == "pd":
        return None
    if lemma.startswith(paradigms._INSEPARABLE_PREFIXES):  # noqa: SLF001
        return None
    for prefix in sorted(SEPARABLE_PREFIXES, key=len, reverse=True):
        if lemma.startswith(prefix) and len(lemma) - len(prefix) >= 4:
            remainder = lemma[len(prefix) :]
            if dictionary is not None and normalise(remainder) not in dictionary:
                continue
            # "hinzukommen" in "um dort hinzukommen" is the zu-infinitive of
            # "hinkommen"; only a separated occurrence can vouch for the
            # longer verb, so the fused form abstains when both verbs exist.
            if remainder.startswith("zu") and dictionary is not None:
                if normalise(prefix + remainder[2:]) in dictionary:
                    return None
            return lemma
    return None


def detect_separable(
    sentence: ParsedSentence,
    *,
    source: str,
    line_id: str,
    dictionary: frozenset[str] | None,
) -> list[Occurrence]:
    found: list[Occurrence] = []
    for verb in sentence.tokens:
        if verb.pos != "VERB":
            continue
        particle = separable_particle(sentence, verb)
        if particle is not None:
            key = verb_lemma_key(sentence, verb)
            if not key:
                continue
            found.append(
                make_occurrence(
                    kind="separable_verb",
                    unit_key=key,
                    parts=[key],
                    tokens=[verb, particle],
                    sentence=sentence,
                    corpus_source=source,
                    line_id=line_id,
                    form_key=form_key(verb, suffix="discontinuous"),
                    evidence={"particle": particle.lower},
                )
            )
            continue
        fused_key = _fused_separable_key(verb, dictionary)
        if fused_key is None:
            continue
        found.append(
            make_occurrence(
                kind="separable_verb",
                unit_key=fused_key,
                parts=[fused_key],
                tokens=[verb],
                sentence=sentence,
                corpus_source=source,
                line_id=line_id,
                form_key=form_key(verb, suffix="fused"),
                evidence={"fused": "true"},
            )
        )
    return found


__all__ = [
    "detect_reflexive",
    "detect_separable",
    "detect_verb_prep",
    "is_sentence_initial",
]
