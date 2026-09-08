"""Helpers every detector shares."""

from src.contracts import Case
from src.phrases.occurrences import Occurrence
from src.phrases.parse import ParsedSentence, ParsedToken

REFLEXIVE_FORMS: frozenset[str] = frozenset({"sich", "mich", "dich", "uns", "euch", "mir", "dir"})

#: Preposition-article contractions. The unit key uses the bare preposition;
#: the card blanks the contracted surface form as it stands.
CONTRACTED_PREPS: dict[str, str] = {
    "am": "an",
    "ans": "an",
    "im": "in",
    "ins": "in",
    "zum": "zu",
    "zur": "zu",
    "beim": "bei",
    "vom": "von",
    "aufs": "auf",
    "fürs": "für",
    "ums": "um",
    "übers": "über",
    "durchs": "durch",
    "hinters": "hinter",
    "unters": "unter",
    "vors": "vor",
}

_CASE_MAP: dict[str, Case] = {"Acc": "Akk", "Dat": "Dat", "Gen": "Gen"}

#: Verbs that never head a collocation worth teaching on their own.
LIGHT_VERB_LEMMAS: frozenset[str] = frozenset({"sein", "haben", "werden", "bleiben", "lassen"})


def base_preposition(prep: ParsedToken) -> str:
    return CONTRACTED_PREPS.get(prep.lower, prep.lower)


def prep_case(sentence: ParsedSentence, prep: ParsedToken) -> Case | None:
    """The case the preposition governs here, from its noun kernel's
    morphology or, for a contracted form, its own."""
    own = prep.morph.get("Case")
    if own in _CASE_MAP:
        return _CASE_MAP[own]
    cases = {
        c.morph["Case"]
        for c in sentence.children(prep.i)
        if c.dep == "nk" and c.morph.get("Case") in _CASE_MAP
    }
    if len(cases) == 1:
        return _CASE_MAP[cases.pop()]
    return None


def is_word(token: ParsedToken) -> bool:
    return token.text.replace("-", "").isalpha()


def make_occurrence(
    *,
    kind: str,
    unit_key: str,
    parts: list[str],
    tokens: list[ParsedToken],
    sentence: ParsedSentence,
    corpus_source: str,
    line_id: str,
    form_key: str = "",
    case: Case | None = None,
    sentence_initial: bool = False,
    needs_context: bool = False,
    evidence: dict[str, str] | None = None,
) -> Occurrence:
    ordered = sorted(tokens, key=lambda t: t.i)
    return Occurrence(
        kind=kind,  # type: ignore[arg-type]
        unit_key=unit_key,
        parts=parts,
        token_indices=[t.i for t in ordered],
        spans=[(t.idx, t.end) for t in ordered],
        surfaces=[t.text for t in ordered],
        corpus_source=corpus_source,
        line_id=line_id,
        text=sentence.text,
        form_key=form_key,
        case=case,
        sentence_initial=sentence_initial,
        needs_context=needs_context,
        evidence=evidence or {},
    )
