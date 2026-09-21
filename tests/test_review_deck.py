"""The review tooling: batches that skip what was read, and findings that
become curated-list entries with reasons."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from scripts import review_deck
from src.contracts import GapSpan, PhraseCard, PhraseUnit
from src.phrases.export import export_deck


def _unit(n: int, kind: str = "verb_prep") -> PhraseUnit:
    return PhraseUnit(
        unit_id=f"vp:unit_{n}",
        kind=kind,  # type: ignore[arg-type]
        lemma_key=f"unit {n}",
        parts=["unit", str(n)],
        display_de=f"unit {n}",
        case="Akk",
        sentence_count=10,
        rank=n,
        source="mined",
        card_count=1,
    )


def _card(unit: PhraseUnit) -> PhraseCard:
    text = f"Satz {unit.rank} hier."
    return PhraseCard(
        card_id=f"{unit.rank:012d}",
        unit_id=unit.unit_id,
        kind=unit.kind,
        sentence_de=text,
        gloss_en="gloss",
        gloss_source="azure",
        gaps=[GapSpan(start=0, end=4, answer="Satz", token_index=0)],
        answers=["Satz"],
        form_key="",
        corpus_source="tatoeba",
        corpus_line_id=str(unit.rank),
    )


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway repo layout so the script's fixed paths point at tmp."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data/phrases").mkdir(parents=True)
    (tmp_path / "data/phrases/exclude.yaml").write_text("- old\n", encoding="utf-8")
    (tmp_path / "data/phrases/excluded_cards.yaml").write_text("", encoding="utf-8")
    (tmp_path / "docs/audits/phase-1-review").mkdir(parents=True)
    units = [_unit(1), _unit(2), _unit(3)]
    export_deck(
        units,
        [_card(u) for u in units],
        tmp_path / "deck",
        now=lambda: datetime(2026, 9, 9, tzinfo=UTC),
    )
    return tmp_path


def test_batches_skip_cards_and_units_already_reviewed(repo: Path) -> None:
    (repo / "docs/audits/phase-1-review/reviewed-card-ids-round-1.txt").write_text(
        "000000000001\n", encoding="utf-8"
    )
    (repo / "docs/audits/phase-1-review/reviewed-unit-ids-round-1.txt").write_text(
        "vp:unit_1\nvp:unit_2\n", encoding="utf-8"
    )
    stats = review_deck.write_batches(
        repo / "batches", deck_dir=repo / "deck", everything=False, card_batch=1, unit_batch=10
    )
    assert stats["cards"] == 2 and stats["card_batches"] == 2
    assert stats["units"] == 1
    first = (repo / "batches/cards_000.txt").read_text(encoding="utf-8")
    assert first.startswith("000000000002\tverb_prep\tunit 2 +Akk\t[Satz] 2 hier.\tgloss")


def test_apply_writes_reasons_and_records_the_round(repo: Path) -> None:
    findings = repo / "batches/findings"
    findings.mkdir(parents=True)
    (repo / "batches/units_00.txt").write_text("vp:unit_3\tverb_prep\n", encoding="utf-8")
    # The batch files are what a reviewer was actually shown. Card 3 was not
    # among them, so the round must not record it as reviewed.
    (repo / "batches/cards_000.txt").write_text(
        "000000000001\tverb_prep\tunit 1\t[Satz] 1 hier.\tgloss\n"
        "000000000002\tverb_prep\tunit 2\t[Satz] 2 hier.\tgloss\n",
        encoding="utf-8",
    )
    (findings / "cards_000.jsonl").write_text(
        json.dumps(
            {
                "card_id": "000000000001",
                "unit": "unit 1 +Akk",
                "category": "BAD_SENTENCE",
                "severity": "low",
                "note": "fragment",
                "action": "drop_card",
            }
        )
        + "\n"
        + json.dumps(
            {
                "card_id": "000000000002",
                "unit": "unit 2 +Akk",
                "category": "BAD_UNIT",
                "severity": "high",
                "note": "not a phrase",
                "action": "drop_card",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (findings / "units_00.jsonl").write_text(
        json.dumps(
            {
                "unit_id": "vp:unit_3",
                "unit": "unit 3",
                "category": "WRONG_CASE",
                "severity": "high",
                "note": "dative",
                "action": "fix_case:Dat",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    stats = review_deck.apply_findings(findings, round_label="t", deck_dir=repo / "deck")
    assert stats == {
        "card_findings": 2,
        "unit_findings": 1,
        "drop_units": 1,
        "drop_cards": 1,
        "overrides": 1,
        "unresolved": 0,
    }
    exclude = (repo / "data/phrases/exclude.yaml").read_text(encoding="utf-8")
    assert '- "unit 2"  # BAD_UNIT: not a phrase' in exclude
    cards = (repo / "data/phrases/excluded_cards.yaml").read_text(encoding="utf-8")
    assert "- 000000000001  # BAD_SENTENCE: fragment" in cards
    overrides = (repo / "data/phrases/unit_overrides.yaml").read_text(encoding="utf-8")
    assert '- {key: "unit 3", case: "Dat"}' in overrides
    audit = repo / "docs/audits/phase-1-review"
    assert (audit / "findings-round-t.jsonl").exists()
    assert (audit / "reviewed-card-ids-round-t.txt").read_text().split() == [
        "000000000001",
        "000000000002",
    ]
    assert (audit / "reviewed-unit-ids-round-t.txt").read_text().split() == ["vp:unit_3"]


def test_apply_merges_an_existing_override_instead_of_replacing_it(repo: Path) -> None:
    (repo / "data/phrases/unit_overrides.yaml").write_text(
        '# a\n# b\n- {key: "unit 3", cefr: "B2"}\n', encoding="utf-8"
    )
    findings = repo / "batches/findings"
    findings.mkdir(parents=True)
    (findings / "units_00.jsonl").write_text(
        json.dumps(
            {
                "unit_id": "vp:unit_3",
                "unit": "unit 3",
                "category": "WRONG_CASE",
                "severity": "high",
                "note": "dative",
                "action": "fix_case:Dat",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    review_deck.apply_findings(findings, round_label="t2", deck_dir=repo / "deck")
    overrides = (repo / "data/phrases/unit_overrides.yaml").read_text(encoding="utf-8")
    assert '- {key: "unit 3", case: "Dat", cefr: "B2"}' in overrides
