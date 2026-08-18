"""Frequency banding module for lexical difficulty control and tier assignment.

Also holds the frequency-derived CEFR banding logic used to build the B2
entries in ``data/fixtures/corpus/vocab_levels.json``. See
``data/fixtures/corpus/frequency/PROVENANCE.md`` for what the frequency data
this reads is, where it came from, and its licence; see
``scripts/step1_extract_vocab.py`` for how ``derive_b2_vocab`` below is
actually invoked to build the fixture.
"""

import re
from collections.abc import Collection, Iterable
from pathlib import Path
from typing import ClassVar

from src.contracts import CEFR, Difficulty
from src.lexicon.lemmatizer import lemma_candidates, normalise

#: A usable frequency-list token: German letters only, at least three
#: characters. Mirrors ``VocabularyStore``'s own content-token floor
#: (``_CONTENT_TOKEN_RE`` in ``vocabulary.py``) so a word banded here is a
#: word the ceiling check would ever actually score.
_ALPHA_TOKEN_RE = re.compile(r"^[a-zäöüß]+$")


class FrequencyBander:
    """Classifies German words into difficulty bands (1: basic, 2: intermediate, 3: advanced)."""

    #: Number of newly-surfaced (not already resolving to A1, A2 or B1)
    #: frequency-ranked lemmas that make up the frequency-derived B2 band.
    #: See ``data/fixtures/corpus/frequency/PROVENANCE.md`` for how this
    #: number was chosen (it trades recall of ordinary B2 vocabulary against
    #: the informal-register and tokenizer noise that increases as the
    #: frequency corpus's rank gets deeper) and what a different value
    #: would change.
    DEFAULT_B2_BAND_SIZE: ClassVar[int] = 6000

    def __init__(self, high_freq_words: Collection[str] | None = None) -> None:
        self.high_freq: set[str] = {w.lower() for w in (high_freq_words or [])}

    def classify_difficulty(self, words: list[str]) -> Difficulty:
        """Classify a list of words or carrier sentence lemmas into difficulty tier 1, 2, or 3."""
        if not words:
            return 1

        rare_count = 0
        long_words_count = 0

        for w in words:
            clean = w.lower().strip()
            if self.high_freq and clean not in self.high_freq:
                rare_count += 1
            if len(clean) >= 12:  # Long compound German nouns/words
                long_words_count += 1

        if long_words_count >= 2 or rare_count >= 3:
            return 3
        if long_words_count >= 1 or rare_count >= 1:
            return 2
        return 1

    # -----------------------------------------------------------------
    # Frequency-derived CEFR banding (above B1)
    #
    # A wordlist scrape is evidence a word is easy (someone chose to print
    # it in a graded list); its absence is not evidence a word is hard, no
    # matter how large the scrape (docs/audits/stage-04-recovery-plan.md
    # fix C). Above B1 there is no official scrape at all -- Goethe
    # publishes wordlists only through B1 -- so this project's B2 band was
    # built from an unofficial course glossary instead, covered 808 lemmas,
    # and (discovered while building this) was partly English-contaminated
    # by a PDF-extraction bug in ``WordlistPdfExtractor`` that picked up the
    # glossary's English translation column alongside the German headword.
    #
    # A word's rank in a large, general-purpose frequency corpus is
    # independent evidence: how often it is actually used tells you
    # something a hand-curated scrape's silence cannot. It is not perfect
    # evidence either (see PROVENANCE.md's "known limitations"), which is
    # why only the top ``DEFAULT_B2_BAND_SIZE`` newly-surfaced lemmas are
    # banded, in frequency-rank order, rather than everything the corpus
    # happens to contain.
    # -----------------------------------------------------------------

    @staticmethod
    def load_ranked_words(path: Path | str) -> list[str]:
        """Load a ``{word} {count}`` frequency list (most frequent first,
        one entry per line -- the format ``de_opensubtitles2018_top50k.txt``
        uses) and return its words as a rank-ordered, deduplicated list.

        Deduplicates via ``normalise()`` (so "Straße" and "strasse" are the
        same rank slot) and drops non-alphabetic or under-length tokens --
        a subtitle-corpus tokenizer emits some numerals and punctuation
        fragments that are never going to be scoreable vocabulary anyway.
        Malformed lines (not exactly ``word count``) are skipped rather
        than raising, since a frequency-list format quirk should not be
        able to crash vocabulary generation.
        """
        seen: set[str] = set()
        ranked: list[str] = []
        with Path(path).open("r", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) != 2:
                    continue
                word, _count = parts
                normalized = normalise(word)
                if not _ALPHA_TOKEN_RE.match(normalized):
                    continue
                if normalized in seen:
                    continue
                seen.add(normalized)
                ranked.append(normalized)
        return ranked

    @staticmethod
    def load_dictionary_filter(path: Path | str) -> frozenset[str]:
        """Load a one-real-word-per-line list and return it normalised.

        Used to filter contamination -- non-German names, slang, and
        tokenizer artefacts -- out of a raw frequency list (see
        ``derive_b2_band``). Blank lines are ignored.
        """
        with Path(path).open("r", encoding="utf-8") as f:
            return frozenset(normalise(line) for line in f if line.strip())

    @classmethod
    def derive_b2_band(
        cls,
        ranked_words: Iterable[str],
        dictionary_filter: Collection[str],
        known_lemmas: Collection[str],
        band_size: int | None = None,
    ) -> list[str]:
        """Return the frequency-derived B2 band: the next ``band_size``
        lemmas, in ``ranked_words`` rank order, that are real dictionary
        words (per ``dictionary_filter``) and do not already resolve
        (directly, or via ``lemma_candidates``) to a lemma in
        ``known_lemmas`` -- typically every A1, A2 and B1 lemma plus the
        closed-class function words and proper nouns already whitelisted
        by ``VocabularyStore``, so this never re-labels an already-easier
        word as B2.

        A lemma ranked beyond this band, or absent from ``ranked_words``
        entirely, is *not* returned here -- by construction, not by
        omission -- so it stays absent from ``vocab_levels.json`` rather
        than being mislabeled B2. ``VocabularyStore`` treats that absence
        as "unknown", not "above B2" (see its docstrings): CEFR
        (``src.contracts``) has no level above B2 to assign it, and this
        project's stance is that inventing one is a bigger risk than
        leaving the gap stated. See the report for this task for that
        reasoning in full.
        """
        limit = cls.DEFAULT_B2_BAND_SIZE if band_size is None else band_size
        known = {normalise(w) for w in known_lemmas}
        allowed = {normalise(w) for w in dictionary_filter}
        band: list[str] = []
        seen: set[str] = set()
        for word in ranked_words:
            normalized = normalise(word)
            if normalized in seen:
                continue
            seen.add(normalized)
            if normalized not in allowed:
                continue
            if any(candidate in known for candidate in lemma_candidates(normalized)):
                continue
            band.append(normalized)
            if len(band) >= limit:
                break
        return band

    @classmethod
    def derive_b2_vocab(
        cls,
        ranked_words: Iterable[str],
        dictionary_filter: Collection[str],
        known_lemmas: Collection[str],
        band_size: int | None = None,
    ) -> dict[str, CEFR]:
        """Same as ``derive_b2_band``, shaped as a ``{lemma: "B2"}`` mapping
        ready to merge into a ``vocab_levels.json``-style dict."""
        band = cls.derive_b2_band(ranked_words, dictionary_filter, known_lemmas, band_size)
        vocab: dict[str, CEFR] = dict.fromkeys(band, "B2")
        return vocab
