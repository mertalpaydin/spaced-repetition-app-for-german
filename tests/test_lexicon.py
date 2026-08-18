"""Unit tests for the vocabulary store, frequency bander, and PDF extractor."""

from pathlib import Path

import pytest
from src.lexicon.extractor import WordlistPdfExtractor
from src.lexicon.frequency import FrequencyBander
from src.lexicon.lemmatizer import compound_split_candidates, lemma_candidates, normalise
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


# ---------------------------------------------------------------------------
# Vocabulary ceiling budget
#
# docs/audits/stage-04-a2-pilot-audit.md, "On the vocabulary ceiling":
# vocabulary_ceiling_violation was the single largest A2 pilot rejection
# category (20 of 48), and zero tolerance -- every word at or below the
# ceiling -- is stricter than any graded reader. The fix: up to two content
# words exactly one CEFR band above the ceiling are tolerated (ordinary
# i+1); a third one-band-over word, or any word two or more bands above,
# still fails. These use invented lemmas with a small hand-built vocab so
# the band distance from the ceiling is exact and unambiguous, independent
# of the real scraped wordlist.
# ---------------------------------------------------------------------------


def test_ceiling_budget_permits_two_one_band_over_content_words() -> None:
    """Two content words exactly one CEFR band above the ceiling (B1 words
    against an A2 ceiling) must be forgiven, not rejected."""
    store = VocabularyStore({"buchara": "B1", "dorimon": "B1"})
    result = store.check_ceiling_budget("Die Buchara und der Dorimon sind alt.", "A2")
    assert result.violations == []
    assert sorted(result.over_budget) == ["Buchara", "Dorimon"]


def test_ceiling_budget_rejects_three_one_band_over_content_words() -> None:
    """A third content word exactly one band above the ceiling exhausts the
    two-word budget, so the item fails and all three are reported."""
    store = VocabularyStore({"buchara": "B1", "dorimon": "B1", "fenrike": "B1"})
    result = store.check_ceiling_budget("Die Buchara, der Dorimon und die Fenrike sind alt.", "A2")
    assert sorted(result.violations) == ["Buchara", "Dorimon", "Fenrike"]


def test_ceiling_budget_rejects_a_single_two_band_over_word_outright() -> None:
    """A word two or more CEFR bands above the ceiling (B2 against A2) fails
    outright -- the budget only ever forgives a ONE-band gap, never two."""
    store = VocabularyStore({"zelinor": "B2"})
    result = store.check_ceiling_budget("Der Zelinor ist alt.", "A2")
    assert result.violations == ["Zelinor"]
    assert result.over_budget == []


def test_validate_sentence_matches_check_ceiling_budget_violations() -> None:
    """``validate_sentence`` is the budget-aware check's ``violations`` list,
    not a separate zero-tolerance rule living alongside it."""
    store = VocabularyStore({"buchara": "B1", "dorimon": "B1", "zelinor": "B2"})
    sentence = "Die Buchara, der Dorimon und der Zelinor sind alt."
    assert (
        store.validate_sentence(sentence, "A2")
        == store.check_ceiling_budget(sentence, "A2").violations
    )


# ---------------------------------------------------------------------------
# Closed-class function words
#
# docs/audits/stage-04-a2-pilot-audit.md: Bevor, Sobald, Trotz, weshalb and
# the rest of the closed subordinating-conjunction / interrogative-adverb /
# preposition class were rejected as too-hard-for-A2 VOCABULARY despite
# being present in vocab_levels.json at B1 or B2, because a conjunction
# failing a vocabulary ceiling is a category mistake, not a vocabulary gap.
# Each word below is deliberately tagged B2 in the store so the test proves
# the FUNCTION_WORDS membership check short-circuits before the (wrong,
# too-high) wordlist level is ever consulted.
# ---------------------------------------------------------------------------

NEWLY_ADDED_CLOSED_CLASS_WORDS = [
    "bevor",
    "sobald",
    "nachdem",
    "während",
    "bis",
    "damit",
    "seitdem",
    "falls",
    "sodass",
    "indem",
    "solange",
    "sooft",
    "weshalb",
    "weswegen",
    "wobei",
    "worauf",
    "woran",
    "wodurch",
    "trotz",
    "wegen",
    "statt",
    "anstatt",
    "innerhalb",
    "außerhalb",
    "aufgrund",
    "mithilfe",
    "laut",
    "gemäß",
    "entlang",
    "gegenüber",
    "jedoch",
    "dennoch",
    "allerdings",
    "folglich",
    "deswegen",
    "darum",
    "daher",
    "zwar",
    "sondern",
    "entweder",
    "weder",
    "sowohl",
]


@pytest.mark.parametrize("word", NEWLY_ADDED_CLOSED_CLASS_WORDS)
def test_closed_class_function_word_passes_a1_ceiling_despite_wordlist_tagging_it_b2(
    word: str,
) -> None:
    store = VocabularyStore({word: "B2", word.capitalize(): "B2"})
    assert store.is_within_ceiling(word, "A1"), f"{word!r} (lowercase) failed an A1 ceiling"
    assert store.is_within_ceiling(word.capitalize(), "A1"), (
        f"{word!r} (sentence-initial capitalised) failed an A1 ceiling"
    )
    assert store.get_level(word) == "A1"


def test_closed_class_conjunction_sentence_passes_a1_ceiling() -> None:
    """End-to-end version of the parametrized check above: a full A1
    sentence built around a newly-whitelisted subordinator, where the
    wordlist mistags the conjunction itself as B2, has zero violations."""
    store = VocabularyStore({"bevor": "B2", "sobald": "B2", "haus": "A1", "gehen": "A1"})
    assert store.validate_sentence("Bevor du gehst, räum dein Haus auf.", "A1") == []
    assert store.validate_sentence("Sobald ich zu Hause bin, rufe ich an.", "A1") == []


# ---------------------------------------------------------------------------
# Frequency-derived B2 banding
#
# Above B1 there is no official Goethe wordlist to scrape, so B2 is derived
# from a public word-frequency corpus instead of a scrape (see
# data/fixtures/corpus/frequency/PROVENANCE.md for what corpus, its licence,
# and how it is combined). These tests exercise the pure banding logic
# against small synthetic inputs -- no network, no dependency on the size of
# the vendored fixture files -- plus a handful of regression checks against
# the real, regenerated data/fixtures/corpus/vocab_levels.json.
# ---------------------------------------------------------------------------


def test_derive_b2_band_takes_next_ranked_words_excluding_known() -> None:
    """Words already resolving to A1/A2/B1 (directly or via a candidate
    lemma) are skipped; the band is the next ``band_size`` survivors, in
    rank order, that also pass the dictionary filter."""
    ranked = ["haus", "vorstand", "xyznotaword", "analyse", "buch", "these"]
    dictionary_filter = {"haus", "vorstand", "analyse", "buch", "these"}  # no "xyznotaword"
    known = {"haus", "buch"}  # already A1 elsewhere
    band = FrequencyBander.derive_b2_band(ranked, dictionary_filter, known, band_size=2)
    assert band == ["vorstand", "analyse"]  # rank order, "these" cut off by band_size=2


def test_derive_b2_band_excludes_words_resolving_via_lemma_candidate() -> None:
    """A frequency-ranked inflected form whose *lemma* is already known
    (e.g. a plural of an A1 noun) is excluded, not just an exact-string
    match -- otherwise frequency banding would re-promote an A1 word's own
    inflected forms to B2 just because the scraped list only ever printed
    the singular."""
    ranked = ["häuser"]  # plural of "Haus", reduces to "haus" via lemma_candidates
    dictionary_filter = {"häuser"}
    known = {"haus"}
    assert FrequencyBander.derive_b2_band(ranked, dictionary_filter, known, band_size=10) == []


def test_derive_b2_band_never_exceeds_band_size() -> None:
    ranked = [f"wort{i}" for i in range(50)]
    dictionary_filter = set(ranked)
    band = FrequencyBander.derive_b2_band(
        ranked, dictionary_filter, known_lemmas=set(), band_size=5
    )
    assert len(band) == 5
    assert band == ranked[:5]


def test_derive_b2_vocab_labels_every_band_word_b2() -> None:
    ranked = ["vorstand", "analyse"]
    vocab = FrequencyBander.derive_b2_vocab(
        ranked, dictionary_filter=set(ranked), known_lemmas=set()
    )
    assert vocab == {"vorstand": "B2", "analyse": "B2"}


def test_load_ranked_words_parses_word_count_pairs_most_frequent_first(tmp_path: Path) -> None:
    freq_file = tmp_path / "freq.txt"
    freq_file.write_text("ich 500\ndu 400\nhaus 12\nnot a valid line\n123 99\n", encoding="utf-8")
    ranked = FrequencyBander.load_ranked_words(freq_file)
    assert ranked == ["ich", "du", "haus"]  # numeral-only and malformed lines dropped


def test_load_ranked_words_deduplicates_via_normalise(tmp_path: Path) -> None:
    freq_file = tmp_path / "freq.txt"
    freq_file.write_text("Straße 500\nstrasse 400\nSTRASSE 300\n", encoding="utf-8")
    ranked = FrequencyBander.load_ranked_words(freq_file)
    assert ranked == [normalise("Straße")]  # first occurrence's rank wins, not re-added


def test_load_dictionary_filter_normalises_and_skips_blank_lines(tmp_path: Path) -> None:
    dict_file = tmp_path / "dict.txt"
    dict_file.write_text("Haus\n\nStraße\n  \n", encoding="utf-8")
    loaded = FrequencyBander.load_dictionary_filter(dict_file)
    assert loaded == {"haus", normalise("Straße")}


@pytest.mark.parametrize("word", ["Vorstand", "Projektleiter", "Analyse", "These"])
def test_previously_rejected_b2_words_now_resolve_at_or_below_b2(
    vocab_store: VocabularyStore, word: str
) -> None:
    """The four words named in the earlier pilot as falsely rejected for
    exceeding a B2 vocabulary ceiling ("Vorstand", "Projektleiter",
    "Analyse", "These" -- all ordinary B2 words) now resolve to a genuine,
    evidenced level at or below B2, not merely "unknown, so it happens to
    pass"."""
    level = vocab_store.get_level(word)
    assert level is not None, f"{word!r} should resolve to a level, not stay unknown"
    assert VocabularyStore.LEVEL_RANKS[level] <= VocabularyStore.LEVEL_RANKS["B2"]
    assert vocab_store.is_within_ceiling(word, "B2")


@pytest.mark.parametrize(
    "word",
    [
        "Bundesverfassungsgerichtsurteil",
        "Kernspintomographie",
        "Immatrikulationsbescheinigung",
        "Approbationsordnung",
        "Streitwertfestsetzung",
    ],
)
def test_genuinely_rare_specialist_words_are_not_mislabeled_b2(
    vocab_store: VocabularyStore, word: str
) -> None:
    """Extending B2 by frequency must not sweep up genuinely rare or
    specialist vocabulary that the frequency corpus never saw. These stay
    unresolved ("unknown"), the same honest state absence has always meant
    in this store -- not a false B2 label."""
    assert vocab_store.get_level(word) is None


def test_extractor_no_longer_produces_english_contaminated_b2() -> None:
    """WordlistPdfExtractor.extract_all used to scrape B2 from a bilingual
    course glossary and, for that one source only, pick up English
    translation-column words alongside the German headwords ("accompany",
    "administer", "advertisement" were all tagged as German B2 vocabulary).
    extract_all no longer extracts B2 at all (see its docstring), so no
    English contamination and no B2 level can come from it any more."""
    raw_dir = Path(__file__).parent.parent / "data" / "raw"
    if not raw_dir.exists() or not list(raw_dir.glob("*.pdf")):
        pytest.skip("data/raw PDFs not present in this environment")
    extractor = WordlistPdfExtractor(raw_dir)
    vocab = extractor.extract_all()
    assert "B2" not in vocab.values()
    assert "accompany" not in vocab


def test_compound_split_candidates_finds_projekt_leiter() -> None:
    assert ("projekt", "leiter") in compound_split_candidates("Projektleiter")


def test_compound_resolution_uses_harder_of_two_known_parts() -> None:
    """ "Projektleiter" ("Projekt" A2 + "Leiter" B1) is not in the frequency
    corpus at all (subtitle dialogue rarely says the word), but both parts
    are already-leveled ordinary vocabulary, so the compound resolves to
    the harder part, B1, not the easier one."""
    store = VocabularyStore({"projekt": "A2", "leiter": "B1"})
    assert store.get_level("Projektleiter") == "B1"
    assert store.is_within_ceiling("Projektleiter", "B1")
    assert not store.is_within_ceiling("Projektleiter", "A2")


def test_compound_fallback_does_not_override_a_direct_hit() -> None:
    """A word that already resolves directly (or via ``lemma_candidates``)
    must be scored on that resolution, never re-scored by a coincidental
    compound split -- "Vorstand" (real B2 word) must not pass an A2 ceiling
    just because some substring split of it happens to resolve two
    unrelated short words. Regression test for a bug caught while building
    the compound-split fallback: it was being consulted unconditionally
    instead of only when nothing else resolved at all."""
    # "vorstand" spuriously splits as "vors" + "tand"; make both resolve to
    # something easy, and confirm the *direct* B2 hit still wins.
    store = VocabularyStore({"vorstand": "B2", "vor": "A1", "tand": "A2"})
    assert store.get_level("Vorstand") == "B2"
    assert not store.is_within_ceiling("Vorstand", "A2")
    assert store.is_within_ceiling("Vorstand", "B2")


def test_compound_resolution_requires_direct_hits_not_fuzzy_stems() -> None:
    """Compound parts are resolved via a direct vocabulary/function-word
    hit only, never through ``lemma_candidates``' inflectional-suffix
    reduction -- chaining two fuzzy single-word resolutions was measurably
    worse than one. A compound whose halves only resolve via suffix
    stripping (not as an exact stem) must not resolve at all."""
    # "leitung" reduces to "leit" only via suffix stripping; "projekt" is a
    # direct hit. Without the direct-hit restriction this would coincide
    # with an unintended split and falsely resolve.
    store = VocabularyStore({"projekt": "A2"})  # deliberately no "leiter"/"leitung"/"leit" entry
    assert store.get_level("Projektleitung") is None
