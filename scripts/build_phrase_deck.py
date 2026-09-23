"""Build the phrase deck. The operator entry point for phase 1.

Stages, each idempotent over ``data/phrases/build/``:

    parse     read both corpora, parse once, write every phrase occurrence
    mine      aggregate occurrences into ranked units (thresholds, report)
    expressions  choose fixed expressions from the n-gram counts (written by
              scripts/count_ngrams.py) and match them over the glossed
              sentences; run it between two mines, then mine again
    requests  corpus sentences for a requested word that has none glossed;
              run it between two mines, then translate its carriers file
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
import re
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path

from src.atomic_write import write_text_atomic
from src.contracts import MODEL_GENERATE, ContextRecord, PhraseCard, PhraseUnit, UnitGlossRecord
from src.lexicon.vocabulary import VocabularyStore, _load_frequency_ranks
from src.llm.env import FreeLaneKeyMissingError, client_from_env, load_env_file
from src.phrases import cards as cards_module
from src.phrases import carrier_validation
from src.phrases import contexts as contexts_module
from src.phrases import unit_glosses as glosses_module
from src.phrases.curated import DEFAULT_PHRASES_DIR, IdiomElement, IdiomSpec, load_curated
from src.phrases.export import DEFAULT_DECK_DIR, check_deck, export_deck, write_schema
from src.phrases.mining import (
    NGRAM_VOCAB_SIZE,
    LemmaCounts,
    WordGate,
    detect_all,
    detect_idioms,
    detect_words,
)
from src.phrases.mining import expressions as expressions_module
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
STAGES = (
    "parse",
    "mine",
    "expressions",
    "requests",
    "cards",
    "contexts",
    "unit-glosses",
    "export",
    "all",
)


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


#: n-grams below this are dropped before the counts are written: a fixed
#: expression recurs, a one-off sequence does not, and keeping the tail would
#: make the counts file unreadable.
NGRAM_MIN_COUNT = 20


def _ngram_vocab() -> frozenset[str]:
    """The commonest surface forms, which is all a fixed expression is made
    of ("soweit ich weiss", "auf jeden Fall"). Bounding the n-gram counter
    to these keeps the parse stage inside its memory."""
    ranks = _load_frequency_ranks(str(VocabularyStore.DEFAULT_FREQUENCY_RANKS_PATH))
    return frozenset(word for word, rank in ranks.items() if rank <= NGRAM_VOCAB_SIZE)


def _word_gate(args: argparse.Namespace) -> WordGate:
    """Which words may become units, and from which sentences.

    The real-word filter is the CEFR list union the dictionary filter, so
    names, typos and rare compounds never enter. Only sentences with an Azure
    or Gemini gloss are carriers, because ``cards.select_cards`` can never use
    any other sentence; that bound is what keeps the occurrence file from
    growing by an order of magnitude.
    """
    vocabulary = VocabularyStore()
    dictionary = carrier_validation._load_dictionary()  # noqa: SLF001
    lemmas = frozenset(vocabulary.vocab) | (dictionary or frozenset())
    store = _load_store(args.store)
    trusted = cards_module.TRUSTED_GLOSS_SOURCES
    glossed = frozenset(german for german, record in store.items() if record.source in trusted)
    return WordGate(
        lemmas=lemmas, glossed=glossed, dictionary=dictionary, cap=args.word_cap, seed=args.seed
    )


def corpora_from_args(args: argparse.Namespace) -> dict[str, CorpusLine]:
    """Every corpus the flags ask for. Shared by the stages that read the
    corpus directly, so ``--skip-leipzig`` and ``--no-default-extras`` mean
    the same thing in each of them."""
    extra: list[tuple[str, Path]] = []
    if not args.no_default_extras:
        extra += [(name, default_corpus_path(file)) for name, file in DEFAULT_EXTRA_CORPORA]
    for spec in args.extra_corpus:
        name, _, path = spec.partition("=")
        if not name or not path:
            raise ValueError(f"--extra-corpus expects name=path, got {spec!r}")
        extra.append((name, Path(path)))
    return read_corpora(
        None if args.skip_tatoeba else args.tatoeba,
        None if args.skip_leipzig else args.leipzig,
        limit=args.limit,
        seed=args.seed,
        extra=extra,
    )


def stage_parse(args: argparse.Namespace) -> int:
    if not parser_available():
        _log("spaCy model de_core_news_sm is not installed; run `uv sync`.")
        return 1
    build_dir: Path = args.build_dir
    build_dir.mkdir(parents=True, exist_ok=True)
    curated = load_curated(args.phrases_dir)
    dictionary = carrier_validation._load_dictionary()  # noqa: SLF001
    try:
        lines = corpora_from_args(args)
    except ValueError as exc:
        _log(str(exc))
        return 1
    ordered = sorted(lines.values(), key=lambda line: (line.source, line.line_id))
    _log(f"parse: {len(ordered):,} distinct sentences")
    gate = _word_gate(args)
    _log(
        f"parse: {len(gate.lemmas):,} words may become units, "
        f"{len(gate.glossed):,} sentences carry a trusted gloss"
    )
    counts = LemmaCounts(
        gate=gate,
        count_ngrams=not args.no_ngrams,
        ngram_vocab=_ngram_vocab(),
    )
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
                parsed,
                curated,
                source=line.source,
                line_id=line.line_id,
                dictionary=dictionary,
                words=gate,
            ):
                kinds[occ.kind] += 1
                total += 1
                yield occ
            if index % 20_000 == 0:
                elapsed = time.monotonic() - started
                _log(f"  {index:,} sentences, {total:,} occurrences, {elapsed / 60:.1f} min")

    write_occurrences(occurrences_path, generate())
    counts.prune_ngrams(NGRAM_MIN_COUNT)
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


def _occurrence_files(build_dir: Path) -> list[Path]:
    """The parse stage's occurrences, plus those of the two targeted stages.

    Expressions are matched over the glossed sentences alone, and requested
    words over the handful of corpus sentences that use them, in passes that
    take minutes rather than hours. Both land in files of their own instead
    of being merged into the 2.5 GiB one.
    """
    paths = [build_dir / "occurrences.jsonl"]
    for name in ("expression_occurrences.jsonl", "requested_occurrences.jsonl"):
        extra = build_dir / name
        if extra.exists():
            paths.append(extra)
    return paths


def _read_all_occurrences(build_dir: Path) -> Iterable[Occurrence]:
    for path in _occurrence_files(build_dir):
        yield from read_occurrences(path)


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
    units = builder.build(_read_all_occurrences(build_dir))
    _write_models(build_dir / "units.jsonl", units)
    write_text_atomic(
        build_dir / "report.json", json.dumps(builder.report, indent=2, ensure_ascii=False)
    )
    _log(f"mine: {len(units):,} units; kinds {builder.report['kinds']}")
    _log(f"mine: rejected {builder.report['rejected']}")
    return 0


# -- expressions ---------------------------------------------------------------


#: The n-gram measure alone ranks proper names highest and fixed expressions
#: below them, so the selection is gated (dictionary, register) rather than
#: cut off at a score. These are the bars those gates use; see
#: ``src.phrases.mining.expressions``.
EXPRESSION_MIN_SCORE = 4.5
EXPRESSION_MIN_EVERYDAY = 0.15
#: How many candidates become units. The tail below this is real German but
#: thin evidence, and every unit costs a review.
EXPRESSION_LIMIT = 600


def _with_corpus_count(
    occurrences: Iterable[Occurrence], counts: dict[str, tuple[int, int]]
) -> Iterable[Occurrence]:
    """Carry each expression's whole-corpus counts on its occurrences.

    The match runs over the glossed sentences alone, so the occurrence tally
    counts carriers. The ranking needs the frequency the n-gram pass
    measured over all four million sentences, and the everyday share of it,
    which is what the register weight turns on. This is where the two meet.
    """
    for occ in occurrences:
        measured = counts.get(occ.unit_key)
        if measured is None:
            yield occ
            continue
        total, everyday = measured
        yield occ.model_copy(
            update={
                "evidence": {
                    **occ.evidence,
                    "corpus_count": str(total),
                    "corpus_everyday": str(everyday),
                }
            }
        )


def stage_expressions(args: argparse.Namespace) -> int:
    """Choose fixed expressions from the n-gram counts and match them.

    Runs between mine and a second mine: the first tells it which units
    already exist, so an expression never duplicates one. Matching parses
    only the sentences that carry a trusted gloss, which is minutes rather
    than the parse stage's hours, because no other sentence could become a
    card anyway.
    """
    if not parser_available():
        _log("spaCy model de_core_news_sm is not installed; run `uv sync`.")
        return 1
    build_dir: Path = args.build_dir
    counts_path = build_dir / "ngram_counts.json"
    if not counts_path.exists():
        _log("expressions: no ngram_counts.json; run scripts/count_ngrams.py first")
        return 1
    payload = json.loads(counts_path.read_text(encoding="utf-8"))
    units_path = build_dir / "units.jsonl"
    # Expression units from an earlier run of this stage are not "already
    # exists": counting them would make the stage pick a different, rarer
    # set every time it ran.
    known = (
        [u.lemma_key for u in _read_units(units_path) if u.kind != "expression"]
        if units_path.exists()
        else []
    )
    dictionary = carrier_validation._load_dictionary() or frozenset()  # noqa: SLF001
    curated = load_curated(args.phrases_dir)
    chosen = expressions_module.choose_expressions(
        Counter(payload["ngrams"]),
        Counter(payload["surfaces"]),
        Counter(payload.get("everyday", {})),
        sentences=payload["sentences"],
        dictionary=dictionary,
        known_keys=known,
        min_score=args.expression_min_score,
        min_everyday_share=args.expression_min_everyday,
        deny=[entry.key for entry in curated.idioms],
    )
    chosen.sort(key=lambda e: (-e.count, e.text))
    chosen = chosen[: args.expression_limit]
    write_text_atomic(
        build_dir / "expressions.json",
        json.dumps(
            [{"text": e.text, "count": e.count, "score": round(e.score, 3)} for e in chosen],
            ensure_ascii=False,
            indent=1,
        ),
    )
    _log(f"expressions: {len(chosen):,} chosen of the candidates that pass every gate")

    specs = [
        IdiomSpec(key=e.text, pattern=[IdiomElement(surface=token) for token in e.tokens])
        for e in chosen
    ]
    everyday_counts = Counter(payload.get("everyday", {}))
    corpus_counts = {e.text: (e.count, everyday_counts[e.text]) for e in chosen}
    store = _load_store(args.store)
    trusted = cards_module.TRUSTED_GLOSS_SOURCES
    glossed = {german for german, record in store.items() if record.source in trusted}
    lines = read_corpora(
        args.tatoeba,
        args.leipzig,
        limit=args.limit,
        seed=args.seed,
        extra=[(name, default_corpus_path(file)) for name, file in DEFAULT_EXTRA_CORPORA],
    )
    carriers = sorted(
        (line for line in lines.values() if line.text in glossed),
        key=lambda line: (line.source, line.line_id),
    )
    _log(f"expressions: matching over {len(carriers):,} glossed sentences")
    started = time.monotonic()
    total = 0

    def generate() -> Iterable[Occurrence]:
        nonlocal total
        for index, (line, parsed) in enumerate(
            zip(carriers, parse_many(entry.text for entry in carriers), strict=True), start=1
        ):
            for occ in _with_corpus_count(
                detect_idioms(
                    parsed,
                    specs,
                    source=line.source,
                    line_id=line.line_id,
                    kind="expression",
                    parts_from_tokens=True,
                ),
                corpus_counts,
            ):
                total += 1
                yield occ
            if index % 20_000 == 0:
                _log(f"  {index:,} sentences, {total:,} occurrences")

    write_occurrences(build_dir / "expression_occurrences.jsonl", generate())
    minutes = (time.monotonic() - started) / 60
    _log(f"expressions: {total:,} occurrences in {minutes:.1f} min")
    return 0


# -- requests ------------------------------------------------------------------


#: Suffixes stripped to get a stem the corpus can be searched for. German
#: inflects, so "Haehnchen" also appears as "Haehnchens" and "bewoelkt" as
#: "bewoelkten". The stem is deliberately loose: every candidate sentence is
#: parsed afterwards, and ``mining.words._word_of`` is what decides which
#: lemma a sentence really teaches, so a false positive costs a parse and
#: nothing else.
_REQUEST_STEM_SUFFIXES: tuple[str, ...] = ("en", "es", "er", "em", "e", "n")
_REQUEST_MIN_STEM = 4
#: Only the vowel transliterations, never ss -> eszett: folding "essen" to
#: "eszett-en" would make the commonest spelling of the word unfindable.
_REQUEST_UMLAUTS: tuple[tuple[str, str], ...] = (("ae", "ä"), ("oe", "ö"), ("ue", "ü"))


def _request_stems(key: str) -> set[str]:
    """Every spelling of ``key`` worth searching the corpus for, shortened to
    a stem. The owner may write the umlaut either way round; the corpus, in
    practice, writes it with the umlaut."""
    spellings = {key.lower()}
    folded = key.lower()
    for ascii_form, german_form in _REQUEST_UMLAUTS:
        folded = folded.replace(ascii_form, german_form)
    spellings.add(folded)
    stems: set[str] = set()
    for spelling in spellings:
        stem = spelling
        for suffix in _REQUEST_STEM_SUFFIXES:
            if spelling.endswith(suffix) and len(spelling) - len(suffix) >= _REQUEST_MIN_STEM:
                stem = spelling[: -len(suffix)]
                break
        stems.add(stem)
    return stems


def _requested_without_occurrences(report: dict[str, object]) -> list[tuple[str, str]]:
    """``(kind, key)`` for each request the mine stage found no occurrence for.

    A request that ``exclude.yaml`` refuses is not one of them: those keys
    were excluded because their occurrences teach a different lemma, and
    hunting the corpus for more of the same would put false sentences in
    front of the learner (CLAUDE.md, the requested-unit plan)."""

    def bucket(name: str) -> list[str]:
        raw = report.get(name)
        return [str(entry) for entry in raw] if isinstance(raw, list) else []

    missing = bucket("requested_missing")
    excluded = set(bucket("requested_but_excluded"))
    out: list[tuple[str, str]] = []
    for entry in missing:
        if entry in excluded:
            continue
        kind, _, key = entry.partition(":")
        if kind and key:
            out.append((kind, key))
    return out


def stage_requests(args: argparse.Namespace) -> int:
    """Find corpus sentences for a requested word that has no glossed one.

    The word detector emits occurrences only from sentences that already
    carry a trusted gloss, because no other sentence can become a card. That
    bound is what keeps ``occurrences.jsonl`` to its present size, but it
    also means a requested word whose corpus sentences are all untranslated
    ("Haehnchen": 46 sentences, none glossed) produces nothing at all, and so
    never reaches ``wanted_carriers.txt`` for the monthly Azure job either.

    This stage lifts the gloss bound for those keys and nothing else. It
    writes its occurrences to a file of their own, the way the expression
    stage does, and it writes the candidate sentences in the format
    ``scripts/monthly_translation_topup.py --carriers-file`` reads, so the
    translation can be scoped to exactly these sentences.

    Run it between two mines: the first says which requests came up empty,
    the second turns these occurrences into units.
    """
    if not parser_available():
        _log("spaCy model de_core_news_sm is not installed; run `uv sync`.")
        return 1
    build_dir: Path = args.build_dir
    report_path = build_dir / "report.json"
    if not report_path.exists():
        _log("requests: no report.json; run --stage mine first")
        return 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    wanted_keys = _requested_without_occurrences(report)
    if not wanted_keys:
        # Nothing is rewritten in this case, deliberately: a second run of
        # this stage sees the keys of the first as present, and blanking the
        # file would throw away the occurrences that made them present.
        _log("requests: every request already has occurrences; nothing to do")
        return 0
    _log(f"requests: {len(wanted_keys)} request(s) with no occurrence: {wanted_keys}")

    stems_by_key = {key: _request_stems(key) for _, key in wanted_keys}
    pattern = re.compile(
        r"\b(?:"
        + "|".join(re.escape(stem) for stem in sorted(set().union(*stems_by_key.values())))
        + r")",
        re.IGNORECASE,
    )
    lines = corpora_from_args(args)
    ordered = sorted(lines.values(), key=lambda line: (line.source, line.line_id))
    _log(f"requests: scanning {len(ordered):,} sentences")
    taken: Counter[str] = Counter()
    candidates: list[CorpusLine] = []
    for line in ordered:
        if not pattern.search(line.text):
            continue
        # The same parser-free shape checks the translation job uses, so a
        # sentence queued here is one the pipeline could accept later.
        if carrier_validation._sentence_shape_reason(line.text) is not None:  # noqa: SLF001
            continue
        hit = next(
            (
                key
                for key, stems in stems_by_key.items()
                if any(s in line.text.lower() for s in stems)
            ),
            None,
        )
        if hit is None:
            continue
        bucket = f"{hit}:{line.source}"
        if taken[bucket] >= args.word_cap:
            continue
        taken[bucket] += 1
        candidates.append(line)
    _log(f"requests: {len(candidates):,} candidate sentence(s) to parse")
    if not candidates:
        _log("requests: no corpus sentence matched any request")
        return 0

    gate = WordGate(
        lemmas=frozenset(key for _, key in wanted_keys),
        # The point of this stage: these sentences count as carriers even
        # though nobody has translated them yet.
        glossed=frozenset(line.text for line in candidates),
        dictionary=carrier_validation._load_dictionary(),  # noqa: SLF001
        cap=args.word_cap,
        seed=args.seed,
    )
    found: list[Occurrence] = []
    used: dict[str, CorpusLine] = {}
    for line, parsed in zip(
        candidates, parse_many(entry.text for entry in candidates), strict=True
    ):
        for occ in detect_words(parsed, gate, source=line.source, line_id=line.line_id):
            found.append(occ)
            used[line.text] = line
    write_occurrences(build_dir / "requested_occurrences.jsonl", iter(found))
    carriers = sorted(used.values(), key=lambda line: (line.source, line.line_id))
    write_text_atomic(
        build_dir / "requested_carriers.txt",
        "".join(f"{line.source}\t{line.line_id}\t{line.text}\n" for line in carriers),
    )
    by_key = Counter(occ.unit_key for occ in found)
    for _, key in wanted_keys:
        _log(f"  {key}: {by_key[key]} occurrence(s)")
    empty = [key for _, key in wanted_keys if not by_key[key]]
    if empty:
        _log(f"requests: NOT IN THE CORPUS, no sentence teaches them: {sorted(empty)}")
    _log(
        f"requests: {len(found):,} occurrences over {len(carriers):,} sentences; "
        f"translate them with\n"
        f"  uv run python scripts/monthly_translation_topup.py "
        f"--carriers-file {build_dir / 'requested_carriers.txt'} --carriers-only"
    )
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
    for raw in _read_all_occurrences(build_dir):
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
            f"{args.gloss_batch_size} units on {MODEL_GENERATE} "
            f"({'free lane only' if args.free_lane_only else 'free lane first, paid overflow'})"
            " need --approved-by-owner and the owner's say-so in chat."
        )
        return 2
    load_env_file()
    # The free lane is the whole budget story for this stage: 10,000 unit
    # glosses cost nothing on it and about a dollar on the paid overflow, so
    # the owner can choose to stop at the daily quota and resume after the
    # Pacific-midnight reset instead of spending.
    client = client_from_env(free_lane_only=args.free_lane_only)
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
    parser.add_argument("--expression-min-score", type=float, default=EXPRESSION_MIN_SCORE)
    parser.add_argument("--expression-min-everyday", type=float, default=EXPRESSION_MIN_EVERYDAY)
    parser.add_argument("--expression-limit", type=int, default=EXPRESSION_LIMIT)
    parser.add_argument("--generate-unit-glosses", action="store_true")
    parser.add_argument(
        "--free-lane-only",
        action="store_true",
        help="refuse the paid overflow: the run stops when the free lane's daily quota is gone",
    )
    parser.add_argument("--max-gloss-calls", type=int, default=250)
    parser.add_argument("--word-cap", type=int, default=60)
    parser.add_argument("--no-ngrams", action="store_true")
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
        "expressions": stage_expressions,
        "requests": stage_requests,
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
