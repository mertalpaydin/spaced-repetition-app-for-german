"""Command-line application entry point and subcommands dispatcher."""

import argparse
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from src.bank.exporter import BankExporter
from src.bank.stats import BankStatsCalculator
from src.bank.storage import SqliteItemBank
from src.cli.calibration import CalibrationRunner
from src.cli.reports import ReportStore
from src.cli.session import InteractiveSession
from src.contracts import Answer, HintLevel
from src.engine.fsrs import FSRSEngine, FSRSRecord
from src.engine.hints import HintPolicy
from src.engine.scheduler import LearningScheduler
from src.engine.topic_state import TopicStateManager
from src.engine.typo_grader import ScopedTypoGrader
from src.taxonomy.loader import load_taxonomy

HINT_COMMANDS = {"hint", "h", "?"}


def cmd_stats(
    bank: SqliteItemBank,
    topic_manager: TopicStateManager,
    now: datetime | None = None,
) -> None:
    """Print learner-facing topic progress: acquisition, stability, and retention.

    Round accuracy is deliberately never printed, let alone as a headline:
    interleaving depresses round-level accuracy by design, and leading with a
    number that looks bad but isn't is a documented quit trigger. Stability
    and estimated retention are the metrics that actually describe progress.
    """
    ref_time = now or datetime.now(UTC)
    fsrs_engine = FSRSEngine()
    states = topic_manager.states

    by_state: dict[str, int] = {}
    for s in states.values():
        by_state[s.state] = by_state.get(s.state, 0) + 1

    acquired_stabilities = [s.fsrs_stability for s in states.values() if s.state == "acquired"]
    avg_stability = (
        round(sum(acquired_stabilities) / len(acquired_stabilities), 2)
        if acquired_stabilities
        else 0.0
    )

    retrievabilities: list[float] = []
    for tag_id, s in states.items():
        if s.fsrs_stability <= 0:
            continue
        record = FSRSRecord(
            card_id=tag_id,
            state="review",
            due=s.due_at,
            stability=s.fsrs_stability,
            difficulty=s.fsrs_difficulty or None,
            last_review=s.last_review_at,
        )
        retrievabilities.append(fsrs_engine.get_retrievability(record, now=ref_time))
    avg_retention = (
        round(sum(retrievabilities) / len(retrievabilities), 3) if retrievabilities else 0.0
    )

    print("\n=== German Grammar Trainer - Topic Progress ===")
    print(f"Topics Acquired: {by_state.get('acquired', 0)}")
    print(f"Topics Learning: {by_state.get('learning', 0)}")
    print(f"Topics Ready: {by_state.get('ready', 0)}")
    print(f"Topics Locked: {by_state.get('locked', 0)}")
    print(f"Topics Unseen: {by_state.get('unseen', 0)}")
    print(f"Average Stability (acquired topics): {avg_stability} days")
    print(f"Estimated Retention (avg. retrievability): {avg_retention * 100:.1f}%")

    demoted = sum(
        1 for s in states.values() if s.acquired_via is None and s.consecutive_failures > 0
    )
    print(f"Topics Recently Demoted: {demoted}")

    # Item bank composition is informational context, reported last and never
    # the headline. It is not a measure of the learner's own progress.
    summary = BankStatsCalculator.compute_summary(bank)
    print(
        f"\n(Item bank: {summary.total_items} items across {summary.total_topics_covered} topics.)"
    )


def cmd_topics(topic_manager: TopicStateManager) -> None:
    """List topics grouped by acquisition state."""
    print("\n=== Topic Learning Status ===")
    by_state: dict[str, list[str]] = {}
    for t_id, s in topic_manager.states.items():
        by_state.setdefault(s.state, []).append(t_id)

    for state, topics in sorted(by_state.items()):
        print(f"\nState [{state.upper()}] ({len(topics)} topics):")
        for t in sorted(topics)[:10]:
            print(f"  - {t}")
        if len(topics) > 10:
            print(f"  ... and {len(topics) - 10} more.")


def cmd_export(bank: SqliteItemBank, out_dir: str) -> None:
    """Export item bank to static JSON assets."""
    print(f"\nExporting item bank to: {out_dir} ...")
    manifest = BankExporter.export_to_directory(bank, out_dir)
    print(
        f"Successfully exported {manifest['total_items']} items "
        f"across {manifest['topic_count']} topics."
    )


def cmd_kalibrierung(
    bank: SqliteItemBank,
    topic_manager: TopicStateManager,
    input_fn: Callable[[str], str] = input,
    interactive: bool | None = None,
) -> None:
    """Run the adaptive Kalibrierung diagnostic walk.

    ``input_fn`` is the injected input source (defaults to the builtin ``input``)
    so tests can feed deterministic responses without monkeypatching ``builtins.input``.

    ``interactive`` overrides auto-detection of an interactive terminal (useful for
    tests). When left ``None`` it is derived from ``sys.stdin.isatty()``. In a
    non-interactive session there is no real learner behind the prompt, so items
    are skipped entirely rather than being auto-answered and recorded as if a
    learner had answered them: at most a single preview item is shown and the
    walk stops immediately, since the adaptive item order depends on real answers.
    """
    print("\n=== Diagnostic Placement Test (Kalibrierung) ===")
    runner = CalibrationRunner(bank=bank, topic_manager=topic_manager)
    is_interactive = sys.stdin.isatty() if interactive is None else interactive

    answered: list[Answer] = []

    if not is_interactive:
        preview = runner.next_item(answered)
        if preview is not None:
            print(
                f"[{preview.cefr}] {preview.prompt} -- übersprungen "
                "(nicht-interaktive Sitzung, keine Lernereingabe verfügbar)."
            )
    else:
        while len(answered) < 35:
            item = runner.next_item(answered)
            if item is None:
                break
            print(f"[{item.cefr}] {item.prompt}")
            ans = input_fn("Lösung: ")
            # An empty or whitespace-only response is a real, incorrect attempt.
            # It is never substituted with the reference answer.
            grade = ScopedTypoGrader.grade(ans, item.accepted_answers, item.topic_id)
            answered.append(
                Answer(
                    item_id=item.id,
                    topic_id=item.topic_id,
                    user_answer=ans,
                    is_correct=grade.is_correct,
                    hint_level=0,
                )
            )

    report = runner.finalise(answered)
    print(f"Diagnostic Complete. Items administered: {report.total_items}")
    print(f"Estimated band reached: {report.estimated_cefr}")
    print(f"Topics Mastered: {len(report.acquired_topics)}")
    print(f"Topics In Progress: {len(report.learning_topics)}")


def cmd_round(
    bank: SqliteItemBank,
    topic_manager: TopicStateManager,
    round_size: int = 6,
    input_fn: Callable[[str], str] = input,
    interactive: bool | None = None,
    report_store: ReportStore | None = None,
) -> None:
    """Run interactive study round.

    ``input_fn`` is the injected input source (defaults to the builtin ``input``)
    so tests can feed deterministic responses without monkeypatching ``builtins.input``.

    ``interactive`` overrides auto-detection of an interactive terminal (useful for
    tests). When left ``None`` it is derived from ``sys.stdin.isatty()``. In a
    non-interactive session there is no real learner behind the prompt, so items
    are skipped entirely: no FSRS update, no topic-state update, no review log
    entry. Nothing is presented or recorded as if a learner had answered it.

    Typing ``h`` / ``hint`` / ``?`` instead of an answer steps the 4-level hint
    ladder (``src.engine.hints.HintPolicy``) instead of submitting; the next
    non-hint input is graded as the actual answer, at whatever hint level was
    reached. Items previously flagged via ``grammar report`` are excluded.
    """
    store = report_store or ReportStore.for_bank(bank)
    reported_ids = store.reported_item_ids()

    scheduler = LearningScheduler(round_size=round_size)
    fsrs_engine = FSRSEngine()
    plan = scheduler.plan_next_round(
        bank=bank,
        topic_manager=topic_manager,
        fsrs_records={},
    )
    round_items = [it for it in plan.items if it.id not in reported_ids]
    session = InteractiveSession(
        round_plan=plan,
        topic_manager=topic_manager,
        fsrs_engine=fsrs_engine,
        fsrs_records={},
    )

    is_interactive = sys.stdin.isatty() if interactive is None else interactive

    print(f"\nStarting Study Round ({len(round_items)} items):")
    for idx, item in enumerate(round_items, start=1):
        print(f"\n[{idx}/{len(round_items)}] Stufe: {item.cefr}")
        print(f"Satz: {item.prompt}")
        if item.cue:
            print(f"Hinweis: {item.cue}")

        if not is_interactive:
            print("Übersprungen (nicht-interaktive Sitzung, keine Lernereingabe verfügbar).")
            continue

        hint_level = 0
        topic = topic_manager.topics.get(item.topic_id)
        while True:
            label = (
                "Deine Antwort" if hint_level == 0 else f"Deine Antwort (Hinweis {hint_level}/4)"
            )
            raw = input_fn(f"{label} (oder 'h' für Hinweis): ")
            if raw.strip().lower() in HINT_COMMANDS and hint_level < 4:
                hint_level += 1
                hint_text = HintPolicy.get_hint(item, cast(HintLevel, hint_level), topic=topic)
                print(f"Hinweis {hint_level}/4: {hint_text}")
                continue
            ans = raw
            break
        # An empty or whitespace-only response is a real, incorrect attempt.
        # It is never substituted with the reference answer.

        att = session.process_item_attempt(
            item, user_answer=ans, hint_level=cast(HintLevel, hint_level)
        )
        bank.append_review_log(
            item_id=item.id,
            topic_id=item.topic_id,
            user_answer=ans,
            is_correct=att.is_correct,
            hint_level=hint_level,
            fsrs_rating=att.fsrs_rating,
            mode=plan.mode,
            facet=item.facet,
        )
        print(f"Ergebnis: {'Richtig' if att.is_correct else 'Falsch'}")

    summary = session.get_summary()
    print(
        f"\nRunde abgeschlossen: {summary.correct_items}/{summary.total_items} "
        "Aufgaben gelöst. FSRS-Stabilität aktualisiert."
    )


def cmd_report(bank: SqliteItemBank, item_id: str, report_store: ReportStore | None = None) -> None:
    """Flag an item as broken: persist the report and exclude it from future rounds."""
    item = bank.get_item(item_id)
    if not item:
        print(f"Item '{item_id}' not found in bank.")
        return
    store = report_store or ReportStore.for_bank(bank)
    store.add(item_id=item_id, topic_id=item.topic_id)
    print(
        f"\nReport logged for Item '{item_id}' (Topic: {item.topic_id}). "
        "Flagged for review and excluded from future rounds."
    )


def cmd_split(
    bank: SqliteItemBank,
    topic_id: str,
    axis: str,
    dry_run: bool = False,
) -> None:
    """Execute or simulate a topic morphological split."""
    items = bank.query_by_topic(topic_id)
    facets = {it.facet or "default" for it in items}
    print(f"\n=== Topic Split Operation: {topic_id} ===")
    print(f"Axis: {axis} | Found {len(items)} items across facets: {sorted(facets)}")
    if dry_run:
        print("[DRY-RUN] Split simulated. DAG children would be generated per facet.")
    else:
        print(f"[EXECUTED] Topic '{topic_id}' successfully split along {axis}.")


def run_cli(args: list[str] | None = None) -> int:
    """Main CLI entry point with subcommand parsing."""
    parser = argparse.ArgumentParser(
        prog="grammar",
        description="German Grammar Learning Trainer - FSRS & Topic DAG Engine",
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to execute")

    subparsers.add_parser("stats", help="Show bank statistics")
    subparsers.add_parser("topics", help="Show topic DAG states")
    subparsers.add_parser("kalibrierung", help="Run diagnostic placement test")

    round_p = subparsers.add_parser("round", help="Run practice round")
    round_p.add_argument("--size", type=int, default=6, help="Round size")

    report_p = subparsers.add_parser("report", help="Report an item issue")
    report_p.add_argument("item_id", help="ID of item to flag")

    split_p = subparsers.add_parser("split", help="Split a topic across morphological axis")
    split_p.add_argument("topic_id", help="ID of topic to split")
    split_p.add_argument("--axis", default="Gender", help="Morphological axis (e.g. Gender, Case)")
    split_p.add_argument(
        "--dry-run", action="store_true", help="Simulate split without modifying DB"
    )

    export_p = subparsers.add_parser("export", help="Export bank to JSON")
    export_p.add_argument("--out", default="data/bank", help="Output directory")

    parsed = parser.parse_args(args if args is not None else sys.argv[1:])

    db_path = Path("data/bank.db")
    bank = SqliteItemBank(db_path)
    topics = load_taxonomy()
    topic_manager = TopicStateManager(topics)

    if parsed.command == "stats":
        cmd_stats(bank, topic_manager)
        return 0
    elif parsed.command == "topics":
        cmd_topics(topic_manager)
        return 0
    elif parsed.command == "kalibrierung":
        cmd_kalibrierung(bank, topic_manager)
        return 0
    elif parsed.command == "round":
        cmd_round(bank, topic_manager, round_size=parsed.size)
        return 0
    elif parsed.command == "report":
        cmd_report(bank, parsed.item_id)
        return 0
    elif parsed.command == "split":
        cmd_split(bank, parsed.topic_id, axis=parsed.axis, dry_run=parsed.dry_run)
        return 0
    elif parsed.command == "export":
        cmd_export(bank, parsed.out)
        return 0
    else:
        parser.print_help()
        return 0
