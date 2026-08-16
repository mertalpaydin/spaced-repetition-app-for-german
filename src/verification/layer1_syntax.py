"""Layer 1: Determinism, Syntax, Length, Distractor Count, and Lexical Ceiling Validator."""

import re
from typing import ClassVar

from src.contracts import CandidateItem, ErrorTaxonomy
from src.generation.prompt_builder import PromptBuilder
from src.generation.spec import TopicSpec
from src.lexicon.vocabulary import VocabularyStore
from src.verification.repair import MIN_DISTRACTORS_AFTER_REPAIR


class Layer1SyntaxValidator:
    """Validates structural invariants: gaps, token limits, distractor counts, and CEFR ceiling."""

    # A small, deliberately conservative blocklist of colloquial / slang tokens
    # that are inappropriate register for graded learner material, regardless
    # of CEFR level. This is what makes ``register_mismatch`` reachable: no
    # other layer has any basis for judging register. Kept intentionally short
    # and unambiguous: words with a legitimate common standard-register sense
    # ("voll" = "full", "alter" = "age/old", "läuft" = "runs") are deliberately
    # excluded, since blocking them would itself be a false-positive source.
    COLLOQUIAL_BLOCKLIST: ClassVar[set[str]] = {
        "digga",
        "krass",
        "geil",
        "bock",
        "chillen",
        "abgefahren",
        "mega",
        "kumpel",
    }

    def __init__(self, vocab_store: VocabularyStore | None = None) -> None:
        self.vocab_store = vocab_store

    def validate(
        self, item: CandidateItem, spec: TopicSpec | None = None
    ) -> tuple[bool, str | None, ErrorTaxonomy | None]:
        """Validate candidate item against Layer 1 deterministic rules."""
        # 1. Gap presence. An ``error_correction`` item presents an already
        # INCORRECT sentence for the learner to fix and correctly has no
        # gap at all -- "Ich sehe ein Mann auf der Straße. Der Mann liest
        # ein Buch." with answer "Der Mann liest" is not malformed, it is
        # exactly the shape this type is meant to have. Requiring "___"
        # unconditionally treated that as a defect (24 rejections in one
        # pilot). Both the presence check and the multiple-gaps check below
        # are about the SAME "___" convention, so both are skipped together.
        if item.type != "error_correction":
            if "___" not in item.prompt and not re.search(r"___\d+___", item.prompt):
                return (
                    False,
                    "Missing gap placeholder '___' in prompt.",
                    "structural_malformation",
                )

            # Multiple single gaps in non-paragraph cloze
            if item.type != "paragraph_cloze" and item.prompt.count("___") > 1:
                return (
                    False,
                    "Multiple gap placeholders in single-sentence item.",
                    "structural_malformation",
                )

        # 2. Distractor count. Duplicate and answer-colliding distractors are
        # REPAIRED before this layer runs (src/verification/repair.py), not
        # rejected: a sloppy distractor list is metadata noise, not a defect
        # in the item. What remains checkable here is whether enough survived
        # repair to render a real multiple choice.
        if len(item.distractors) < MIN_DISTRACTORS_AFTER_REPAIR:
            return (
                False,
                f"Only {len(item.distractors)} usable distractors after repair, "
                f"need at least {MIN_DISTRACTORS_AFTER_REPAIR}.",
                "structural_malformation",
            )

        # 2b. Parenthetical cue check. docs/audits/stage-04-recovery-plan.md
        # fix D reverses the unconditional ban this check used to be. The ban
        # traced to stage-04-pilot-2026-08-14.md item 7,
        # "...wohnt dort ___ (Katze) drin.", which that audit misdiagnosed:
        # the item was bad because "dort ... drin" is not idiomatic and
        # because "(Katze)" supplied the NOUN while the gap wanted the
        # ARTICLE. The parentheses were never the fault.
        #
        # CLAUDE.md rule 2 forbids naming the grammar TOPIC. A lexeme in
        # parentheses names a lexeme. "Gestern ___ (gehen) ich nach Hause."
        # is what every German textbook prints and it never says the word
        # "Präteritum" -- the terminology blocklist below still catches that.
        # So parentheses are permitted, subject to two narrow rules that
        # encode what item 7 actually got wrong.
        for aside in re.findall(r"\(([^)]*)\)", item.prompt):
            cue_text = aside.strip()
            if not cue_text:
                return (
                    False,
                    "Prompt contains an empty parenthetical.",
                    "structural_malformation",
                )
            # Rule 1: the cue must not hand over the answer. A cue is the
            # citation form the learner inflects; if it already matches the
            # target form there is nothing left to retrieve.
            if cue_text.lower() == item.proposed_answer.strip().lower():
                return (
                    False,
                    f"Parenthetical cue '{cue_text}' is identical to the answer, "
                    "so the item tests nothing.",
                    "answer_leak",
                )
            # Rule 2: a cue must be a single citation form, not a phrase.
            # Multi-word asides in generated carriers have been glosses and
            # translations rather than cues, which do leak.
            if len(cue_text.split()) > 1:
                return (
                    False,
                    f"Parenthetical '{cue_text}' is a phrase, not a single cue lemma.",
                    "structural_malformation",
                )

        # 3. Topic leak check (forbidden grammatical terminology named in the prompt)
        leaks = PromptBuilder.check_for_topic_leaks(item.prompt)
        if leaks:
            return (
                False,
                f"Prompt contains forbidden grammatical terminology: {leaks}.",
                "topic_leak",
            )

        # 4. Token length check. A too-long carrier sentence is not a broken
        # structure (the gap, distractors, and punctuation are all fine) --
        # it is a difficulty/readability mismatch for the target tier, i.e. a
        # pedagogical defect.
        #
        # docs/audits/stage-04-a2-pilot-audit.md: the A2 limit of 18 tokens
        # (set per-topic in data/specs/*.yaml, not owned by this module)
        # rejected four otherwise-good items at 20 to 25 tokens, and several
        # of the best items in the audited clean set ran past 18. That limit
        # lives in the spec files, which this module does not own and does
        # not edit. What this module does own is the fallback used when no
        # spec is supplied at all -- raised to 30 so a spec-less caller (a
        # direct ``validate()`` call with no ``TopicSpec``) reflects the same
        # "graded reader, not zero tolerance" judgment the audit argues for.
        max_tokens = spec.max_tokens_per_sentence if spec else 30
        tokens = re.findall(r"\b\w+\b", item.prompt)
        if len(tokens) > max_tokens and item.type != "paragraph_cloze":
            return (
                False,
                f"Prompt length ({len(tokens)} tokens) exceeds limit ({max_tokens}).",
                "pedagogical_flaw",
            )

        # 5. Register check: colloquial / slang tokens are the wrong register for
        # graded learner material at any CEFR level. Checked against the visible
        # prompt only (the accepted answer is checked for vocabulary ceiling
        # below, not register, since a single colloquial answer word is far
        # rarer than a colloquial carrier sentence and would need its own
        # judgment call about whether the *answer itself* is meant to be
        # colloquial, e.g. modal particle topics).
        prompt_tokens = re.findall(r"\b[A-ZÄÖÜa-zäöüß]+\b", item.prompt)
        colloquial_hits = [t for t in prompt_tokens if t.lower() in self.COLLOQUIAL_BLOCKLIST]
        if colloquial_hits:
            return (
                False,
                f"Prompt uses colloquial/slang register unsuitable for graded material: "
                f"{colloquial_hits}.",
                "register_mismatch",
            )

        # 6. Vocabulary ceiling check. Checked against BOTH the visible prompt
        # AND every accepted answer: an item whose prompt is clean but whose
        # answer itself is the above-ceiling word (the answer never appears in
        # the prompt by construction) would otherwise slip through undetected.
        # docs/audits/stage-04-recovery-plan.md fix C: this check rejected 25
        # of 100 items in batch_51fc18e48f7b, on a wordlist that does not
        # contain "Vorstand", "Projektleiter", "Analyse" or "These". A
        # vocabulary list is never complete, so it can prove a word is easy
        # but never that a word is hard. Above A2 the ceiling is therefore
        # advisory: the violation is recorded on the item for reporting, not
        # used to reject. A1 and A2 keep the hard gate, which is where a
        # ceiling earns its keep and where the wordlist is actually dense
        # (3460 A1 and 2947 A2 lemmas, against 808 for B2).
        #
        # docs/audits/stage-04-a2-pilot-audit.md, "On the vocabulary
        # ceiling": zero tolerance ("every word at or below the ceiling")
        # was stricter than any graded reader and was the single largest
        # rejection category in the A2 pilot (20 of 48). ``check_ceiling_
        # budget`` replaces it with a budget: up to two content words one
        # CEFR band above the ceiling are tolerated (ordinary i+1), while
        # anything two or more bands above -- or a third one-band-over word
        # -- still fails outright. ``over_budget`` lists which words the
        # budget was spent on; when the item otherwise passes, that spend is
        # recorded in the returned reason instead of disappearing silently.
        if self.vocab_store and spec:
            prompt_result = self.vocab_store.check_ceiling_budget(
                item.prompt, spec.vocabulary_ceiling
            )
            answer_result = self.vocab_store.check_ceiling_budget(
                item.proposed_answer, spec.vocabulary_ceiling
            )
            all_violations = prompt_result.violations + [
                v for v in answer_result.violations if v not in prompt_result.violations
            ]
            if all_violations and spec.vocabulary_ceiling in ("A1", "A2"):
                return (
                    False,
                    f"Vocabulary ceiling ({spec.vocabulary_ceiling}) exceeded by: "
                    f"{all_violations}.",
                    "vocabulary_ceiling_violation",
                )

            over_budget_words = prompt_result.over_budget + [
                w for w in answer_result.over_budget if w not in prompt_result.over_budget
            ]
            if over_budget_words:
                return (
                    True,
                    f"Vocabulary ceiling ({spec.vocabulary_ceiling}) budget spent on "
                    f"content word(s) one band above ceiling, forgiven within the "
                    f"{self.vocab_store.CEILING_BUDGET_MAX_ONE_BAND_WORDS}-word allowance: "
                    f"{over_budget_words}.",
                    None,
                )

        return True, None, None
