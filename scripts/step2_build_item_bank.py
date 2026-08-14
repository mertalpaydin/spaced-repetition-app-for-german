"""Step 2: Generate, verify, and populate the SQLite Item Bank (data/bank.db).

Run this script directly in your IDE (Right click -> Run Python File, or hit F5).
"""

import json
from pathlib import Path
from typing import Any

from src.bank.storage import SqliteItemBank
from src.contracts import BankItem, InsertReport, Topic
from src.taxonomy.facets import derive_facet
from src.taxonomy.loader import load_taxonomy


def ingest_items(
    bank: SqliteItemBank,
    items_data: list[dict[str, Any]],
    topics_by_id: dict[str, Topic],
    source_batch_id: str = "bootstrap_sample",
) -> InsertReport:
    """Stamp derived fields onto raw item dicts and insert them into ``bank``.

    Every ingested grammar item has three fields recomputed here rather than
    trusted from the source JSON, because none of them can be safely derived
    later (at query/export time) without breaking offline use or promotion
    counting:

    - ``facet``: derived, never hand-written (01-foundation.md:168) -- from
      the topic's ``morph_spec`` and the item's own accepted answer.
    - ``confusion_group``: copied from ``Topic.confusion_group``. The offline
      minimal-pair fallback (02-content-pipeline.md stage 5) reads this
      straight off the item at runtime and has no way to derive it itself.
    - ``tag_id``: for grammar items (this fixture carries no vocabulary
      items), the general-purpose stock/export identifier is the topic id.
      Only stamped when the source did not already supply one, so items that
      arrive pre-tagged (e.g. future vocabulary ingest) are left alone.

    Items whose ``topic_id`` is not in the taxonomy are still inserted (with
    ``facet`` and ``confusion_group`` left unset) so `bank.insert`'s
    referential-integrity concerns surface as a reported rejection/warning
    downstream rather than a silent drop here.
    """
    items: list[BankItem] = []
    for raw in items_data:
        item = BankItem.model_validate(raw)
        topic = topics_by_id.get(item.topic_id)
        if topic is None:
            print(
                f"  Warning: unknown topic_id '{item.topic_id}' for item {item.id}; "
                "facet and confusion_group left unset."
            )
            item = item.model_copy(update={"facet": None})
        else:
            item = item.model_copy(
                update={
                    "facet": derive_facet(item, topic),
                    "confusion_group": item.confusion_group or topic.confusion_group,
                    "tag_id": item.tag_id or item.topic_id,
                }
            )
        items.append(item)
    return bank.insert(items, source_batch_id=source_batch_id)


def main() -> None:
    print("\n=======================================================")
    print("  Step 2: Building and Verifying Item Bank")
    print("=======================================================\n")

    db_path = Path("data/bank.db")
    bank = SqliteItemBank(db_path)

    golden_sample = Path("data/fixtures/production/golden_bank_sample.json")
    if not golden_sample.exists():
        print(f"Error: {golden_sample} not found.")
        return

    with golden_sample.open("r", encoding="utf-8") as f:
        items_data = json.load(f)

    topics_by_id = {t.id: t for t in load_taxonomy()}

    print(f"Ingesting and verifying {len(items_data)} items from golden bank sample...")
    report = ingest_items(bank, items_data, topics_by_id)
    print(
        f"  Inserted: {report.inserted}  Duplicates: {report.duplicates}  "
        f"Rejected: {report.rejected}"
    )
    for reason in report.rejection_reasons:
        print(f"    - {reason}")

    print("\nItem Bank Population Complete!")
    print(f"Database Path: {db_path.resolve()}")
    print(f"Total Items in Database: {bank.count_total()}\n")


if __name__ == "__main__":
    main()
