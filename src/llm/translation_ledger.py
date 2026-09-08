"""Month-to-date Azure Translator spend, persisted across runs.

TODO.md 5.1 step 2, the standing monthly job. The owner's instruction, 2026-08-27:
"I want to run a azure free translation run every week or month whenever limits
are reset so that we maintain a healthy translated corpus."

## Why this file exists at all

``AzureTranslator.characters_used`` is a PER-PROCESS counter. Its own docstring
says so: "It does not survive a restart, so a caller running more than once a
month must persist its own total." ``scripts/build_translations.py``'s
``--max-characters`` bounds ONE invocation and nothing else. Before this module
there was no month-to-date figure anywhere, so two runs in one month silently
spent twice, and the only thing that noticed was Azure, which notices by
answering HTTP 403 in the middle of a batch rather than by letting a job stop
cleanly.

This module is that missing number, and nothing else. It does not translate, it
does not read a corpus, and it does not know what a carrier is.

## The file format

A single small JSON object beside the translation store::

    {
      "version": 1,
      "months": {
        "2026-08": {
          "azure_characters": 56275,
          "gemini_characters": 0,
          "batches": 12,
          "runs": 2,
          "last_run_at": "2026-08-27T09:14:03.117000+00:00"
        }
      }
    }

Keyed by calendar month in **UTC**, ``YYYY-MM``. Rollover is therefore
automatic and needs no human action: a month with no entry has spent nothing,
which is exactly what a new month means. ``month_key`` is the only place that
decision lives.

**Azure characters and Gemini characters are counted separately and never
added together.** Only ``azure_characters`` is charged against the F0 monthly
allowance, because only Azure characters consume it. A batch Azure refused and
the Gemini fallback translated spent none of the free tier; it spent real money
instead, which is already a ``cost_log`` row (CLAUDE.md rule 4).
``scripts/build_translations.py`` reasons identically about its own
``characters_spent`` versus ``gemini_fallback_characters``, and conflating the
two there was a bug once already. ``gemini_characters`` is kept here so a month
where Azure was down all week is visible as such rather than as a month that
mysteriously translated nothing.

## Written the same way the store is written

Temp file in the target's own directory, ``fsync``, then a replace, so the path
on disk is the complete old content or the complete new content at every
instant and never a half-written object. Same pattern and same reasoning as
``build_translations._write_store_atomic``.

**The replace goes through ``src.atomic_write.replace_with_retry``**, because
``os.replace`` is not reliably atomic on Windows against a reader: it fails
with ``WinError`` 5 or 32 whenever another process holds the destination open,
even for reading. That killed the first real run of the scheduled job at 27% of
a month's allowance, and an F0 allowance not spent inside its calendar month
expires, so the crash cost a month rather than a retry. That helper is shared
with the batch-job store and the candidate pool, which had the identical
temp-and-replace pattern and the identical hole.

## Why the clock is an argument

CLAUDE.md section 8: anything touching the clock takes it as an injected
dependency. Here it is load-bearing rather than stylistic. Month rollover is
the single most important behaviour in this file and the only alternative way
to test it is to wait until the first of the month.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.atomic_write import replace_with_retry

#: Bumped only if the on-disk shape changes incompatibly. A ledger written by a
#: newer version is refused rather than silently misread, because misreading it
#: means over- or under-spending a quota that cannot be checked any other way.
LEDGER_VERSION = 1

#: A clock, injected. Returns an aware ``datetime``. The monthly allowance
#: itself is deliberately NOT restated in this module: it belongs to Azure, so
#: ``src.llm.translation.AZURE_F0_MONTHLY_CHARACTERS`` stays its one home and
#: callers pass it in as ``budget``/``monthly_budget``.
Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """The default clock. Aware, UTC, and the only place ``datetime.now`` is
    called in this module."""
    return datetime.now(UTC)


def month_key(moment: datetime) -> str:
    """The calendar month ``moment`` falls in, as ``YYYY-MM``, in UTC.

    A naive ``datetime`` is taken to be UTC rather than local time. Guessing
    local time would make the same instant fall in different months on two
    machines, and this number is compared against a quota Azure meters
    globally.
    """
    if moment.tzinfo is None:
        return moment.strftime("%Y-%m")
    return moment.astimezone(UTC).strftime("%Y-%m")


class MonthlySpend(BaseModel):
    """One calendar month's translation spend.

    Pydantic per CLAUDE.md section 8: this is a shape that crosses a
    persistence boundary and is read back by a later process, which is exactly
    the case that rule is about.
    """

    model_config = ConfigDict(frozen=True)

    #: Characters Azure actually translated. The only field charged against the
    #: F0 allowance.
    azure_characters: int = 0
    #: Characters the Gemini fallback translated. Recorded for visibility, and
    #: deliberately NOT charged against the F0 allowance, because Azure never
    #: saw them.
    gemini_characters: int = 0
    #: Successful batches this month, across every run. The denominator for
    #: "how much work does one batch actually get through".
    batches: int = 0
    #: How many separate invocations touched this month. Two runs in one month
    #: is the case this whole module exists to make safe, so it is counted.
    runs: int = 0
    last_run_at: datetime | None = None
    #: Azure answered a quota 403 this month. THE stop condition for the
    #: top-up job, replacing the character count as the gate at the owner's
    #: instruction (2026-09-08): the count only sees what this job sent, and
    #: another script put ~66,000 characters through Azure that month with no
    #: ledger. Azure's refusal is the only figure that cannot drift. The
    #: character count stays for the months-remaining estimate.
    azure_quota_rejected: bool = False
    azure_quota_rejected_at: datetime | None = None
    #: What Azure actually answered, so a refusal that was really a bad key
    #: can be told from a spent month by reading the file.
    azure_quota_rejected_detail: str | None = None


class TranslationLedger(BaseModel):
    """Every month this job has ever spent anything in.

    Mutable on purpose: a run reads it once, updates it after every successful
    batch, and writes it back each time. Months are never pruned. The whole
    file is a few hundred bytes a year and the history is what the
    months-remaining estimate is checked against.
    """

    version: int = LEDGER_VERSION
    months: dict[str, MonthlySpend] = Field(default_factory=dict)

    def spend_for(self, key: str) -> MonthlySpend:
        """This month's spend, or a zeroed one if the month has no entry.

        The zero is the rollover: a month nobody has spent in yet has spent
        nothing, and no human has to create the entry first.
        """
        return self.months.get(key, MonthlySpend())

    def remaining(self, key: str, budget: int) -> int:
        """Characters of Azure F0 allowance still available in ``key``.

        Never negative. A month that somehow overspent (a run against a resource
        that is not F0, a hand-edited ledger) reports zero remaining rather than
        a negative budget that would read as headroom to arithmetic downstream.
        """
        return max(0, budget - self.spend_for(key).azure_characters)

    def record_batch(
        self,
        key: str,
        *,
        azure_characters: int = 0,
        gemini_characters: int = 0,
        at: datetime | None = None,
    ) -> None:
        """Add one successful batch to ``key``'s running total.

        Called after the batch has landed, never before. A batch that raised
        spent no Azure quota (``AzureTranslator`` only increments its own
        counter after a successful call, for the identical reason) so it is not
        recorded here either.
        """
        current = self.spend_for(key)
        self.months[key] = current.model_copy(
            update={
                "azure_characters": current.azure_characters + azure_characters,
                "gemini_characters": current.gemini_characters + gemini_characters,
                "batches": current.batches + 1,
                "last_run_at": at if at is not None else current.last_run_at,
            }
        )

    def quota_rejected(self, key: str) -> bool:
        """Whether Azure has already refused ``key`` on quota. A month with no
        entry has not been refused, which is what rollover means."""
        return self.spend_for(key).azure_quota_rejected

    def record_quota_rejection(self, key: str, at: datetime, detail: str | None = None) -> None:
        """Azure said no. Remembered until the month rolls over, so no later
        run this month sends a batch that can only be refused the same way
        or, worse, fall through to a paid provider for it."""
        current = self.spend_for(key)
        self.months[key] = current.model_copy(
            update={
                "azure_quota_rejected": True,
                "azure_quota_rejected_at": at,
                "azure_quota_rejected_detail": detail,
            }
        )

    def record_run(self, key: str, at: datetime) -> None:
        """Note that an invocation touched ``key``, whether or not it spent.

        Recorded at the START of a run, so a run that crashes before its first
        batch still leaves a trace that it happened. "The job fired and did
        nothing" and "the job never fired" are different problems with different
        fixes, and the whole point of a scheduled job is that nobody is watching
        when it runs.
        """
        current = self.spend_for(key)
        self.months[key] = current.model_copy(update={"runs": current.runs + 1, "last_run_at": at})


class LedgerVersionError(RuntimeError):
    """The ledger on disk was written by a version this code does not
    understand. Refused rather than guessed at: the number in it decides how
    much free quota is left, and reading it wrong spends money."""


def load_ledger(path: Path) -> TranslationLedger:
    """The ledger on disk, or a fresh empty one.

    A missing file is a first run, not an error. A malformed one is treated as
    empty as well, with the caller free to notice via the returned object being
    empty; the alternative, crashing a scheduled job on a corrupt accounting
    file, would stop the corpus from progressing at all, and the worst case of
    treating it as empty is that this month's budget is spent a second time,
    which Azure itself will then refuse. A ledger with a FUTURE version is the
    one case that does raise, because that file is real data written by code
    that knew something this code does not.
    """
    if not path.exists():
        return TranslationLedger()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return TranslationLedger()
    if isinstance(raw, dict):
        version = raw.get("version")
        if isinstance(version, int) and version > LEDGER_VERSION:
            raise LedgerVersionError(
                f"{path} was written by ledger version {version}; this code understands "
                f"version {LEDGER_VERSION}. Refusing to guess at a spend total."
            )
    try:
        return TranslationLedger.model_validate(raw)
    except ValidationError:
        return TranslationLedger()


def save_ledger_atomic(path: Path, ledger: TranslationLedger) -> None:
    """Write ``ledger`` so the path is never observed half-written.

    Temp file in the same directory, flushed and ``fsync``'d, then swapped in
    with one ``os.replace``. Identical reasoning to
    ``build_translations._write_store_atomic``: a process killed at any instant
    leaves either the complete previous ledger or the complete new one, and the
    orphaned temp file is removed on any exception.

    **The replace is retried, because on Windows it is not reliably atomic
    against a reader.** ``os.replace`` fails with ``WinError 5`` (access
    denied) or ``WinError 32`` (sharing violation) whenever any other process
    holds the destination open, even for reading. This is not hypothetical: it
    killed the first real run of the scheduled job on 2026-08-28 at 27% of the
    month's allowance, because the ledger was being read to report progress
    while the job wrote it. Anti-virus scanning the file, or an editor with it
    open, does exactly the same thing.

    Retrying is the right fix rather than a workaround, because the condition
    is transient by nature: the reader closes the file microseconds later.
    Crashing instead throws away the rest of a run that may have hours of free
    quota left to spend, and the allowance it did not spend expires with the
    month. ``POSIX`` ``rename`` has no such problem and the retry costs it
    nothing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(ledger.model_dump_json(indent=2))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        replace_with_retry(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class RemainingEstimate:
    """How long the corpus takes to finish at the free tier's own rate.

    This is the number the owner will actually read, so every input to it is
    kept on the object rather than collapsed into one figure he cannot check.
    """

    #: Carriers with no trusted machine translation, AFTER this run.
    carriers_remaining: int
    #: The sum of those carriers' own German text lengths. Not an estimate: the
    #: carrier set was read, so this is measured.
    characters_remaining: int
    #: The F0 allowance this estimate assumes per month.
    monthly_budget: int
    #: characters_remaining / monthly_budget, unrounded.
    months: float

    @property
    def mean_characters_per_carrier(self) -> float:
        if self.carriers_remaining <= 0:
            return 0.0
        return self.characters_remaining / self.carriers_remaining

    @property
    def whole_months(self) -> int:
        """Rounded UP. Thirteen and a half months of work is fourteen monthly
        runs, not thirteen."""
        return math.ceil(self.months)

    def sentence(self) -> str:
        """The estimate as one line of plain English, with its assumptions
        attached, so the figure is never quoted without them."""
        if self.carriers_remaining <= 0:
            return (
                "No untrusted carriers remain. The corpus is fully covered by "
                "machine translations this project trusts."
            )
        runs = "run" if self.whole_months == 1 else "runs"
        return (
            f"{self.carriers_remaining:,} carriers still lack a trusted machine "
            f"translation, {self.characters_remaining:,} characters "
            f"({self.mean_characters_per_carrier:.1f} per carrier). At the full "
            f"{self.monthly_budget:,} characters/month of Azure F0 that is "
            f"{self.months:.1f} months, so {self.whole_months} more monthly "
            f"{runs}. That assumes every character goes to Azure, no run fails, "
            "and no month is skipped; it is a floor on the calendar time, not a "
            "promise."
        )


def estimate_months_remaining(
    *, characters_remaining: int, carriers_remaining: int, monthly_budget: int
) -> RemainingEstimate:
    """The months-remaining arithmetic, in one place, so it can be tested.

    Deliberately the simplest defensible division: characters left divided by
    the monthly allowance. Two things it does NOT do, both on purpose.

    It does not extrapolate from the run's own measured throughput. A run that
    was interrupted, or that hit the throughput ceiling, or that started
    halfway through a month, would produce a per-run rate that says nothing
    about a full month, and dressing that up as a trend would be a worse number
    than the honest ceiling.

    It does not subtract what is left of the current month. The current month's
    remainder is reported separately and is at most one month of the answer;
    folding it in would make the headline figure drift by a fraction every day
    of the month for no gain in accuracy.
    """
    if monthly_budget <= 0:
        raise ValueError("monthly_budget must be positive")
    months = characters_remaining / monthly_budget if characters_remaining > 0 else 0.0
    return RemainingEstimate(
        carriers_remaining=max(0, carriers_remaining),
        characters_remaining=max(0, characters_remaining),
        monthly_budget=monthly_budget,
        months=months,
    )
