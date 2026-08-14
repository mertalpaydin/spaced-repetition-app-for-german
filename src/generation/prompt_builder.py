"""Prompt builder module for generating LLM requests with strict topic-leak prevention."""

import json
import re
from typing import ClassVar

from src.contracts import Difficulty
from src.corpus.tatoeba import CarrierSentence
from src.generation.spec import TopicSpec


class PromptBuilder:
    """Constructs deterministic, structured prompts for batch item generation."""

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

    @classmethod
    def get_grammar_blocklist(cls) -> set[str]:
        """Derive blocklist dynamically from taxonomy grammatical labels."""
        terms = set(cls.GRAMMAR_TERMS_BLOCKLIST)
        metalanguage_markers = {
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
        }
        try:
            from src.taxonomy.loader import load_taxonomy

            for topic in load_taxonomy():
                for word in re.findall(r"\b[a-zA-ZäöüÄÖÜß]+\b", topic.name_de.lower()):
                    if any(marker in word for marker in metalanguage_markers):
                        terms.add(word)
        except Exception:
            pass
        return terms

    @classmethod
    def check_for_topic_leaks(cls, text: str) -> list[str]:
        """Scan a prompt or generated string for forbidden grammatical terminology."""
        lower_text = text.lower()
        leaks: list[str] = []
        blocklist = cls.get_grammar_blocklist()
        for term in blocklist:
            if re.search(rf"\b{re.escape(term)}\b", lower_text):
                leaks.append(term)
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
        types_str = ", ".join(spec.item_types)

        prompt_payload = {
            "instruction": "Generate German grammar training items per spec.",
            "topic_id": spec.topic_id,
            "target_cefr": spec.cefr,
            "difficulty_tier": difficulty,
            "difficulty_guidelines": tier_desc,
            "allowed_item_types": spec.item_types,
            "count": count,
            "max_tokens_per_sentence": spec.max_tokens_per_sentence,
            "vocabulary_ceiling": spec.vocabulary_ceiling,
            "prohibitions": [
                "NEVER name or hint at grammar rules in the prompt (NO 'Setze ins Dativ').",
                "Ensure only ONE grammatically correct filler fits the gap.",
                "Provide exactly 3 distractors per item with implied_topic_id.",
            ],
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
                        "type": types_str,
                        "difficulty": difficulty,
                        "prompt": "Natural German sentence with gap marked as ___",
                        "cue": "optional base form or infinitive if cloze_cued",
                        "proposed_answer": "exact target form",
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
