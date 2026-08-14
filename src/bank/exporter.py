"""Item Bank exporter for serializing bank items to static frontend JSON assets."""

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.bank.storage import SqliteItemBank
from src.contracts import BankItem

# Explicit allowlist of BankItem fields that may ship to the browser bundle.
#
# BankItem sets ``extra="allow"`` (so verification-pipeline code can attach
# working data to an item mid-pipeline), which means a plain ``model_dump()``
# serializes *whatever attributes happen to be set*, not just the declared
# contract fields -- rejection reasons, cost data, or audit flags stamped
# onto an item earlier in the pipeline would ship straight to the client.
# Every field here is part of the stage 5 contract (02-content-pipeline.md)
# or is already consumed by web/app.js (topic_id for topic partitioning,
# rule_hint for hint level 3); nothing else is ever exported.
EXPORTED_BANK_ITEM_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "topic_id",
        "tag_id",
        "dimension",
        "type",
        "cefr",
        "difficulty",
        "prompt",
        "cue",
        "rule_hint",
        "accepted_answers",
        "distractors",
        "block_id",
        "block_position",
        "confusion_group",
        "facet",
        "carrier_lemmas",
        "domain",
        "source_sentence_id",
    }
)


def _export_dict(item: BankItem) -> dict[str, Any]:
    """Serialize a single BankItem to only its allowlisted, client-facing fields."""
    return item.model_dump(include=set(EXPORTED_BANK_ITEM_FIELDS))


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
            serialized_items = [_export_dict(it) for it in topic_items]
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
            json.dump([_export_dict(it) for it in items], f, indent=2, ensure_ascii=False)

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
            # Verify each item validates as BankItem, and that no field
            # outside the client-facing allowlist was exported.
            for raw_item in items_data:
                BankItem.model_validate(raw_item)
                if not set(raw_item.keys()) <= EXPORTED_BANK_ITEM_FIELDS:
                    return False

        return True


# ==============================================================================
# Shared export-schema fixture validation
#
# data/fixtures/schemas/bank_export_item.schema.json is the linchpin fixture
# named by 02-content-pipeline.md stage 5: both the Python bank suite and the
# web suite must assert an exported item against this SAME document, so a
# server-side export change that breaks the client fails on the Python side
# first instead of drifting silently until a client bug report weeks later.
#
# There is no ``jsonschema`` dependency in this repo (see pyproject.toml),
# and part of the fixture's purpose is to stay simple enough that the
# dependency-free web/JS test suite can assert against it too. So this is a
# deliberately minimal structural validator -- it supports exactly the
# vocabulary the fixture uses (``type`` incl. nullable unions, ``enum``,
# ``required``, ``properties``, ``items``, ``additionalProperties: false``)
# and nothing more.
# ==============================================================================

BANK_EXPORT_ITEM_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "fixtures"
    / "schemas"
    / "bank_export_item.schema.json"
)


def _matches_json_type(value: Any, type_spec: str | list[str]) -> bool:
    """Check ``value`` against a JSON-Schema ``type`` (or list of types)."""
    types = type_spec if isinstance(type_spec, list) else [type_spec]
    for t in types:
        if t == "null" and value is None:
            return True
        if t == "string" and isinstance(value, str):
            return True
        if t == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if t == "array" and isinstance(value, list):
            return True
        if t == "object" and isinstance(value, dict):
            return True
    return False


def validate_against_json_schema(instance: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Validate ``instance`` against the minimal schema subset described above.

    Returns a list of human-readable violation strings; empty means valid.
    """
    errors: list[str] = []
    for field in schema.get("required", []):
        if field not in instance:
            errors.append(f"missing required field '{field}'")

    properties: dict[str, Any] = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        unknown = set(instance.keys()) - set(properties.keys())
        if unknown:
            errors.append(f"unexpected fields: {sorted(unknown)}")

    for key, value in instance.items():
        prop_schema = properties.get(key)
        if prop_schema is None:
            continue
        if "type" in prop_schema and not _matches_json_type(value, prop_schema["type"]):
            errors.append(f"field '{key}' has wrong type (expected {prop_schema['type']})")
            continue
        if "enum" in prop_schema and value is not None and value not in prop_schema["enum"]:
            errors.append(f"field '{key}' value {value!r} not in {prop_schema['enum']}")
        is_array_field = prop_schema.get("type") == "array" and "items" in prop_schema
        if is_array_field and isinstance(value, list):
            item_schema = prop_schema["items"]
            for i, elem in enumerate(value):
                if item_schema.get("type") == "object" and isinstance(elem, dict):
                    for sub_err in validate_against_json_schema(elem, item_schema):
                        errors.append(f"{key}[{i}]: {sub_err}")
                elif "type" in item_schema and not _matches_json_type(elem, item_schema["type"]):
                    errors.append(f"field '{key}[{i}]' has wrong type")

    return errors


def load_bank_export_item_schema() -> dict[str, Any]:
    """Load the shared bank-export item schema fixture."""
    with BANK_EXPORT_ITEM_SCHEMA_PATH.open("r", encoding="utf-8") as f:
        result: dict[str, Any] = json.load(f)
        return result
