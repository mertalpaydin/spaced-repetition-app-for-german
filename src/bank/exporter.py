"""Item Bank exporter for serializing bank items to static frontend JSON assets."""

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.bank.storage import SqliteItemBank
from src.contracts import BankItem


class BankExporter:
    """Exports SQLite item bank to structured, topic-partitioned JSON files and manifest."""

    @classmethod
    def export_to_directory(
        cls,
        bank: SqliteItemBank,
        output_dir: Path | str,
    ) -> dict[str, Any]:
        """Export all items from the database into topic files and a root manifest."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        items = bank.get_all_items()
        by_topic: dict[str, list[BankItem]] = defaultdict(list)
        cefr_counts: dict[str, int] = defaultdict(int)

        for it in items:
            by_topic[it.topic_id].append(it)
            cefr_counts[it.cefr] += 1

        # 1. Export topic-specific JSON files
        topic_manifest_entries: list[dict[str, Any]] = []
        for topic_id, topic_items in by_topic.items():
            topic_file = out / f"items_{topic_id}.json"
            serialized_items = [it.model_dump() for it in topic_items]
            with topic_file.open("w", encoding="utf-8") as f:
                json.dump(serialized_items, f, indent=2, ensure_ascii=False)

            topic_manifest_entries.append(
                {
                    "topic_id": topic_id,
                    "item_count": len(topic_items),
                    "file": f"items_{topic_id}.json",
                    "cefr": topic_items[0].cefr if topic_items else "A1",
                }
            )

        # 2. Export consolidated all_items.json
        all_items_file = out / "all_items.json"
        with all_items_file.open("w", encoding="utf-8") as f:
            json.dump([it.model_dump() for it in items], f, indent=2, ensure_ascii=False)

        # 3. Export manifest.json
        manifest = {
            "format_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "total_items": len(items),
            "topic_count": len(by_topic),
            "cefr_breakdown": dict(cefr_counts),
            "topics": sorted(topic_manifest_entries, key=lambda x: str(x["topic_id"])),
        }

        manifest_file = out / "manifest.json"
        with manifest_file.open("w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

        return manifest

    @classmethod
    def validate_export(cls, output_dir: Path | str) -> bool:
        """Verify that exported files adhere to schema and manifest counts."""
        out = Path(output_dir)
        manifest_file = out / "manifest.json"
        if not manifest_file.exists():
            return False

        with manifest_file.open("r", encoding="utf-8") as f:
            manifest = json.load(f)

        if "topics" not in manifest or "total_items" not in manifest:
            return False

        # Check each topic file
        for entry in manifest["topics"]:
            t_file = out / entry["file"]
            if not t_file.exists():
                return False
            with t_file.open("r", encoding="utf-8") as f:
                items_data = json.load(f)
            if len(items_data) != entry["item_count"]:
                return False
            # Verify each item validates as BankItem
            for raw_item in items_data:
                BankItem.model_validate(raw_item)

        return True
