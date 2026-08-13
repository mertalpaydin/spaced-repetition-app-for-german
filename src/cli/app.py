"""Command-line application entry point and subcommands dispatcher."""

import argparse
import sys
from pathlib import Path

from src.bank.exporter import BankExporter
from src.bank.stats import BankStatsCalculator
from src.bank.storage import SqliteItemBank
from src.engine.topic_state import TopicStateManager
from src.taxonomy.loader import load_taxonomy


def cmd_stats(bank: SqliteItemBank) -> None:
    """Print item bank statistics."""
    summary = BankStatsCalculator.compute_summary(bank)
    print("\n=== German Grammar Trainer - Item Bank Statistics ===")
    print(f"Total Items: {summary.total_items}")
    print(f"Topics Covered: {summary.total_topics_covered}")
    print(f"Average Items/Topic: {summary.avg_items_per_topic}")
    print("Items per CEFR Level:")
    for cefr, count in sorted(summary.items_per_cefr.items()):
        print(f"  [{cefr}]: {count}")
    print("Items per Difficulty:")
    for diff, count in sorted(summary.items_per_difficulty.items()):
        print(f"  Tier {diff}: {count}")


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


def run_cli(args: list[str] | None = None) -> int:
    """Main CLI entry point with subcommand parsing."""
    parser = argparse.ArgumentParser(
        prog="grammar-trainer",
        description="German Grammar Learning Trainer - FSRS & Topic DAG Engine",
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to execute")

    # Subcommands
    subparsers.add_parser("stats", help="Show bank statistics")
    subparsers.add_parser("topics", help="Show topic DAG states")

    export_p = subparsers.add_parser("export", help="Export bank to JSON")
    export_p.add_argument("--out", default="data/bank", help="Output directory")

    parsed = parser.parse_args(args if args is not None else sys.argv[1:])

    db_path = Path("data/bank.db")
    bank = SqliteItemBank(db_path)
    topics = load_taxonomy()
    topic_manager = TopicStateManager(topics)

    if parsed.command == "stats":
        cmd_stats(bank)
        return 0
    elif parsed.command == "topics":
        cmd_topics(topic_manager)
        return 0
    elif parsed.command == "export":
        cmd_export(bank, parsed.out)
        return 0
    else:
        parser.print_help()
        return 0
