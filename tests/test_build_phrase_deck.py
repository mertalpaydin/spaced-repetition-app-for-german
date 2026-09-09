"""End to end over a 300-line corpus sample, against golden outputs.

The golden files are regenerated only by a commit that explains why the
expected units or cards changed (CLAUDE.md section 7).
"""

import json
from pathlib import Path

import pytest
from scripts.build_phrase_deck import main
from src.phrases import parse

FIXTURES = Path("data/fixtures/phrases")

requires_model = pytest.mark.skipif(
    not parse.parser_available(), reason="de_core_news_sm is not installed"
)


def _units_summary(path: Path) -> list[dict[str, object]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [
        {k: r[k] for k in ("unit_id", "kind", "rank", "sentence_count", "case", "trivial")}
        for r in rows
    ]


def _cards_summary(path: Path) -> list[dict[str, object]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return sorted(
        (
            {k: r[k] for k in ("card_id", "unit_id", "sentence_de", "answers", "form_key")}
            for r in rows
        ),
        key=lambda r: str(r["card_id"]),
    )


@requires_model
@pytest.mark.golden
def test_build_over_the_sample_corpus_matches_the_golden_outputs(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    out_dir = tmp_path / "deck"
    common = ["--build-dir", str(build_dir), "--phrases-dir", "data/phrases"]
    assert (
        main(
            [
                "--stage",
                "parse",
                "--tatoeba",
                str(FIXTURES / "sample_corpus.tsv"),
                "--skip-leipzig",
                "--no-default-extras",
                *common,
            ]
        )
        == 0
    )
    assert main(["--stage", "mine", *common]) == 0
    assert main(["--stage", "cards", "--store", str(FIXTURES / "sample_store.jsonl"), *common]) == 0
    assert main(["--stage", "export", "--out", str(out_dir), *common]) == 0
    assert main(["--check", "--out", str(out_dir)]) == 0

    expected_units = json.loads((FIXTURES / "expected_units.json").read_text(encoding="utf-8"))
    expected_cards = json.loads((FIXTURES / "expected_cards.json").read_text(encoding="utf-8"))
    assert _units_summary(build_dir / "units.jsonl") == expected_units
    assert _cards_summary(build_dir / "cards.jsonl") == expected_cards


def test_missing_corpus_file_is_an_error_not_a_smaller_deck(tmp_path: Path) -> None:
    from scripts.build_phrase_deck import read_corpora

    with pytest.raises(FileNotFoundError):
        read_corpora(tmp_path / "absent.tsv", None, limit=10, seed=1)


def test_contexts_stage_refuses_without_the_approval_flag(tmp_path: Path) -> None:
    from src.contracts import GapSpan, PhraseCard, PhraseUnit

    build_dir = tmp_path / "build"
    build_dir.mkdir()
    unit = PhraseUnit(
        unit_id="cn:trotzdem",
        kind="connector",
        lemma_key="trotzdem",
        parts=["trotzdem"],
        display_de="trotzdem",
        sentence_count=1,
        rank=1,
        source="curated",
        card_count=1,
    )
    card = PhraseCard(
        card_id="abcdefabcdef",
        unit_id=unit.unit_id,
        kind="connector",
        sentence_de="Trotzdem kam sie.",
        gloss_en="Nevertheless she came.",
        gloss_source="azure",
        gaps=[GapSpan(start=0, end=8, answer="Trotzdem", token_index=0)],
        answers=["Trotzdem"],
        form_key="initial",
        corpus_source="tatoeba",
        corpus_line_id="1",
        needs_context=True,
    )
    (build_dir / "units.jsonl").write_text(unit.model_dump_json() + "\n", encoding="utf-8")
    (build_dir / "cards.jsonl").write_text(card.model_dump_json() + "\n", encoding="utf-8")
    common = [
        "--stage",
        "contexts",
        "--build-dir",
        str(build_dir),
        "--contexts",
        str(tmp_path / "c.jsonl"),
    ]
    assert main(common) == 0  # no --generate-contexts: a dry report only
    assert main([*common, "--generate-contexts"]) == 2  # refused, nothing written
    assert not (tmp_path / "c.jsonl").exists()
