"""TODO.md 5.1 step 2: the resumable English-gloss backfill.

Builds and maintains ``data/fixtures/translations/de_en.jsonl``, a JSONL
store mapping one German carrier sentence to one English gloss, so the item
pipeline can look a translation up at build time without ever calling out to
a translation provider on the critical path (CLAUDE.md rule 3).

## Three sources, in the owner's priority order (TODO.md section 4)

1. **Already in the store.** Loaded first; anything present is never
   re-translated. See "Resumability" below.
2. **Tatoeba's own English translations**, via the readers this script
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
3. **The ``Translator`` from ``src/llm/translation.py``** (Azure primary,
   Gemini fallback) for everything left over -- all of Leipzig, plus the
   38.3% of Tatoeba's own carriers that source measured as untranslated.
   This step IS subject to ``--max-characters``, see below.

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
store and are retried on the next run. A batch's real Gemini spend, when
the fallback fires, is unaffected by any of this: it is already logged
through ``GeminiLlmClient``'s own cost log regardless of whether this
script's run-level counter also counts it (CLAUDE.md rule 4), so a Gemini
failure that already cost money is not swept under this script's separate
"skipped for budget, free to retry" accounting.

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

from scripts.corpus_reading import CorpusLine, read_corpus_lines
from scripts.eval_tatoeba_translation_quality import Pair, read_links_and_english, read_pairs_file

# Matches step7_corpus_pilot.py's and build_verb_government.py's own defaults
# for these two staged corpora -- the only place either file actually exists
# in this environment (checked at build time; data/raw/_extract/ itself does
# not exist here, only under this uploads mount).
DEFAULT_TATOEBA_PATH = Path(
    "/mnt/user-data/uploads/Language_Learning_App/data/raw/_extract/tatoeba_deu.tsv"
)
DEFAULT_LEIPZIG_PATH = Path(
    "/mnt/user-data/uploads/Language_Learning_App/data/raw/_extract/leipzig_sample.txt"
)
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
    carriers_seen: int = 0
    already_in_store: int = 0
    from_tatoeba: int = 0
    machine_translated: int = 0
    skipped_for_budget: int = 0
    failed: int = 0
    failure_examples: list[str] = field(default_factory=list)
    characters_spent: int = 0
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
            },
            "carriers_seen": self.carriers_seen,
            "already_in_store": self.already_in_store,
            "from_tatoeba": self.from_tatoeba,
            "machine_translated": self.machine_translated,
            "skipped_for_budget": self.skipped_for_budget,
            "failed": self.failed,
            "failure_examples": self.failure_examples,
            "characters_spent": self.characters_spent,
            "azure_fallback_events": self.azure_fallback_events,
            "azure_fallback_examples": self.azure_fallback_examples,
            "store_size_after": self.store_size_after,
            "warnings": self.warnings,
        }

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def _read_one_corpus(path: Path, fmt: str, label: str, limit: int, seed: int) -> list[CorpusLine]:
    """One corpus's own lines, or an empty list with a warning printed if the
    file is missing -- degrades the run, never crashes it, matching
    ``step7_corpus_pilot.py``'s and ``build_verb_government.py``'s own
    ``_read_one_corpus``."""
    if not path.exists():
        print(f"  WARNING: {label} corpus not found at {path}; skipping this source.")
        return []
    lines = read_corpus_lines(path, fmt, limit, seed)
    print(f"  {label}: {len(lines):,} length-plausible lines read from {path}")
    return lines


def _shortest_translations(pairs: list[Pair]) -> tuple[dict[str, str], dict[str, str]]:
    """Group Tatoeba pairs by id and by exact German text, keeping the
    SHORTEST English translation in each group (module docstring, TODO.md
    section 4's owner decision). Both groupings are built, not just text,
    because a carrier is matched by id first and falls back to text --
    mirroring ``eval_tatoeba_translation_quality.py``'s own ``covered``/
    ``covered_pairs`` join. The two readers this script reuses draw German
    text through the identical column of the identical staged file this
    script also reads as corpus carriers, so an id match and a text match
    normally agree; keeping both catches the one case where they would not
    -- a ``--pairs`` custom export re-encoding the text slightly differently
    from ``scripts/corpus_reading.py``'s own read of the raw per-language
    file (e.g. differing quote-character normalisation)."""
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
    still without a store entry against the Tatoeba pairs, id first then
    text (matching ``eval_tatoeba_translation_quality.py``'s own join).
    Mutates ``store`` and ``report`` in place; returns the carriers that are
    STILL untranslated, for the machine-translation step."""
    still_todo: list[CorpusLine] = []
    for line in todo:
        english = None
        if line.line_id:
            english = shortest_by_id.get(line.line_id)
        if english is None:
            english = shortest_by_text.get(line.text)
        if english is None:
            still_todo.append(line)
            continue
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
) -> None:
    """Step 3 of the module docstring's priority order. Mutates ``store``
    and ``report`` in place. Stops cleanly at a whole-batch boundary once
    the next batch would exceed ``max_characters`` of SUCCESSFUL
    translation (module docstring, "The character budget is per run") --
    never attempts a partial batch to top up the remainder."""
    if translator is None or not still_todo:
        report.skipped_for_budget += len(still_todo)
        return

    index = 0
    while index < len(still_todo):
        chunk = still_todo[index : index + batch_size]
        chunk_chars = sum(len(line.text) for line in chunk)
        if report.characters_spent + chunk_chars > max_characters:
            break

        failures_before = (
            len(translator.failures) if isinstance(translator, FallbackTranslator) else 0
        )
        try:
            translations = translator.translate([line.text for line in chunk])
        except TranslationError as exc:
            report.failed += len(chunk)
            if len(report.failure_examples) < 10:
                report.failure_examples.append(str(exc))
            index += len(chunk)
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
        report.characters_spent += chunk_chars
        report.machine_translated += len(chunk)
        index += len(chunk)

    report.skipped_for_budget += len(still_todo) - report.machine_translated - report.failed

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
    than faking that part away."""
    ref_time = now or datetime.now(UTC)
    report = TranslationBackfillReport(
        seed=seed,
        limit_per_source=limit_per_source,
        max_characters=max_characters,
        batch_size=batch_size,
        store_path=str(store_path),
        translator_mode=translator_mode,
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
    todo = [line for text, line in carriers.items() if text not in store]
    report.already_in_store = len(carriers) - len(todo)

    shortest_by_id, shortest_by_text = _shortest_translations(tatoeba_pairs)
    still_todo = _fill_from_tatoeba(
        todo, shortest_by_id, shortest_by_text, store, report, now=ref_time
    )

    _run_machine_translation(
        still_todo,
        translator,
        max_characters=max_characters,
        batch_size=batch_size,
        store=store,
        report=report,
        now=ref_time,
    )

    if report.from_tatoeba or report.machine_translated:
        _write_store_atomic(store_path, store)
    report.store_size_after = len(store)
    return report


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

    load_env_file()

    carriers: dict[str, CorpusLine] = {}
    if not args.skip_tatoeba:
        for line in _read_one_corpus(args.tatoeba, "tatoeba", "Tatoeba", args.limit, args.seed):
            carriers.setdefault(line.text, line)
    if not args.skip_leipzig:
        for line in _read_one_corpus(args.leipzig, "lines", "Leipzig", args.limit, args.seed):
            carriers.setdefault(line.text, line)

    if not carriers:
        print("No carriers read from either source; nothing to do.")
        TranslationBackfillReport(
            seed=args.seed,
            limit_per_source=args.limit,
            max_characters=args.max_characters,
            batch_size=args.batch_size or 1,
            store_path=str(args.store),
        ).write(Path(args.report_file))
        return 0

    print(f"\n  Carriers seen (distinct German text): {len(carriers):,}")

    tatoeba_pairs: list[Pair] = []
    if args.pairs:
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
        print("  No --pairs or --links/--english given; skipping the Tatoeba-translation step.")

    gemini_client = client_from_env()
    azure = azure_from_env(llm_client=gemini_client)
    mode = _translator_mode(azure, gemini_client)

    translator: Translator | None
    if mode == "fallback":
        assert azure is not None and gemini_client is not None
        translator = FallbackTranslator(
            primary=azure, fallback=GeminiTranslator(llm_client=gemini_client, model=MODEL_GENERATE)
        )
    elif mode == "azure_only":
        translator = azure
    elif mode == "gemini_only":
        assert gemini_client is not None
        print(
            "\n  *** NO AZURE KEY CONFIGURED: running Gemini-only. Quality is lower "
            "than the dedicated translation engine, and Gemini calls are not free "
            "once its own free lane closes. ***\n"
        )
        translator = GeminiTranslator(llm_client=gemini_client, model=MODEL_GENERATE)
    else:
        print(
            "\n  *** NO TRANSLATOR CONFIGURED: no AZURE_TRANSLATOR_KEY and no Gemini "
            "key. Machine translation is skipped entirely this run; only the "
            "Tatoeba-translation step (if given) will fill the store. ***\n"
        )
        translator = None

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
    )

    print(f"\n  Already in store:          {report.already_in_store:,}")
    print(f"  Filled from Tatoeba pairs: {report.from_tatoeba:,}")
    print(f"  Machine translated:        {report.machine_translated:,}")
    print(f"  Skipped for budget:        {report.skipped_for_budget:,}")
    print(f"  Failed:                    {report.failed:,}")
    for example in report.failure_examples:
        print(f"    - {example}")
    print(f"  Characters spent this run: {report.characters_spent:,} / {args.max_characters:,}")
    print(f"  Azure-fallback events:     {report.azure_fallback_events:,}")
    print(f"  Store size after this run: {report.store_size_after:,}")
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
