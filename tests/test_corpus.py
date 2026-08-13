"""Unit tests for the carrier sentence corpus module (Tatoeba)."""

from pathlib import Path

import pytest
from src.corpus.tatoeba import TatoebaCorpus
from src.lexicon.vocabulary import VocabularyStore


@pytest.fixture
def tatoeba_sample_tsv(data_fixtures_dir: Path) -> Path:
    return data_fixtures_dir / "corpus" / "tatoeba_sample.tsv"


def test_tatoeba_corpus_loads_sample(tatoeba_sample_tsv: Path, data_fixtures_dir: Path) -> None:
    """Verify Tatoeba TSV loading and CarrierSentence parsing."""
    vocab_path = data_fixtures_dir / "corpus" / "vocab_levels.json"
    vocab_store = VocabularyStore.load(vocab_path) if vocab_path.exists() else None

    corpus = TatoebaCorpus.load_from_tsv(tatoeba_sample_tsv, vocab_store=vocab_store)
    assert len(corpus.sentences) >= 8

    first = corpus.sentences[0]
    assert first.german_text == "Das Buch liegt auf dem Tisch."
    assert first.english_text == "The book is on the table."
    assert "Buch" in first.carrier_lemmas or "Tisch" in first.carrier_lemmas


def test_tatoeba_filtering(tatoeba_sample_tsv: Path) -> None:
    """Test filtering by token length and CEFR level."""
    corpus = TatoebaCorpus.load_from_tsv(tatoeba_sample_tsv)

    short_sentences = corpus.filter_by_length(min_tokens=3, max_tokens=10)
    assert len(short_sentences) > 0
    assert all(3 <= s.token_count <= 10 for s in short_sentences)

    a2_filtered = corpus.filter_by_cefr("A2")
    assert len(a2_filtered) > 0
    assert all(s.estimated_cefr in ["A1", "A2"] for s in a2_filtered)


def test_tatoeba_missing_file_raises_error(tmp_path: Path) -> None:
    """Test Tatoeba loader raises FileNotFoundError for invalid path."""
    with pytest.raises(FileNotFoundError):
        TatoebaCorpus.load_from_tsv(tmp_path / "non_existent.tsv")
