"""English for the mined phrase units, most common rendering first.

Curated units (connectors, idioms, seeds) carry a gloss from their list; the
mined ones (separable verbs, verb + preposition, reflexive verbs,
collocations) have none until this opt-in stage asks the model, in batches
of units ordered by rank, for one to three English equivalents each. A unit
with several senses gets several ("aussehen: to look, to appear"). Every
reply is parsed strictly and checked for shape before it is stored;
rejections are stored too, with why, so a re-run does not re-ask.

Opt-in twice over, like the context stage: the build stage does nothing
without ``--generate-unit-glosses`` and refuses without
``--approved-by-owner``, and the owner approves the run in chat on top.
"""

import json
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from src.atomic_write import write_text_atomic
from src.contracts import (
    MODEL_GENERATE,
    PURPOSE_UNIT_GLOSS,
    PhraseCard,
    PhraseUnit,
    UnitGlossRecord,
)

PROMPT_VERSION = 1
DEFAULT_UNIT_GLOSSES_PATH = Path("data/phrases/unit_glosses.jsonl")
DEFAULT_BATCH_SIZE = 40
MAX_GLOSSES = 3
_MAX_GLOSS_CHARS = 60

_KIND_LABEL = {
    "verb_prep": "verb + preposition",
    "reflexive_verb": "reflexive verb",
    "separable_verb": "separable verb",
    "noun_verb": "noun-verb collocation",
    "adj_noun": "adjective-noun collocation",
    "connector": "connector",
    "two_part_connector": "two-part connector",
    "idiom": "fixed expression",
}


class GlossClient(Protocol):
    def generate_many(
        self,
        prompts: list[str],
        model: str = ...,
        purpose: str = ...,
        *,
        cache_namespace: str | None = ...,
    ) -> list[str]: ...


class ApprovalRequired(RuntimeError):
    """The stage was asked to spend and the owner had not approved the run."""


def units_needing_gloss(units: Iterable[PhraseUnit]) -> list[PhraseUnit]:
    """Mined units without a gloss, most frequent first. Trivial units are
    never shown, so they are never asked."""
    todo = [u for u in units if u.gloss_en is None and not u.trivial]
    return sorted(todo, key=lambda u: (u.rank, u.unit_id))


def example_sentences(cards: Iterable[PhraseCard]) -> dict[str, str]:
    """The shortest glossed sentence per unit, to pin the sense the deck uses."""
    best: dict[str, str] = {}
    for card in cards:
        if card.gloss_en is None:
            continue
        current = best.get(card.unit_id)
        if current is None or len(card.sentence_de) < len(current):
            best[card.unit_id] = card.sentence_de
    return best


def build_prompt(batch: list[PhraseUnit], examples: dict[str, str]) -> str:
    lines = []
    for unit in batch:
        case = f", + {unit.case}" if unit.case else ""
        example = examples.get(unit.unit_id)
        tail = f' | example: "{example}"' if example else ""
        lines.append(f"{unit.unit_id} | {unit.display_de} ({_KIND_LABEL[unit.kind]}{case}){tail}")
    return (
        "Translate each German phrase below into English for a learner's flashcard.\n"
        "Give 1 to 3 short English equivalents per phrase, MOST COMMON FIRST. When the "
        "phrase has clearly distinct senses, give one equivalent per sense (aussehen: "
        '"to look", "to appear"); when it has one sense, give one or two wordings. '
        'Verbs and verb phrases start with "to"; keep a governing preposition '
        '("to wait for"); translate collocations as a whole phrase ("to make a decision"); '
        "each equivalent under six words; English only, never repeat the German.\n"
        "Answer with JSON only, one object per phrase, same ids, in the same order:\n"
        '[{"unit_id": "...", "glosses": ["...", "..."]}]\n\n' + "\n".join(lines)
    )


_OBJECT_RE = re.compile(r"\{[^{}]*\}")


def _loads_or_none(text: str) -> object:
    try:
        return json.loads(text)
    except ValueError:
        return None


def parse_response(raw: str) -> dict[str, list[str]] | None:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < 0:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        # One malformed object (a missing key, a stray comma) must not sink
        # the other 39 in the batch: pick out the objects that do parse.
        data = [_loads_or_none(m) for m in _OBJECT_RE.findall(text)]
    if not isinstance(data, list):
        return None
    out: dict[str, list[str]] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        unit_id = item.get("unit_id")
        glosses = item.get("glosses")
        if not isinstance(unit_id, str) or not isinstance(glosses, list):
            continue
        out[unit_id] = [g.strip() for g in glosses if isinstance(g, str) and g.strip()]
    return out


def reject_reason(unit: PhraseUnit, glosses: list[str]) -> str | None:
    if not glosses:
        return "empty"
    display = unit.display_de.lower()
    for g in glosses[:MAX_GLOSSES]:
        if len(g) > _MAX_GLOSS_CHARS:
            return "too_long"
        if g.lower() == display or any(part.lower() == g.lower() for part in unit.parts):
            return "german_echoed"
        if any(ch in g for ch in "\n{}[]"):
            return "malformed"
    return None


def load_records(path: Path = DEFAULT_UNIT_GLOSSES_PATH) -> dict[str, UnitGlossRecord]:
    records: dict[str, UnitGlossRecord] = {}
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = UnitGlossRecord.model_validate_json(line)
            records[record.unit_id] = record
    return records


def save_records(
    records: Iterable[UnitGlossRecord], path: Path = DEFAULT_UNIT_GLOSSES_PATH
) -> None:
    ordered = sorted(records, key=lambda r: r.unit_id)
    write_text_atomic(path, "".join(r.model_dump_json() + "\n" for r in ordered))


def generate_unit_glosses(
    units: Iterable[PhraseUnit],
    cards: Iterable[PhraseCard],
    *,
    client: GlossClient,
    existing: dict[str, UnitGlossRecord],
    approved: bool,
    max_calls: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    model: str = MODEL_GENERATE,
) -> list[UnitGlossRecord]:
    """New records for units not already in ``existing``, most frequent first,
    ``batch_size`` units per call. Raises ``ApprovalRequired`` before any call
    when ``approved`` is false."""
    todo = [u for u in units_needing_gloss(units) if u.unit_id not in existing]
    if not todo:
        return []
    batches = [todo[i : i + batch_size] for i in range(0, len(todo), batch_size)][:max_calls]
    if not approved:
        raise ApprovalRequired(
            f"{len(batches)} call(s) on {model} for {sum(len(b) for b in batches)} unit(s) "
            "need --approved-by-owner, and the owner's say-so in chat, before they run."
        )
    examples = example_sentences(cards)
    prompts = [build_prompt(batch, examples) for batch in batches]
    replies = client.generate_many(
        prompts,
        model=model,
        purpose=PURPOSE_UNIT_GLOSS,
        cache_namespace=f"unit_gloss_v{PROMPT_VERSION}",
    )
    produced: list[UnitGlossRecord] = []
    stamp = now()
    for batch, raw in zip(batches, replies, strict=True):
        parsed = parse_response(raw)
        for unit in batch:
            glosses = (parsed or {}).get(unit.unit_id)
            if parsed is None:
                reason: str | None = "unparseable"
                kept: list[str] = []
            elif glosses is None:
                reason, kept = "missing", []
            else:
                reason = reject_reason(unit, glosses)
                kept = glosses[:MAX_GLOSSES] if reason is None else []
            produced.append(
                UnitGlossRecord(
                    unit_id=unit.unit_id,
                    display_de=unit.display_de,
                    glosses=kept,
                    accepted=reason is None,
                    reject_reason=reason,
                    model=model,
                    prompt_version=PROMPT_VERSION,
                    generated_at=stamp,
                )
            )
    return produced


def apply_unit_glosses(
    units: Iterable[PhraseUnit], records: dict[str, UnitGlossRecord]
) -> list[PhraseUnit]:
    """Curated glosses win; a mined unit gets its accepted renderings joined
    with " / ", the order the model gave (most common first)."""
    out: list[PhraseUnit] = []
    for unit in units:
        record = records.get(unit.unit_id)
        if unit.gloss_en is None and record is not None and record.accepted and record.glosses:
            out.append(unit.model_copy(update={"gloss_en": " / ".join(record.glosses)}))
        else:
            out.append(unit)
    return out
