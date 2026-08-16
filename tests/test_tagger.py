"""Unit tests for src/taxonomy/tagger.py, the spaCy-backed morphological tagger."""

import logging
import sys

import pytest
from src.taxonomy import tagger


@pytest.fixture(autouse=True)
def _clear_model_cache() -> None:
    """Every test gets a clean ``_load_model`` cache, before and after.

    ``_load_model`` is process-global (``lru_cache(maxsize=1)``); without
    this, a test that monkeypatches ``spacy.load`` to simulate a missing
    model would poison every later test's cached result too.
    """
    tagger._load_model.cache_clear()
    yield
    tagger._load_model.cache_clear()


def test_analysis_available_when_model_installed() -> None:
    """The declared ``de_core_news_sm`` dependency must actually load in this
    environment -- if this fails, every other test in this module is moot.
    """
    assert tagger.analysis_available() is True


def test_tag_answer_resolves_dative_masculine_from_sentence_context() -> None:
    """'dem' alone is Dat Masc Sg or Dat Neut Sg; 'Tisch' is masculine, and
    only the full sentence -- not the answer in isolation -- carries that.
    """
    tagged = tagger.tag_answer("Das Buch liegt auf ___ Tisch.", "dem")
    assert tagged is not None
    assert tagged.feats.get("Case") == "Dat"
    assert tagged.feats.get("Gender") == "Masc"
    assert tagged.feats.get("Number") == "Sing"


def test_tag_answer_resolves_attributive_adjective_agreement() -> None:
    tagged = tagger.tag_answer("Sie half einer ___ Dame beim Einkaufen.", "alten")
    assert tagged is not None
    assert tagged.feats.get("Case") == "Dat"
    assert tagged.feats.get("Gender") == "Fem"
    assert tagged.feats.get("Number") == "Sing"


def test_tag_answer_strips_bracketed_cue_before_parsing() -> None:
    """Regression test: leaving a ``cloze_cued`` item's trailing ``(cue)`` in
    the parsed sentence ("Das Auto hat das große (groß) Fenster.") visibly
    confuses the tagger -- it mistags the accusative "das große" as
    nominative and "Fenster" as plural. Confirmed directly against spaCy
    before ``_fill_gap`` was made to strip the cue; this pins the fix.
    """
    tagged = tagger.tag_answer("Das Auto hat das ___ (groß) Fenster.", "große")
    assert tagged is not None
    assert tagged.feats.get("Case") == "Acc"
    assert tagged.feats.get("Gender") == "Neut"
    assert tagged.feats.get("Number") == "Sing"


def test_tag_answer_returns_none_when_prompt_has_no_gap() -> None:
    assert tagger.tag_answer("Das Buch liegt auf dem Tisch.", "dem") is None


def test_tag_answer_returns_none_for_empty_answer() -> None:
    assert tagger.tag_answer("Das Buch liegt auf ___ Tisch.", "   ") is None


def test_load_model_degrades_to_none_when_model_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Windows-owner-has-no-model scenario: ``spacy.load`` raising
    ``OSError`` (spaCy's own signal for "model not found") must degrade to
    ``None``, never propagate.
    """
    import spacy

    def _raise_not_found(name: str, **kwargs: object) -> None:
        raise OSError(f"[E050] Can't find model {name!r}")

    monkeypatch.setattr(spacy, "load", _raise_not_found)
    assert tagger._load_model() is None
    assert tagger.analysis_available() is False
    assert tagger.tag_answer("Das Buch liegt auf ___ Tisch.", "dem") is None


def test_load_model_degrades_to_none_when_spacy_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting a module to ``None`` in ``sys.modules`` makes a subsequent
    ``import`` of it raise ``ImportError`` -- this simulates spaCy itself
    being absent, not just the model.
    """
    monkeypatch.setitem(sys.modules, "spacy", None)
    assert tagger._load_model() is None
    assert tagger.tag_answer("Das Buch liegt auf ___ Tisch.", "dem") is None


def test_missing_model_logs_a_warning_exactly_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Requirement: log the fallback once, clearly. ``lru_cache`` means the
    function body -- including the log call -- runs at most once regardless
    of how many callers ask.
    """
    import spacy

    def _raise_not_found(name: str, **kwargs: object) -> None:
        raise OSError("model missing")

    monkeypatch.setattr(spacy, "load", _raise_not_found)
    with caplog.at_level(logging.WARNING, logger="src.taxonomy.tagger"):
        tagger._load_model()
        tagger._load_model()
        tagger.tag_answer("Das Buch liegt auf ___ Tisch.", "dem")
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
