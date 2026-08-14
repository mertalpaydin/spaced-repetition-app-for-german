"""Unit tests for Bank Health Auditor and CI/CD workflow configurations."""

from pathlib import Path

from src.audit.bank_health import BankHealthAuditor
from src.bank.storage import SqliteItemBank
from src.contracts import MIN_STOCK_PER_TIER, BankItem, Distractor
from src.taxonomy.loader import load_taxonomy


def test_ci_workflow_files_exist() -> None:
    """Verify GitHub Actions CI/CD workflow files are present."""
    gh_dir = Path(".github/workflows")
    assert (gh_dir / "ci.yml").exists()
    assert (gh_dir / "nightly_batch.yml").exists()
    assert (gh_dir / "deploy_pages.yml").exists()

    ci_content = (gh_dir / "ci.yml").read_text(encoding="utf-8")
    assert "mypy --strict" in ci_content
    assert "--cov-fail-under=85" in ci_content


def test_bank_health_auditor_detects_defects(tmp_path: Path) -> None:
    """Test auditor detecting defective distractors and missing gaps."""
    db_file = tmp_path / "audit_test.db"
    bank = SqliteItemBank(db_file)

    # 1. Clean item
    clean_item = BankItem(
        id="clean_01",
        topic_id="pronomen_personal_nom",
        type="cloze_free",
        difficulty=1,
        cefr="A1",
        prompt="___ heiße Peter.",
        accepted_answers=["Ich"],
        distractors=[Distractor(text="Du"), Distractor(text="Er"), Distractor(text="Wir")],
    )
    bank.insert_item(clean_item)

    # 2. Defective item (duplicate distractors + missing gap)
    bad_item = BankItem(
        id="bad_01",
        topic_id="dativ_nach_praeposition",
        type="cloze_free",
        difficulty=1,
        cefr="A2",
        prompt="Das Buch liegt auf dem Tisch.",  # missing ___
        accepted_answers=["dem"],
        distractors=[Distractor(text="den"), Distractor(text="den"), Distractor(text="den")],
    )
    bank.insert_item(bad_item)

    report = BankHealthAuditor.audit_bank(bank, min_items_per_topic=1)

    assert report.total_items == 2
    assert report.passed_audit is False
    assert len(report.defective_items) >= 2
    assert any("missing gap" in d for d in report.defective_items)
    assert any("duplicate distractors" in d for d in report.defective_items)


def test_bank_health_auditor_passes_clean_bank(tmp_path: Path) -> None:
    """Test auditor passing when all items satisfy constraints."""
    db_file = tmp_path / "clean_audit.db"
    bank = SqliteItemBank(db_file)

    clean_item = BankItem(
        id="clean_02",
        topic_id="pronomen_personal_nom",
        type="cloze_free",
        difficulty=1,
        cefr="A1",
        prompt="___ gehe nach Hause.",
        accepted_answers=["Ich"],
        distractors=[Distractor(text="Du"), Distractor(text="Er"), Distractor(text="Wir")],
    )
    bank.insert_item(clean_item)

    report = BankHealthAuditor.audit_bank(bank, min_items_per_topic=1)
    assert report.passed_audit is True
    assert len(report.defective_items) == 0


def test_min_stock_per_tier_matches_stage5_dod_cold_seed_floor() -> None:
    """The stage 5 DoD requires 12 items/topic cold-seeded A1-B2; the audit floor
    must match it, not a smaller placeholder."""
    assert MIN_STOCK_PER_TIER == 12


def test_bank_health_reports_honest_shortfall_without_fabricating_items(
    tmp_path: Path,
) -> None:
    """A near-empty bank must report the real numeric shortfall against the
    DoD floor rather than silently passing or hiding the gap. Nothing here
    fabricates bank content to close it."""
    db_file = tmp_path / "shortfall_test.db"
    bank = SqliteItemBank(db_file)
    topics = load_taxonomy()

    # Stock exactly one topic to the DoD floor; every other topic is empty.
    fully_stocked_topic = topics[0].id
    for i in range(MIN_STOCK_PER_TIER):
        bank.insert_item(
            BankItem(
                id=f"stocked_{i}",
                topic_id=fully_stocked_topic,
                type="cloze_free",
                difficulty=1,
                cefr="A1",
                prompt=f"___ Satz Nummer {i}.",
                accepted_answers=["Ein"],
                distractors=[Distractor(text="a"), Distractor(text="b"), Distractor(text="c")],
            )
        )

    report = BankHealthAuditor.audit_bank(bank)  # default floor = MIN_STOCK_PER_TIER

    assert report.total_items == MIN_STOCK_PER_TIER
    # Every topic except the one stocked above is understocked.
    assert len(report.understocked_topics) == len(topics) - 1
    expected_shortfall = sum(MIN_STOCK_PER_TIER for t in topics if t.id != fully_stocked_topic)
    assert report.total_shortfall == expected_shortfall
    assert report.total_shortfall > 0
