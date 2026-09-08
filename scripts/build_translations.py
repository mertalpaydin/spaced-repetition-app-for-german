"""TODO.md 5.1 step 2: the resumable English-gloss backfill.

Builds and maintains ``data/fixtures/translations/de_en.jsonl``, a JSONL
store mapping one German carrier sentence to one English gloss, so the item
pipeline can look a translation up at build time without ever calling out to
a translation provider on the critical path (CLAUDE.md rule 3).

## Two modes: whole corpus, or one carrier list

The default mode reads the staged corpora (``--tatoeba``/``--leipzig``) and
works through every length-plausible carrier in them. That is the right
shape for a standing store and the wrong shape for a build. Measured: the
two corpora hold 450,490 distinct carriers, Tatoeba glosses 200,555 of them
for free, and the remaining 248,935 need about 17,000,000 characters of
machine translation, which is roughly eight and a half months of Azure F0's
2,000,000/month allowance.

``--carriers-from PATH`` is the other mode, and it is the one a build should
use. A single 396-item pilot needs a gloss for 396 sentences, 230 of which
are already in the store, so 166 new translations and about 11,000
characters: one run, inside one night's budget, instead of eight and a half
months of nightly runs. The file is one German sentence per line, UTF-8,
blank lines skipped and whitespace stripped; a line that parses as a JSON
object is read for its ``german`` key and skipped if it has none, so the
same flag accepts either a plain sentence list or a JSONL dump of items
without a second flag for the difference.

When ``--carriers-from`` is given it REPLACES corpus reading entirely:
``--tatoeba`` and ``--leipzig`` are not read at all, and ``carriers_seen``
is the count of distinct sentences in the given file. Everything downstream
is identical: the store is still consulted first, the Tatoeba pair join
still runs if ``--pairs``/``--links`` are given, and machine translation is
still bounded by the same per-run character budget. The report records
which mode produced it in ``carriers_source`` (``"corpora"``, or the path),
so a stored gloss can be traced back to the run shape that wrote it.

## The Tatoeba join is opt-in, and OFF by default

``--trust-tatoeba`` decides whether source 2 below runs at all, and it
defaults to **off**. With it off, ``_fill_from_tatoeba`` does not run and
every carrier the store lacks goes to machine translation. With it on,
today's behaviour is restored exactly.

The owner's decision, from a hand audit of all 430 accepted exercises in the
last pilot. Ten defects; four of them were items whose German is correct and
whose English gloss is wrong, and **three of those four came from Tatoeba's
own human translations, not from machine translation**:

    Wenn ich im Lotto gewaenne, wuerde ich mir ein neues Auto kaufen.
    "If I won the lottery, I'd buy you a new car."      <- mir is himself

    Ich habe eine Freundin, die sich selbst die Haare schneidet.
    "I have a friend who cuts his own hair."            <- Freundin is female

    Das Haus, in dem man lacht, wird vom Glueck bedacht.
    "The house in which one laughs is considered by luck."  <- meaningless

A separate hand check of 120 Tatoeba pairs found 2 outright wrong and 6
loose. So Tatoeba's translations are no longer trusted on the path that
produces exercises.

**The records are not deleted, and this flag is why.** Feature 5.3 (click a
word, see it in several corpus sentences with their translations) is fed
entirely by them, and it needs breadth far more than it needs precision.
``--trust-tatoeba`` is how that store gets rebuilt cheaply: 200,555 carriers
glossed for nothing instead of 13,000,000 characters of machine translation,
which is half a year of Azure F0. The distrust is scoped to the exercise
path, which is ``scripts/step7_corpus_pilot.py``'s own
``--trust-stored-tatoeba`` (defaulting to distrust for the same reason).

## Three sources, in the owner's priority order (TODO.md section 4)

1. **Already in the store.** Loaded first; anything present is never
   re-translated. See "Resumability" below.
2. **Tatoeba's own English translations** (ONLY under ``--trust-tatoeba``,
   see above), via the readers this script
   reuses rather than re-implements
   (``scripts.eval_tatoeba_translation_quality.read_pairs_file`` /
   ``read_links_and_english``). Where a German sentence has more than one
   Tatoeba translation (13.1% of them, per that script's own measurement),
   the SHORTEST one is kept -- the owner's decision (TODO.md section 4): a
   short translation is the more literal one, and literal is what maps word
   to word for a learner, whereas a long one paraphrases, and paraphrase is
   where tense and determiners drift. This step costs nothing: it is a
   dictionary lookup, not an API call, so it is not subject to
   ``--max-characters`` and always runs to completion in one pass.
   **The id half of that join belongs to Tatoeba carriers only.**
   ``shortest_by_id`` is keyed on Tatoeba sentence ids, and a Leipzig line id
   is a different namespace that happens to use the same integers, so the
   join checks ``CorpusLine.source`` before it consults an id at all. Text
   matching stays open to every carrier because the German string is its own
   proof. See ``_fill_from_tatoeba`` for the measured damage the unfenced
   version did.

3. **The ``Translator`` from ``src/llm/translation.py``** (Azure primary,
   Gemini fallback) for everything left over. Without ``--trust-tatoeba``
   that is every carrier the store does not already hold; with it, all of
   Leipzig plus the 38.3% of Tatoeba's own carriers that source measured as
   untranslated. This step IS subject to ``--max-characters``, see below.

## Resumability, keyed on exact German text

The store is loaded before anything else, and a carrier already present
(matched by its own exact sentence text, not a normalised or lower-cased
form) is skipped entirely -- no re-lookup, no re-translation, no API call.
Exact text, not corpus id, because the id is what the *building* pipeline
never has at lookup time: it only ever has the carrier sentence string
itself (this is also why the store's own key is ``german``, not
``corpus_line_id`` -- a Tatoeba id and a Leipzig id do not even share a
namespace). This does mean two carriers that differ only in whitespace or
case would be treated as different sentences and both translated; that is
the safer failure mode for a language where case carries grammatical
information (a German noun's capitalisation is not decorative), so no
normalisation is applied.

Because carriers are read with ``--limit`` effectively uncapped (the same
"read once, filter, then slice" ``read_corpus_lines`` this project already
uses elsewhere) and a deterministic ``--seed``, a rerun sees the exact same
carrier set in the exact same order every night; the ones already in the
store from previous nights are skipped, and the run's character budget is
spent moving further into the ones that are not. No separate position
pointer is needed -- the store itself is the pointer.

## The character budget is per RUN, not per month

``--max-characters`` (default ``DEFAULT_MAX_CHARACTERS_PER_RUN``, 60,000)
bounds how many characters of SUCCESSFUL machine translation this one
invocation performs. It is deliberately not the same thing as
``AzureTranslator.monthly_character_budget`` (that class's own per-process
running total, reset to zero by every fresh nightly process, per its own
docstring) -- this script's own budget is what actually protects the
monthly ceiling in aggregate, by keeping each night's slice small enough
that roughly 33+ nightly runs fit inside Azure F0's 2,000,000/month
allowance with headroom. A run stops cleanly at a whole-batch boundary
(never mid-batch) once the next batch would exceed the remaining budget,
and reports exactly how many carriers were left untouched
(``skipped_for_budget``) rather than guessing at how much work remains.

Only successfully translated characters count against the budget. A batch
that raises ``TranslationError`` (both providers refused, or Gemini
returned nothing) spent no real Azure quota -- ``AzureTranslator`` itself
only increments its own counter after a successful call, for the identical
reason -- so it is not charged here either; those carriers stay out of the
store and are retried on the next run.

``characters_spent`` counts ONLY what Azure actually translated. A batch
Azure refused and the Gemini fallback translated spent none of the F0
allowance this counter exists to protect, so charging it there was simply
wrong, and it contradicted the paragraph above, which already reasons
correctly about the identical case for a failed batch. Those characters are
reported separately as ``gemini_fallback_characters`` so the two are
visible and never conflated. Gemini's real cost needs no accounting here at
all: every fallback call goes through ``GeminiLlmClient`` and is already a
row in the cost log (CLAUDE.md rule 4). What ``gemini_fallback_characters``
adds is visibility into how much of a run's work Azure declined, which is
the number that says whether the fallback is firing more than it should.

The run's STOPPING condition still counts both, azure and fallback
characters together, against ``--max-characters``. The budget's first job
is protecting the F0 allowance, but its second is bounding one invocation's
total work, and a Gemini-only run (no Azure key at all) would otherwise
have no cap on a provider that genuinely costs money once its free lane
closes.

## Batch size is chosen for BLAST RADIUS, not just throughput

``AzureTranslator`` genuinely batches (one HTTP call per up to
``AZURE_MAX_BATCH`` sentences), so grouping carriers into batches of that
size is a real efficiency win when Azure is in play. ``GeminiTranslator``
does not batch at all -- it is one ``generate()`` call per sentence,
by that class's own docstring, specifically so a bad line cannot merge,
reorder or drop a neighbour's translation. Grouping Gemini calls into a
large outer batch here would buy nothing (still one call per sentence
underneath) while making a single bad sentence lose every already-good
translation ahead of it in the same batch, because ``Translator.translate``
raises for the whole input rather than returning partial results. So the
default batch size is chosen from the translator mode, not fixed: full
``AZURE_MAX_BATCH`` whenever Azure is available (``fallback``/``azure_only``
modes), 1 when the run is Gemini-only. ``--batch-size`` overrides either
default explicitly.

## Gemini-only mode

If ``AZURE_TRANSLATOR_KEY`` is not configured, ``azure_from_env`` returns
``None`` (its own contract) and this script does not stop: it runs on
``GeminiTranslator`` alone and says so loudly, in the console banner AND in
the written report's own ``warnings`` list -- never just a quiet downgrade,
since Gemini is the fallback for a reason (lower quality, not free once its
own free lane closes) and a silent switch would leave a worse gloss in the
store with nothing to say why.

## Atomic write

The store is rewritten in full on every run that changes it: every record
loaded from the existing file plus every record produced this run, written
to a fresh temp file in the store's own directory, ``fsync``'d, then swapped
into place with a single ``os.replace``. ``os.replace`` is an atomic rename
on the same filesystem, so the target path is either the complete old
content or the complete new content at every instant -- a process killed at
any point before the swap leaves the real store file completely untouched,
and the orphaned temp file is cleaned up on any exception during the write.
A killed run therefore never leaves a half-written or corrupt store; at
worst it loses that one run's own progress, which the next run simply
redoes (cheap, since the free-tier translation this loses is what the
per-run budget was already sized to keep small).

## Three hooks the standing monthly job needs, all defaulting to off

``scripts/monthly_translation_topup.py`` (the owner's standing job: spend Azure
F0's 2,000,000 characters a month, every month, until the corpus is covered)
calls ``run_backfill`` rather than owning a second copy of the batch loop. It
needs three things this script did not have, all added as parameters that
default to today's behaviour exactly:

* ``distrust_stored_sources`` names store ``source`` values to treat as a cache
  MISS. Empty here; the monthly job passes ``{"tatoeba"}``, because replacing
  those 200,555 glosses is most of what that job exists to do. The stored record
  is left alone until a real translation lands to overwrite it.
* ``on_batch`` fires once per SUCCESSFUL batch. That is what lets a caller
  persist month-to-date spend as it goes, which is the one thing
  ``AzureTranslator.characters_used`` (per process) and ``--max-characters``
  (per invocation) between them cannot do.
* ``checkpoint_every`` flushes the store every N successful batches instead of
  once at the end, so a killed run does not throw away glosses that a ledger has
  already charged the month for.

The checkpoint runs BEFORE ``on_batch``, deliberately. See
``_run_machine_translation``.

## Reuse, not reimplementation

- Tatoeba pair readers: ``scripts.eval_tatoeba_translation_quality``
  (``read_pairs_file``, ``read_links_and_english``, ``Pair``).
- Corpus carrier reader: ``scripts.corpus_reading.read_corpus_lines``, the
  one reader ``scripts/step7_corpus_pilot.py`` and
  ``scripts/build_verb_government.py`` already share.
- Translation itself: ``src/llm/translation.py`` in full (not modified
  here) -- ``AzureTranslator``, ``GeminiTranslator``, ``FallbackTranslator``,
  ``azure_from_env``, ``TranslationError``, ``AZURE_MAX_BATCH``.
- Gemini client construction: ``src.generation.blanking.sentence_source.
  client_from_env`` -- ``forbid_batch=True, forbid_paid_lane=False``, the
  same pilot-lane policy ``step6_blank_pilot.py``/``step7_corpus_pilot.py``
  already use and for the identical reason: this script has no ``--batch``
  opt-in, so a real Batch API submission is never appropriate here, but the
  paid lane itself should stay open once the free lane's daily quota closes
  rather than silently degrading a nightly job.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from src.contracts import MODEL_GENERATE
from src.generation.blanking.sentence_source import client_from_env
from src.llm.client import GeminiLlmClient
from src.llm.env import load_env_file
from src.llm.translation import (
    AZURE_MAX_BATCH,
    AzureTranslator,
    FallbackTranslator,
    GeminiTranslator,
    TranslationError,
    Translator,
    azure_from_env,
)

from scripts.corpus_reading import (
    SOURCE_LEIPZIG,
    SOURCE_TATOEBA,
    CorpusLine,
    default_corpus_path,
    read_corpus_lines,
)
from scripts.eval_tatoeba_translation_quality import Pair, read_links_and_english, read_pairs_file

# Matches step7_corpus_pilot.py's and build_verb_government.py's own defaults
# for these two staged corpora -- the only place either file actually exists
# in this environment (checked at build time; data/raw/_extract/ itself does
# not exist here, only under this uploads mount).
DEFAULT_TATOEBA_PATH = default_corpus_path("tatoeba_deu.tsv")
DEFAULT_LEIPZIG_PATH = default_corpus_path("leipzig_sample.txt")
DEFAULT_STORE_PATH = Path("data/fixtures/translations/de_en.jsonl")
DEFAULT_REPORT_PATH = Path("data/translation_backfill_report.json")

# Effectively "the whole corpus" -- read_corpus_lines reads and filters the
# entire file regardless, then shuffles once (deterministic under --seed)
# and slices; a limit this large is never actually reached by either staged
# corpus (module docstring, "Resumability"), it exists only as a guard
# against an unboundedly large future corpus file, matching build_verb_
# government.py's own _EFFECTIVELY_UNCAPPED reasoning.
DEFAULT_LIMIT_PER_SOURCE = 2_000_000
DEFAULT_SEED = 7

# The owner's own instruction: leave headroom under Azure F0's 2,000,000
# characters/month rather than spend right up to it, so one slow or unlucky
# night is not the night that tips the month over.
DEFAULT_MAX_CHARACTERS_PER_RUN = 60_000

TranslatorMode = Literal["fallback", "azure_only", "gemini_only", "none"]

#: The owner's decision, 2026-08-27: Tatoeba's own translations are not
#: trusted for anything that becomes an exercise, so the join is opt-in.
#: See the module docstring for the four audited defects behind it.
DEFAULT_TRUST_TATOEBA = False

#: Store ``source`` values that ``run_backfill`` treats as a cache MISS, so the
#: carrier is translated again and the record overwritten. Empty by default:
#: this script's own contract has always been "anything in the store is done",
#: and the standing monthly job (``scripts/monthly_translation_topup.py``) is
#: the caller that wants otherwise. It passes ``frozenset({"tatoeba"})``, which
#: is the same distrust ``step7_corpus_pilot.--trust-stored-tatoeba`` already
#: applies at the pilot layer, expressed here so the replacement pass and the
#: first-time pass run through one loop instead of two that drift.
NO_DISTRUSTED_SOURCES: frozenset[str] = frozenset()


@dataclass(frozen=True)
class BatchOutcome:
    """What one SUCCESSFUL machine-translation batch did, handed to
    ``run_backfill``'s ``on_batch`` hook the instant it lands.

    Exists so a caller can persist accounting per batch rather than per run.
    ``AzureTranslator.characters_used`` is per process and ``--max-characters``
    is per invocation, so a job that runs more than once a month has to keep its
    own month-to-date total; ``src/llm/translation_ledger.py`` is that total and
    this is how it is fed. A crash then loses at most one batch of accounting
    instead of the whole run's.

    Azure characters and Gemini characters are separate fields and are never
    summed here, for the same reason ``TranslationBackfillReport`` keeps
    ``characters_spent`` and ``gemini_fallback_characters`` apart: only one of
    them consumes the Azure F0 allowance.
    """

    #: The German text of every carrier in this batch, in order. Carried
    #: because the ordering of ``still_todo`` is the only other way a caller
    #: could work out WHICH carriers a batch covered, and inferring it from
    #: counts breaks the moment a batch in front of it fails: a failed batch
    #: advances the cursor without translating anything.
    texts: tuple[str, ...]
    source: Literal["azure", "gemini"]
    azure_characters: int
    gemini_characters: int

    @property
    def carriers(self) -> int:
        return len(self.texts)

    @property
    def characters(self) -> int:
        """Both providers' characters together. What the RUN's own budget
        bounds, as opposed to what the F0 allowance is charged."""
        return self.azure_characters + self.gemini_characters


def tatoeba_policy_sentence(trust_tatoeba: bool) -> str:
    """One plain sentence saying what this run's Tatoeba setting MEANS, not
    just which way the switch was thrown.

    Printed and written to the report from this one function so the console
    and the JSON can never say different things, and so a stored gloss can be
    traced to a policy rather than to a bare boolean nobody can interpret two
    months later.
    """
    if trust_tatoeba:
        return (
            "--trust-tatoeba GIVEN: Tatoeba's own human translations were used as "
            "glosses wherever they exist, free. That is the right mode for rebuilding "
            "the feature 5.3 corpus store, which needs breadth. It is the WRONG mode "
            "for building a pilot's exercises: a hand audit of 430 accepted items "
            "found 4 wrong glosses and 3 of the 4 were Tatoeba's own."
        )
    return (
        "--trust-tatoeba NOT given (the default): Tatoeba's own translations were "
        "not used at all, and every carrier the store lacked went to machine "
        "translation. The Tatoeba records already in the store are untouched and "
        "still feed feature 5.3; they are simply not trusted for exercises."
    )


class TranslationRecord(BaseModel):
    """One stored gloss. Pydantic per CLAUDE.md section 8 ("Pydantic models
    for every LLM input and output. No raw dicts crossing a module
    boundary.") -- this is the persisted shape of exactly that output,
    whichever of the three sources produced it."""

    model_config = ConfigDict(frozen=True)
    german: str
    english: str
    source: Literal["tatoeba", "azure", "gemini"]
    # Audit trail only, never read back for resumability (which keys purely
    # on ``german`` -- module docstring). Lets a human spot, from the store
    # alone, whether a given gloss came from an early or a late night of the
    # six-week job, without cross-referencing the cost log.
    written_at: datetime


@dataclass
class TranslationBackfillReport:
    """Everything ``main()`` prints AND writes to JSON -- one object, so the
    two can never drift apart, matching this project's own established
    pattern (``step7_corpus_pilot.py``'s ``CorpusPilotReport``)."""

    seed: int
    limit_per_source: int
    max_characters: int
    batch_size: int
    store_path: str
    translator_mode: TranslatorMode = "none"
    # "corpora" for the whole-corpus mode, or the --carriers-from path
    # (module docstring, "Two modes"), so a stored gloss can be traced back
    # to the run shape that produced it.
    carriers_source: str = "corpora"
    # Off by default (DEFAULT_TRUST_TATOEBA). Recorded in the run block
    # because it is the single biggest thing that decides what a stored
    # gloss actually IS, and a store holds records from many runs.
    trust_tatoeba: bool = DEFAULT_TRUST_TATOEBA
    carriers_seen: int = 0
    already_in_store: int = 0
    # Carriers that ARE in the store but whose stored source this run distrusts
    # (``distrust_stored_sources``), so they were re-translated and the record
    # overwritten. Counted separately from ``already_in_store``, which now means
    # "in the store with a source this run trusts", so the two numbers together
    # still add up to the carriers the store held.
    replacing_distrusted: int = 0
    from_tatoeba: int = 0
    machine_translated: int = 0
    skipped_for_budget: int = 0
    failed: int = 0
    failure_examples: list[str] = field(default_factory=list)
    # Azure characters only (module docstring, "The character budget is per
    # RUN"). What the Gemini fallback translated is counted separately, in
    # gemini_fallback_characters, and never added here.
    characters_spent: int = 0
    gemini_fallback_characters: int = 0
    azure_fallback_events: int = 0
    azure_fallback_examples: list[str] = field(default_factory=list)
    store_size_after: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "run": {
                "seed": self.seed,
                "limit_per_source": self.limit_per_source,
                "max_characters": self.max_characters,
                "batch_size": self.batch_size,
                "store_path": self.store_path,
                "translator_mode": self.translator_mode,
                "carriers_source": self.carriers_source,
                "trust_tatoeba": self.trust_tatoeba,
            },
            "tatoeba_policy": tatoeba_policy_sentence(self.trust_tatoeba),
            "carriers_seen": self.carriers_seen,
            "already_in_store": self.already_in_store,
            "replacing_distrusted": self.replacing_distrusted,
            "from_tatoeba": self.from_tatoeba,
            "machine_translated": self.machine_translated,
            "skipped_for_budget": self.skipped_for_budget,
            "failed": self.failed,
            "failure_examples": self.failure_examples,
            "characters_spent": self.characters_spent,
            "gemini_fallback_characters": self.gemini_fallback_characters,
            "azure_fallback_events": self.azure_fallback_events,
            "azure_fallback_examples": self.azure_fallback_examples,
            "store_size_after": self.store_size_after,
            "warnings": self.warnings,
        }

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def _read_one_corpus(
    path: Path, fmt: str, label: str, limit: int, seed: int, source: str
) -> list[CorpusLine]:
    """One corpus's own lines, or an empty list with a warning printed if the
    file is missing -- degrades the run, never crashes it, matching
    ``step7_corpus_pilot.py``'s and ``build_verb_government.py``'s own
    ``_read_one_corpus``.

    ``source`` is passed explicitly rather than derived, because ``fmt``
    cannot name a corpus for the ``lines`` shape and ``_fill_from_tatoeba``'s
    id join keys on exactly this field."""
    if not path.exists():
        print(f"  WARNING: {label} corpus not found at {path}; skipping this source.")
        return []
    lines = read_corpus_lines(path, fmt, limit, seed, source=source)
    print(f"  {label}: {len(lines):,} length-plausible lines read from {path}")
    return lines


def _read_carriers_from_file(path: Path) -> tuple[dict[str, CorpusLine], int]:
    """The ``--carriers-from`` mode's carrier set (module docstring, "Two
    modes"): one German sentence per line, keyed by its own exact text so
    duplicates collapse exactly the way the corpus mode's ``setdefault``
    already makes them collapse.

    A line that parses as a JSON OBJECT is read for its ``german`` key and
    skipped if it has none. That single concession is what lets one flag
    accept either a plain sentence list or a JSONL dump of items, with no
    second flag to say which; anything that is not a JSON object is taken
    literally as the sentence, so German text that merely contains braces or
    digits is unaffected.

    ``line_id`` and ``source`` are the empty string throughout: this file
    format carries neither a corpus id nor a corpus name, and
    ``_fill_from_tatoeba`` falls back to an exact-text match for a carrier
    without both, so the Tatoeba join still works in this mode. An empty
    ``source`` is deliberately not "assume Tatoeba": a carrier list can hold
    sentences from anywhere, and the id join is only safe for a line whose
    corpus is confirmed.

    Returns the carriers AND the number of JSON objects skipped for having no
    usable ``german`` key, because silence there is the dangerous failure
    mode: this project's own item JSONL (``data/corpus_pilot_review.jsonl``)
    carries ``prompt`` and ``accepted_answers``, not ``german``, so feeding it
    here directly would skip every single line and finish with an empty
    carrier set, a clean exit code and nothing said. The caller reports the
    count and refuses a run whose whole input was skipped.
    """
    carriers: dict[str, CorpusLine] = {}
    skipped_json_objects = 0
    with path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            text = raw_line.strip()
            if not text:
                continue
            if text.startswith("{"):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict):
                    german = parsed.get("german")
                    if not isinstance(german, str) or not german.strip():
                        skipped_json_objects += 1
                        continue
                    text = german.strip()
            carriers.setdefault(text, CorpusLine(line_id="", text=text))
    return carriers, skipped_json_objects


def _shortest_translations(pairs: list[Pair]) -> tuple[dict[str, str], dict[str, str]]:
    """Group Tatoeba pairs by id and by exact German text, keeping the
    SHORTEST English translation in each group (module docstring, TODO.md
    section 4's owner decision). Both groupings are built, not just text,
    because a TATOEBA carrier is matched by id first and falls back to text.
    The two readers this script reuses draw German text through the
    identical column of the identical staged file this script also reads as
    corpus carriers, so an id match and a text match normally agree; keeping
    both catches the one case where they would not -- a ``--pairs`` custom
    export re-encoding the text slightly differently from
    ``scripts/corpus_reading.py``'s own read of the raw per-language file
    (e.g. differing quote-character normalisation).

    ``shortest_by_id`` is meaningful for Tatoeba carriers and no others:
    every key in it is a Tatoeba sentence id. ``_fill_from_tatoeba`` is
    where that is enforced."""
    by_id: dict[str, list[str]] = collections.defaultdict(list)
    by_text: dict[str, list[str]] = collections.defaultdict(list)
    for pair in pairs:
        if not pair.english:
            continue
        if pair.german_id:
            by_id[pair.german_id].append(pair.english)
        if pair.german:
            by_text[pair.german].append(pair.english)
    shortest_by_id = {gid: min(options, key=len) for gid, options in by_id.items()}
    shortest_by_text = {text: min(options, key=len) for text, options in by_text.items()}
    return shortest_by_id, shortest_by_text


def _load_store(path: Path) -> dict[str, TranslationRecord]:
    """Every record already on disk, keyed by its own ``german`` text --
    module docstring, "Resumability"."""
    store: dict[str, TranslationRecord] = {}
    if not path.exists():
        return store
    with path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                record = TranslationRecord.model_validate_json(raw_line)
            except ValidationError:
                # A malformed line must not crash a six-week nightly job over
                # one bad row (a hand edit, or a store predating this
                # script). Skipped, not fatal: at worst this one German
                # sentence gets retranslated on the next run -- the atomic
                # write below is what keeps this case rare in the first
                # place.
                continue
            store[record.german] = record
    return store


def _write_store_atomic(path: Path, store: dict[str, TranslationRecord]) -> None:
    """Temp file plus rename (module docstring, "Atomic write") -- the
    target path is only ever replaced by a fully written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            # Sorted for a deterministic, diffable file across runs -- two
            # runs that end with the same content byte-for-byte the same,
            # regardless of dict insertion order.
            for german in sorted(store):
                handle.write(store[german].model_dump_json())
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


class CrossCorpusGlossError(RuntimeError):
    """A carrier from one corpus was about to be given another corpus's
    English by id. See ``_fill_from_tatoeba`` for why this is a bug and not
    a data condition."""


def reject_cross_corpus_gloss(
    line: CorpusLine, english: str, shortest_by_text: dict[str, str]
) -> None:
    """Raise unless this carrier is entitled to a Tatoeba-sourced gloss.

    A store record whose German is a Leipzig carrier and whose ``source`` is
    ``"tatoeba"`` is a contradiction in terms unless that exact German text
    genuinely appears in the Tatoeba pairs. This function is that sentence,
    executable, and it runs immediately before every single
    ``source="tatoeba"`` record is written -- the earliest point at which the
    contradiction exists at all, and the loudest, since it raises rather than
    warns.

    Two things are deliberately allowed through:

    * a carrier whose ``source`` is ``""``. That means "unknown", which is
      what ``--carriers-from`` produces and what any pre-``source``
      construction produces. Such a carrier can only ever have matched by
      text anyway, because ``_fill_from_tatoeba`` refuses the id join
      without a confirmed Tatoeba source.
    * a carrier from any corpus whose exact text IS in ``shortest_by_text``.
      A text match is self-verifying: the German string is the key, so the
      English really is a translation of this sentence no matter which
      corpus this copy of it was read from.

    Everything else is the bug. ``_fill_from_tatoeba``'s own source fence
    means this cannot fire today; that is the point. It is the tripwire that
    makes reintroducing the unfenced id join fail on the first collision
    instead of quietly poisoning thousands of glosses that only a hand audit
    of accepted exercises would ever surface.
    """
    if not line.source or line.source == SOURCE_TATOEBA:
        return
    if line.text in shortest_by_text:
        return
    raise CrossCorpusGlossError(
        f"carrier from corpus {line.source!r} (line id {line.line_id!r}) was about "
        "to be given a Tatoeba gloss without an exact-text match. A Tatoeba "
        "sentence id means nothing in another corpus's id namespace: "
        f"{line.text!r} -> {english!r}"
    )


def _fill_from_tatoeba(
    todo: list[CorpusLine],
    shortest_by_id: dict[str, str],
    shortest_by_text: dict[str, str],
    store: dict[str, TranslationRecord],
    report: TranslationBackfillReport,
    *,
    now: datetime,
) -> list[CorpusLine]:
    """Step 2 of the module docstring's priority order: match every carrier
    still without a store entry against the Tatoeba pairs. Mutates ``store``
    and ``report`` in place; returns the carriers that are STILL
    untranslated, for the machine-translation step.

    **The id join is Tatoeba's alone.** ``shortest_by_id`` is keyed on
    TATOEBA SENTENCE IDS; ``CorpusLine.line_id`` is whatever id the line's
    own corpus gave it. Leipzig line ids and Tatoeba sentence ids are both
    bare integers in the same numeric range and mean nothing to each other,
    so consulting ``shortest_by_id`` for a Leipzig carrier is not a lookup,
    it is a coincidence. This module's own docstring already said as much
    ("a Tatoeba id and a Leipzig id do not even share a namespace") while
    the join here ignored it, and the result shipped: Leipzig line 541845,
    "Genauere Untersuchungen in Graz haben ergeben, dass die Verletzung
    schlimmer ist als gedacht.", was stored with Tatoeba sentence 541845's
    English, "She crossed the street." Measured against the owner's real
    store, 7,365 of the 7,499 Leipzig carriers in it, 98.2%, were labelled
    ``source="tatoeba"`` and therefore wrong; two of them reached the last
    pilot's 430 accepted items, and the verifier now READS the gloss when
    judging answer uniqueness, so a poisoned gloss corrupts a verification
    decision as well as a display string.

    The id join is NOT deleted, only fenced. It does most of the work behind
    Tatoeba's own 61.7% coverage, and dropping it would cost genuine glosses
    for no reason: for a carrier that really came from Tatoeba, an id match
    is exactly right.

    Text matching stays open to every carrier regardless of source. A text
    match is self-verifying in a way an id match is not: the German string
    itself is the key, so a Leipzig sentence that also happens to exist in
    Tatoeba genuinely does have that English.
    """
    still_todo: list[CorpusLine] = []
    for line in todo:
        english = None
        if line.line_id and line.source == SOURCE_TATOEBA:
            english = shortest_by_id.get(line.line_id)
        if english is None:
            english = shortest_by_text.get(line.text)
        if english is None:
            still_todo.append(line)
            continue
        reject_cross_corpus_gloss(line, english, shortest_by_text)
        store[line.text] = TranslationRecord(
            german=line.text, english=english, source="tatoeba", written_at=now
        )
        report.from_tatoeba += 1
    return still_todo


def _default_batch_size(translator_mode: TranslatorMode) -> int:
    """Module docstring, "Batch size is chosen for blast radius, not just
    throughput": ``AZURE_MAX_BATCH`` whenever Azure is in play, 1 when the
    run is Gemini-only, since ``GeminiTranslator`` never batches underneath
    regardless of what is asked of it here."""
    if translator_mode in ("fallback", "azure_only"):
        return AZURE_MAX_BATCH
    return 1


def _run_machine_translation(
    still_todo: list[CorpusLine],
    translator: Translator | None,
    *,
    max_characters: int,
    batch_size: int,
    store: dict[str, TranslationRecord],
    report: TranslationBackfillReport,
    now: datetime,
    on_batch: Callable[[BatchOutcome], None] | None = None,
    checkpoint: Callable[[], None] | None = None,
    stop_when: Callable[[], bool] | None = None,
) -> None:
    """Step 3 of the module docstring's priority order. Mutates ``store``
    and ``report`` in place. Stops cleanly at a whole-batch boundary once
    the next batch would exceed ``max_characters`` of SUCCESSFUL
    translation (module docstring, "The character budget is per run") --
    never attempts a partial batch to top up the remainder.

    ``checkpoint`` (if given) is called after a successful batch to flush the
    store to disk, and ``on_batch`` immediately after it. **That order is
    deliberate.** Checkpoint first, then account: a crash between the two
    leaves work done and not charged, which the next run simply skips because
    the store already holds it. The reverse order loses the work and bills the
    month for it. Neither hook is called for a batch that failed, because a
    failed batch spent no Azure quota and wrote no record.

    Both default to ``None``, so the nightly ``--carriers-from`` path is
    unchanged: one store write at the end of ``run_backfill`` and no ledger.
    """
    if translator is None or not still_todo:
        report.skipped_for_budget += len(still_todo)
        return

    # Local, not read off the report: these three are what THIS call did,
    # and the report's own counters may already be nonzero on entry (nothing
    # forbids calling this twice against one report, and the
    # skipped_for_budget arithmetic below silently produced a negative number
    # if they were).
    budget_spent = 0
    translated_here = 0
    failed_here = 0

    index = 0
    while index < len(still_todo):
        chunk = still_todo[index : index + batch_size]
        chunk_chars = sum(len(line.text) for line in chunk)
        # Both azure and fallback characters gate the stop, even though only
        # azure ones are reported as characters_spent: the budget bounds one
        # invocation's total work as well as protecting the F0 allowance
        # (module docstring).
        if budget_spent + chunk_chars > max_characters:
            break

        failures_before = (
            len(translator.failures) if isinstance(translator, FallbackTranslator) else 0
        )
        try:
            translations = translator.translate([line.text for line in chunk])
        except TranslationError as exc:
            report.failed += len(chunk)
            failed_here += len(chunk)
            if len(report.failure_examples) < 10:
                report.failure_examples.append(str(exc))
            index += len(chunk)
            if stop_when is not None and stop_when():
                break
            continue
        except Exception as exc:  # noqa: BLE001 -- a deliberate last resort
            # Both translators now wrap every failure of their own as a
            # TranslationError, so this should be unreachable. It stays
            # because the alternative failure mode is unacceptable: this job
            # runs unattended for about six weeks, and one unforeseen
            # exception escaping here would crash the run before the store
            # or the report is written, throwing away that night's work.
            # Recording the batch as failed and letting the next run retry
            # it is strictly better. If this branch ever fires, the bug is
            # in the translator, not here, so the exception TYPE is recorded
            # to make that diagnosable.
            report.failed += len(chunk)
            failed_here += len(chunk)
            if len(report.failure_examples) < 10:
                report.failure_examples.append(f"{type(exc).__name__}: {exc}")
            index += len(chunk)
            continue
        failures_after = (
            len(translator.failures) if isinstance(translator, FallbackTranslator) else 0
        )

        if isinstance(translator, FallbackTranslator) and failures_after > failures_before:
            source: Literal["azure", "gemini"] = "gemini"
        elif report.translator_mode == "gemini_only":
            source = "gemini"
        else:
            source = "azure"

        for line, english in zip(chunk, translations, strict=True):
            store[line.text] = TranslationRecord(
                german=line.text, english=english, source=source, written_at=now
            )
        if source == "azure":
            report.characters_spent += chunk_chars
        else:
            # Azure never saw these characters, so they cost none of the F0
            # allowance characters_spent exists to protect. Their real cost
            # is already a cost_log row, written by GeminiLlmClient.
            report.gemini_fallback_characters += chunk_chars
        budget_spent += chunk_chars
        report.machine_translated += len(chunk)
        translated_here += len(chunk)
        index += len(chunk)

        if checkpoint is not None:
            checkpoint()
        if on_batch is not None:
            on_batch(
                BatchOutcome(
                    texts=tuple(line.text for line in chunk),
                    source=source,
                    azure_characters=chunk_chars if source == "azure" else 0,
                    gemini_characters=0 if source == "azure" else chunk_chars,
                )
            )
        # Asked AFTER the batch is stored and accounted, never before, so a
        # stop leaves nothing half-done. The one caller today is the monthly
        # top-up asking "has Azure refused us on quota yet?": from that point
        # every further batch would either be refused again or, under a
        # fallback translator, silently move to the paid provider.
        if stop_when is not None and stop_when():
            break

    report.skipped_for_budget += len(still_todo) - translated_here - failed_here

    if isinstance(translator, FallbackTranslator):
        report.azure_fallback_events = len(translator.failures)
        report.azure_fallback_examples = translator.failures[:5]


def run_backfill(
    *,
    carriers: dict[str, CorpusLine],
    store_path: Path,
    translator: Translator | None,
    translator_mode: TranslatorMode,
    tatoeba_pairs: list[Pair],
    max_characters: int,
    batch_size: int,
    seed: int,
    limit_per_source: int,
    carriers_source: str = "corpora",
    trust_tatoeba: bool = DEFAULT_TRUST_TATOEBA,
    distrust_stored_sources: frozenset[str] = NO_DISTRUSTED_SOURCES,
    on_batch: Callable[[BatchOutcome], None] | None = None,
    checkpoint_every: int = 0,
    stop_when: Callable[[], bool] | None = None,
    now: datetime | None = None,
) -> TranslationBackfillReport:
    """The whole backfill, independent of argparse, the environment, and
    corpus/pair file reading -- ``translator`` and ``carriers``/
    ``tatoeba_pairs`` are injected (CLAUDE.md section 8: "functions that
    touch the network, the filesystem, or the clock take those as injected
    dependencies so they can be faked in tests"), so a test drives this
    directly with a fake ``Translator`` and an in-memory carrier set, never
    the network. ``store_path`` is still real filesystem I/O -- the store
    itself is the resumability mechanism, so a test uses ``tmp_path`` rather
    than faking that part away.

    ``trust_tatoeba`` defaults to ``False`` here and not only at the argparse
    layer, deliberately: the safe reading of this function's contract is the
    one that does not put a Tatoeba translation in front of a learner, so a
    caller that wants that join has to ask for it in as many words.

    ``distrust_stored_sources`` names store ``source`` values to treat as a
    cache MISS rather than as done. Empty by default, so this script's own
    long-standing contract ("anything in the store is finished") is unchanged.
    ``scripts/monthly_translation_topup.py`` passes ``{"tatoeba"}``: those
    records are 5.3's corpus and are never deleted, but they are not a trusted
    gloss, and the standing job's whole purpose is to replace them. Asking for
    the Tatoeba JOIN and the Tatoeba DISTRUST in one call is a contradiction
    (the join would immediately rewrite what the distrust just queued for
    replacement) and raises rather than silently picking one.

    ``on_batch`` and ``checkpoint_every`` are how a caller that runs more than
    once a month keeps its own accounting. ``on_batch`` fires once per
    successful batch with a ``BatchOutcome``; ``checkpoint_every`` N flushes the
    store to disk every N successful batches (0 disables it, which is the
    nightly path's unchanged behaviour of one write at the end). Flushing costs
    a full rewrite of the store each time, so N trades crash-loss against I/O:
    at N=10 a killed run loses at most ten batches of glosses that the ledger
    has already charged the month for."""
    ref_time = now or datetime.now(UTC)
    if trust_tatoeba and "tatoeba" in distrust_stored_sources:
        raise ValueError(
            "trust_tatoeba=True with 'tatoeba' in distrust_stored_sources is "
            "contradictory: the join would refill the very records the distrust "
            "queued for machine translation. Pick one."
        )
    report = TranslationBackfillReport(
        seed=seed,
        limit_per_source=limit_per_source,
        max_characters=max_characters,
        batch_size=batch_size,
        store_path=str(store_path),
        translator_mode=translator_mode,
        carriers_source=carriers_source,
        trust_tatoeba=trust_tatoeba,
    )
    if translator_mode == "gemini_only":
        report.warnings.append(
            "NO AZURE KEY CONFIGURED: running Gemini-only. Quality is lower than "
            "the dedicated translation engine, and Gemini calls are not free once "
            "its own free lane closes (unlike Azure F0)."
        )
    elif translator_mode == "none":
        report.warnings.append(
            "NO TRANSLATOR CONFIGURED: no Azure key and no Gemini key. Machine "
            "translation is skipped entirely this run."
        )

    report.carriers_seen = len(carriers)

    store = _load_store(store_path)
    todo: list[CorpusLine] = []
    for text, line in carriers.items():
        record = store.get(text)
        if record is None:
            todo.append(line)
        elif record.source in distrust_stored_sources:
            # In the store, but not with a source this run trusts. Queued for
            # re-translation; the existing record stays on disk untouched until
            # a new translation actually lands to overwrite it, so a run that
            # never gets this far leaves 5.3's corpus exactly as it was.
            todo.append(line)
            report.replacing_distrusted += 1
        else:
            report.already_in_store += 1

    if trust_tatoeba:
        shortest_by_id, shortest_by_text = _shortest_translations(tatoeba_pairs)
        still_todo = _fill_from_tatoeba(
            todo, shortest_by_id, shortest_by_text, store, report, now=ref_time
        )
    else:
        # Not "join and then discard": the pairs are never consulted at all,
        # so ``from_tatoeba`` stays 0 and every one of these carriers is
        # machine translation's problem. Nothing already in the store is
        # touched -- the existing Tatoeba records stay exactly where they
        # are, for feature 5.3 (module docstring).
        still_todo = todo

    checkpoint: Callable[[], None] | None = None
    if checkpoint_every > 0:
        batches_since_flush = 0

        def checkpoint() -> None:
            nonlocal batches_since_flush
            batches_since_flush += 1
            if batches_since_flush >= checkpoint_every:
                batches_since_flush = 0
                _write_store_atomic(store_path, store)

    _run_machine_translation(
        still_todo,
        translator,
        max_characters=max_characters,
        batch_size=batch_size,
        store=store,
        report=report,
        now=ref_time,
        on_batch=on_batch,
        checkpoint=checkpoint,
        stop_when=stop_when,
    )

    if report.from_tatoeba or report.machine_translated:
        _write_store_atomic(store_path, store)
    report.store_size_after = len(store)
    return report


def translator_from_env(
    gemini_client: GeminiLlmClient | None,
) -> tuple[Translator | None, TranslatorMode]:
    """The three-source translator this script's own priority order names
    (module docstring, source 3), built from the environment: Azure primary
    with a Gemini fallback when both are configured, either one alone when
    only one is, and ``None`` when neither is.

    Public and separate from ``main()`` so a second caller gets EXACTLY this
    construction rather than a lookalike. ``scripts/step7_corpus_pilot.py``
    (TODO.md 2.1b) fills one pilot's glosses through the same store and the
    same translator, and a divergence between the two -- a different
    fallback wiring, a different Gemini model -- would mean two glosses of
    the same sentence could differ by which script happened to write it
    first. The mode is returned alongside because callers key both their
    batch size (``_default_batch_size``) and their "which provider wrote
    this record" labelling on it.

    Says nothing on the console: the Gemini-only and no-translator cases each
    warrant a loud warning, but what that warning should say differs per
    caller, so it stays with the caller.
    """
    azure = azure_from_env(llm_client=gemini_client)
    mode = _translator_mode(azure, gemini_client)
    if mode == "fallback":
        assert azure is not None and gemini_client is not None
        return (
            FallbackTranslator(
                primary=azure,
                fallback=GeminiTranslator(llm_client=gemini_client, model=MODEL_GENERATE),
            ),
            mode,
        )
    if mode == "azure_only":
        return azure, mode
    if mode == "gemini_only":
        assert gemini_client is not None
        return GeminiTranslator(llm_client=gemini_client, model=MODEL_GENERATE), mode
    return None, mode


def _translator_mode(
    azure: AzureTranslator | None, gemini_client: GeminiLlmClient | None
) -> TranslatorMode:
    if azure is not None and gemini_client is not None:
        return "fallback"
    if azure is not None:
        return "azure_only"
    if gemini_client is not None:
        return "gemini_only"
    return "none"


def main() -> int:
    parser = argparse.ArgumentParser(description="TODO.md 5.1 step 2: the translation backfill.")
    parser.add_argument("--tatoeba", type=Path, default=DEFAULT_TATOEBA_PATH)
    parser.add_argument("--leipzig", type=Path, default=DEFAULT_LEIPZIG_PATH)
    parser.add_argument("--skip-tatoeba", action="store_true")
    parser.add_argument("--skip-leipzig", action="store_true")
    parser.add_argument(
        "--carriers-from",
        type=Path,
        default=None,
        help=(
            "One German sentence per line (or a JSONL dump with a 'german' key). "
            "Replaces corpus reading entirely: --tatoeba and --leipzig are not read. "
            "This is the right mode for building one pilot's glosses."
        ),
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT_PER_SOURCE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--pairs", type=Path, default=None, help="Tatoeba custom export, both texts."
    )
    parser.add_argument(
        "--links", type=Path, default=None, help="deu-eng links file (numeric ids)."
    )
    parser.add_argument(
        "--english", type=Path, default=None, help="eng_sentences.tsv, with --links."
    )
    parser.add_argument(
        "--trust-tatoeba",
        action="store_true",
        help=(
            "Use Tatoeba's own English translations as glosses where they exist "
            "(free, no API call). OFF by default: a hand audit of 430 accepted "
            "exercises found 4 wrong glosses and 3 of the 4 were Tatoeba's own "
            "human translations, and a separate check of 120 pairs found 2 wrong "
            "and 6 loose. Turn it ON only to rebuild the feature 5.3 corpus store, "
            "which wants breadth over precision and would otherwise cost about "
            "13,000,000 characters, roughly half a year of Azure F0. Never turn it "
            "on to build a pilot's exercises."
        ),
    )
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE_PATH)
    parser.add_argument("--max-characters", type=int, default=DEFAULT_MAX_CHARACTERS_PER_RUN)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Override the translate() call size (default: chosen from translator mode).",
    )
    parser.add_argument("--report-file", type=str, default=str(DEFAULT_REPORT_PATH))
    args = parser.parse_args()

    if args.pairs and (args.links or args.english):
        parser.error("give either --pairs, or --links/--english, not both")
    if bool(args.links) != bool(args.english):
        parser.error("--links and --english must be given together")
    if args.carriers_from and (args.skip_tatoeba or args.skip_leipzig):
        # Meaningless rather than merely redundant: --carriers-from already
        # means neither corpus is read, so a --skip flag alongside it can only
        # mean the caller believes corpora are still in play.
        parser.error(
            "--carriers-from already replaces both corpora; drop --skip-tatoeba/--skip-leipzig"
        )
    if (args.pairs or args.links or args.english) and not args.trust_tatoeba:
        # Contradictory rather than merely redundant, and worth refusing
        # loudly: the pair files exist only to feed the Tatoeba join, so a
        # run that names them without --trust-tatoeba believes that join is
        # about to happen when it is not. Reading them and quietly ignoring
        # them would leave the caller certain a gloss came from Tatoeba when
        # it came from Azure.
        parser.error(
            "--pairs/--links/--english only feed the Tatoeba join, which is off by "
            "default. Add --trust-tatoeba if you really want Tatoeba's own "
            "translations (feature 5.3's corpus store), or drop these flags."
        )

    print(f"\n  Tatoeba policy: {tatoeba_policy_sentence(args.trust_tatoeba)}\n")

    load_env_file()

    carriers_source = "corpora"
    carriers: dict[str, CorpusLine] = {}
    if args.carriers_from:
        if not args.carriers_from.exists():
            parser.error(f"--carriers-from file not found: {args.carriers_from}")
        carriers_source = str(args.carriers_from)
        carriers, skipped_json_objects = _read_carriers_from_file(args.carriers_from)
        print(f"  Carrier list: {len(carriers):,} distinct sentences from {args.carriers_from}")
        if skipped_json_objects:
            print(
                f"  WARNING: {skipped_json_objects:,} JSON lines had no usable 'german' key "
                "and were skipped."
            )
        if not carriers and skipped_json_objects:
            # Never a quiet success. An items JSONL keyed on something other
            # than 'german' skips every line, and without this the run would
            # write an empty report and exit 0 as though it had finished.
            parser.error(
                f"every line of {args.carriers_from} was a JSON object with no 'german' key. "
                "This file format needs one German sentence per line, or JSON objects "
                "carrying a 'german' field."
            )
    else:
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

    if not carriers:
        print("No carriers read; nothing to do.")
        TranslationBackfillReport(
            seed=args.seed,
            limit_per_source=args.limit,
            max_characters=args.max_characters,
            batch_size=args.batch_size or 1,
            store_path=str(args.store),
            carriers_source=carriers_source,
            trust_tatoeba=args.trust_tatoeba,
        ).write(Path(args.report_file))
        return 0

    print(f"\n  Carriers seen (distinct German text): {len(carriers):,}")

    tatoeba_pairs: list[Pair] = []
    if not args.trust_tatoeba:
        print(
            "  Tatoeba-translation step: NOT RUN (no --trust-tatoeba). Every carrier "
            "the store lacks goes to machine translation."
        )
    elif args.pairs:
        if not args.pairs.exists():
            print(
                f"  WARNING: --pairs not found at {args.pairs}; "
                "skipping the Tatoeba-translation step."
            )
        else:
            tatoeba_pairs = read_pairs_file(args.pairs)
            print(f"  Tatoeba pairs read from {args.pairs}: {len(tatoeba_pairs):,}")
    elif args.links and args.english:
        if not (args.links.exists() and args.english.exists() and args.tatoeba.exists()):
            print(
                "  WARNING: --links/--english/--tatoeba not all found; "
                "skipping the Tatoeba-translation step."
            )
        else:
            tatoeba_pairs = read_links_and_english(args.links, args.english, args.tatoeba)
            print(
                f"  Tatoeba pairs read from {args.links} joined with {args.english}: "
                f"{len(tatoeba_pairs):,}"
            )
    else:
        print(
            "  --trust-tatoeba given but no --pairs or --links/--english to read; "
            "skipping the Tatoeba-translation step."
        )

    gemini_client = client_from_env()
    translator, mode = translator_from_env(gemini_client)
    if mode == "gemini_only":
        print(
            "\n  *** NO AZURE KEY CONFIGURED: running Gemini-only. Quality is lower "
            "than the dedicated translation engine, and Gemini calls are not free "
            "once its own free lane closes. ***\n"
        )
    elif mode == "none":
        print(
            "\n  *** NO TRANSLATOR CONFIGURED: no AZURE_TRANSLATOR_KEY and no Gemini "
            "key. Machine translation is skipped entirely this run; only the "
            "Tatoeba-translation step (if given) will fill the store. ***\n"
        )

    batch_size = args.batch_size if args.batch_size is not None else _default_batch_size(mode)

    report = run_backfill(
        carriers=carriers,
        store_path=args.store,
        translator=translator,
        translator_mode=mode,
        tatoeba_pairs=tatoeba_pairs,
        max_characters=args.max_characters,
        batch_size=batch_size,
        seed=args.seed,
        limit_per_source=args.limit,
        carriers_source=carriers_source,
        trust_tatoeba=args.trust_tatoeba,
    )

    print(f"\n  Carriers source:           {report.carriers_source}")
    print(f"  Trust Tatoeba:             {report.trust_tatoeba}")
    print(f"  Already in store:          {report.already_in_store:,}")
    print(f"  Filled from Tatoeba pairs: {report.from_tatoeba:,}")
    print(f"  Machine translated:        {report.machine_translated:,}")
    print(f"  Skipped for budget:        {report.skipped_for_budget:,}")
    print(f"  Failed:                    {report.failed:,}")
    for example in report.failure_examples:
        print(f"    - {example}")
    total_characters = report.characters_spent + report.gemini_fallback_characters
    print(f"  Characters this run:       {total_characters:,} / {args.max_characters:,}")
    print(f"    of which Azure:          {report.characters_spent:,}")
    print(f"    of which Gemini:         {report.gemini_fallback_characters:,}")
    print(f"  Azure-fallback events:     {report.azure_fallback_events:,}")
    print(f"  Store size after this run: {report.store_size_after:,}")
    print(f"\n  What that means: {tatoeba_policy_sentence(report.trust_tatoeba)}")
    for warning in report.warnings:
        print(f"  WARNING: {warning}")

    report_path = Path(args.report_file)
    report.write(report_path)
    print(f"\n  Report file: {report_path}")

    if mode == "none" and report.skipped_for_budget > 0:
        print(
            "\n  FAILING: real translation work remains "
            f"({report.skipped_for_budget:,} carriers) and no translator is "
            "configured at all. Set AZURE_TRANSLATOR_KEY and/or a Gemini key."
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
