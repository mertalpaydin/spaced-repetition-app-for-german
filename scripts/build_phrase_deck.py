"""Build the phrase deck. The operator entry point for phase 1.

Stages, each idempotent over ``data/phrases/build/``:

    parse     read both corpora, parse once, write every phrase occurrence
    mine      aggregate occurrences into ranked units (thresholds, report)
    cards     pick glossed sentences per unit; list what to gloss next
    contexts  OPT-IN model stage: a preceding sentence for sentence-initial
              connectors (free lane, needs approval)
    unit-glosses  OPT-IN model stage: English for the mined units, most
              common rendering first (needs approval)
    export    write web/data/deck/ (manifest, units, shards) and the JSON schema
    all       parse, mine, cards, export (never contexts)

``--check`` validates a committed deck without the corpus, for CI.
"""

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path

from src.atomic_write import write_text_atomic
from src.contracts import MODEL_GENERATE, ContextRecord, PhraseCard, PhraseUnit, UnitGlossRecord
from src.lexicon.vocabulary import VocabularyStore
from src.llm.env import FreeLaneKeyMissingError, client_from_env, load_env_file
from src.phrases import cards as cards_module
from src.phrases import carrier_validation
from src.phrases import contexts as contexts_module
from src.phrases import unit_glosses as glosses_module
from src.phrases.curated import DEFAULT_PHRASES_DIR, load_curated
from src.phrases.export import DEFAULT_DECK_DIR, check_deck, export_deck, write_schema
from src.phrases.mining import LemmaCounts, detect_all
from src.phrases.occurrences import Occurrence, read_occurrences, write_occurrences
from src.phrases.parse import parse_many, parser_available
from src.phrases.units import (
    DEFAULT_VOCAB_PATH,
    Thresholds,
    UnitBuilder,
    canonical_occurrence,
    unit_id_for,
    without_governing_preposition,
)
from src.run_lock import LockHeld, run_lock

from scripts.build_translations import DEFAULT_STORE_PATH, _load_store
from scripts.corpus_reading import (
    SOURCE_TATOEBA,
    CorpusLine,
    default_corpus_path,
    read_corpus_lines,
)

DEFAULT_BUILD_DIR = Path("data/phrases/build")
DEFAULT_TATOEBA_PATH = default_corpus_path("tatoeba_deu.tsv")
DEFAULT_LEIPZIG_PATH = default_corpus_path("leipzig_news_2025.txt")
#: The corpora beyond Tatoeba and the 2025 Leipzig news file, added 2026-09-09
#: so no single register dominates the ranking. Every one is `id<TAB>sentence`
#: under data/raw/_extract/; the extraction is described in docs/phrase-deck.md.
DEFAULT_EXTRA_CORPORA: tuple[tuple[str, str], ...] = (
    ("leipzig_news_2024", "deu_news_2024_1M-sentences.txt"),
    ("leipzig_mixed_2011", "deu_mixed-typical_2011_1M-sentences.txt"),
    ("leipzig_web_2021", "deu-de_web_2021_1M-sentences.txt"),
    ("opensubtitles_2018", "opensubtitles_2018_sample.txt"),
)
DEFAULT_LIMIT = 2_000_000
DEFAULT_SEED = 7
STAGES = ("parse", "mine", "cards", "contexts", "unit-glosses", "export", "all")


def _log(message: str) -> None:
    print(message, flush=True)


# -- parse ---------------------------------------------------------------------


def read_corpora(
    tatoeba: Path | None,
    leipzig: Path | None,
    *,
    limit: int,
    seed: int,
    extra: Sequence[tuple[str, Path]] = (),
) -> dict[str, CorpusLine]:
    """Every corpus, deduplicated on sentence text (the first corpus to
    contribute a sentence owns it). A missing file is an error, not a
    smaller deck."""
    lines: dict[str, CorpusLine] = {}
    corpora: list[tuple[Path | None, str, str]] = [
        (tatoeba, "tatoeba", SOURCE_TATOEBA),
        (leipzig, "lines", "leipzig_news_2025"),
    ]
    corpora += [(path, "lines", name) for name, path in extra]
    for path, fmt, source in corpora:
        if path is None:
            continue
        if not path.exists():
            raise FileNotFoundError(f"corpus file missing: {path}")
        for line in read_corpus_lines(path, fmt, limit, seed, source=source):
            lines.setdefault(line.text, line)
    return lines


def stage_parse(args: argparse.Namespace) -> int:
    if not parser_available():
        _log("spaCy model de_core_news_sm is not installed; run `uv sync`.")
        return 1
    build_dir: Path = args.build_dir
    build_dir.mkdir(parents=True, exist_ok=True)
    curated = load_curated(args.phrases_dir)
    dictionary = carrier_validation._load_dictionary()  # noqa: SLF001
    extra: list[tuple[str, Path]] = []
    if not args.no_default_extras:
        extra += [(name, default_corpus_path(file)) for name, file in DEFAULT_EXTRA_CORPORA]
    for spec in args.extra_corpus:
        name, _, path = spec.partition("=")
        if not name or not path:
            _log(f"--extra-corpus expects name=path, got {spec!r}")
            return 1
        extra.append((name, Path(path)))
    lines = read_corpora(
        None if args.skip_tatoeba else args.tatoeba,
        None if args.skip_leipzig else args.leipzig,
        limit=args.limit,
        seed=args.seed,
        extra=extra,
    )
    ordered = sorted(lines.values(), key=lambda line: (line.source, line.line_id))
    _log(f"parse: {len(ordered):,} distinct sentences")
    counts = LemmaCounts()
    started = time.monotonic()
    occurrences_path = build_dir / "occurrences.jsonl"
    total = 0
    kinds: Counter[str] = Counter()

    def generate() -> Iterable[Occurrence]:
        nonlocal total
        for index, (line, parsed) in enumerate(
            zip(ordered, parse_many(entry.text for entry in ordered), strict=True), start=1
        ):
            counts.add(parsed, line.source)
            for occ in detect_all(
                parsed, curated, source=line.source, line_id=line.line_id, dictionary=dictionary
            ):
                kinds[occ.kind] += 1
                total += 1
                yield occ
            if index % 20_000 == 0:
                elapsed = time.monotonic() - started
                _log(f"  {index:,} sentences, {total:,} occurrences, {elapsed / 60:.1f} min")

    write_occurrences(occurrences_path, generate())
    write_text_atomic(
        build_dir / "lemma_counts.json", json.dumps(counts.to_dict(), ensure_ascii=False)
    )
    stats = {
        "sentences": len(ordered),
        "by_source": dict(Counter(line.source for line in ordered)),
        "occurrences": total,
        "occurrences_by_kind": dict(kinds),
        "minutes": round((time.monotonic() - started) / 60, 1),
    }
    write_text_atomic(build_dir / "parse_stats.json", json.dumps(stats, indent=2))
    _log(f"parse: done, {total:,} occurrences in {stats['minutes']} min")
    return 0


# -- mine ----------------------------------------------------------------------


def _read_units(path: Path) -> list[PhraseUnit]:
    return [
        PhraseUnit.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_models(path: Path, models: Iterable[PhraseUnit | PhraseCard]) -> None:
    write_text_atomic(path, "".join(m.model_dump_json() + "\n" for m in models))


def stage_mine(args: argparse.Namespace) -> int:
    build_dir: Path = args.build_dir
    occurrences_path = build_dir / "occurrences.jsonl"
    if not occurrences_path.exists():
        _log("mine: no occurrences.jsonl; run --stage parse first")
        return 1
    counts = LemmaCounts.from_dict(
        json.loads((build_dir / "lemma_counts.json").read_text(encoding="utf-8"))
    )
    builder = UnitBuilder(counts, load_curated(args.phrases_dir), Thresholds())
    units = builder.build(read_occurrences(occurrences_path))
    _write_models(build_dir / "units.jsonl", units)
    write_text_atomic(
        build_dir / "report.json", json.dumps(builder.report, indent=2, ensure_ascii=False)
    )
    _log(f"mine: {len(units):,} units; kinds {builder.report['kinds']}")
    _log(f"mine: rejected {builder.report['rejected']}")
    return 0


# -- cards ---------------------------------------------------------------------


def stage_cards(args: argparse.Namespace) -> int:
    build_dir: Path = args.build_dir
    units_path = build_dir / "units.jsonl"
    if not units_path.exists():
        _log("cards: no units.jsonl; run --stage mine first")
        return 1
    units = _read_units(units_path)
    wanted_ids = {u.unit_id for u in units}
    by_unit: dict[str, list[Occurrence]] = defaultdict(list)
    dictionary = carrier_validation._load_dictionary()  # noqa: SLF001
    infinitives = VocabularyStore.load(DEFAULT_VOCAB_PATH).vocab
    for raw in read_occurrences(build_dir / "occurrences.jsonl"):
        occ = canonical_occurrence(raw, dictionary, infinitives)
        if occ is None:
            continue
        unit_id = unit_id_for(occ.kind, occ.unit_key)
        if unit_id not in wanted_ids and occ.kind == "adj_noun" and len(occ.parts) == 3:
            occ = without_governing_preposition(occ)
            unit_id = unit_id_for(occ.kind, occ.unit_key)
        if unit_id in wanted_ids:
            by_unit[unit_id].append(occ)
    store = _load_store(args.store)
    glosses = {
        german: cards_module.Gloss(english=record.english, source=record.source)
        for german, record in store.items()
    }
    _log(f"cards: {len(store):,} stored glosses, {len(by_unit):,} units with occurrences")
    if not parser_available():
        _log("cards: spaCy model missing; carrier validation cannot run")
        return 1

    def validate(text: str) -> bool:
        return carrier_validation.validate_carrier(text).accepted

    curated = load_curated(args.phrases_dir)
    selection = cards_module.select_cards(
        units,
        by_unit,
        glosses,
        validate=validate,
        k=args.k,
        max_validations=args.max_validations,
        excluded_card_ids=frozenset(curated.excluded_cards),
    )
    _write_models(build_dir / "cards.jsonl", selection.cards)
    write_text_atomic(
        build_dir / "wanted_carriers.txt",
        "".join(f"{w.corpus_source}\t{w.line_id}\t{w.text}\n" for w in selection.wanted),
    )
    write_text_atomic(build_dir / "cards_stats.json", json.dumps(selection.stats, indent=2))
    _log(f"cards: {selection.stats}")
    return 0


# -- contexts ------------------------------------------------------------------


def _read_cards(path: Path) -> list[PhraseCard]:
    return [
        PhraseCard.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def stage_contexts(args: argparse.Namespace) -> int:
    build_dir: Path = args.build_dir
    cards = _read_cards(build_dir / "cards.jsonl")
    units = {u.unit_id: u for u in _read_units(build_dir / "units.jsonl")}
    existing = contexts_module.load_records(args.contexts)
    todo = [c for c in contexts_module.cards_needing_context(cards) if c.card_id not in existing]
    _log(f"contexts: {len(existing)} stored, {len(todo)} card(s) still need one")
    if not args.generate_contexts:
        _log("contexts: nothing generated (pass --generate-contexts to spend)")
        return 0
    if not todo:
        return 0
    if not args.approved_by_owner:
        _log(
            f"contexts: REFUSED. {min(len(todo), args.max_context_calls)} call(s) on the free "
            "lane need --approved-by-owner and the owner's say-so in chat."
        )
        return 2
    load_env_file()
    try:
        client = client_from_env(free_lane_only=True)
    except FreeLaneKeyMissingError as exc:
        _log(str(exc))
        return 2
    if client is None:
        _log("contexts: no Gemini key configured")
        return 2

    def validate(text: str) -> bool:
        return carrier_validation.validate_carrier(text).accepted

    produced: list[ContextRecord] = []
    try:
        produced = contexts_module.generate_contexts(
            cards,
            units,
            client=client,
            validate=validate,
            existing=existing,
            approved=True,
            max_calls=args.max_context_calls,
        )
    finally:
        if produced:
            merged = {**existing, **{r.card_id: r for r in produced}}
            contexts_module.save_records(merged.values(), args.contexts)
    accepted = sum(1 for r in produced if r.accepted)
    _log(f"contexts: {len(produced)} generated, {accepted} accepted")
    return 0


# -- unit glosses --------------------------------------------------------------


def stage_unit_glosses(args: argparse.Namespace) -> int:
    build_dir: Path = args.build_dir
    units = _read_units(build_dir / "units.jsonl")
    cards = _read_cards(build_dir / "cards.jsonl") if (build_dir / "cards.jsonl").exists() else []
    existing = glosses_module.load_records(args.unit_glosses)
    todo = [u for u in glosses_module.units_needing_gloss(units) if u.unit_id not in existing]
    batches = -(-len(todo) // args.gloss_batch_size)
    _log(f"unit-glosses: {len(existing)} stored, {len(todo)} unit(s) still need one")
    if not args.generate_unit_glosses:
        _log("unit-glosses: nothing generated (pass --generate-unit-glosses to spend)")
        return 0
    if not todo:
        return 0
    if not args.approved_by_owner:
        _log(
            f"unit-glosses: REFUSED. {min(batches, args.max_gloss_calls)} call(s) of "
            f"{args.gloss_batch_size} units on {MODEL_GENERATE} (free lane first, paid "
            "overflow) need --approved-by-owner and the owner's say-so in chat."
        )
        return 2
    load_env_file()
    client = client_from_env()
    if client is None:
        _log("unit-glosses: no Gemini key configured")
        return 2
    produced: list[UnitGlossRecord] = []
    try:
        produced = glosses_module.generate_unit_glosses(
            units,
            cards,
            client=client,
            existing=existing,
            approved=True,
            max_calls=args.max_gloss_calls,
            batch_size=args.gloss_batch_size,
        )
    finally:
        if produced:
            merged = {**existing, **{r.unit_id: r for r in produced}}
            glosses_module.save_records(merged.values(), args.unit_glosses)
    accepted = sum(1 for r in produced if r.accepted)
    _log(f"unit-glosses: {len(produced)} generated, {accepted} accepted")
    return 0


# -- export --------------------------------------------------------------------


def stage_export(args: argparse.Namespace) -> int:
    build_dir: Path = args.build_dir
    units = _read_units(build_dir / "units.jsonl")
    cards = _read_cards(build_dir / "cards.jsonl") if (build_dir / "cards.jsonl").exists() else []
    cards = contexts_module.apply_contexts(cards, contexts_module.load_records(args.contexts))
    units = cards_module.with_card_counts(units, cards)
    units = glosses_module.apply_unit_glosses(units, glosses_module.load_records(args.unit_glosses))
    corpus: dict[str, int] = {}
    stats_path = build_dir / "parse_stats.json"
    if stats_path.exists():
        corpus = dict(json.loads(stats_path.read_text(encoding="utf-8")).get("by_source", {}))
    manifest = export_deck(units, cards, args.out, shard_size=args.shard_size, corpus=corpus)
    write_schema()
    _log(
        f"export: {manifest.unit_count:,} units, {manifest.card_count:,} cards, "
        f"{len(manifest.shards)} shards, version {manifest.deck_version} -> {args.out}"
    )
    return 0


# -- main ----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--stage", choices=STAGES, default="all")
    parser.add_argument("--check", action="store_true", help="validate the deck at --out and exit")
    parser.add_argument("--tatoeba", type=Path, default=DEFAULT_TATOEBA_PATH)
    parser.add_argument("--leipzig", type=Path, default=DEFAULT_LEIPZIG_PATH)
    parser.add_argument("--skip-tatoeba", action="store_true")
    parser.add_argument("--skip-leipzig", action="store_true")
    parser.add_argument(
        "--extra-corpus",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="an additional id<TAB>sentence corpus; repeatable",
    )
    parser.add_argument(
        "--no-default-extras",
        action="store_true",
        help="read only Tatoeba and the 2025 Leipzig news file (tests, quick runs)",
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)
    parser.add_argument("--phrases-dir", type=Path, default=DEFAULT_PHRASES_DIR)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE_PATH)
    parser.add_argument("--contexts", type=Path, default=contexts_module.DEFAULT_CONTEXTS_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_DECK_DIR)
    parser.add_argument("--shard-size", type=int, default=200)
    parser.add_argument("--k", type=int, default=6, help="cards per unit")
    parser.add_argument(
        "--max-validations",
        type=int,
        default=None,
        help="bound the carrier-validator calls (a partial build); default unbounded",
    )
    parser.add_argument("--generate-contexts", action="store_true")
    parser.add_argument("--approved-by-owner", action="store_true")
    parser.add_argument("--max-context-calls", type=int, default=100)
    parser.add_argument(
        "--unit-glosses", type=Path, default=glosses_module.DEFAULT_UNIT_GLOSSES_PATH
    )
    parser.add_argument("--generate-unit-glosses", action="store_true")
    parser.add_argument("--max-gloss-calls", type=int, default=250)
    parser.add_argument("--gloss-batch-size", type=int, default=glosses_module.DEFAULT_BATCH_SIZE)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.check:
        problems = check_deck(args.out)
        for problem in problems:
            _log(f"check: {problem}")
        _log("check: ok" if not problems else f"check: {len(problems)} problem(s)")
        return 1 if problems else 0
    stages = ["parse", "mine", "cards", "export"] if args.stage == "all" else [args.stage]
    runners = {
        "parse": stage_parse,
        "mine": stage_mine,
        "cards": stage_cards,
        "contexts": stage_contexts,
        "unit-glosses": stage_unit_glosses,
        "export": stage_export,
    }
    try:
        with run_lock(args.build_dir / ".lock", label="build_phrase_deck"):
            for stage in stages:
                code = runners[stage](args)
                if code != 0:
                    return code
    except LockHeld as exc:
        _log(f"another build is running: {exc}")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
