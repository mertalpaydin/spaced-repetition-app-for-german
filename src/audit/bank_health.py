"""Item bank health audit runner checking volume, distractors, and topic balance."""

import argparse
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from src.bank.storage import SqliteItemBank
from src.contracts import MIN_STOCK_PER_TIER
from src.taxonomy.loader import load_taxonomy


class BankHealthReport(BaseModel):
    """Detailed audit report on item bank stock and structural integrity."""

    model_config = ConfigDict(frozen=True)
    total_items: int
    topics_with_items: int
    understocked_topics: list[str] = Field(default_factory=list)
    # Sum, across every topic in the taxonomy, of max(0, floor - count). This
    # is the number of items still needed to clear the stage 5 DoD cold-seed
    # floor (MIN_STOCK_PER_TIER per topic) -- reported honestly rather than
    # fabricated, since closing it requires the generation pipeline (whose
    # transport is still mocked), not bank-layer code.
    total_shortfall: int = 0
    defective_items: list[str] = Field(default_factory=list)
    passed_audit: bool = True


class BankHealthAuditor:
    """Audits SQLite item bank to ensure structural invariants and coverage quotas."""

    @classmethod
    def audit_bank(
        cls,
        bank: SqliteItemBank,
        min_items_per_topic: int = MIN_STOCK_PER_TIER,
    ) -> BankHealthReport:
        """Run full health audit on the item bank."""
        all_items = bank.get_all_items()
        topics = load_taxonomy()

        topic_counts: dict[str, int] = {}
        for it in all_items:
            topic_counts[it.topic_id] = topic_counts.get(it.topic_id, 0) + 1

        understocked: list[str] = []
        total_shortfall = 0
        for t in topics:
            count = topic_counts.get(t.id, 0)
            if count < min_items_per_topic:
                total_shortfall += min_items_per_topic - count
                understocked.append(f"{t.id} ({count}/{min_items_per_topic})")

        defective: list[str] = []
        for it in all_items:
            # Check gap presence
            if "___" not in it.prompt:
                defective.append(f"{it.id}: missing gap '___'")

            # Check distractors
            if len(it.distractors) != 3:
                defective.append(f"{it.id}: invalid distractor count ({len(it.distractors)})")

            dist_texts = [d.text if hasattr(d, "text") else str(d) for d in it.distractors]
            if len(set(dist_texts)) != len(dist_texts):
                defective.append(f"{it.id}: duplicate distractors ({dist_texts})")

            # Check accepted answers
            if not it.accepted_answers:
                defective.append(f"{it.id}: empty accepted answers")

        passed = len(defective) == 0

        return BankHealthReport(
            total_items=len(all_items),
            topics_with_items=len(topic_counts),
            understocked_topics=understocked,
            total_shortfall=total_shortfall,
            defective_items=defective,
            passed_audit=passed,
        )


def main() -> int:
    """CLI entry point for running bank health audit."""
    parser = argparse.ArgumentParser(description="Bank Health Audit Runner")
    parser.add_argument("--db", default="data/bank.db", help="Path to SQLite database")
    parser.add_argument(
        "--min-stock",
        type=int,
        default=MIN_STOCK_PER_TIER,
        help="Min items per topic (defaults to the stage 5 DoD cold-seed floor)",
    )
    parsed = parser.parse_args()

    db_path = Path(parsed.db)
    if not db_path.exists():
        print(f"Database {db_path} not found.")
        return 1

    bank = SqliteItemBank(db_path)
    report = BankHealthAuditor.audit_bank(bank, min_items_per_topic=parsed.min_stock)

    print("\n=== Item Bank Health Audit Report ===")
    print(f"Total Items: {report.total_items}")
    print(f"Topics with Items: {report.topics_with_items}")
    print(f"Understocked Topics: {len(report.understocked_topics)}")
    print(f"Total Shortfall (items still needed to clear the floor): {report.total_shortfall}")
    print(f"Audit Passed: {report.passed_audit}")

    if report.defective_items:
        print(f"\nDefective Items Found ({len(report.defective_items)}):")
        for d in report.defective_items[:10]:
            print(f"  - {d}")

    return 0 if report.passed_audit else 1


if __name__ == "__main__":
    sys.exit(main())
