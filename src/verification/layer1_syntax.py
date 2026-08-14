"""Layer 1: Determinism, Syntax, Length, Distractor Count, and Lexical Ceiling Validator."""

import re
from typing import ClassVar

from src.contracts import CandidateItem, ErrorTaxonomy
from src.generation.prompt_builder import PromptBuilder
from src.generation.spec import TopicSpec
from src.lexicon.vocabulary import VocabularyStore


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
        # 1. Gap presence
        if "___" not in item.prompt and not re.search(r"___\d+___", item.prompt):
            return False, "Missing gap placeholder '___' in prompt.", "structural_malformation"

        # Multiple single gaps in non-paragraph cloze
        if item.type != "paragraph_cloze" and item.prompt.count("___") > 1:
            return (
                False,
                "Multiple gap placeholders in single-sentence item.",
                "structural_malformation",
            )

        # 2. Distractor count check (must have exactly 3 distractors)
        if len(item.distractors) != 3:
            return (
                False,
                f"Expected exactly 3 distractors, found {len(item.distractors)}.",
                "structural_malformation",
            )

        # Distractor non-emptiness and uniqueness
        distractor_texts = [d.text.strip().lower() for d in item.distractors]
        if any(not t for t in distractor_texts):
            return False, "Empty distractor text found.", "structural_malformation"
        if len(set(distractor_texts)) != 3:
            return False, "Duplicate distractors found.", "structural_malformation"
        if item.proposed_answer.strip().lower() in distractor_texts:
            return (
                False,
                "Proposed answer is present among distractors.",
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
        max_tokens = spec.max_tokens_per_sentence if spec else 35
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
        if self.vocab_store and spec:
            violations = self.vocab_store.validate_sentence(item.prompt, spec.vocabulary_ceiling)
            answer_violations = self.vocab_store.validate_sentence(
                item.proposed_answer, spec.vocabulary_ceiling
            )
            all_violations = violations + [v for v in answer_violations if v not in violations]
            if all_violations:
                return (
                    False,
                    f"Vocabulary ceiling ({spec.vocabulary_ceiling}) exceeded by: "
                    f"{all_violations}.",
                    "vocabulary_ceiling_violation",
                )

        return True, None, None
