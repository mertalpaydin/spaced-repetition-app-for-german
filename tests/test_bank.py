"""Unit and golden tests for the item bank, storage, migrations, dedup, and export."""

import json
from pathlib import Path

import pytest
from src.bank.dedup import ItemDeduplicator
from src.bank.exporter import BankExporter
from src.bank.migrations import run_migrations
from src.bank.stats import BankStatsCalculator
from src.bank.storage import SqliteItemBank
from src.contracts import BankItem, Distractor, VerificationResult


@pytest.fixture
def temp_bank(tmp_path: Path) -> SqliteItemBank:
    db_file = tmp_path / "test_bank.db"
    return SqliteItemBank(db_file)


@pytest.fixture
def golden_bank_sample_path(data_fixtures_dir: Path) -> Path:
    return data_fixtures_dir / "production" / "golden_bank_sample.json"


@pytest.fixture
def sample_bank_item() -> BankItem:
    return BankItem(
        id="item_001",
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        cefr="A2",
        prompt="Das Buch liegt auf ___ Tisch.",
        cue=None,
        accepted_answers=["dem"],
        distractors=[
            Distractor(text="den", implied_topic_id="kasus_akkusativ_formen"),
            Distractor(text="des", implied_topic_id="kasus_genitiv_formen"),
            Distractor(text="das", implied_topic_id="artikel_bestimmt_nom"),
        ],
        rule_hint="Wechselpräposition auf + Dativ bei Wo?",
        carrier_lemmas=["Buch", "liegen", "Tisch"],
    )


def test_migrations_apply_cleanly_on_fresh_db(tmp_path: Path) -> None:
    """Test that migrations run idempotently on a fresh database."""
    db_file = tmp_path / "fresh_migration.db"
    run_migrations(db_file)
    assert db_file.exists()

    # Re-running migrations should be a no-op without error
    run_migrations(db_file)


def test_sqlite_bank_crud_operations(temp_bank: SqliteItemBank, sample_bank_item: BankItem) -> None:
    """Test inserting, retrieving, and counting items in SQLite bank."""
    assert temp_bank.count_total() == 0

    temp_bank.insert_item(sample_bank_item, source_batch_id="batch_123")
    assert temp_bank.count_total() == 1
    assert temp_bank.count_by_topic("dativ_nach_praeposition") == 1

    retrieved = temp_bank.get_item("item_001")
    assert retrieved is not None
    assert retrieved.id == sample_bank_item.id
    assert retrieved.prompt == sample_bank_item.prompt
    assert len(retrieved.distractors) == 3
    assert len(retrieved.carrier_lemmas) == 3
    assert retrieved.accepted_answers == ["dem"]

    # Non-existent query
    assert temp_bank.get_item("non_existent") is None


def test_sqlite_query_filters_by_topic_and_difficulty(
    temp_bank: SqliteItemBank, sample_bank_item: BankItem
) -> None:
    """Test query filtering by topic_id and difficulty tier."""
    temp_bank.insert_item(sample_bank_item)

    item2 = BankItem(
        id="item_002",
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=2,
        cefr="A2",
        prompt="Der Hund schläft unter ___ großen Tisch.",
        accepted_answers=["dem"],
        distractors=[Distractor(text="den"), Distractor(text="des"), Distractor(text="das")],
    )
    temp_bank.insert_item(item2)

    diff1_items = temp_bank.query_by_difficulty("dativ_nach_praeposition", 1)
    assert len(diff1_items) == 1
    assert diff1_items[0].id == "item_001"

    diff2_items = temp_bank.query_by_difficulty("dativ_nach_praeposition", 2)
    assert len(diff2_items) == 1
    assert diff2_items[0].id == "item_002"

    topic_items = temp_bank.query_by_topic("dativ_nach_praeposition", max_count=1)
    assert len(topic_items) == 1


def test_sqlite_batch_and_verification_logging(temp_bank: SqliteItemBank) -> None:
    """Test inserting batch audit records and verification logs."""
    temp_bank.insert_batch_record(
        batch_id="batch_001",
        model="gemini-3.5-flash-lite",
        total_items=10,
        passed_items=9,
        drop_rate=0.10,
        cost_usd=0.00045,
    )

    ver_res = VerificationResult(passed=True, layer_failed=None, reason=None)
    temp_bank.insert_verification_log(ver_res, item_id="item_001", batch_id="batch_001")


def test_deduplication_rejects_near_duplicate_prompts() -> None:
    """Test exact and token-similarity prompt deduplication."""
    existing = [
        "Das Buch liegt auf ___ Tisch.",
        "Er geht heute in ___ Schule.",
    ]

    # Exact duplicate
    is_dup, reason = ItemDeduplicator.is_duplicate("Das Buch liegt auf ___ Tisch.", existing)
    assert is_dup is True
    assert "Exact" in (reason or "")

    # Near duplicate (same sentence with minor casing/punctuation)
    is_dup2, reason2 = ItemDeduplicator.is_duplicate("das buch liegt auf ___ tisch!", existing)
    assert is_dup2 is True

    # High similarity variation
    is_dup3, reason3 = ItemDeduplicator.is_duplicate(
        "Das Buch liegt hier auf ___ Tisch.", existing, threshold=0.75
    )
    assert is_dup3 is True

    # Novel sentence
    is_dup4, reason4 = ItemDeduplicator.is_duplicate("Die Katze schläft unter ___ Bett.", existing)
    assert is_dup4 is False


def test_json_export_and_validation(
    temp_bank: SqliteItemBank, sample_bank_item: BankItem, tmp_path: Path
) -> None:
    """Test exporting SQLite bank to topic files and manifest validation."""
    temp_bank.insert_item(sample_bank_item)

    export_dir = tmp_path / "export"
    manifest = BankExporter.export_to_directory(temp_bank, export_dir)

    assert manifest["total_items"] == 1
    assert manifest["topic_count"] == 1
    assert (export_dir / "manifest.json").exists()
    assert (export_dir / "all_items.json").exists()
    assert (export_dir / "items_dativ_nach_praeposition.json").exists()

    is_valid = BankExporter.validate_export(export_dir)
    assert is_valid is True


def test_bank_stats_computes_accurate_coverage(
    temp_bank: SqliteItemBank, sample_bank_item: BankItem
) -> None:
    """Test BankStatsCalculator summary metrics."""
    temp_bank.insert_item(sample_bank_item)

    summary = BankStatsCalculator.compute_summary(temp_bank)
    assert summary.total_items == 1
    assert summary.total_topics_covered == 1
    assert summary.items_per_cefr["A2"] == 1
    assert summary.items_per_difficulty[1] == 1
    assert summary.avg_items_per_topic == 1.0


@pytest.mark.golden
def test_golden_bank_fixture_loads_valid_bank_items(
    golden_bank_sample_path: Path,
) -> None:
    """Verify that all items in golden_bank_sample.json parse cleanly as BankItem."""
    assert golden_bank_sample_path.exists()
    with golden_bank_sample_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    assert len(data) >= 8
    for raw in data:
        item = BankItem.model_validate(raw)
        assert len(item.accepted_answers) >= 1
        assert len(item.distractors) == 3
        assert item.cefr in ["A1", "A2", "B1", "B2"]
