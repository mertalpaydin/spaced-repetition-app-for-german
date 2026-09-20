"""Matching a word list against the deck: normalisation, the probes, and the
classification of what the list holds."""

from pathlib import Path

import pytest
from scripts.rank_wordlist import Index, classify, clean, main, stem_variants
from src.contracts import PhraseUnit


def _unit(unit_id: str, kind: str, lemma_key: str, rank: int) -> PhraseUnit:
    return PhraseUnit(
        unit_id=unit_id,
        kind=kind,  # type: ignore[arg-type]
        lemma_key=lemma_key,
        parts=lemma_key.split(),
        display_de=lemma_key,
        sentence_count=10,
        rank=rank,
        source="mined",
        card_count=1,
    )


UNITS = [
    _unit("nn:frage", "noun", "frage", 1),
    _unit("vb:stellen", "verb", "stellen", 2),
    _unit("nv:frage_stellen", "noun_verb", "frage stellen", 3),
    _unit("rv:sich_interessieren_fuer", "reflexive_verb", "sich interessieren für", 4),
]


def test_clean_folds_the_non_breaking_space_an_export_leaves_behind() -> None:
    assert clean("dieselbe\u00a0") == "dieselbe"
    assert clean("  auf   jeden  Fall ") == "auf jeden Fall"


def test_classify_separates_words_verbs_phrases_and_case_drills() -> None:
    assert classify(["NOUN"], ["haushalt"], ["haushalt"]) == "single word"
    assert classify(["VERB"], ["schicken"], ["schicken"]) == "single verb"
    assert classify(["VERB", "ADP"], ["leben", "von"], ["leben", "von"]) == "phrase"
    assert classify(["ADP", "DET", "NOUN"], ["hinter", "dem", "baum"], ["hinter", "baum"]) == (
        "case drill"
    )
    # a reflexive is a phrase however short
    assert classify(["VERB"], ["sich", "freuen"], ["freuen"]) == "phrase"


def test_the_index_finds_a_unit_by_lemma_by_order_and_by_stem() -> None:
    index = Index.of(UNITS)
    assert index.find(["frage"], ["frage"])[0][0].unit_id == "nn:frage"
    # the reflexive is dropped from the probe, and the order does not matter
    found, how = index.find(["interessieren", "für"], ["interessiere", "für"])
    assert found[0].unit_id == "rv:sich_interessieren_fuer" and how
    # a first-person form the tagger left as its own lemma
    found, how = index.find(["interessiere", "für"], ["interessiere", "für"])
    assert found[0].unit_id == "rv:sich_interessieren_fuer" and how == "stem"
    # a single word that is only part of a phrase is reported as such
    assert index.find(["xyz"], ["xyz"]) == ([], "")


def test_stem_variants_offer_the_plausible_infinitives() -> None:
    assert ["interessieren", "für"] in stem_variants(["interessiere", "für"])
    assert ["bedanken", "bei"] in stem_variants(["bedanke", "bei"])


@pytest.mark.skipif(not Path("web/data/deck/manifest.json").exists(), reason="no deck exported yet")
def test_main_writes_a_ranked_csv(tmp_path: Path) -> None:
    source = tmp_path / "list.csv"
    source.write_text("Column 1\nFrage\nwarten auf\n", encoding="utf-8")
    out = tmp_path / "ranked.csv"
    assert main([str(source), "--out", str(out)]) == 0
    written = out.read_text(encoding="utf-8-sig").splitlines()
    assert written[0].startswith("word,list_position,entry_kind,deck_rank")
    assert len(written) == 3
