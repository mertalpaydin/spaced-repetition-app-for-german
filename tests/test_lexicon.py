"""Unit tests for the vocabulary store, frequency bander, and PDF extractor."""

from pathlib import Path

import pytest
from src.lexicon.extractor import WordlistPdfExtractor
from src.lexicon.frequency import FrequencyBander
from src.lexicon.lemmatizer import lemma_candidates, normalise
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


def test_lemmatisation_handles_separable_verbs() -> None:
    """ "Ich rufe dich morgen an" lemmatises to "anrufen", not "rufen" + "an".

    A lemmatiser that only strips inflection from the finite verb token and
    ignores the separated prefix elsewhere in the sentence mis-lemmatises to
    the (unrelated, non-separable-reading) bare verb, silently corrupting
    vocab tagging for one of the most common A1-B1 construction patterns in
    German.
    """
    candidates = lemma_candidates("rufe", context_tokens=["Ich", "rufe", "dich", "morgen", "an"])
    assert "anrufen" in candidates
    assert "rufen" in candidates
    # The separable-verb reading uses more sentence evidence than the bare
    # stem, so it must be preferred (tried before the bare stem candidate).
    assert candidates.index("anrufen") < candidates.index("rufen")

    # Without the separating prefix present anywhere in the sentence, the
    # separable-verb candidate must not be fabricated.
    no_context_candidates = lemma_candidates("rufe")
    assert "anrufen" not in no_context_candidates
    assert "rufen" in no_context_candidates


def test_lemmatisation_resolves_separable_verb_against_vocab_store() -> None:
    """A vocab store that only knows "anrufen" (not "rufen") must still
    accept "Ich rufe dich morgen an." -- the separable-verb reading is the
    one that resolves, exactly as required by 01-foundation.md stage 2.
    """
    store = VocabularyStore({"anrufen": "A2"})
    violations = store.validate_sentence("Ich rufe dich morgen an.", "A2")
    assert violations == []


def test_umlaut_and_eszett_normalisation_is_lossless() -> None:
    """normalise("Straße") round-trips (idempotent), and "Strasse" maps to
    the same lookup key as "Straße" -- the old `.lower()`-only implementation
    treated them as two distinct, unrelated dictionary keys.
    """
    strasse_key = normalise("Straße")
    assert normalise(strasse_key) == strasse_key
    assert normalise("Strasse") == strasse_key
    assert normalise("STRASSE") == strasse_key

    store = VocabularyStore({"straße": "A2"})
    assert store.get_level("Straße") == "A2"
    assert store.get_level("Strasse") == "A2"
    assert store.get_level("strasse") == "A2"


def test_lemmatisation_resolves_inflected_ceiling_violations(vocab_store: VocabularyStore) -> None:
    """Inflected surface forms absent from the scraped wordlist resolve via
    their lemma instead of tripping a false vocabulary-ceiling violation.
    """
    assert vocab_store.is_within_ceiling("Regens", "B1")  # genitive of "Regen"
    assert vocab_store.is_within_ceiling("blieben", "B1")  # preterite of "bleiben"
    assert vocab_store.is_within_ceiling("gemacht", "A1")  # participle of "machen"
    assert vocab_store.is_within_ceiling("gefahren", "A1")  # participle of "fahren"
    assert vocab_store.is_within_ceiling("Gehst", "A1")  # 2sg present of "gehen"
    assert vocab_store.is_within_ceiling("Sprich", "A1")  # irregular imperative of "sprechen"
    assert vocab_store.is_within_ceiling("spielende", "B2")  # attributive Partizip I of "spielen"
    assert vocab_store.is_within_ceiling("erstellenden", "B2")  # declined Partizip I of "erstellen"
    assert vocab_store.is_within_ceiling("beschlossenen", "B2")  # declined irregular Partizip II
    assert vocab_store.is_within_ceiling("Strömen", "B2")  # umlaut dative plural of "Strom"


def test_proper_nouns_are_whitelisted_not_added_as_vocabulary() -> None:
    """Given names never trip the ceiling check via a dedicated whitelist,
    not by being enumerated into the scraped vocabulary dictionary.
    """
    store = VocabularyStore({"haus": "A1"})  # deliberately does not contain any names
    assert store.is_within_ceiling("Anna", "A1")
    assert store.is_within_ceiling("Lisa", "A1")
    assert "anna" not in store.vocab
    assert "lisa" not in store.vocab
