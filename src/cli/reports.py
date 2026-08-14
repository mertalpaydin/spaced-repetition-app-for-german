"""Learner-flagged item reports.

Kept as an independent append-only store rather than a table inside
``bank.db``: the bank schema (``src/bank/migrations.py``, ``src/bank/storage.py``)
is owned elsewhere, so persistence for ``grammar report`` lives entirely in
files owned by the CLI. The store sits alongside the bank's own database file
so each bank (including per-test temporary banks) gets its own report log.
"""

import json
from pathlib import Path

from src.bank.storage import SqliteItemBank


class ReportStore:
    """Append-only JSON-lines log of items flagged via ``grammar report <item_id>``."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def add(self, item_id: str, topic_id: str, reason: str | None = None) -> None:
        """Append a report record. Never edits or removes a prior record."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"item_id": item_id, "topic_id": topic_id, "reason": reason}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def reported_item_ids(self) -> set[str]:
        """Return the set of item IDs ever reported, honoured by round assembly."""
        if not self.path.exists():
            return set()
        ids: set[str] = set()
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                item_id = record.get("item_id")
                if item_id:
                    ids.add(str(item_id))
        return ids

    @classmethod
    def for_bank(cls, bank: SqliteItemBank) -> "ReportStore":
        """Derive a report store path from a bank's own database location."""
        return cls(Path(bank.db_path).parent / "item_reports.jsonl")
