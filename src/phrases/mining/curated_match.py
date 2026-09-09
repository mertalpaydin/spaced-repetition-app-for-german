"""Connectors and idioms: matched against the curated lists, never mined."""

from src.phrases.curated import ConnectorPart, ConnectorSpec, IdiomSpec
from src.phrases.mining.common import make_occurrence
from src.phrases.occurrences import Occurrence
from src.phrases.parse import ParsedSentence, ParsedToken, is_sentence_initial

_NOT_A_CONNECTOR_DEPS: frozenset[str] = frozenset(
    {"op", "svp", "pd", "oa", "da", "sb", "nk", "ag", "mnr", "pg", "og"}
)

_COMPARATIVE_WORDS: frozenset[str] = frozenset(
    {"mehr", "weniger", "eher", "lieber", "besser", "höher", "länger", "öfter", "größer"}
)


def _next_word(sentence: ParsedSentence, i: int) -> ParsedToken | None:
    for token in sentence.tokens[i + 1 :]:
        if token.pos != "PUNCT":
            return token
    return None


def _previous_word(sentence: ParsedSentence, i: int) -> ParsedToken | None:
    for token in reversed(sentence.tokens[:i]):
        if token.pos != "PUNCT":
            return token
    return None


def _match_part(
    sentence: ParsedSentence, part: ConnectorPart, *, start: int
) -> list[ParsedToken] | None:
    """The first match of ``part`` at or after token ``start``."""
    for form in part.forms:
        words = form.lower().split()
        for i in range(start, len(sentence.tokens) - len(words) + 1):
            window = sentence.tokens[i : i + len(words)]
            if [t.lower for t in window] != words:
                continue
            if part.pos is not None and window[0].pos not in part.pos:
                continue
            # "darum bitten", "dagegen sein", "daher|kommen": the same word
            # as a prepositional object, predicate or separable prefix.
            if len(words) == 1 and window[0].dep in _NOT_A_CONNECTOR_DEPS:
                continue
            if part.not_after is not None:
                previous = _previous_word(sentence, i)
                if previous is not None and previous.lower in {w.lower() for w in part.not_after}:
                    continue
            if part.next_must_be_comparative:
                following = _next_word(sentence, window[-1].i)
                if following is None:
                    continue
                if (
                    following.morph.get("Degree") != "Cmp"
                    and following.lower not in _COMPARATIVE_WORDS
                ):
                    continue
            return window
    return None


def _multiword_forms(specs: list[ConnectorSpec]) -> list[list[str]]:
    forms: list[list[str]] = []
    for spec in specs:
        parts = [spec.part()] if spec.kind == "connector" else [spec.first, spec.second]
        for part in parts:
            if part is None:
                continue
            forms.extend(f.lower().split() for f in part.forms if " " in f)
    return forms


def _inside_longer_form(
    sentence: ParsedSentence, window: list[ParsedToken], forms: list[list[str]]
) -> bool:
    """ "ob" inside "als ob": a single-word match that is part of a longer
    connector form in this sentence belongs to that longer form."""
    if len(window) != 1:
        return False
    i = window[0].i
    lower = [t.lower for t in sentence.tokens]
    for form in forms:
        n = len(form)
        for start in range(max(0, i - n + 1), min(i, len(lower) - n) + 1):
            if lower[start : start + n] == form:
                return True
    return False


def detect_connectors(
    sentence: ParsedSentence,
    specs: list[ConnectorSpec],
    *,
    source: str,
    line_id: str,
) -> list[Occurrence]:
    found: list[Occurrence] = []
    longer = _multiword_forms(specs)
    for spec in specs:
        if spec.kind == "connector":
            window = _match_part(sentence, spec.part(), start=0)
            if window is None or _inside_longer_form(sentence, window, longer):
                continue
            initial = is_sentence_initial(sentence, window[0].i)
            found.append(
                make_occurrence(
                    kind="connector",
                    unit_key=spec.key,
                    parts=[spec.key],
                    tokens=window,
                    sentence=sentence,
                    corpus_source=source,
                    line_id=line_id,
                    form_key="initial" if initial else "medial",
                    sentence_initial=initial,
                    needs_context=initial and spec.needs_context_when_initial,
                )
            )
            continue
        if spec.first is None or spec.second is None:
            continue
        first = _match_part(sentence, spec.first, start=0)
        if first is None:
            continue
        second = _match_part(sentence, spec.second, start=first[-1].i + 1)
        if second is None:
            continue
        found.append(
            make_occurrence(
                kind="two_part_connector",
                unit_key=spec.key,
                parts=[first[0].lower, second[0].lower],
                tokens=[*first, *second],
                sentence=sentence,
                corpus_source=source,
                line_id=line_id,
                form_key=f"{first[0].lower}|{second[0].lower}",
                sentence_initial=is_sentence_initial(sentence, first[0].i),
            )
        )
    return found


def _element_matches(token: ParsedToken, surface: str | None, lemma: str | None) -> bool:
    if surface is not None and token.lower == surface.lower():
        return True
    return lemma is not None and token.lemma.lower() == lemma


def detect_idioms(
    sentence: ParsedSentence,
    specs: list[IdiomSpec],
    *,
    source: str,
    line_id: str,
) -> list[Occurrence]:
    found: list[Occurrence] = []
    for spec in specs:
        first = spec.pattern[0]
        for start, token in enumerate(sentence.tokens):
            if not _element_matches(token, first.surface, first.lemma):
                continue
            matched = [token]
            position = start
            for element in spec.pattern[1:]:
                hit = None
                for candidate in sentence.tokens[position + 1 : position + 2 + spec.max_gap]:
                    if _element_matches(candidate, element.surface, element.lemma):
                        hit = candidate
                        break
                if hit is None:
                    break
                matched.append(hit)
                position = hit.i
            if len(matched) != len(spec.pattern):
                continue
            found.append(
                make_occurrence(
                    kind="idiom",
                    unit_key=spec.key,
                    parts=[spec.key],
                    tokens=matched,
                    sentence=sentence,
                    corpus_source=source,
                    line_id=line_id,
                    form_key="|".join(t.lower for t in matched),
                    sentence_initial=is_sentence_initial(sentence, matched[0].i),
                )
            )
            break
    return found
