"""Unit and golden tests for the item bank, storage, migrations, dedup, and export."""

import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from scripts.step2_build_item_bank import ingest_items
from src.bank.dedup import ItemDeduplicator
from src.bank.exporter import (
    EXPORTED_BANK_ITEM_FIELDS,
    BankExporter,
    load_bank_export_item_schema,
    validate_against_json_schema,
)
from src.bank.migrations import run_migrations
from src.bank.stats import BankStatsCalculator
from src.bank.storage import SqliteItemBank
from src.contracts import BankItem, Distractor, VerificationResult
from src.taxonomy.loader import load_taxonomy


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
        gloss_en="The book is lying on the table.",
    )


def test_migrations_apply_cleanly_on_fresh_db(tmp_path: Path) -> None:
    """Test that migrations run idempotently on a fresh database."""
    db_file = tmp_path / "fresh_migration.db"
    run_migrations(db_file)
    assert db_file.exists()

    # Re-running migrations should be a no-op without error
    run_migrations(db_file)


def test_run_migrations_idempotent_preserves_review_log_rows(tmp_path: Path) -> None:
    """Running migrations repeatedly must not duplicate, drop, or reset review_log rows."""
    db_file = tmp_path / "idempotent_migration.db"
    bank = SqliteItemBank(db_file)  # runs migrations once via __init__

    bank.append_review_log(
        item_id="item_001",
        topic_id="dativ_nach_praeposition",
        user_answer="dem",
        is_correct=True,
        hint_level=0,
        fsrs_rating="good",
        mode="duel",
        facet="masculine",
    )

    # Re-running migrations against the now-populated database must be a no-op.
    run_migrations(db_file)
    run_migrations(db_file)

    logs = bank.get_review_logs()
    assert len(logs) == 1
    row = logs[0]
    assert row["mode"] == "duel"
    assert row["facet"] == "masculine"
    assert row["fsrs_rating"] == "good"
    assert isinstance(row["fsrs_rating"], str)


def test_review_log_round_trips_mode_and_facet(temp_bank: SqliteItemBank) -> None:
    """append_review_log/get_review_logs must carry mode and facet through unchanged."""
    temp_bank.append_review_log(
        item_id="item_042",
        topic_id="dativ_nach_praeposition",
        user_answer="",
        is_correct=False,
        hint_level=0,
        fsrs_rating="again",
        mode="recalibration",
        facet="feminine",
    )

    logs = temp_bank.get_review_logs(topic_id="dativ_nach_praeposition")
    assert len(logs) == 1
    assert logs[0]["mode"] == "recalibration"
    assert logs[0]["facet"] == "feminine"
    assert logs[0]["fsrs_rating"] == "again"
    assert logs[0]["is_correct"] == 0

    # Default mode applies when the caller does not specify one.
    temp_bank.append_review_log(
        item_id="item_043",
        topic_id="dativ_nach_praeposition",
        user_answer="dem",
        is_correct=True,
        hint_level=0,
        fsrs_rating="good",
    )
    logs_all = temp_bank.get_review_logs(topic_id="dativ_nach_praeposition")
    assert any(log["mode"] == "review" and log["facet"] is None for log in logs_all)


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


def _make_item(
    item_id: str,
    *,
    topic_id: str = "dativ_nach_praeposition",
    tag_id: str | None = None,
    dimension: str = "grammar",
    difficulty: int = 1,
    prompt: str = "Das Buch liegt auf ___ Tisch.",
    accepted_answers: list[str] | None = None,
    carrier_lemmas: list[str] | None = None,
) -> BankItem:
    return BankItem(
        id=item_id,
        topic_id=topic_id,
        tag_id=tag_id,
        dimension=dimension,  # type: ignore[arg-type]
        type="cloze_free",
        difficulty=difficulty,  # type: ignore[arg-type]
        cefr="A2",
        prompt=prompt,
        accepted_answers=accepted_answers if accepted_answers is not None else ["dem"],
        distractors=[Distractor(text="den"), Distractor(text="des"), Distractor(text="das")],
        carrier_lemmas=carrier_lemmas or [],
    )


def test_accepted_answers_non_empty_and_deduplicated() -> None:
    """BankItem.accepted_answers must reject empty input and deduplicate exact repeats."""
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        _make_item("bad_item", accepted_answers=[])

    item = _make_item("dup_item", accepted_answers=["dem", "dem", "Dem"])
    # Exact duplicates collapse; case-distinct strings are preserved and order kept.
    assert item.accepted_answers == ["dem", "Dem"]


def test_no_item_where_prompt_contains_an_accepted_answer(temp_bank: SqliteItemBank) -> None:
    """The bank's insert-time check must reject a cloze prompt that leaks its own
    accepted answer as a standalone word outside the gap -- the last line of
    defence named in 02-content-pipeline.md stage 5."""
    leaking = _make_item(
        "leak_01",
        prompt="Das Buch liegt auf dem ___ Tisch.",
        accepted_answers=["dem"],
    )
    report = temp_bank.insert([leaking])
    assert report.inserted == 0
    assert report.rejected == 1
    assert "leak_01" in report.rejection_reasons[0]
    assert temp_bank.get_item("leak_01") is None

    # A prompt where the answer only appears inside a longer, unrelated word
    # (not as a standalone token) must not false-positive.
    safe = _make_item(
        "safe_01",
        prompt="Das ist Test Satz Nummer eins mit ___ Lücke.",
        accepted_answers=["a"],
    )
    report2 = temp_bank.insert([safe])
    assert report2.inserted == 1
    assert report2.rejected == 0


def test_insert_is_idempotent_on_item_id(
    temp_bank: SqliteItemBank, sample_bank_item: BankItem
) -> None:
    """Re-inserting an item with an id already in the bank must not raise and
    must not duplicate rows -- it should be a reported no-op."""
    first = temp_bank.insert_item(sample_bank_item)
    assert first is True
    second = temp_bank.insert_item(sample_bank_item)
    assert second is False
    assert temp_bank.count_total() == 1

    report = temp_bank.insert([sample_bank_item, sample_bank_item])
    assert report.inserted == 0
    assert report.duplicates == 2
    assert report.rejected == 0
    assert temp_bank.count_total() == 1


def test_referential_integrity_every_tag_id_exists(temp_bank: SqliteItemBank) -> None:
    """Grammar items must resolve their tag_id to a real taxonomy topic; vocab
    items must resolve their tag_id to one of their own carrier lemmas."""
    topic_ids = {t.id for t in load_taxonomy()}
    known_topic = next(iter(topic_ids))

    grammar_item = _make_item(
        "gram_01", topic_id=known_topic, tag_id=known_topic, dimension="grammar"
    )
    vocab_item = _make_item(
        "vocab_01",
        topic_id="",
        tag_id="Buch",
        dimension="vocab",
        prompt="Ich lese ein ___.",
        accepted_answers=["Buch"],
        carrier_lemmas=["Buch", "lesen"],
    )
    report = temp_bank.insert([grammar_item, vocab_item])
    assert report.inserted == 2

    for item in temp_bank.get_all_items():
        assert item.tag_id is not None
        if item.dimension == "grammar":
            assert item.tag_id in topic_ids
        else:
            assert item.tag_id in item.carrier_lemmas


def test_stock_count_matches_unseen_items_for_tag(temp_bank: SqliteItemBank) -> None:
    """stock() must count only items the learner has never attempted."""
    tag = "dativ_nach_praeposition"
    items = [_make_item(f"stock_{i}", topic_id=tag, tag_id=tag, difficulty=1) for i in range(4)]
    report = temp_bank.insert(items)
    assert report.inserted == 4

    assert temp_bank.stock(tag, 1) == 4

    # Mark two items as seen via review_logs.
    temp_bank.append_review_log(
        item_id="stock_0",
        topic_id=tag,
        user_answer="dem",
        is_correct=True,
        hint_level=0,
        fsrs_rating="good",
    )
    temp_bank.append_review_log(
        item_id="stock_1",
        topic_id=tag,
        user_answer="dem",
        is_correct=False,
        hint_level=1,
        fsrs_rating="again",
    )

    assert temp_bank.stock(tag, 1) == 2
    # A different difficulty tier for the same tag is unaffected.
    assert temp_bank.stock(tag, 2) == 0


def test_export_round_trip_is_lossless(temp_bank: SqliteItemBank) -> None:
    """sqlite -> BankItem -> sqlite must preserve every field, including
    None-vs-empty-string distinctions and accepted_answers ordering."""
    item = BankItem(
        id="lossless_01",
        topic_id="dativ_nach_praeposition",
        tag_id="dativ_nach_praeposition",
        dimension="grammar",
        type="cloze_free",
        difficulty=2,
        cefr="B1",
        prompt="Er wohnt seit ___ Jahren hier.",
        cue="seit + Dativ",
        accepted_answers=["drei", "3"],
        distractors=[
            Distractor(text="drei", implied_topic_id=None),
            Distractor(text="vier", implied_topic_id="kasus_akkusativ_formen"),
            Distractor(text="fünf", implied_topic_id=None),
        ],
        rule_hint=None,
        facet="Number=Plur",
        confusion_group="temporal_dauer",
        block_id="block_1",
        block_position=2,
        domain="alltag",
        carrier_lemmas=["wohnen", "Jahr"],
        source_sentence_id="sent_042",
        gloss_en="He has been living here for three years.",
    )
    temp_bank.insert_item(item)
    round_tripped = temp_bank.get_item("lossless_01")
    assert round_tripped is not None
    assert round_tripped.model_dump() == item.model_dump()


def test_bank_round_trip_preserves_gloss_en(temp_bank: SqliteItemBank) -> None:
    """``gloss_en`` must survive sqlite, and a row banked without one must come
    back as ``None`` rather than an empty string.

    Until migration v4 the ``items`` table had no column for this field at all,
    so every insert silently discarded the English translation the pilot had
    already paid to produce. The client cannot show what the bank threw away
    (TODO.md 5.1 step 3), which makes this the first of two places the field
    was being dropped; ``test_export_carries_gloss_en_to_the_client_bundle``
    covers the second.
    """
    glossed = _make_item("gloss_present_01")
    temp_bank.insert_item(glossed.model_copy(update={"gloss_en": "The book is on the table."}))
    unglossed = _make_item("gloss_absent_01")
    temp_bank.insert_item(unglossed)

    present = temp_bank.get_item("gloss_present_01")
    absent = temp_bank.get_item("gloss_absent_01")
    assert present is not None and absent is not None
    assert present.gloss_en == "The book is on the table."
    assert absent.gloss_en is None


def test_export_carries_gloss_en_to_the_client_bundle(
    temp_bank: SqliteItemBank, tmp_path: Path
) -> None:
    """The exported JSON the PWA fetches must carry ``gloss_en``.

    ``EXPORTED_BANK_ITEM_FIELDS`` is an explicit allowlist, so a field absent
    from it is dropped on the way to the browser however faithfully the rest of
    the pipeline carried it. The gloss is learner-facing (TODO.md section 4,
    "Every exercise shows its English translation, always"), so it belongs in
    the bundle; an item banked without one exports an explicit ``null``, which
    is what web/app.js degrades on.
    """
    assert "gloss_en" in EXPORTED_BANK_ITEM_FIELDS
    glossed = _make_item("exported_gloss_01").model_copy(
        update={"gloss_en": "The book is on the table."}
    )
    temp_bank.insert_item(glossed)
    temp_bank.insert_item(_make_item("exported_gloss_02"))

    export_dir = tmp_path / "export"
    BankExporter.export_to_directory(temp_bank, export_dir)

    with (export_dir / "all_items.json").open("r", encoding="utf-8") as f:
        exported = {item["id"]: item for item in json.load(f)}

    assert exported["exported_gloss_01"]["gloss_en"] == "The book is on the table."
    assert "gloss_en" in exported["exported_gloss_02"]
    assert exported["exported_gloss_02"]["gloss_en"] is None
    assert BankExporter.validate_export(export_dir) is True


def test_export_contains_no_internal_fields(temp_bank: SqliteItemBank, tmp_path: Path) -> None:
    """Exported JSON must never contain fields outside the client allowlist --
    rejection reasons, cost data, and audit flags stamped on a BankItem
    earlier in the pipeline must not ship to the client bundle."""
    item = _make_item("internal_01")
    # Simulate internal pipeline metadata accidentally left attached, thanks
    # to BankItem's extra="allow" config.
    leaky_item = item.model_copy(
        update={
            "verification_cost_usd": 0.004,
            "rejection_reason": "topic_leak",
            "audit_flagged": True,
        }
    )
    temp_bank.insert_item(leaky_item)

    export_dir = tmp_path / "export"
    BankExporter.export_to_directory(temp_bank, export_dir)

    all_items_file = export_dir / "all_items.json"
    with all_items_file.open("r", encoding="utf-8") as f:
        exported = json.load(f)

    assert len(exported) == 1
    exported_keys = set(exported[0].keys())
    assert "verification_cost_usd" not in exported_keys
    assert "rejection_reason" not in exported_keys
    assert "audit_flagged" not in exported_keys
    assert exported_keys <= EXPORTED_BANK_ITEM_FIELDS


def test_export_delta_returns_exactly_items_after_since_id(tmp_path: Path) -> None:
    """export_delta must return exactly the items inserted after since_id,
    regardless of insert order, over many random orderings (property test)."""

    @given(order=st.permutations(list(range(8))))
    @settings(max_examples=25, deadline=None)
    def _check(order: tuple[int, ...]) -> None:
        db_file = tmp_path / f"delta_{'_'.join(map(str, order))}.db"
        bank = SqliteItemBank(db_file)
        for i in order:
            bank.insert_item(_make_item(f"delta_item_{i}"))

        # since_id anchored on the item inserted third (arbitrary cursor
        # position); everything inserted after it in *insertion* order (not
        # id order) must come back, and nothing inserted before it.
        cursor_item_id = f"delta_item_{order[2]}"
        expected_after = {f"delta_item_{i}" for i in order[3:]}

        delta = bank.export_delta(cursor_item_id)
        assert {it.id for it in delta.items} == expected_after
        assert delta.total_items == len(expected_after)

    _check()


def test_export_delta_empty_when_client_is_current(temp_bank: SqliteItemBank) -> None:
    """A client whose since_id is the most recently inserted item gets an empty delta."""
    for i in range(5):
        temp_bank.insert_item(_make_item(f"current_{i}"))

    delta = temp_bank.export_delta("current_4")
    assert delta.items == []
    assert delta.total_items == 0

    full = temp_bank.export_full()
    assert full.total_items == 5


def test_confusion_group_written_at_ingest_for_every_grammar_item(
    temp_bank: SqliteItemBank, golden_bank_sample_path: Path
) -> None:
    """scripts.step2_build_item_bank.ingest_items must stamp confusion_group
    from the topic onto every grammar item at ingest -- the offline
    minimal-pair fallback cannot derive it at runtime."""
    with golden_bank_sample_path.open("r", encoding="utf-8") as f:
        items_data = json.load(f)

    topics_by_id = {t.id: t for t in load_taxonomy()}
    report = ingest_items(temp_bank, items_data, topics_by_id)
    assert report.rejected == 0

    saw_a_confusion_group = False
    for item in temp_bank.get_all_items():
        topic = topics_by_id.get(item.topic_id)
        assert topic is not None, f"golden fixture item has unknown topic_id {item.topic_id!r}"
        assert item.confusion_group == topic.confusion_group
        assert item.tag_id == item.topic_id
        if topic.confusion_group is not None:
            saw_a_confusion_group = True
    assert saw_a_confusion_group, (
        "expected at least one golden item's topic to have a confusion_group"
    )


@pytest.mark.golden
def test_export_schema_matches_client_expectation(
    temp_bank: SqliteItemBank, sample_bank_item: BankItem, tmp_path: Path
) -> None:
    """Every exported item must validate against the shared export-schema
    fixture (data/fixtures/schemas/bank_export_item.schema.json). The same
    fixture is meant to be asserted from the web test suite (owned by
    another agent) so a server-side export change that breaks the client
    fails here first."""
    temp_bank.insert_item(sample_bank_item)
    export_dir = tmp_path / "export"
    BankExporter.export_to_directory(temp_bank, export_dir)

    schema = load_bank_export_item_schema()
    with (export_dir / "all_items.json").open("r", encoding="utf-8") as f:
        exported_items = json.load(f)

    assert exported_items, "expected at least one exported item to validate against"
    for raw_item in exported_items:
        errors = validate_against_json_schema(raw_item, schema)
        assert errors == [], f"item {raw_item.get('id')} violates export schema: {errors}"
