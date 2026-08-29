"""The persisted boundary between the corpus half of the pilot and the model
half of it. TODO.md item 1 (the phase A/B split).

## Why this exists

``scripts/step7_corpus_pilot.py`` does two very different jobs in one process.

**Phase A** reads both corpora, validates carriers, tags every sentence with
spaCy, matches selectors, blanks a word and filters by CEFR. It touches no
network, is deterministic on ``--seed``, and over a whole corpus it is roughly
**2.5 hours of silence**: two spaCy passes at a measured 78 and 92 sentences a
second over ~450,000 lines.

**Phase B** translates, runs the model verification passes and writes the bank.
It is almost entirely waiting on somebody else's API.

Until this module existed there was no boundary between them, and phase A had
no checkpoint of any kind -- the tagger's cache is an in-process ``lru_cache``
and dies with the process. Any interruption restarted the whole thing from
zero. That is what made a polling scheduled job impossible: a run stopped after
thirty minutes never reached an LLM call, and never would, however many times
it was restarted.

Persisting the pool between the two makes phase B cheap to re-run, which is
what lets it be driven by a scheduler that wakes up, spends whatever free-lane
quota exists, queues the rest as a batch and exits.

## What is in here, and what deliberately is not

The pool holds the finished ``BankItem`` list, the corpus provenance those
items still need, and the rejections phase A already decided. It does **not**
hold the corpus, the tagged sentences, or the candidate pool before sampling.
Those are large, and phase B never looks at them: re-deriving them is exactly
the 2.5 hours this file exists to avoid paying twice, but *carrying* them would
cost gigabytes to save something nothing reads.

**Provenance is subset to what the sampled items actually reference.** The full
map is one entry per corpus line, so about 450,000 of them; the items in a
pool reference a few thousand at most.

## The fingerprint, and why it warns rather than refuses

``inputs_fingerprint`` records the phase-A inputs that change what the pool
contains: the corpus paths, the per-source limit, the quota, the seed and the
lemma cap. Phase B compares it and **warns** on a mismatch rather than
refusing, because the mismatch is not always wrong: re-running phase B against
an older pool with a different verification batch size is a legitimate
experiment, and those flags are deliberately not in the fingerprint. What the
fingerprint catches is the case that silently produces nonsense, which is a
pool built from one corpus being verified as if it came from another.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.atomic_write import write_text_atomic
from src.contracts import BankItem
from src.generation.batch_client import RejectedCandidateRecord

#: Bumped only when the on-disk shape changes incompatibly. A pool written by a
#: newer version than the reader understands is refused rather than guessed at.
POOL_FORMAT_VERSION = 1

DEFAULT_POOL_PATH = Path("data/corpus_candidate_pool.json")


class PooledProvenance(BaseModel):
    """One corpus line's identity and text, as phase B still needs it.

    A Pydantic mirror of ``step7_corpus_pilot.CorpusProvenance`` (a frozen
    dataclass), because a dataclass has no serialisation contract of its own
    and this one crosses a file.
    """

    model_config = ConfigDict(frozen=True)

    source: str
    line_id: str
    text: str


class PoolFormatError(RuntimeError):
    """A pool file could not be read as a pool.

    Distinct from an empty pool: "phase A produced nothing" is a result, and
    "this file is not what phase B thinks it is" is an operator problem.
    """


class CandidatePool(BaseModel):
    """Everything phase B needs and nothing it does not."""

    model_config = ConfigDict(extra="forbid")

    version: int = POOL_FORMAT_VERSION
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    #: The phase-A inputs that decide what is in here. See the module
    #: docstring on why a mismatch warns rather than refuses.
    inputs_fingerprint: str = ""
    seed: int = 0
    per_topic_quota: int = 0
    #: The assembled items, glossless. Phase B fills ``gloss_en``.
    items: list[BankItem] = Field(default_factory=list)
    #: Keyed by ``_carrier_hash_id(text)``, subset to what ``items`` reference.
    provenance: dict[str, PooledProvenance] = Field(default_factory=dict)
    #: What phase A already rejected: the CEFR filter, the uniqueness gate and
    #: the cross-topic duplicate drop. Carried so the rejected file phase B
    #: writes is the whole run's rejections, not just the model's.
    rejected: list[RejectedCandidateRecord] = Field(default_factory=list)
    #: The report fields phase A filled, so phase B can write one report
    #: covering both halves rather than two half-reports.
    report_prefix: dict[str, Any] = Field(default_factory=dict)

    def save(self, path: Path | str) -> None:
        """Write the pool, creating parent directories.

        Written whole and then moved into place, so an interrupted write
        cannot leave a half-file that a later phase B would read as real.
        """
        # Through ``write_text_atomic`` rather than ``Path.replace`` directly:
        # on Windows the replace fails whenever another process holds the
        # destination open, even for reading, and a pool represents hours of
        # spaCy nobody wants to repeat. See ``src/atomic_write.py``.
        write_text_atomic(Path(path), self.model_dump_json(indent=2))

    @classmethod
    def load(cls, path: Path | str) -> CandidatePool:
        """Read a pool, or raise ``PoolFormatError`` explaining why not."""
        source = Path(path)
        if not source.exists():
            raise PoolFormatError(
                f"No candidate pool at {source}. Run phase A first "
                f"(--phase a), or point --pool-file at an existing pool."
            )
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PoolFormatError(f"Could not read {source}: {exc}") from exc
        if not isinstance(raw, dict):
            raise PoolFormatError(f"{source} is not a JSON object.")
        version = raw.get("version")
        if version != POOL_FORMAT_VERSION:
            raise PoolFormatError(
                f"{source} is pool format version {version!r}, but this code "
                f"reads version {POOL_FORMAT_VERSION}. Re-run phase A."
            )
        try:
            return cls.model_validate(raw)
        except ValueError as exc:
            raise PoolFormatError(f"{source} is not a valid candidate pool: {exc}") from exc

    def describe(self) -> str:
        """One line for the operator, since a pool is otherwise opaque."""
        return (
            f"{len(self.items)} items, {len(self.provenance)} carriers, "
            f"{len(self.rejected)} rejections already recorded, "
            f"built {self.created_at.isoformat(timespec='seconds')}"
        )


def fingerprint_inputs(
    *,
    tatoeba_path: Path | str | None,
    leipzig_path: Path | str | None,
    limit_per_source: int,
    per_topic_quota: int,
    seed: int,
    max_items_per_lemma: int,
) -> str:
    """A short stable hash of the phase-A inputs that change the pool.

    Deliberately excludes everything phase B controls -- the verification batch
    size, the pass count, the translation budget, the bank path. Re-running
    phase B against one pool with different verification settings is a
    legitimate experiment and must not look like a mismatch.
    """
    parts = [
        str(tatoeba_path or ""),
        str(leipzig_path or ""),
        str(limit_per_source),
        str(per_topic_quota),
        str(seed),
        str(max_items_per_lemma),
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]
