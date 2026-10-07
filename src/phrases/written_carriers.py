"""Sentences written for a word the corpus never uses.

Every card in this deck is an attested corpus sentence, which is the whole
point of mining one. But a word the owner asks for by hand may not be in the
corpus at all: of his list of 2026-10-07, six words ("Angestellte",
"aufgrund", "dazu", "dieselbe", "solche", "wodurch") had not one sentence
between them. The alternatives were to refuse those words or to write their
sentences, and he chose to write them (owner, 2026-10-07).

This is an opt-in build stage and it is NOT on the path of answering a card
(rule 3), and it goes through ``src/llm/client.py`` like every other call in
``src/`` (rule 4; the browser's own Gemini call is a separate, documented
waiver that does not apply here).

A written sentence then earns its place the same way a corpus sentence does.
It is handed to the real parser and the real detectors by the build stage, so
its occurrence is identical in shape to a mined one, and before that it must
pass every gate here:

* the surface form the model claims must actually occur in the sentence, so
  the gap lines up and ``answers`` slices back out of ``sentence_de``
  (rule 6);
* the English must not quote the German it translates, which would print the
  answer on the card (rule 2);
* the sentence must pass ``carrier_validation`` exactly as a corpus line
  does, which is 21 rules over a spaCy parse;
* the gloss must pass ``gloss_is_sane``, the same length-ratio check the
  machine translations get.

``web/lib/gemini.js`` does the same job in the browser for a word the learner
adds there, and the two prompts and gates are deliberately alike.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from src.contracts import MODEL_GENERATE, PURPOSE_SENTENCE_GENERATION
from src.phrases.cards import gloss_is_sane

#: Bumped when the prompt changes, so the client's cache does not serve an
#: answer to a different question.
PROMPT_VERSION = 1

#: How many sentences to ask for per word. The card stage keeps six, and the
#: gates below reject some, so this asks for a few more than that.
SENTENCES_PER_WORD = 8

_KIND_NAMES: dict[str, str] = {
    "noun": "noun",
    "verb": "verb",
    "adjective": "adjective",
    "adverb": "adverb",
    "separable_verb": "separable verb",
    "reflexive_verb": "reflexive verb",
}


class ApprovalRequired(RuntimeError):
    """Raised before any call when the owner has not approved the run."""


class SentenceClient(Protocol):
    def generate(self, prompt: str, *, model: str, purpose: str, namespace: str) -> str: ...


@dataclass(frozen=True)
class WrittenSentence:
    german: str
    english: str
    surface: str


@dataclass(frozen=True)
class WrittenResult:
    lemma: str
    kind: str
    accepted: list[WrittenSentence]
    rejected: list[tuple[WrittenSentence, str]]
    model: str
    generated_at: datetime


def build_prompt(lemma: str, kind: str, display: str, count: int = SENTENCES_PER_WORD) -> str:
    """The instruction given to the model. Mirrors ``promptFor`` in
    ``web/lib/gemini.js``; keep the two in step."""
    kind_name = _KIND_NAMES.get(kind, kind)
    return "\n".join(
        [
            f"Write {count} short German sentences for a vocabulary flashcard.",
            f'The word being taught is "{display}" ({kind_name}).',
            "",
            "Rules:",
            f'- Every sentence must contain "{lemma}" in some inflected form.',
            "- Everyday register, 5 to 14 words, one or two clauses, ending in . ! or ?",
            "- Use a different grammatical form of the word in each sentence.",
            "- Nothing offensive, no proper names, no brand names.",
            "- Give the English translation of each sentence.",
            f'- The English must NOT contain "{lemma}" or any other German word.',
            f'- Give the exact surface form of "{lemma}" as it appears in your sentence.',
            "",
            "Answer as JSON only, no prose and no code fence:",
            '{"cards":[{"de":"...","en":"...","surface":"..."}]}',
        ]
    )


def parse_response(raw: str) -> list[WrittenSentence] | None:
    """The model's JSON, or ``None`` when it did not answer with any."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|```$", "", text).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    rows = payload.get("cards") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    out: list[WrittenSentence] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            WrittenSentence(
                german=str(row.get("de", "")).strip(),
                english=str(row.get("en", "")).strip(),
                surface=str(row.get("surface", "")).strip(),
            )
        )
    return out


def reject_reason(
    sentence: WrittenSentence, lemma: str, validate: Callable[[str], bool]
) -> str | None:
    """Why this sentence cannot become a card, or ``None`` if it can."""
    if not sentence.german or not sentence.english or not sentence.surface:
        return "incomplete"
    if sentence.surface not in sentence.german:
        # Without this the gap would not line up with the sentence and
        # ``answers`` would not slice back out of it (rule 6).
        return "surface_not_in_sentence"
    if lemma.lower() in sentence.english.lower():
        # Rule 2: the English is shown before the answer, so it must not
        # quote the German it translates.
        return "english_quotes_the_german"
    if not gloss_is_sane(sentence.german, sentence.english):
        return "gloss_implausible"
    if not validate(sentence.german):
        return "carrier_rejected"
    return None


def write_sentences(
    wanted: Sequence[tuple[str, str, str]],
    *,
    client: SentenceClient,
    validate: Callable[[str], bool],
    approved: bool,
    max_calls: int,
    count: int = SENTENCES_PER_WORD,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    model: str = MODEL_GENERATE,
) -> list[WrittenResult]:
    """One call per word in ``wanted``, which is ``(lemma, kind, display)``.

    Raises ``ApprovalRequired`` before any call when ``approved`` is false,
    so the refusal costs nothing.
    """
    todo = list(wanted)
    if not todo:
        return []
    if not approved:
        raise ApprovalRequired(
            f"{min(len(todo), max_calls)} sentence call(s) on {model} (free lane, cost 0) "
            "need --approved-by-owner, and the owner's say-so in chat, before they run."
        )
    results: list[WrittenResult] = []
    for lemma, kind, display in todo[:max_calls]:
        raw = client.generate(
            build_prompt(lemma, kind, display, count),
            model=model,
            purpose=PURPOSE_SENTENCE_GENERATION,
            namespace=f"written_carrier_v{PROMPT_VERSION}",
        )
        parsed = parse_response(raw)
        accepted: list[WrittenSentence] = []
        rejected: list[tuple[WrittenSentence, str]] = []
        seen: set[str] = set()
        for sentence in parsed or []:
            if sentence.german in seen:
                continue
            seen.add(sentence.german)
            reason = reject_reason(sentence, lemma, validate)
            if reason is None:
                accepted.append(sentence)
            else:
                rejected.append((sentence, reason))
        results.append(
            WrittenResult(
                lemma=lemma,
                kind=kind,
                accepted=accepted,
                rejected=rejected,
                model=model,
                generated_at=now(),
            )
        )
    return results


def sentences_of(results: Iterable[WrittenResult]) -> dict[str, str]:
    """Every accepted sentence mapped to its English, for the gloss store."""
    return {s.german: s.english for result in results for s in result.accepted}
