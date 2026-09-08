"""A preceding sentence for cards whose connector opens the sentence.

The one model call in the deck build. Opt-in twice over: the build stage does
nothing without ``--generate-contexts`` and refuses without
``--approved-by-owner``, and the owner approves the run in chat on top of that.
Runs on the free lane only. Every reply is parsed strictly and checked
deterministically before it is stored; rejections are stored too, with why,
so a re-run does not re-ask.
"""

import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from src.atomic_write import write_text_atomic
from src.contracts import (
    MODEL_GENERATE,
    PURPOSE_PHRASE_CONTEXT,
    ContextRecord,
    ContextRequest,
    ContextResponse,
    PhraseCard,
    PhraseUnit,
)

PROMPT_VERSION = 1
DEFAULT_CONTEXTS_PATH = Path("data/phrases/contexts.jsonl")
_MIN_WORDS, _MAX_WORDS = 4, 16


class ContextClient(Protocol):
    def generate(self, prompt: str, model: str = ..., purpose: str = ..., **kwargs: Any) -> str: ...


class ApprovalRequired(RuntimeError):
    """The stage was asked to spend and the owner had not approved the run."""


def build_prompt(request: ContextRequest) -> str:
    return (
        "You write one German sentence that comes immediately BEFORE the sentence "
        "below, so that its opening connector has something to refer to.\n"
        f"Connector: {request.connector_display}\n"
        f"Sentence: {request.sentence_de}\n"
        f"Its English: {request.gloss_en}\n"
        "Rules: A2-B1 German, 5 to 14 words, same register, no new names, "
        "must not start with a connector and must not contain the connector, "
        "ends with a full stop. Also give a plain English translation of your sentence.\n"
        'Answer with JSON only: {"context_de": "...", "context_en": "..."}'
    )


def parse_response(raw: str) -> ContextResponse | None:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        return None
    try:
        return ContextResponse.model_validate(json.loads(text[start : end + 1]))
    except (ValueError, ValidationError):
        return None


def reject_reason(
    response: ContextResponse, request: ContextRequest, validate: Callable[[str], bool]
) -> str | None:
    de = response.context_de.strip()
    connector = request.connector_display.lower()
    words = de.split()
    if not (_MIN_WORDS <= len(words) <= _MAX_WORDS):
        return "length"
    if de[-1] not in ".!?":
        return "no_terminal_punctuation"
    if any(ch in de for ch in '"„“\n'):
        return "quotes_or_newline"
    if connector.split()[0] in {w.lower().strip(".,!?") for w in words}:
        return "contains_connector"
    if not response.context_en.strip():
        return "empty_english"
    if not validate(de):
        return "carrier_rejected"
    return None


def load_records(path: Path = DEFAULT_CONTEXTS_PATH) -> dict[str, ContextRecord]:
    records: dict[str, ContextRecord] = {}
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = ContextRecord.model_validate_json(line)
            records[record.card_id] = record
    return records


def save_records(records: Iterable[ContextRecord], path: Path = DEFAULT_CONTEXTS_PATH) -> None:
    ordered = sorted(records, key=lambda r: r.card_id)
    write_text_atomic(path, "".join(r.model_dump_json() + "\n" for r in ordered))


def cards_needing_context(cards: Iterable[PhraseCard]) -> list[PhraseCard]:
    return [c for c in cards if c.needs_context and c.context_de is None]


def generate_contexts(
    cards: Iterable[PhraseCard],
    units: dict[str, PhraseUnit],
    *,
    client: ContextClient,
    validate: Callable[[str], bool],
    existing: dict[str, ContextRecord],
    approved: bool,
    max_calls: int,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    model: str = MODEL_GENERATE,
) -> list[ContextRecord]:
    """New records for cards not already in ``existing``. Raises
    ``ApprovalRequired`` before any call when ``approved`` is false."""
    todo = [c for c in cards_needing_context(cards) if c.card_id not in existing]
    if not todo:
        return []
    if not approved:
        raise ApprovalRequired(
            f"{len(todo)} context call(s) on {model} (free lane, cost 0) need "
            "--approved-by-owner, and the owner's say-so in chat, before they run."
        )
    produced: list[ContextRecord] = []
    for card in todo[:max_calls]:
        unit = units[card.unit_id]
        request = ContextRequest(
            card_id=card.card_id,
            unit_id=card.unit_id,
            connector_display=unit.display_de,
            sentence_de=card.sentence_de,
            gloss_en=card.gloss_en,
            prompt_version=PROMPT_VERSION,
        )
        raw = client.generate(
            build_prompt(request),
            model=model,
            purpose=PURPOSE_PHRASE_CONTEXT,
            namespace=f"phrase_context_v{PROMPT_VERSION}",
        )
        response = parse_response(raw)
        reason = "unparseable" if response is None else reject_reason(response, request, validate)
        produced.append(
            ContextRecord(
                card_id=card.card_id,
                unit_id=card.unit_id,
                sentence_de=card.sentence_de,
                context_de=response.context_de.strip() if response and reason is None else None,
                context_en=response.context_en.strip() if response and reason is None else None,
                accepted=reason is None,
                reject_reason=reason,
                model=model,
                prompt_version=PROMPT_VERSION,
                generated_at=now(),
            )
        )
    return produced


def apply_contexts(
    cards: Iterable[PhraseCard], records: dict[str, ContextRecord]
) -> list[PhraseCard]:
    out: list[PhraseCard] = []
    for card in cards:
        record = records.get(card.card_id)
        if record is not None and record.accepted and record.context_de and record.context_en:
            out.append(
                card.model_copy(
                    update={
                        "context_de": record.context_de,
                        "context_en": record.context_en,
                        "context_source": "gemini",
                    }
                )
            )
        else:
            out.append(card)
    return out
