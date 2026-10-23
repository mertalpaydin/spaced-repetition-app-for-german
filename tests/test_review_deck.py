"""The review tooling: batches that skip what was read, and findings that
become curated-list entries with reasons."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from scripts import review_deck
from src.contracts import GapSpan, PhraseCard, PhraseUnit
from src.phrases.export import export_deck


def _unit(n: int, kind: str = "verb_prep", requested_order: int | None = None) -> PhraseUnit:
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
        requested_order=requested_order,
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


def test_a_band_includes_a_requested_unit_from_below_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A requested unit is taught before the whole mined pool, so a band that
    honoured the corpus rank alone would review cards the learner meets in
    months and skip cards he meets tomorrow (owner, 2026-10-08)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "docs/audits/phase-1-review").mkdir(parents=True)
    units = [_unit(1), _unit(2), _unit(9746, requested_order=3)]
    export_deck(
        units,
        [_card(u) for u in units],
        tmp_path / "deck",
        now=lambda: datetime(2026, 10, 8, tzinfo=UTC),
    )

    stats = review_deck.write_batches(
        tmp_path / "batches",
        deck_dir=tmp_path / "deck",
        everything=False,
        card_batch=10,
        unit_batch=10,
        max_rank=2,
    )
    assert stats["cards"] == 3 and stats["units"] == 3
    assert "unit 9746" in (tmp_path / "batches/cards_000.txt").read_text(encoding="utf-8")


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
        "held_back_rule_1": 0,
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


def test_apply_keeps_hand_written_overrides_it_did_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found 2026-10-07: apply rebuilds unit_overrides.yaml from lines whose
    key is DOUBLE QUOTED and silently deleted everything else, comments and
    bare-key entries included. Two hand-written glosses ("das Land" is a
    country, "der Fall" is a case) vanished after three rounds, and the only
    symptom was the audit reporting them missing again."""
    phrases = tmp_path / "phrases"
    phrases.mkdir()
    overrides = phrases / "unit_overrides.yaml"
    overrides.write_text(
        "# Reviewer corrections to units: case, citation form, CEFR level. Applied\n"
        "# after the unit decision; the key is the miner's lowercase lemma key.\n"
        "# Added by hand: the model keeps glossing these with the German word.\n"
        "- {key: land, gloss_en: country}\n"
        '- {key: "warten auf", case: "Akk"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(review_deck, "PHRASES_DIR", phrases)
    monkeypatch.setattr(review_deck, "AUDIT_DIR", tmp_path / "audit")

    review_deck.merge_overrides({})

    text = overrides.read_text(encoding="utf-8")
    assert "- {key: land, gloss_en: country}" in text, "a bare key must survive the rewrite"
    assert '- {key: "warten auf", case: "Akk"}' in text
    assert "Added by hand" in text, "comments must survive the rewrite"


def test_merge_overrides_run_twice_writes_the_header_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found 2026-10-22: the header is two lines and only the first was
    recognised, so the second was carried through as a hand-written comment
    and written again under a fresh header. Seventeen copies had piled up."""
    phrases = tmp_path / "phrases"
    phrases.mkdir()
    overrides = phrases / "unit_overrides.yaml"
    overrides.write_text("- {key: land, gloss_en: country}\n", encoding="utf-8")
    monkeypatch.setattr(review_deck, "PHRASES_DIR", phrases)

    review_deck.merge_overrides({})
    review_deck.merge_overrides({})

    text = overrides.read_text(encoding="utf-8")
    assert text.count("the miner's lowercase lemma key") == 1


def test_merge_keeps_every_field_of_an_entry_it_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found 2026-10-20: "statt" carried kind: adverb, a review round applied
    fix_cefr:B1, and the entry came back as cefr alone. The merge read the
    existing fields through a whitelist, (case|display|cefr|gloss_en), and
    kind was not on it, so the correction made an hour earlier was dropped
    and the word went back to being taught as an adjective."""
    phrases = tmp_path / "phrases"
    phrases.mkdir()
    overrides = phrases / "unit_overrides.yaml"
    overrides.write_text(
        '- {key: "statt", kind: "adverb"}\n'
        '- {key: "ewig leben", display: "ewiges Leben", kind: "adj_noun"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(review_deck, "PHRASES_DIR", phrases)

    review_deck.merge_overrides({"statt": {"cefr": "B1"}, "ewig leben": {"cefr": "B2"}})

    text = overrides.read_text(encoding="utf-8")
    assert '- {key: "statt", cefr: "B1", kind: "adverb"}' in text
    assert '- {key: "ewig leben", cefr: "B2", display: "ewiges Leben", kind: "adj_noun"}' in text
    # and a field the round DOES set still wins over the old value
    review_deck.merge_overrides({"statt": {"cefr": "B2"}})
    assert 'cefr: "B2", kind: "adverb"' in overrides.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("note", "expected"),
    [
        # real reviewer notes, rounds top1000f and req1020b
        ("dazu is a pronominal adverb, not an adjective", (True, "adverb")),
        (
            "'heutzutage' is strictly an adverb, but the unit is labeled 'adjective'",
            (True, "adverb"),
        ),
        ("aufgrund is a preposition, not an adjective", (True, "adverb")),
        ("wieso is an interrogative adverb, not an adjective", (True, "adverb")),
        ("solche is a demonstrative pronoun, not an adjective", (True, None)),
        ("Unit specifies adjective but 'bloß' is used as a modal particle.", (True, "adverb")),
        ('"daraufhin" is a pronominal adverb, not an adjective.', (True, "adverb")),
        (
            "Unit specifies adverb but 'bereit' is used as a predicative adjective.",
            (True, "adjective"),
        ),
        # round words1022b: the card is a normal use of the requested word
        (
            "In 'dir gegenüber', 'gegenüber' is a postposition governing the dative pronoun "
            "'dir', not an adverb.",
            (True, "adverb"),
        ),
        # ... but a construction the word sits inside IS about the card
        (
            "Here 'wohl' is part of the reflexive verb phrase 'sich wohl fühlen', "
            "not the modal adverb 'wohl'.",
            (False, None),
        ),
        # about the card, not the label: the word is inside a longer unit
        (
            "'außer' is a preposition in the fixed phrase 'außer Atem', not an adverb.",
            (False, None),
        ),
        ("Typo in sentence: 'Ergebnisssen' has three 's'.", (False, None)),
    ],
)
def test_kind_complaint_tells_a_label_problem_from_a_card_problem(
    note: str, expected: tuple[bool, str | None]
) -> None:
    assert review_deck.kind_complaint(note) == expected


def test_apply_never_drops_a_requested_unit_and_corrects_its_kind(repo: Path) -> None:
    """Rule 1, enforced where the drops happen. Until 2026-10-22 it lived in a
    scratch script, so a round applied without that script could delete the
    owner's words; round top1000f proposed deleting forty-nine of them."""
    units = [_unit(1), _unit(2, requested_order=1)]
    export_deck(
        units,
        [_card(u) for u in units],
        repo / "deck",
        now=lambda: datetime(2026, 10, 22, tzinfo=UTC),
    )
    findings = repo / "batches" / "findings"
    findings.mkdir(parents=True)
    (findings / "cards_000.jsonl").write_text(
        json.dumps(
            {
                "card_id": "000000000002",
                "unit": "unit 2",
                "category": "WRONG_UNIT",
                "action": "drop_card",
                "note": "unit is a pronominal adverb, not an adjective",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (findings / "units_00.jsonl").write_text(
        "".join(
            json.dumps(r) + "\n"
            for r in [
                {
                    "unit_id": "vp:unit_2",
                    "category": "BAD_UNIT",
                    "action": "drop_unit",
                    "note": "x",
                },
                {
                    "unit_id": "vp:unit_1",
                    "category": "BAD_UNIT",
                    "action": "drop_unit",
                    "note": "x",
                },
            ]
        ),
        encoding="utf-8",
    )
    stats = review_deck.apply_findings(findings, round_label="t", deck_dir=repo / "deck")

    exclude = (repo / "data/phrases/exclude.yaml").read_text(encoding="utf-8")
    assert '"unit 1"' in exclude or "unit 1" in exclude, "a mined unit can still be dropped"
    assert "unit 2" not in exclude, "a requested unit is never dropped"
    assert "000000000002" not in (repo / "data/phrases/excluded_cards.yaml").read_text(
        encoding="utf-8"
    ), "a kind complaint is not a reason to drop the card"
    assert 'kind: "adverb"' in (repo / "data/phrases/unit_overrides.yaml").read_text(
        encoding="utf-8"
    ), "the kind complaint becomes a kind correction"
    assert stats["held_back_rule_1"] == 2
