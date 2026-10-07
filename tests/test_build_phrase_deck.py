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
    # The parse stage reads the gloss store too, to decide which sentences may
    # carry a single-word card. Without this the test would read the owner's
    # real 51 MB store and behave differently on a machine that has none.
    store = ["--store", str(FIXTURES / "sample_store.jsonl")]
    assert (
        main(
            [
                "--stage",
                "parse",
                "--tatoeba",
                str(FIXTURES / "sample_corpus.tsv"),
                "--skip-leipzig",
                "--no-default-extras",
                *store,
                *common,
            ]
        )
        == 0
    )
    assert main(["--stage", "mine", *common]) == 0
    assert main(["--stage", "cards", *store, *common]) == 0
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


def test_unit_gloss_stage_names_the_lane_it_would_spend_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--free-lane-only is the zero-spend choice, so the refusal has to say
    which lane the run would use before the owner approves it."""
    from src.contracts import PhraseUnit

    build_dir = tmp_path / "build"
    build_dir.mkdir()
    unit = PhraseUnit(
        unit_id="nn:frage",
        kind="noun",
        lemma_key="frage",
        parts=["frage"],
        display_de="die Frage",
        sentence_count=400,
        rank=12,
        source="mined",
        card_count=1,
    )
    (build_dir / "units.jsonl").write_text(unit.model_dump_json() + "\n", encoding="utf-8")
    common = [
        "--stage",
        "unit-glosses",
        "--build-dir",
        str(build_dir),
        "--unit-glosses",
        str(tmp_path / "g.jsonl"),
        "--generate-unit-glosses",
    ]
    assert main(common) == 2
    assert "free lane first, paid overflow" in capsys.readouterr().out
    assert main([*common, "--free-lane-only"]) == 2
    assert "free lane only" in capsys.readouterr().out
    assert not (tmp_path / "g.jsonl").exists()


@requires_model
def test_requests_stage_finds_carriers_for_a_word_with_no_glossed_sentence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The word detector emits only from glossed sentences, so a requested
    word whose corpus sentences are all untranslated produces nothing at all.
    This stage is the exception it needs: it finds those sentences, writes
    occurrences for them, and queues them for the monthly Azure job."""
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    corpus = tmp_path / "corpus.tsv"
    corpus.write_text(
        "\n".join(
            [
                "1\tdeu\tDas Hähnchen im Ofen ist endlich fertig.",
                "2\tdeu\tSie hat gestern das ganze Hähnchen alleine gegessen.",
                # matches the stem, teaches a different lemma: dropped by the
                # parse, which is the whole reason the prefilter may be loose
                "3\tdeu\tDie Hähnchenbrust war gestern leider viel zu trocken.",
                # a sentence about nothing that was requested
                "4\tdeu\tDer kleine Hund schläft den ganzen Tag im Garten.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (build_dir / "report.json").write_text(
        json.dumps(
            {
                "requested_missing": ["noun:hähnchen", "noun:sorgen", "verb:knuspern"],
                "requested_but_excluded": ["noun:sorgen"],
            }
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "--stage",
                "requests",
                "--tatoeba",
                str(corpus),
                "--skip-leipzig",
                "--no-default-extras",
                "--build-dir",
                str(build_dir),
                "--phrases-dir",
                "data/phrases",
            ]
        )
        == 0
    )
    occurrences = [
        json.loads(line)
        for line in (build_dir / "requested_occurrences.jsonl")
        .read_text(encoding="utf-8")
        .split("\n")
        if line
    ]
    assert {occ["unit_key"] for occ in occurrences} == {"hähnchen"}
    carriers = (build_dir / "requested_carriers.txt").read_text(encoding="utf-8").splitlines()
    assert [line.split("\t")[-1] for line in carriers] == [
        "Das Hähnchen im Ofen ist endlich fertig.",
        "Sie hat gestern das ganze Hähnchen alleine gegessen.",
    ]
    assert all(line.split("\t")[0] == "tatoeba" for line in carriers)
    out = capsys.readouterr().out
    # An excluded key is never hunted for: its occurrences teach a different
    # lemma, which is why it was excluded.
    assert "sorgen" not in out
    # A key the corpus does not use at all is named, not silently missing.
    assert "knuspern" in out and "NOT IN THE CORPUS" in out


def test_requests_stage_is_idempotent_once_the_requests_have_occurrences(tmp_path: Path) -> None:
    """A second run sees the first run's keys as present and must not blank
    the file that made them present."""
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    (build_dir / "report.json").write_text(json.dumps({"requested_missing": []}), encoding="utf-8")
    (build_dir / "requested_occurrences.jsonl").write_text("kept\n", encoding="utf-8")
    assert main(["--stage", "requests", "--build-dir", str(build_dir)]) == 0
    assert (build_dir / "requested_occurrences.jsonl").read_text(encoding="utf-8") == "kept\n"


def test_requests_stage_says_to_mine_first_when_there_is_no_report(tmp_path: Path) -> None:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    assert main(["--stage", "requests", "--build-dir", str(build_dir)]) == 1


def test_an_adverb_unit_claims_its_adjective_occurrences() -> None:
    """Found 2026-10-07: not one of the 430 adverb units in the deck had a
    single card, so "einmal", "oft", "fast" and "bald" were unteachable.

    The word detector files both ADJ and ADV tokens under the provisional
    kind "adjective" and the mine stage settles which a lemma mostly is, so
    an adverb unit is "av:<lemma>" while all of its occurrences map to
    "aj:<lemma>". The card stage matched on that id and found nothing.
    """
    from scripts.build_phrase_deck import match_to_unit
    from src.phrases.occurrences import Occurrence

    occ = Occurrence(
        kind="adjective",
        unit_key="oft",
        parts=["oft"],
        text="Ich gehe oft ins Kino.",
        spans=[(9, 12)],
        surfaces=["oft"],
        token_indices=[2],
        corpus_source="tatoeba",
        line_id="1",
        form_key="adv",
    )
    matched = match_to_unit(occ, {"av:oft"})
    assert matched is not None, "an adverb unit must claim its adjective occurrences"
    unit_id, out = matched
    assert unit_id == "av:oft"
    # Handed over as the unit's own kind, so the card it becomes carries
    # "adverb" rather than the provisional kind.
    assert out.kind == "adverb"

    # The ordinary match still wins, and a lemma no unit wants is refused.
    same_kind = match_to_unit(occ, {"aj:oft"})
    assert same_kind is not None and same_kind[0] == "aj:oft"
    assert match_to_unit(occ, {"nn:oft"}) is None
