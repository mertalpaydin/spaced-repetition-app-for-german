"""The standing monthly Azure F0 translation top-up.

The owner's instruction, 2026-08-27, verbatim: *"I want to run a azure free
translation run every week or month whenever limits are reset so that we
maintain a healthy translated corpus. I do not want to use potentially bad
translations for anything."*

Two things follow from that sentence and they shape everything below.

**It is a standing job, not a build step.** It fires on a schedule forever,
nobody watches it, and it must never spend the same month's free quota twice.

**Its output is trusted everywhere.** Not only for exercises. Planned feature
5.3 (click a word, see it in several corpus sentences with their translations)
reads the same store, so "good enough for breadth" is not a standard this job
may fall back on. Tatoeba's own translations are therefore treated as work
still to do, not as work already done.

## Why a separate script rather than a mode on build_translations.py

``scripts/build_translations.py`` is a BUILD tool with two shapes, a corpus
sweep and ``--carriers-from`` for one pilot's 400-odd sentences. Both are "spend
up to N characters, once, now". This job is a different thing: it has a memory
that outlives the process, it has a priority order over the corpus, and its
budget is a calendar month rather than an invocation. Bolting a third mode onto
that script would put a persistent ledger and a months-remaining projection
inside a file whose entire contract is per-run, and every one of its flags
(``--max-characters``, ``--trust-tatoeba``, ``--pairs``) would need a paragraph
saying whether it still means anything in monthly mode.

So the SCHEDULING and PRIORITISATION live here, and every piece of actual work
is ``build_translations``' own, imported, not copied:

- ``build_translations.run_backfill`` does the translating, including the
  whole-batch stop, the failure handling and the store write.
- ``build_translations._load_store`` / ``_write_store_atomic`` read and write
  the store. This file never opens it.
- ``build_translations.translator_from_env`` builds the translator, so the
  monthly job and the nightly one can never wire Azure and Gemini differently.
- ``build_translations._default_batch_size`` picks the batch size, so the
  blast-radius reasoning (Gemini does not batch underneath) is not restated.

Two small ADDITIVE parameters were added to ``run_backfill`` for this caller,
both defaulting to today's behaviour: ``distrust_stored_sources`` and the
``on_batch``/``checkpoint_every`` pair. That is the alternative to a second copy
of the batch loop, which is the thing that would actually drift.

## The measured situation, 2026-08-27

- 450,490 distinct carriers, 26,933,263 characters, 59.8 mean.
- Azure F0 allows 2,000,000 characters a month, permanently, no card.
- The store holds 200,555 Tatoeba glosses (distrusted), 1,139 azure, 100 gemini.
- **449,251 carriers therefore have no trusted machine translation.**
- 26,933,263 / 2,000,000 = 13.47, so the whole corpus is **about 13.4 months**
  of free tier. That is the number this script reports back every run, recomputed
  from what is actually left rather than quoted from here.

## What was missing before this file existed

``AzureTranslator.characters_used`` is a per-process counter, reset to zero by
every fresh run, by its own docstring. ``--max-characters`` bounds ONE
invocation. Nothing tracked month-to-date spend across runs at all, so a monthly
job could not exist: run it twice and it silently spends twice, and Azure's
refusal at the ceiling arrives as an HTTP 403 in the middle of a batch rather
than as a clean stop. ``src/llm/translation_ledger.py`` is that missing number.

## Priority order over the corpus

**Sentences that cannot host an exercise are skipped entirely**, before any
pass. Measured on the real corpus: 129,469 of 450,502 lines fail carrier
validation, and every one of them was previously queued for a share of a
2,000,000-character monthly allowance it could never repay. Scraped junk,
headlines with no finite verb, Swiss spelling, quotations opening mid-sentence.
``--keep-carrier-invalid`` restores the old behaviour.

Then three passes, in this order, each exhausted before the next begins:

0. **Carriers that are, or are about to be, exercises.** Everything in the
   phase-A candidate pool, plus anything already banked.
1. **Carriers with no gloss at all.** Nothing in the store for that exact
   German text.
2. **Carriers whose stored gloss has** ``source == "tatoeba"``. Replacements.
   The old record stays on disk until a real translation lands to overwrite it,
   so 5.3's corpus never has a hole in it.

**Pass 0 exists because of arithmetic, and it was added after that arithmetic
went wrong in practice.** A 1,225-item bank needs about 68,000 characters,
3.4% of one month. The corpus at large is 13.4 months. With ordering by a hash
of the sentence -- which is what this job did throughout, and still does inside
each pass -- those 1,225 carriers were reached by coincidence somewhere across
13 months. On 2026-08-29 a real phase B found 462 of its 1,224 carriers with no
gloss at all and only 79 with an Azure one, so it fell through to the Gemini
fallback for 1,144 of them: the built consumer starved while the allowance went
to sentences for an unbuilt one.

Groups 1 and 2 in that order because a carrier with no gloss cannot be shown at
all, whereas a carrier with a Tatoeba gloss is merely one this project will not
put in front of a learner. Coverage before quality, when the alternative is
nothing.

Feature 5.3 is not harmed by pass 0. It wants breadth, and pass 0 is 3.4% of
one month out of thirteen.

**Ordering inside each group is a keyed hash of the sentence, not a shuffle.**
``blake2b(text, key=seed)`` gives every carrier a fixed rank that depends on
nothing but its own text and the seed. That matters over 13 months:
``random.Random(seed).shuffle(list)`` depends on the length of the list, so
adding one sentence to the corpus repermutes the whole thing and next month's
run would re-draw a slice it has already done. A keyed hash cannot do that. A
carrier's rank in August is its rank in September.

Forward progress needs nothing else. A translated carrier leaves group 1 (it now
has a gloss) and never enters group 2 (its source is azure or gemini, not
tatoeba), so each run consumes a fresh prefix of what remains. The store is the
position pointer, exactly as it already is for the nightly job.

## Stopping

The run's budget is ``min(--max-characters, monthly budget - headroom - month
to date)``, and ``run_backfill`` stops at a whole-batch boundary once the next
batch would exceed it. Nothing is ever attempted at the edge to top the budget
up. A month whose budget is already spent does no translation at all and says
so, which is the correct behaviour for a job that may be scheduled weekly
against a monthly allowance: three of every four runs are meant to be no-ops.

Only Azure characters are charged against the F0 allowance, because only Azure
characters consume it. Characters the Gemini fallback translated are recorded
separately (they cost real money instead, already a ``cost_log`` row per
CLAUDE.md rule 4) and are bounded by their own per-month circuit breaker,
``--max-gemini-characters-per-month``. Without that, a month in which Azure is
simply down would let every scheduled run fall through to a paid provider and
spend the LLM budget on translation.

## Failure

Never crashes on a provider failure. ``run_backfill`` already records a failed
batch and moves on; this file additionally writes the ledger and the report on
the way out of every path, including the one where no translator is configured
at all. The exit code is the scheduled job's only signal, so it is meaningful:
**0 when the run did what it should have** (translated something, or correctly
declined because the month is spent or the corpus is finished), **1 when there
was budget and work and the run translated nothing anyway**. That last case is
"it fired and silently did nothing", which is the failure a scheduled job is
most likely to have and the least likely to be noticed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from src.generation.blanking import carrier_validation
from src.generation.blanking.sentence_source import client_from_env
from src.generation.candidate_pool import DEFAULT_POOL_PATH, CandidatePool, PoolFormatError
from src.llm.env import load_env_file
from src.llm.translation import AZURE_F0_MONTHLY_CHARACTERS, Translator
from src.llm.translation_ledger import (
    Clock,
    LedgerVersionError,
    RemainingEstimate,
    estimate_months_remaining,
    load_ledger,
    month_key,
    save_ledger_atomic,
    utc_now,
)

from scripts.build_translations import (
    DEFAULT_LEIPZIG_PATH,
    DEFAULT_LIMIT_PER_SOURCE,
    DEFAULT_SEED,
    DEFAULT_STORE_PATH,
    DEFAULT_TATOEBA_PATH,
    BatchOutcome,
    TranslationRecord,
    TranslatorMode,
    _default_batch_size,
    _load_store,
    _read_one_corpus,
    run_backfill,
    translator_from_env,
)
from scripts.corpus_reading import SOURCE_LEIPZIG, SOURCE_TATOEBA, CorpusLine

#: Beside the store, as the owner asked. The store is the corpus of glosses and
#: this is how much it cost; keeping them in one directory means a backup or a
#: move takes both or neither.
DEFAULT_LEDGER_PATH = DEFAULT_STORE_PATH.parent / "azure_f0_ledger.json"

DEFAULT_REPORT_PATH = Path("data/monthly_translation_topup_report.json")

#: Azure F0's own allowance. Named from ``src/llm/translation.py`` rather than
#: repeated, so there is one literal 2,000,000 in this project.
DEFAULT_MONTHLY_BUDGET = AZURE_F0_MONTHLY_CHARACTERS

#: Characters held back from the monthly budget. Zero by default, so the
#: default really is the 2,000,000 the owner named. ``--headroom-characters``
#: is how he buys margin against Azure's own metering disagreeing with this
#: ledger by a few thousand characters, which it can: this counts what was
#: sent, Azure counts what it billed, and a batch that timed out after Azure
#: processed it is counted by one side and not the other.
DEFAULT_HEADROOM_CHARACTERS = 0

#: Store ``source`` values that are NOT a trusted machine translation. The
#: owner's 2026-08-27 decision, and the reason this job has any work to do:
#: 200,555 of the 201,794 records in the store today are Tatoeba's.
DISTRUSTED_STORE_SOURCES = frozenset({"tatoeba"})

#: How many successful batches between full store flushes. Every batch would be
#: safest and costs a complete rewrite of a store that will reach 450,000
#: records; 10 batches is about 1,000 carriers and 60,000 characters at risk in
#: a crash, 3% of a month, for a tenth of the I/O. The LEDGER is written every
#: batch regardless, which is the accounting requirement.
DEFAULT_CHECKPOINT_EVERY = 10

#: The Gemini circuit breaker. If Azure is down, every scheduled run this month
#: would otherwise fall through to the paid fallback and translate the corpus
#: with an LLM at real cost, against a 7.50 USD/month ceiling (CLAUDE.md
#: section 9). 100,000 characters is roughly 1,600 carriers, enough that a bad
#: hour still gets work done and small enough that a bad month cannot empty the
#: budget. Set to 0 to disable machine translation's Gemini path entirely.
DEFAULT_MAX_GEMINI_CHARACTERS_PER_MONTH = 100_000


def _order_key(text: str, seed: int) -> bytes:
    """This carrier's permanent rank, as bytes to sort on.

    Keyed BLAKE2b of the sentence itself. Deliberately not ``hash()`` (salted
    per process by ``PYTHONHASHSEED``, so it would reorder the corpus between
    two runs on the same machine) and deliberately not
    ``random.Random(seed).shuffle`` (a permutation of a list, so it changes for
    every carrier when the corpus grows by one line). A carrier's rank here
    depends on its own text and the seed and nothing else, which is what makes
    thirteen consecutive monthly runs walk forward through the corpus instead of
    re-drawing overlapping slices of it.
    """
    return hashlib.blake2b(
        text.encode("utf-8"), digest_size=16, key=str(seed).encode("ascii")
    ).digest()


def _carrier_is_usable(text: str) -> bool:
    """Whether ``text`` could host an exercise at all.

    The same validator the pipeline itself uses, so a sentence this job pays to
    translate is one the pipeline would accept. Measured on the real corpus:
    129,469 of 450,502 lines fail it, and every one of those was previously
    queued for a share of a 2,000,000-character monthly allowance it could never
    repay -- scraped junk, headlines with no finite verb, Swiss spelling,
    quotations that open mid-sentence.

    Degrades to accepting everything when spaCy is unavailable, rather than
    rejecting everything. A missing model must not silently turn a translation
    job into a no-op; it should behave as it did before this filter existed.
    """
    if not carrier_validation.analysis_available():
        return True
    return carrier_validation.validate_carrier(text).accepted


def load_exercise_carriers(
    pool_path: Path | None, bank_path: Path | None
) -> tuple[frozenset[str], list[str]]:
    """German carrier texts that are, or are about to be, real exercises.

    Read from a phase-A candidate pool and from a built bank. Both are
    optional and a missing one is not an error: a machine that has never run
    phase A simply has no pass 0, and the job behaves as it did before.

    Returns the texts plus human-readable notes for the report, because "pass 0
    was empty" and "pass 0 was skipped because the file is not there" look
    identical in a character count and are very different to an operator.
    """
    texts: set[str] = set()
    notes: list[str] = []

    if pool_path is not None:
        if pool_path.exists():
            try:
                pool = CandidatePool.load(pool_path)
            except PoolFormatError as exc:
                notes.append(f"pool at {pool_path} could not be read ({exc})")
            else:
                found = {p.text for p in pool.provenance.values()}
                texts |= found
                notes.append(f"{len(found)} carrier(s) from the candidate pool {pool_path}")
        else:
            notes.append(f"no candidate pool at {pool_path}")

    if bank_path is not None:
        if bank_path.exists():
            try:
                with sqlite3.connect(bank_path) as connection:
                    rows = connection.execute("SELECT prompt FROM items").fetchall()
            except sqlite3.Error as exc:
                notes.append(f"bank at {bank_path} could not be read ({exc})")
            else:
                # A banked item stores its prompt with the gap, not the carrier
                # it came from, so this cannot be matched against corpus text
                # directly. Counted and reported rather than silently ignored;
                # closing the gap needs the carrier recorded on the item, which
                # is a schema change and not this job's to make.
                notes.append(
                    f"{len(rows)} item(s) in the bank at {bank_path}, not matched: "
                    "a banked item records its gapped prompt, not its carrier"
                )
        else:
            notes.append(f"no bank at {bank_path}")

    return frozenset(texts), notes


@dataclass(frozen=True)
class PrioritisedCarriers:
    """The corpus split into this job's passes, each already in order."""

    #: Pass 0: carriers that are, or are about to be, real exercises -- the
    #: candidate pool and anything already in the bank. See ``prioritise``.
    exercises: list[CorpusLine]
    #: Pass a: nothing in the store for this German text at all.
    ungossed: list[CorpusLine]
    #: Pass b: in the store, but the gloss is Tatoeba's, so it is a replacement.
    replacements: list[CorpusLine]
    #: Carriers whose stored gloss this job trusts. Nothing to do for these.
    trusted: int
    #: Corpus lines dropped because they cannot host an exercise at all.
    #: Counted rather than silently discarded: this is a large number and an
    #: operator should see it.
    carrier_invalid: int = 0

    @property
    def todo(self) -> list[CorpusLine]:
        """Every pass, concatenated, in priority order. Each pass is exhausted
        before the next begins purely by being earlier in this list:
        ``run_backfill`` consumes it in order and stops at a whole-batch
        boundary, so the only batch that can ever mix two passes is the single
        one that straddles a join."""
        return [*self.exercises, *self.ungossed, *self.replacements]

    @property
    def carriers_untrusted(self) -> int:
        return len(self.exercises) + len(self.ungossed) + len(self.replacements)

    @property
    def characters_untrusted(self) -> int:
        """Measured, not estimated: these carriers were read, so this is the
        sum of their own German text lengths."""
        return sum(len(line.text) for line in self.todo)


def prioritise(
    carriers: dict[str, CorpusLine],
    store: dict[str, TranslationRecord],
    *,
    seed: int,
    distrusted_sources: frozenset[str] = DISTRUSTED_STORE_SOURCES,
    exercise_carriers: frozenset[str] = frozenset(),
    is_carrier_valid: Callable[[str], bool] | None = None,
) -> PrioritisedCarriers:
    """Split ``carriers`` into the passes, ordered (module docstring).

    ``exercise_carriers`` is pass 0: German texts that are already in the bank
    or in a candidate pool waiting to be verified. They jump the queue, and the
    reason is arithmetic. A 1,225-item bank needs about 68,000 characters, 3.4%
    of one month's Azure allowance, while the corpus at large is 13.4 months of
    it. Ordering by a hash of the sentence, as this job used to do throughout,
    reaches those carriers by coincidence somewhere across those 13 months, so
    the one consumer that exists today waits on the one that does not.

    ``is_carrier_valid`` drops sentences that cannot host an exercise at all.
    Measured on the real corpus: 129,469 of 450,502 lines fail carrier
    validation -- scraped junk, headlines with no finite verb, Swiss spelling,
    quotations that start mid-sentence. They were previously queued on equal
    terms with everything else. Passing ``None`` keeps every carrier, which is
    what a caller wants when it has already filtered.

    Pure: no I/O, no clock, no network. The store and the predicate are passed
    in so this can be driven directly from a test with a dict.
    """
    exercises: list[CorpusLine] = []
    ungossed: list[CorpusLine] = []
    replacements: list[CorpusLine] = []
    trusted = 0
    carrier_invalid = 0
    for text, line in carriers.items():
        record = store.get(text)
        is_exercise = text in exercise_carriers
        # A carrier already sampled into the pool or the bank is exempt from
        # the validity filter: it demonstrably hosts an exercise, whatever a
        # re-run of the validator would say about it today.
        if not is_exercise and is_carrier_valid is not None and not is_carrier_valid(text):
            carrier_invalid += 1
            continue
        if record is not None and record.source not in distrusted_sources:
            trusted += 1
            continue
        if is_exercise:
            exercises.append(line)
        elif record is None:
            ungossed.append(line)
        else:
            replacements.append(line)
    for group in (exercises, ungossed, replacements):
        group.sort(key=lambda line: (_order_key(line.text, seed), line.text))
    return PrioritisedCarriers(
        exercises=exercises,
        ungossed=ungossed,
        replacements=replacements,
        trusted=trusted,
        carrier_invalid=carrier_invalid,
    )


@dataclass
class MonthlyTopupReport:
    """Everything ``main()`` prints AND writes to JSON, one object, matching
    ``TranslationBackfillReport``'s own reason for existing: the console and the
    file can never say different things."""

    month: str
    store_path: str
    ledger_path: str
    seed: int
    monthly_budget: int
    headroom_characters: int
    translator_mode: TranslatorMode = "none"
    carriers_source: str = "corpora"
    batch_size: int = 1

    # What the month looked like when this run started.
    month_to_date_before: int = 0
    run_budget: int = 0
    # What the next whole batch would have cost. ``run_backfill`` never sends a
    # partial batch to use up a remainder, so a budget below this figure buys
    # nothing at all, and that has to be distinguishable from a run that failed:
    # it is the normal way a month ends.
    next_batch_characters: int = 0

    # The corpus, split.
    carriers_seen: int = 0
    carriers_trusted_before: int = 0
    carriers_ungossed_before: int = 0
    carriers_tatoeba_replacements_before: int = 0

    # What this run did.
    translated: int = 0
    translated_from_ungossed: int = 0
    translated_from_replacements: int = 0
    failed: int = 0
    failure_examples: list[str] = field(default_factory=list)
    azure_characters: int = 0
    gemini_characters: int = 0
    batches: int = 0

    # Where the month stands now.
    month_to_date_after: int = 0
    month_remaining_after: int = 0
    gemini_characters_this_month: int = 0
    store_size_after: int = 0

    # The number he will actually read.
    carriers_untrusted_after: int = 0
    characters_untrusted_after: int = 0
    months_remaining: float = 0.0
    months_remaining_whole: int = 0
    months_remaining_sentence: str = ""

    warnings: list[str] = field(default_factory=list)

    def apply_estimate(self, estimate: RemainingEstimate) -> None:
        self.carriers_untrusted_after = estimate.carriers_remaining
        self.characters_untrusted_after = estimate.characters_remaining
        self.months_remaining = estimate.months
        self.months_remaining_whole = estimate.whole_months
        self.months_remaining_sentence = estimate.sentence()

    def to_dict(self) -> dict[str, object]:
        return {
            "run": {
                "month": self.month,
                "store_path": self.store_path,
                "ledger_path": self.ledger_path,
                "seed": self.seed,
                "monthly_budget": self.monthly_budget,
                "headroom_characters": self.headroom_characters,
                "translator_mode": self.translator_mode,
                "carriers_source": self.carriers_source,
                "batch_size": self.batch_size,
                "month_to_date_before": self.month_to_date_before,
                "run_budget": self.run_budget,
                "next_batch_characters": self.next_batch_characters,
            },
            "corpus_before": {
                "carriers_seen": self.carriers_seen,
                "trusted": self.carriers_trusted_before,
                "ungossed": self.carriers_ungossed_before,
                "tatoeba_replacements": self.carriers_tatoeba_replacements_before,
            },
            "this_run": {
                "translated": self.translated,
                "translated_from_ungossed": self.translated_from_ungossed,
                "translated_from_replacements": self.translated_from_replacements,
                "failed": self.failed,
                "failure_examples": self.failure_examples,
                "azure_characters": self.azure_characters,
                "gemini_characters": self.gemini_characters,
                "batches": self.batches,
            },
            "month_after": {
                "month_to_date": self.month_to_date_after,
                "remaining_this_month": self.month_remaining_after,
                "gemini_characters_this_month": self.gemini_characters_this_month,
                "store_size": self.store_size_after,
            },
            "remaining": {
                "carriers_untrusted": self.carriers_untrusted_after,
                "characters_untrusted": self.characters_untrusted_after,
                "months_remaining": round(self.months_remaining, 2),
                "months_remaining_whole": self.months_remaining_whole,
                "sentence": self.months_remaining_sentence,
            },
            "warnings": self.warnings,
        }

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def run_topup(
    *,
    carriers: dict[str, CorpusLine],
    store_path: Path,
    ledger_path: Path,
    translator: Translator | None,
    translator_mode: TranslatorMode,
    monthly_budget: int = DEFAULT_MONTHLY_BUDGET,
    headroom_characters: int = DEFAULT_HEADROOM_CHARACTERS,
    max_characters: int | None = None,
    max_gemini_characters_per_month: int = DEFAULT_MAX_GEMINI_CHARACTERS_PER_MONTH,
    batch_size: int | None = None,
    seed: int = DEFAULT_SEED,
    limit_per_source: int = DEFAULT_LIMIT_PER_SOURCE,
    checkpoint_every: int = DEFAULT_CHECKPOINT_EVERY,
    carriers_source: str = "corpora",
    exercise_carriers: frozenset[str] = frozenset(),
    is_carrier_valid: Callable[[str], bool] | None = None,
    clock: Clock = utc_now,
) -> MonthlyTopupReport:
    """One scheduled top-up, independent of argparse, the environment and file
    reading.

    ``translator``, ``carriers`` and ``clock`` are injected (CLAUDE.md section
    8), so a test drives this with a fake translator, an in-memory carrier set
    and a clock it can walk across a month boundary. ``store_path`` and
    ``ledger_path`` are real filesystem I/O against ``tmp_path``, because both
    files ARE the mechanism under test: the store is the position pointer and
    the ledger is the month-to-date total.
    """
    now = clock()
    key = month_key(now)

    ledger = load_ledger(ledger_path)
    ledger.record_run(key, now)
    save_ledger_atomic(ledger_path, ledger)

    effective_budget = max(0, monthly_budget - headroom_characters)
    spent_before = ledger.spend_for(key)
    remaining_this_month = ledger.remaining(key, effective_budget)
    run_budget = (
        remaining_this_month
        if max_characters is None
        else min(max_characters, remaining_this_month)
    )

    store = _load_store(store_path)
    # Pass 0 first, then the corpus at large, and never a sentence that cannot
    # host an exercise. See `prioritise` for the arithmetic behind both.
    prioritised = prioritise(
        carriers,
        store,
        seed=seed,
        exercise_carriers=exercise_carriers,
        is_carrier_valid=is_carrier_valid,
    )
    todo = prioritised.todo
    resolved_batch_size = (
        batch_size if batch_size is not None else _default_batch_size(translator_mode)
    )

    report = MonthlyTopupReport(
        month=key,
        store_path=str(store_path),
        ledger_path=str(ledger_path),
        seed=seed,
        monthly_budget=monthly_budget,
        headroom_characters=headroom_characters,
        translator_mode=translator_mode,
        carriers_source=carriers_source,
        batch_size=resolved_batch_size,
        month_to_date_before=spent_before.azure_characters,
        run_budget=run_budget,
        carriers_seen=len(carriers),
        carriers_trusted_before=prioritised.trusted,
        carriers_ungossed_before=len(prioritised.ungossed),
        carriers_tatoeba_replacements_before=len(prioritised.replacements),
        next_batch_characters=sum(len(line.text) for line in todo[:resolved_batch_size]),
        store_size_after=len(store),
    )

    if todo and 0 < run_budget < report.next_batch_characters:
        # Measured, not theoretical: the smoke run that found this had a budget
        # of 3,000, a batch size of 100 and carriers averaging 71.5 characters,
        # so the first batch wanted 7,150 and the run did nothing while looking
        # exactly like a failure. In production the gap is one batch at the end
        # of a month, about 6,000 characters out of 2,000,000, and it is normal.
        report.warnings.append(
            f"Budget left this run ({run_budget:,} characters) is smaller than the "
            f"next whole batch ({report.next_batch_characters:,} characters, "
            f"{resolved_batch_size} carriers), so no batch was attempted. A partial "
            "batch is never sent. Those characters expire unspent, which is how a "
            "month normally ends; lower --batch-size only if the waste matters."
        )

    if translator_mode == "gemini_only":
        report.warnings.append(
            "NO AZURE KEY CONFIGURED: this run would translate on Gemini, which is "
            "not the free tier this job exists to spend and is not free once its own "
            "free lane closes. Set AZURE_TRANSLATOR_KEY."
        )
    elif translator_mode == "none":
        report.warnings.append(
            "NO TRANSLATOR CONFIGURED: no Azure key and no Gemini key. Nothing was "
            "translated this run."
        )
    if remaining_this_month <= 0 and effective_budget > 0:
        report.warnings.append(
            f"Month {key} has already spent its {effective_budget:,}-character budget "
            f"({spent_before.azure_characters:,} used). Nothing to do until the month "
            "rolls over, which needs no action."
        )

    # The Gemini circuit breaker (module docstring, "Stopping"). Checked before
    # any call, against the LEDGER rather than this process, because the case it
    # guards is many scheduled runs in one bad month, not one long run.
    gemini_this_month = spent_before.gemini_characters
    gemini_blocked = (
        translator_mode in ("gemini_only", "fallback")
        and gemini_this_month >= max_gemini_characters_per_month
    )
    if gemini_blocked and translator_mode == "gemini_only":
        report.warnings.append(
            f"Gemini circuit breaker: {gemini_this_month:,} Gemini characters already "
            f"this month, at or over the {max_gemini_characters_per_month:,} cap, and "
            "there is no Azure key to fall back FROM. Skipping machine translation. "
            "Raise --max-gemini-characters-per-month only if you mean to pay for it."
        )
        translator = None
    elif gemini_blocked:
        report.warnings.append(
            f"Gemini characters this month ({gemini_this_month:,}) are at or over the "
            f"{max_gemini_characters_per_month:,} cap. Azure is still primary, so the "
            "run continues, but the fallback firing this much means Azure is failing."
        )

    ungossed_texts = {line.text for line in prioritised.ungossed}
    translated_texts: list[str] = []

    def on_batch(outcome: BatchOutcome) -> None:
        """Persist the month's accounting the instant a batch lands.

        Called by ``run_backfill`` after the store checkpoint, so a crash between
        the two leaves work done and uncharged rather than charged and lost.
        Writing the whole ledger every batch is a few hundred bytes and one
        ``os.replace``; the alternative, holding the total in memory until the
        run ends, is precisely the bug this file exists to fix.
        """
        translated_texts.extend(outcome.texts)
        ledger.record_batch(
            key,
            azure_characters=outcome.azure_characters,
            gemini_characters=outcome.gemini_characters,
            at=now,
        )
        save_ledger_atomic(ledger_path, ledger)

    ordered: dict[str, CorpusLine] = {line.text: line for line in todo}
    backfill = run_backfill(
        carriers=ordered,
        store_path=store_path,
        translator=translator,
        translator_mode=translator_mode,
        tatoeba_pairs=[],
        max_characters=run_budget,
        batch_size=resolved_batch_size,
        seed=seed,
        limit_per_source=limit_per_source,
        carriers_source=carriers_source,
        trust_tatoeba=False,
        distrust_stored_sources=DISTRUSTED_STORE_SOURCES,
        on_batch=on_batch,
        checkpoint_every=checkpoint_every,
        now=now,
    )

    report.translated = backfill.machine_translated
    # Counted from the batches themselves, not inferred from how far the cursor
    # moved: a failed batch advances the cursor without translating anything, so
    # "the first N carriers were pass a" is wrong the moment anything fails.
    report.translated_from_ungossed = sum(1 for text in translated_texts if text in ungossed_texts)
    report.translated_from_replacements = (
        backfill.machine_translated - report.translated_from_ungossed
    )
    report.failed = backfill.failed
    report.failure_examples = list(backfill.failure_examples)
    report.azure_characters = backfill.characters_spent
    report.gemini_characters = backfill.gemini_fallback_characters
    report.store_size_after = backfill.store_size_after
    report.warnings.extend(backfill.warnings)

    spent_after = ledger.spend_for(key)
    report.batches = spent_after.batches - spent_before.batches
    report.month_to_date_after = spent_after.azure_characters
    report.month_remaining_after = ledger.remaining(key, effective_budget)
    report.gemini_characters_this_month = spent_after.gemini_characters

    # Measured, not modelled. Only a SUCCESSFUL batch's characters are counted
    # by run_backfill, so subtracting them from what the two passes held is the
    # exact remainder; a failed batch leaves its carriers, and their characters,
    # still to do.
    report.apply_estimate(
        estimate_months_remaining(
            characters_remaining=prioritised.characters_untrusted
            - (backfill.characters_spent + backfill.gemini_fallback_characters),
            carriers_remaining=prioritised.carriers_untrusted - backfill.machine_translated,
            monthly_budget=max(1, effective_budget),
        )
    )
    return report


def _print_report(report: MonthlyTopupReport) -> None:
    print(f"\n  Month:                     {report.month}")
    print(f"  Translator mode:           {report.translator_mode}")
    print(f"  Carriers seen:             {report.carriers_seen:,}")
    print(f"    already trusted:         {report.carriers_trusted_before:,}")
    print(f"    no gloss at all:         {report.carriers_ungossed_before:,}")
    print(f"    Tatoeba, to replace:     {report.carriers_tatoeba_replacements_before:,}")
    print(f"\n  Translated this run:       {report.translated:,} in {report.batches:,} batches")
    print(f"    of which first glosses:  {report.translated_from_ungossed:,}")
    print(f"    of which replacements:   {report.translated_from_replacements:,}")
    print(f"  Failed:                    {report.failed:,}")
    for example in report.failure_examples:
        print(f"    - {example}")
    print(f"\n  Azure characters this run: {report.azure_characters:,}")
    print(f"  Gemini characters this run:{report.gemini_characters:,}")
    print(
        f"  Month to date (Azure):     {report.month_to_date_after:,} / "
        f"{report.monthly_budget - report.headroom_characters:,}"
    )
    print(f"  Remaining this month:      {report.month_remaining_after:,}")
    print(f"  Store size:                {report.store_size_after:,}")
    print(f"\n  Still untrusted:           {report.carriers_untrusted_after:,} carriers")
    print(f"  Months remaining:          {report.months_remaining:.1f}")
    print(f"\n  {report.months_remaining_sentence}")
    for warning in report.warnings:
        print(f"\n  WARNING: {warning}")


def _read_corpora(args: argparse.Namespace) -> dict[str, CorpusLine]:
    """The whole-corpus carrier set, through ``build_translations``' own reader
    so the two jobs can never disagree about what a carrier is."""
    carriers: dict[str, CorpusLine] = {}
    if not args.skip_tatoeba:
        for line in _read_one_corpus(
            args.tatoeba, "tatoeba", "Tatoeba", args.limit, args.seed, SOURCE_TATOEBA
        ):
            carriers.setdefault(line.text, line)
    if not args.skip_leipzig:
        for line in _read_one_corpus(
            args.leipzig, "lines", "Leipzig", args.limit, args.seed, SOURCE_LEIPZIG
        ):
            carriers.setdefault(line.text, line)
    return carriers


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "The standing monthly Azure F0 translation top-up. Safe to schedule: "
            "it remembers what the month has already spent."
        )
    )
    parser.add_argument("--tatoeba", type=Path, default=DEFAULT_TATOEBA_PATH)
    parser.add_argument("--leipzig", type=Path, default=DEFAULT_LEIPZIG_PATH)
    parser.add_argument("--skip-tatoeba", action="store_true")
    parser.add_argument("--skip-leipzig", action="store_true")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT_PER_SOURCE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--pool-file",
        type=str,
        default=str(DEFAULT_POOL_PATH),
        help=(
            "Phase-A candidate pool. Its carriers are translated FIRST, before "
            "the corpus at large: they are the sentences that actually become "
            "exercises, and a 1,225-item bank needs about 3.4%% of one month's "
            "allowance."
        ),
    )
    parser.add_argument(
        "--bank-file",
        type=str,
        default="data/bank.db",
        help="Built bank, read alongside the pool for pass 0.",
    )
    parser.add_argument(
        "--no-priority-carriers",
        action="store_true",
        help="Disable pass 0 and treat the corpus uniformly, as before.",
    )
    parser.add_argument(
        "--keep-carrier-invalid",
        action="store_true",
        help=(
            "Translate sentences that cannot host an exercise. Off by default: "
            "129,469 of 450,502 corpus lines fail carrier validation and were "
            "previously queued for allowance they can never repay."
        ),
    )
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE_PATH)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_PATH)
    parser.add_argument(
        "--monthly-budget",
        type=int,
        default=DEFAULT_MONTHLY_BUDGET,
        help="Azure F0's monthly character allowance (default: 2,000,000).",
    )
    parser.add_argument(
        "--headroom-characters",
        type=int,
        default=DEFAULT_HEADROOM_CHARACTERS,
        help=(
            "Characters held back from --monthly-budget, so the job stops short of "
            "the ceiling rather than discovering it as a 403 mid-batch. Default 0."
        ),
    )
    parser.add_argument(
        "--max-characters",
        type=int,
        default=None,
        help=(
            "Cap this ONE invocation as well (default: the whole remaining monthly "
            "budget). Useful for a first supervised run."
        ),
    )
    parser.add_argument(
        "--max-gemini-characters-per-month",
        type=int,
        default=DEFAULT_MAX_GEMINI_CHARACTERS_PER_MONTH,
        help=(
            "Circuit breaker on the paid fallback. If Azure is down all month, this "
            "is what stops every scheduled run spending the LLM budget on translation."
        ),
    )
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=DEFAULT_CHECKPOINT_EVERY,
        help=(
            "Flush the store to disk every N successful batches (0 = only at the "
            "end). The ledger is written every batch regardless."
        ),
    )
    parser.add_argument("--report-file", type=str, default=str(DEFAULT_REPORT_PATH))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.monthly_budget <= 0:
        parser.error("--monthly-budget must be positive")
    if args.headroom_characters < 0:
        parser.error("--headroom-characters cannot be negative")
    if args.headroom_characters >= args.monthly_budget:
        parser.error(
            "--headroom-characters is at or above --monthly-budget, which leaves no "
            "budget at all. Lower --monthly-budget instead if that is what you meant."
        )

    load_env_file()

    carriers = _read_corpora(args)
    if not carriers:
        print("No carriers read; nothing to do. Check --tatoeba/--leipzig.")
        return 1

    exercise_carriers, pass_zero_notes = load_exercise_carriers(
        None if args.no_priority_carriers else Path(args.pool_file),
        None if args.no_priority_carriers else Path(args.bank_file),
    )
    print("\n  Pass 0, carriers that are or will be exercises:")
    for note in pass_zero_notes:
        print(f"    {note}")
    if not exercise_carriers and not args.no_priority_carriers:
        print("    none found; this run is corpus-wide only")

    gemini_client = client_from_env()
    translator, mode = translator_from_env(gemini_client)
    if mode == "gemini_only":
        print("\n  *** NO AZURE KEY CONFIGURED. This job exists to spend Azure F0. ***\n")
    elif mode == "none":
        print("\n  *** NO TRANSLATOR CONFIGURED. Nothing will be translated. ***\n")

    try:
        report = run_topup(
            carriers=carriers,
            store_path=args.store,
            ledger_path=args.ledger,
            translator=translator,
            translator_mode=mode,
            monthly_budget=args.monthly_budget,
            headroom_characters=args.headroom_characters,
            max_characters=args.max_characters,
            max_gemini_characters_per_month=args.max_gemini_characters_per_month,
            batch_size=args.batch_size,
            seed=args.seed,
            limit_per_source=args.limit,
            checkpoint_every=args.checkpoint_every,
        )
    except LedgerVersionError as exc:
        # The one thing worth refusing to run over: a ledger this code cannot
        # read means the month-to-date figure is unknown, and guessing at it
        # spends a quota that cannot be checked any other way.
        print(f"\n  FAILING: {exc}")
        return 1

    _print_report(report)

    report_path = Path(args.report_file)
    report.write(report_path)
    print(f"\n  Report file: {report_path}")

    had_work = report.carriers_untrusted_after + report.translated > 0
    # A budget too small for one whole batch is not a failure, it is how a month
    # ends. Reporting it as one would make a weekly schedule cry wolf for the
    # last three weeks of every month, which is the fastest way to teach someone
    # to stop reading the exit code.
    budget_fits_a_batch = report.run_budget >= report.next_batch_characters
    if report.run_budget > 0 and had_work and budget_fits_a_batch and report.translated == 0:
        print(
            "\n  FAILING: this run had budget and had work and translated nothing. "
            "That is the silent-no-op case; see the warnings above and the report."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
