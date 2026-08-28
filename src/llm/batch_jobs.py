"""Batch jobs that outlive the process that submitted them. TODO.md item 1.

## Why this exists

``GeminiLlmClient._call_batch_many`` submits a real Gemini batch job and then
blocks in ``_poll_batch_job`` until it finishes, and **the job's name is never
written down anywhere**. Two consequences, and the second is the one that
matters:

- A process killed mid-poll loses the job entirely. Google has already accepted
  it and will bill whatever it processes; this side simply forgets it exists.
- Nothing can collect a batch that is already running. A scheduled job that
  wakes up every thirty minutes cannot pick up work a previous wake-up
  submitted, because there is nothing on disk telling it there is any.

The second is what blocks the whole resumable-pilot design. The intended shape
of a run is: spend the free lane until its daily quota is gone, queue the
remainder as one real batch job, and exit. Something has to remember the job.

## What is recorded, and why the prompts are in it

A pending job carries its Google job name, the model and purpose it was
submitted under, and **the prompts themselves, in submission order**.

The prompts are the part that looks redundant and is not. Collection writes
each response into ``src/llm/cache.py``, which is content-addressed on
``(model, prompt)``. Without the exact prompt text there is no key to write
under, and the whole point of collecting into the cache is that the next
phase-B run finds the work already done and pays nothing. Order matters for the
same reason: Google returns inlined responses positionally, and pairing them
with the wrong prompts would poison the cache with confidently wrong entries.

At the verification pass's own sizes this is a few megabytes: a 1,225-item bank
at batch 5 is 245 prompts of roughly 7 KB.

## This store is not the cache, and does not try to be

It holds work **in flight**. A job leaves it when it has been collected, or
when it reached a terminal state that produced nothing. Results live in the
cache; the record of what is outstanding lives here. Keeping the two separate
is what lets a collection run be interrupted safely: a job whose responses were
written to the cache but which was not yet removed from this file is collected
again next time, finds every prompt already cached, and costs nothing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

#: Bumped only when the on-disk shape changes incompatibly.
STORE_FORMAT_VERSION = 1

DEFAULT_BATCH_JOB_STORE = Path(".cache/pending_batch_jobs.json")


class PendingBatchJob(BaseModel):
    """One submitted, not-yet-collected Gemini batch job."""

    model_config = ConfigDict(frozen=True, protected_namespaces=())

    #: Google's own job name, the handle everything else is done through.
    job_name: str
    #: The model it was submitted under. Half of the cache key.
    model: str
    #: Cost-log attribution, carried so a collected job's rows say what the
    #: work was for rather than "generation".
    purpose: str
    #: The submitted prompts, in submission order. The other half of the cache
    #: key, and the reason order is load-bearing: Google returns inlined
    #: responses positionally.
    prompts: list[str]
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def size(self) -> int:
        return len(self.prompts)


class BatchJobStore(BaseModel):
    """The set of jobs this machine has submitted and not yet collected."""

    model_config = ConfigDict(extra="forbid")

    version: int = STORE_FORMAT_VERSION
    jobs: list[PendingBatchJob] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path | str = DEFAULT_BATCH_JOB_STORE) -> BatchJobStore:
        """Read the store, or return an empty one.

        A missing file is an empty store, not an error: "nothing is
        outstanding" is the normal state and must not need a file to say so. A
        corrupt or future-version file returns empty **and does not delete
        itself**, so an operator can still read the job names out of it by
        hand; the alternative, guessing at a shape this code does not
        understand, is how a wrong-data run starts.
        """
        source = Path(path)
        if not source.exists():
            return cls()
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        if not isinstance(raw, dict) or raw.get("version") != STORE_FORMAT_VERSION:
            return cls()
        try:
            return cls.model_validate(raw)
        except ValueError:
            return cls()

    def save(self, path: Path | str = DEFAULT_BATCH_JOB_STORE) -> None:
        """Write the store whole, then move it into place.

        Atomic because this file is the only record that a billable job exists:
        a half-written one is a job nobody will ever collect.
        """
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".partial")
        temporary.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(target)

    def add(self, job: PendingBatchJob) -> None:
        """Record a submitted job, replacing any earlier entry of that name."""
        self.jobs = [existing for existing in self.jobs if existing.job_name != job.job_name]
        self.jobs.append(job)

    def remove(self, job_name: str) -> None:
        """Drop a job that has been collected or has terminally failed."""
        self.jobs = [existing for existing in self.jobs if existing.job_name != job_name]

    def describe(self) -> str:
        """One line for the operator."""
        if not self.jobs:
            return "no batch jobs outstanding"
        prompts = sum(job.size for job in self.jobs)
        oldest = min(job.submitted_at for job in self.jobs)
        return (
            f"{len(self.jobs)} job(s) outstanding, {prompts} prompt(s), "
            f"oldest submitted {oldest.isoformat(timespec='seconds')}"
        )


def record_submission(
    *,
    job_name: str,
    model: str,
    purpose: str,
    prompts: list[str],
    path: Path | str = DEFAULT_BATCH_JOB_STORE,
) -> PendingBatchJob:
    """Append one submitted job to the on-disk store and return it.

    Called immediately after ``batches.create`` returns, and deliberately
    before anything else can fail: from the moment Google accepts a job it is
    billable, so the window between "submitted" and "written down" is the
    window in which work can be paid for and lost.
    """
    store = BatchJobStore.load(path)
    job = PendingBatchJob(job_name=job_name, model=model, purpose=purpose, prompts=prompts)
    store.add(job)
    store.save(path)
    return job
