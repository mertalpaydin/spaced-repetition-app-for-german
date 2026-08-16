"""Prompt builder module for generating LLM requests with strict topic-leak prevention."""

import json
import re
from typing import ClassVar

from src.contracts import Difficulty
from src.corpus.tatoeba import CarrierSentence
from src.generation.spec import TopicSpec


class PromptBuilder:
    """Constructs deterministic, structured prompts for batch item generation."""

    # Minimum length (in characters) either half of a two-part compound must
    # have to count as a grammar "stem" in ``_compound_leak_match``. All real
    # stems below are >= 4 characters; this floor exists purely to stop a
    # hypothetical future one- or two-letter stem from matching almost
    # anything.
    _MIN_COMPOUND_STEM_LEN: ClassVar[int] = 3

    GRAMMAR_TERMS_BLOCKLIST: ClassVar[set[str]] = {
        "nominativ",
        "akkusativ",
        "dativ",
        "genitiv",
        "kasus",
        "fall",
        "präsens",
        "praesens",
        "perfekt",
        "präteritum",
        "praeteritum",
        "plusquamperfekt",
        "futur",
        "zeitform",
        "tempus",
        "konjunktiv",
        "indikativ",
        "imperativ",
        "modus",
        "passiv",
        "aktiv",
        "vorgangspassiv",
        "zustandspassiv",
        "partizip",
        "adjektivdeklination",
        "relativsatz",
        "nebensatz",
        "konjunktion",
        "subjunktion",
        "wechselpräposition",
        "wechselpraeposition",
        "reflexiv",
        "modalverb",
    }

    # Bare grammatical morphemes that name metalanguage on their own but are
    # too short/common a substring to blocklist outright (e.g. "form" alone
    # would flag "Formular"). They serve two purposes:
    #   1. As substring markers when mining taxonomy topic names for
    #      additional whole-word blocklist entries (see ``get_grammar_blocklist``).
    #   2. As one half of a two-part compound leak check (see
    #      ``_compound_leak_match``): "objekt"/"subjekt" are included
    #      specifically so "Akkusativobjekt" is caught as
    #      akkusativ + objekt.
    METALANGUAGE_STEMS: ClassVar[set[str]] = {
        "verb",
        "satz",
        "form",
        "endung",
        "deklination",
        "konjunktiv",
        "passiv",
        "kasus",
        "komparativ",
        "superlativ",
        "partizip",
        "infinitiv",
        "negation",
        "pronomen",
        "artikel",
        "präposition",
        "praeposition",
        "tempus",
        "modus",
        "objekt",
        "subjekt",
    }

    @classmethod
    def get_grammar_blocklist(cls) -> set[str]:
        """Derive blocklist dynamically from taxonomy grammatical labels."""
        terms = set(cls.GRAMMAR_TERMS_BLOCKLIST)
        try:
            from src.taxonomy.loader import load_taxonomy

            for topic in load_taxonomy():
                for word in re.findall(r"\b[a-zA-ZäöüÄÖÜß]+\b", topic.name_de.lower()):
                    if any(marker in word for marker in cls.METALANGUAGE_STEMS):
                        terms.add(word)
        except Exception:
            pass
        return terms

    @classmethod
    def _compound_leak_match(cls, word: str, stems: set[str]) -> bool:
        """True if ``word`` is a direct two-part compound of two grammar stems.

        German compounds freely without spaces or hyphens, so a genuine leak
        can hide inside a single word that is not itself a listed blocklist
        entry: "Dativform" (dativ + form), "Akkusativobjekt"
        (akkusativ + objekt), "Konjunktivsatz" (konjunktiv + satz). This
        splits ``word`` at every position and requires BOTH halves, taken on
        their own, to be grammar metalanguage (from ``GRAMMAR_TERMS_BLOCKLIST``
        or ``METALANGUAGE_STEMS``).

        Requiring *both* halves is the deliberate false-positive guard: an
        ordinary word that merely contains one grammar-looking substring is
        let through. "Formular" (form + ular), "verbessert" (verb +
        essert), "Ersatz" (er + satz), "Unfall" (un + fall), "jedenfalls"
        (... + falls) all have at most one side that is a real stem -- the
        other side ("ular", "essert", "er", "un", "falls") is not
        metalanguage, so none of these trip the compound check. They are
        ordinary lexical words, not topic leaks.

        Known, accepted gap: this only catches *direct* concatenation with
        no linking element (Fugenlaut). A compound spelled with a Fugen-s
        ("Perfekts-form") is not caught. None of the confirmed leak patterns
        we've seen need one, so that complexity is not added pre-emptively.
        """
        n = len(word)
        min_len = cls._MIN_COMPOUND_STEM_LEN
        for i in range(min_len, n - min_len + 1):
            if word[:i] in stems and word[i:] in stems:
                return True
        return False

    @classmethod
    def check_for_topic_leaks(cls, text: str) -> list[str]:
        """Scan a prompt or generated string for forbidden grammatical terminology.

        Matching rule: a word counts as a leak if it is EITHER
          1. a whole word equal to a blocklist term (checked by tokenising
             the text on word boundaries and comparing whole tokens, not by
             substring search), OR
          2. a two-part compound in which both halves are, independently,
             grammar metalanguage (``_compound_leak_match``).

        We never fall back to plain substring containment. That is the
        exact defect this function used to have: "verbessert" contains
        "verb" and "jedenfalls" contains "fall" as raw substrings, so a
        naive ``term in text`` scan flagged ordinary German sentences as
        topic leaks. Tokenising first, and requiring an exact whole-word (or
        whole-compound-half) match, is what lets "Er hat sein Deutsch
        verbessert" and "Das ist jedenfalls richtig" through while still
        catching "Setze ins Perfekt" and "Die Dativform ist unregelmäßig".
        """
        lower_text = text.lower()
        blocklist = cls.get_grammar_blocklist()
        stems = cls.GRAMMAR_TERMS_BLOCKLIST | cls.METALANGUAGE_STEMS
        words = re.findall(r"[a-zA-ZäöüÄÖÜß]+", lower_text)

        leaks: list[str] = []
        for word in words:
            if word in leaks:
                continue
            if word in blocklist or cls._compound_leak_match(word, stems):
                leaks.append(word)
        return leaks

    def build_generation_prompt(
        self,
        spec: TopicSpec,
        count: int,
        difficulty: Difficulty,
        seed_sentences: list[CarrierSentence] | None = None,
    ) -> str:
        """Construct a structured generation prompt for single items.

        Guarantees byte-level determinism given identical inputs.
        """
        seeds = seed_sentences or []
        seed_texts = [s.german_text for s in seeds[:3]]

        tier_desc = spec.difficulty_tiers.get(difficulty, "Standard single clause sentence.")

        prohibitions = [
            "NEVER name or hint at grammar rules in the prompt (NO 'Setze ins Dativ').",
            "Ensure only ONE grammatically correct filler fits the gap.",
            "Provide exactly 3 distractors per item with implied_topic_id.",
            # 01-foundation.md's solvability rule / docs/audits/
            # stage-04-pilot-2026-08-15.md fix 5: eligible_types is a
            # correctness constraint, not a stylistic preference. The model
            # must pick per-item from the allowed list, not default to
            # cloze_free regardless of what the topic actually permits.
            "Each item's own 'type' field must be EXACTLY one value chosen "
            "from allowed_item_types below, never a value outside that list.",
            # docs/audits/generation-track-plan.md Cycle 3: the gloss
            # disambiguates a gap nothing else in the German sentence
            # narrows to one lexeme (a tense choice, most often), and does
            # so WITHOUT naming the grammar topic the way a category label
            # would -- see CLAUDE.md rule 2 and this project's product plan.
            # It must translate MEANING only.
            "For 'gloss_en', give a natural English translation of the "
            "COMPLETE sentence with the gap filled by the intended answer. "
            "Translate meaning only: NEVER a grammar hint, NEVER a rule "
            "statement, NEVER a category name like 'past tense' or "
            "'Perfekt'. Just what the finished German sentence says, in "
            "natural English.",
        ]
        if spec.forcing_element:
            prohibitions.append(
                "The carrier sentence MUST include this forcing element, or the "
                f"gap cannot be solved by reasoning and the item is invalid: "
                f"{spec.forcing_element.note}"
            )

        # docs/audits/stage-04-recovery-plan.md fix D. Parentheses in the
        # carrier used to be rejected unconditionally by verification layer 1,
        # so the cue could only live in a separate JSON field the learner may
        # never see. That ban was a misdiagnosis and is reversed; the inline
        # bracketed lemma is the standard textbook rendering and is the
        # cheapest way to make a gap solvable by reasoning.
        #
        # Rule 2 forbids naming the grammar TOPIC. A lexeme in brackets names
        # a lexeme, and "Gestern ___ (gehen) ich nach Hause." never says the
        # word "Präteritum".
        if "cloze_cued" in spec.item_types:
            prohibitions.append(
                "For a 'cloze_cued' item, write the cue INLINE in the carrier as a "
                "single bracketed citation form directly before or after the gap, "
                "e.g. 'Gestern ___ (gehen) ich nach Hause.', AND repeat it in the "
                "'cue' field. The bracketed word must be the dictionary form the "
                "learner has to inflect (infinitive for a verb, nominative singular "
                "for a noun, uninflected stem for an adjective). It must NEVER be "
                "the target form itself, and it must be the word the gap tests, not "
                "some other word in the sentence."
            )

        prompt_payload = {
            "instruction": "Generate German grammar training items per spec.",
            "topic_id": spec.topic_id,
            "target_cefr": spec.cefr,
            "difficulty_tier": difficulty,
            "difficulty_guidelines": tier_desc,
            "allowed_item_types": spec.item_types,
            "forcing_element": spec.forcing_element.note if spec.forcing_element else None,
            "count": count,
            "max_tokens_per_sentence": spec.max_tokens_per_sentence,
            "vocabulary_ceiling": spec.vocabulary_ceiling,
            "prohibitions": prohibitions,
            "gold_few_shot_examples": [
                {
                    "prompt": g.prompt,
                    "accepted_answers": g.accepted_answers,
                    "difficulty": g.difficulty,
                }
                for g in spec.gold_examples
            ],
            "seed_context_examples": seed_texts,
            "required_output_schema": {
                "items": [
                    {
                        "topic_id": spec.topic_id,
                        "type": "one value from allowed_item_types above, chosen per item",
                        "difficulty": difficulty,
                        "prompt": "Natural German sentence with gap marked as ___",
                        "cue": "optional base form or infinitive if cloze_cued",
                        "proposed_answer": "exact target form",
                        "gloss_en": (
                            "natural English translation of the complete "
                            "sentence with the gap filled by proposed_answer; "
                            "meaning only, never a grammar hint"
                        ),
                        "distractors": [
                            {"text": "distractor1", "implied_topic_id": "alternate_topic_id"},
                            {"text": "distractor2", "implied_topic_id": "alternate_topic_id"},
                            {"text": "distractor3", "implied_topic_id": "alternate_topic_id"},
                        ],
                    }
                ]
            },
        }

        # Deterministic json formatting
        return json.dumps(prompt_payload, indent=2, sort_keys=True, ensure_ascii=False)

    def build_paragraph_block_prompt(
        self,
        primary_spec: TopicSpec,
        filler_specs: list[TopicSpec],
        gap_count: int = 4,
    ) -> str:
        """Construct prompt for a paragraph cloze block with interleaved gap tags."""
        payload = {
            "instruction": "Generate a short coherent German text (60-100 words) with gaps.",
            "primary_context_topic": primary_spec.topic_id,
            "filler_topics": [f.topic_id for f in filler_specs],
            "target_gap_count": gap_count,
            "cefr": primary_spec.cefr,
            "rules": [
                "The carrier text must form a coherent narrative or paragraph.",
                "No two adjacent gaps may test the same topic_id (interleaving within block).",
                "Gaps must be marked sequentially as ___1___, ___2___, etc.",
                "Provide exactly 3 distractors per gap.",
            ],
        }
        return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
