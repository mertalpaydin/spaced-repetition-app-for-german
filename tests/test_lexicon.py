"""Unit tests for the vocabulary store, frequency bander, and PDF extractor."""

from pathlib import Path

import pytest
from src.lexicon.extractor import WordlistPdfExtractor
from src.lexicon.frequency import FrequencyBander
from src.lexicon.vocabulary import VocabularyStore


@pytest.fixture
def vocab_store(data_fixtures_dir: Path) -> VocabularyStore:
    """Load vocabulary store from golden fixture if present, or create a mock store."""
    vocab_path = data_fixtures_dir / "corpus" / "vocab_levels.json"
    if vocab_path.exists():
        return VocabularyStore.load(vocab_path)
    # Fallback mock dictionary
    return VocabularyStore(
        {
            "haus": "A1",
            "tisch": "A1",
            "buch": "A1",
            "wohnung": "A2",
            "fahrkarte": "A2",
            "entscheidung": "B1",
            "wirtschaft": "B1",
            "funktionsverbgefüge": "B2",
            "wissenschaftlich": "B2",
        }
    )


def test_vocabulary_store_lookup(vocab_store: VocabularyStore) -> None:
    """Test CEFR level queries for German words."""
    assert vocab_store.get_level("der") == "A1"  # function word
    assert vocab_store.get_level("und") == "A1"  # function word
    assert vocab_store.get_level("buch") in ["A1", "A2"]


def test_vocabulary_ceiling_check(vocab_store: VocabularyStore) -> None:
    """Test ceiling constraints on words."""
    assert vocab_store.is_within_ceiling("tisch", "A1")
    assert vocab_store.is_within_ceiling("tisch", "B2")


def test_sentence_validation(vocab_store: VocabularyStore) -> None:
    """Test sentence validation against CEFR ceiling."""
    # Basic A1 sentence
    a1_sentence = "Das Buch liegt auf dem Tisch."
    violations_a1 = vocab_store.validate_sentence(a1_sentence, "A1")
    assert len(violations_a1) == 0, f"Expected no violations for A1 sentence, got {violations_a1}"

    # Complex B2 sentence evaluated at A1 ceiling should report complex words
    b2_sentence = "Die wissenschaftliche Analyse erfordert präzise Methodik."
    violations_b2 = vocab_store.validate_sentence(b2_sentence, "A1")
    assert len(violations_b2) > 0


def test_vocabulary_store_save_load(tmp_path: Path) -> None:
    """Test serialization and deserialization."""
    store = VocabularyStore({"apfel": "A1", "birne": "A2"})
    save_file = tmp_path / "test_vocab.json"
    store.save(save_file)
    assert save_file.exists()

    loaded = VocabularyStore.load(save_file)
    assert loaded.get_level("apfel") == "A1"
    assert loaded.get_level("birne") == "A2"


def test_frequency_bander() -> None:
    """Test frequency band classification into difficulty tiers 1, 2, 3."""
    bander = FrequencyBander(high_freq_words=["tisch", "haus", "kind", "auto"])
    assert bander.classify_difficulty([]) == 1
    assert bander.classify_difficulty(["tisch", "haus"]) == 1
    assert bander.classify_difficulty(["entscheidungsfindung"]) == 2  # long word >= 12 chars
    assert (
        bander.classify_difficulty(["bundesverfassungsgericht", "arbeitsunfähigkeitsbescheinigung"])
        == 3
    )


def test_pdf_extractor_handles_empty_dir(tmp_path: Path) -> None:
    """Test PDF extractor returns empty dict when no files exist."""
    extractor = WordlistPdfExtractor(tmp_path)
    result = extractor.extract_all()
    assert result == {}


def test_pdf_extractor_extracts_from_raw_dir() -> None:
    """Test PDF extractor extracts real lemmas from data/raw PDFs if present."""
    raw_dir = Path(__file__).parent.parent / "data" / "raw"
    if raw_dir.exists() and list(raw_dir.glob("*.pdf")):
        extractor = WordlistPdfExtractor(raw_dir)
        vocab = extractor.extract_all()
        assert len(vocab) > 1000
        assert "tisch" in vocab or "haus" in vocab
