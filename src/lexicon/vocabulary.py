"""VocabularyStore for CEFR vocabulary ceiling management and sentence validation."""

import json
import re
from pathlib import Path
from typing import ClassVar, NamedTuple

from src.contracts import CEFR
from src.lexicon.lemmatizer import lemma_candidates, normalise

#: Regex for the content tokens a ceiling/vocabulary check actually scores:
#: three letters or more, so short function-word fragments and stray
#: initials don't get scored as content words.
_CONTENT_TOKEN_RE = re.compile(r"\b[A-ZÄÖÜa-zäöüß]{3,}\b")

#: Regex for every alphabetic token, including short ones. Used only as
#: sentence *context* (e.g. recovering a separable verb prefix elsewhere in
#: the sentence), never scored directly.
_ALL_TOKEN_RE = re.compile(r"\b[A-ZÄÖÜa-zäöüß]+\b")


class CeilingBudgetResult(NamedTuple):
    """Outcome of a budgeted CEFR ceiling check against a sentence.

    docs/audits/stage-04-a2-pilot-audit.md, "On the vocabulary ceiling":
    zero-tolerance ("every word must sit at or below the ceiling") is
    stricter than any graded reader, so the ceiling now carries a budget --
    up to ``VocabularyStore.CEILING_BUDGET_MAX_ONE_BAND_WORDS`` content words
    may sit exactly one CEFR band above the ceiling before the item is
    rejected.

    ``violations`` are the words that fail the check outright: two or more
    bands above the ceiling always fail, and one-band-above words fail too
    once the budget is exhausted. ``over_budget`` is every content word
    found sitting exactly one band above the ceiling, whether or not it was
    ultimately forgiven -- kept separate from ``violations`` so a budget
    spend is recorded on the result and stays visible, rather than being
    silently forgiven.
    """

    violations: list[str]
    over_budget: list[str]


class VocabularyStore:
    """Stores vocabulary mappings and validates sentence tokens against CEFR ceilings."""

    LEVEL_RANKS: ClassVar[dict[CEFR, int]] = {
        "A1": 1,
        "A2": 2,
        "B1": 3,
        "B2": 4,
    }

    # Proper nouns (given names) are excluded from the CEFR ceiling entirely,
    # the same way a learner's own name would be -- they carry no vocabulary
    # difficulty of their own. This is a deliberately small, explicit
    # whitelist of common German first names, kept separate from the
    # scraped vocabulary data: names are not "vocabulary" in the CEFR sense
    # and do not belong in vocab_levels.json (data/fixtures/corpus). A
    # generic "capitalised word => proper noun" heuristic would silently
    # defeat the ceiling check, since every German noun is capitalised.
    PROPER_NOUNS: ClassVar[frozenset[str]] = frozenset(
        {
            "anna",
            "lisa",
            "lena",
            "laura",
            "julia",
            "sophie",
            "sarah",
            "nina",
            "maria",
            "petra",
            "sabine",
            "katrin",
            "andrea",
            "ursula",
            "max",
            "paul",
            "peter",
            "thomas",
            "michael",
            "stefan",
            "markus",
            "klaus",
            "werner",
            "tim",
            "jan",
            "lukas",
            "felix",
            "julian",
            # Country and city names: the same "carries no vocabulary
            # difficulty of its own" reasoning as a given name -- confirmed
            # live: "Schweden" (Sweden) was rejected as violating a B1
            # ceiling for a futur_i item ("...nach Schweden reisen"), a
            # travel-topic sentence pattern common across every CEFR level.
            "deutschland",
            "österreich",
            "schweiz",
            "frankreich",
            "spanien",
            "italien",
            "england",
            "amerika",
            "schweden",
            "polen",
            "türkei",
            "griechenland",
            "portugal",
            "russland",
            "china",
            "japan",
            "berlin",
            "münchen",
            "hamburg",
            "köln",
            "frankfurt",
            "wien",
            "zürich",
            "europa",
            "asien",
            "afrika",
        }
    )

    # High frequency function words inherently permissible at all levels.
    # Merged with ``_CLOSED_CLASS_FUNCTION_WORDS`` below into ``FUNCTION_WORDS``;
    # kept as a separate literal here (rather than folding the two together)
    # so this set can stay what it has always been -- pronouns, articles,
    # auxiliaries, high-frequency adverbs -- without the closed-class
    # conjunctions and prepositions added for the vocabulary-ceiling category
    # fix jumbled in anonymously.
    _HIGH_FREQUENCY_FUNCTION_WORDS: ClassVar[frozenset[str]] = frozenset(
        {
            "der",
            "die",
            "das",
            "dem",
            "den",
            "des",
            "ein",
            "eine",
            "einen",
            "einem",
            "einer",
            "eines",
            "kein",
            "keine",
            "keinen",
            "keinem",
            "keiner",
            "keines",
            "ich",
            "du",
            "er",
            "sie",
            "es",
            "wir",
            "ihr",
            "mich",
            "dich",
            "ihn",
            "uns",
            "euch",
            "ihnen",
            "mir",
            "dir",
            "ihm",
            "mein",
            "dein",
            "sein",
            "unser",
            "euer",
            "und",
            "oder",
            "aber",
            "denn",
            "weil",
            "da",
            "dass",
            "wenn",
            "als",
            "ob",
            "obwohl",
            "trotzdem",
            "deshalb",
            "ist",
            "sind",
            "war",
            "waren",
            "hat",
            "haben",
            "hatte",
            "hatten",
            "wird",
            "werden",
            "wurde",
            "wurden",
            "kann",
            "können",
            "muss",
            "müssen",
            "will",
            "wollen",
            "darf",
            "dürfen",
            "soll",
            "sollen",
            "nicht",
            "sehr",
            "hier",
            "dort",
            "heute",
            "gestern",
            "morgen",
            "in",
            "an",
            "auf",
            "neben",
            "hinter",
            "über",
            "unter",
            "vor",
            "zwischen",
            "mit",
            "nach",
            "bei",
            "seit",
            "von",
            "zu",
            "aus",
            "durch",
            "für",
            "gegen",
            "ohne",
            "um",
            "wie",
            "so",
            "ja",
            "nein",
            "auch",
        }
    )

    # Closed-class function words: subordinating conjunctions, interrogative
    # / relative adverbs, prepositions and conjunctive adverbs.
    # docs/audits/stage-04-a2-pilot-audit.md, "On the vocabulary ceiling":
    # ``Bevor``, ``Sobald``, ``Trotz`` and ``weshalb`` were rejected as
    # too-hard-for-A2 VOCABULARY, but a conjunction failing a vocabulary
    # ceiling is a category mistake -- these words are grammar, and grammar
    # is exactly what this application exists to teach. A closed class also
    # cannot run out (no text mints a new German subordinating conjunction),
    # so whitelisting the whole class costs nothing the way whitelisting an
    # open class (nouns, verbs) would. Kept as its own frozenset, separate
    # from ``_HIGH_FREQUENCY_FUNCTION_WORDS`` above, purely so this rationale
    # stays legible instead of being merged anonymously into a general
    # high-frequency whitelist.
    #
    # Entries are pre-normalised (``normalise()``'s eszett-to-"ss" mapping
    # applied by hand) so they match the normalised lookup key: "außerhalb"
    # -> "ausserhalb", "gemäß" -> "gemäss".
    _CLOSED_CLASS_FUNCTION_WORDS: ClassVar[frozenset[str]] = frozenset(
        {
            # subordinating conjunctions
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
            # interrogative / relative adverbs used as subordinators
            "weshalb",
            "weswegen",
            "wobei",
            "worauf",
            "woran",
            "wodurch",
            # prepositions (govern a case, not vocabulary difficulty)
            "trotz",
            "wegen",
            "statt",
            "anstatt",
            "innerhalb",
            "ausserhalb",
            "aufgrund",
            "mithilfe",
            "laut",
            "gemäss",
            "entlang",
            "gegenüber",
            # conjunctive adverbs
            "jedoch",
            "dennoch",
            "allerdings",
            "folglich",
            "deswegen",
            "darum",
            "daher",
            # correlative / coordinating pairs
            "zwar",
            "sondern",
            "entweder",
            "weder",
            "sowohl",
        }
    )

    #: The lookup used everywhere else in this class: every high-frequency
    #: function word plus the whole closed grammatical class above.
    FUNCTION_WORDS: ClassVar[frozenset[str]] = (
        _HIGH_FREQUENCY_FUNCTION_WORDS | _CLOSED_CLASS_FUNCTION_WORDS
    )

    #: Budget for the CEFR ceiling check (task: "give the ceiling a
    #: budget"): at most this many content words may sit exactly one CEFR
    #: band above the ceiling before the item is rejected. Any word two or
    #: more bands above the ceiling is always rejected outright.
    CEILING_BUDGET_MAX_ONE_BAND_WORDS: ClassVar[int] = 2

    def __init__(self, vocab: dict[str, CEFR] | None = None) -> None:
        self.vocab: dict[str, CEFR] = {normalise(k): v for k, v in (vocab or {}).items()}

    def get_level(self, word: str) -> CEFR | None:
        """Get the assigned CEFR level for a German word.

        Tries the surface form first (direct dictionary hit), then falls
        back to progressively reduced morphological candidates (see
        ``src.lexicon.lemmatizer``) so that an inflected form absent from
        the scraped wordlist -- but whose lemma is present -- still
        resolves. Returns the level of the first candidate that is found,
        or ``None`` if nothing resolves.
        """
        normalized = normalise(word)
        if normalized in self.FUNCTION_WORDS:
            return "A1"
        for candidate in lemma_candidates(normalized):
            level = self.vocab.get(candidate)
            if level is not None:
                return level
        return None

    def is_within_ceiling(
        self, word: str, ceiling: CEFR, context_tokens: list[str] | None = None
    ) -> bool:
        """Check if a word is within or below the specified CEFR ceiling.

        A word is considered within ceiling if *any* morphological reading
        of it (surface form or a reduced candidate lemma, optionally
        combined with a separable-verb prefix found in ``context_tokens``)
        resolves to a level at or below ``ceiling``. This is deliberately
        more generous than a first-match lookup: the underlying vocabulary
        data is scraped surface forms with arbitrary per-form level
        assignment, so an inflected surface form can be mis-leveled even
        though its lemma is not. A word is only rejected when no candidate
        resolves within the ceiling at all.
        """
        normalized = normalise(word)
        if normalized in self.FUNCTION_WORDS or normalized in self.PROPER_NOUNS:
            return True

        resolved_any = False
        for candidate in lemma_candidates(normalized, context_tokens):
            level = self.vocab.get(candidate)
            if level is None:
                continue
            resolved_any = True
            if self.LEVEL_RANKS[level] <= self.LEVEL_RANKS[ceiling]:
                return True

        # docs/audits/stage-04-recovery-plan.md fix C. This used to return
        # False here, treating "absent from the wordlist" as identical to
        # "above the ceiling". It is not. The list holds 11,633 entries and
        # does not contain "Vorstand", "Projektleiter", "Analyse" or "These",
        # all of which are ordinary B2 words; every one of them was rejected
        # as too hard for B2.
        #
        # A wordlist is evidence a word is EASY. Its silence is not evidence
        # that a word is hard, and no scrape will ever be complete enough to
        # make it so. An unresolved word is unknown, and unknown passes --
        # counted, via ``unknown_words``, so the list's coverage stays
        # visible instead of silently punitive.
        if not resolved_any:
            return True
        return False

    def unknown_words(self, sentence: str) -> list[str]:
        """Return tokens that resolve to no level at all.

        Reported by the pilot so wordlist coverage is measurable. A rising
        unknown rate means the list needs extending; it does not mean the
        generated items got harder.
        """
        tokens = _CONTENT_TOKEN_RE.findall(sentence)
        all_tokens = _ALL_TOKEN_RE.findall(sentence)
        unknown: list[str] = []
        for token in tokens:
            normalized = normalise(token)
            if normalized in self.FUNCTION_WORDS or normalized in self.PROPER_NOUNS:
                continue
            if all(self.vocab.get(c) is None for c in lemma_candidates(normalized, all_tokens)):
                unknown.append(token)
        return unknown

    def _best_resolved_rank(self, word: str, context_tokens: list[str] | None = None) -> int | None:
        """Return the numeric CEFR rank (1-4) of the easiest resolving
        morphological reading of ``word``, or ``None`` if no candidate
        resolves at all.

        "Easiest" mirrors ``is_within_ceiling``'s own generosity: if any
        candidate reading resolves to a lower (easier) level, that is the
        reading used, on the same reasoning that a scraped wordlist can
        mis-level one inflected surface form without the word itself being
        hard.
        """
        normalized = normalise(word)
        best: int | None = None
        for candidate in lemma_candidates(normalized, context_tokens):
            level = self.vocab.get(candidate)
            if level is None:
                continue
            rank = self.LEVEL_RANKS[level]
            if best is None or rank < best:
                best = rank
        return best

    def _band_distance_over_ceiling(
        self, word: str, ceiling: CEFR, context_tokens: list[str] | None = None
    ) -> int:
        """How many CEFR bands the easiest resolving reading of ``word``
        sits above ``ceiling``. ``0`` for a function word, a proper noun, an
        unresolved (unknown) word, or a word already at or below ceiling --
        all of these are "no distance to charge against the budget".
        """
        normalized = normalise(word)
        if normalized in self.FUNCTION_WORDS or normalized in self.PROPER_NOUNS:
            return 0
        best = self._best_resolved_rank(normalized, context_tokens)
        if best is None:
            return 0
        return max(0, best - self.LEVEL_RANKS[ceiling])

    def check_ceiling_budget(self, sentence: str, ceiling: CEFR) -> CeilingBudgetResult:
        """Check ``sentence`` against ``ceiling`` with the budgeted rule.

        docs/audits/stage-04-a2-pilot-audit.md, "On the vocabulary ceiling":
        requiring every word to sit at or below the grammar level is
        stricter than any graded reader. Content words two or more CEFR
        bands above the ceiling are always violations (``Ablauf``, B2,
        against an A2 ceiling). Content words exactly one band above the
        ceiling are tolerated up to ``CEILING_BUDGET_MAX_ONE_BAND_WORDS`` of
        them (ordinary i+1: a B1 noun in an A2 sentence, the gap elsewhere);
        beyond that budget they become violations too. Either way, every
        one-band-over word found is reported back via ``over_budget`` so a
        forgiven word stays visible on the result rather than disappearing
        silently.
        """
        tokens = _CONTENT_TOKEN_RE.findall(sentence)
        all_tokens = _ALL_TOKEN_RE.findall(sentence)

        hard_violations: list[str] = []
        one_band_over: list[str] = []
        for token in tokens:
            normalized = normalise(token)
            if normalized in self.FUNCTION_WORDS or normalized in self.PROPER_NOUNS:
                continue
            distance = self._band_distance_over_ceiling(token, ceiling, context_tokens=all_tokens)
            if distance >= 2:
                hard_violations.append(token)
            elif distance == 1:
                one_band_over.append(token)

        if len(one_band_over) > self.CEILING_BUDGET_MAX_ONE_BAND_WORDS:
            violations = [*hard_violations, *one_band_over]
        else:
            violations = hard_violations

        return CeilingBudgetResult(violations=violations, over_budget=one_band_over)

    def validate_sentence(self, sentence: str, ceiling: CEFR) -> list[str]:
        """Return a list of words in the sentence that violate the budgeted
        CEFR ceiling (see ``check_ceiling_budget``)."""
        return self.check_ceiling_budget(sentence, ceiling).violations

    def save(self, path: Path | str) -> None:
        """Save vocabulary store to JSON."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            json.dump(self.vocab, f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: Path | str) -> "VocabularyStore":
        """Load vocabulary store from JSON."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Vocabulary file not found: {p}")
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(vocab=data)
