"""Topic generation spec sheet contracts, loader, and generator."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from src.contracts import CEFR, Difficulty, ItemType, Topic

# stage-04-pilot-2026-08-15.md D6: a machine-checkable classification of what
# forces the answer in the carrier. Each kind maps to a specific mechanical
# (or explicitly-declared-unverifiable) check in
# ``scripts/check_gold_examples.py`` -- see that module's ``FORCING_ELEMENT_
# CHECKS`` table for what each kind actually asserts.
ForcingElementKind = Literal[
    "temporal_anchor",
    "motion_or_location_verb",
    "governing_word",
    "governing_preposition",
    "correlative_first_half",
    "unambiguous_antecedent",
    "clause_relation",
    "anteriority_anchor",
    "subject_person_marker",
    "discourse_referent",
]


class ForcingElement(BaseModel):
    """Declares what in the carrier forces the gap's answer, and how to check it.

    ``kind`` selects a mechanical (or explicitly-unverifiable) check; ``note``
    is a human-readable description used in the generation prompt and, for
    kinds with a closed set (e.g. ``governing_word``), the string the checker
    looks for.
    """

    model_config = ConfigDict(frozen=True)
    kind: ForcingElementKind
    note: str


class GoldExample(BaseModel):
    """A human-verified or anchored gold example demonstrating proper item structure."""

    model_config = ConfigDict(frozen=True)
    prompt: str
    accepted_answers: list[str]
    difficulty: Difficulty = 1
    cue: str | None = None


class TopicSpec(BaseModel):
    """Specification sheet guiding LLM generation for a single grammar topic."""

    model_config = ConfigDict(frozen=True)
    topic_id: str
    cefr: CEFR
    target_form: str
    item_types: list[ItemType]
    max_tokens_per_sentence: int = 16
    vocabulary_ceiling: CEFR
    difficulty_tiers: dict[int, str] = Field(
        default_factory=lambda: {
            1: "Simple single clause, high-frequency vocabulary, concrete context.",
            2: "One subordinate or coordinate clause, mid-frequency vocabulary.",
            3: "Nested clauses, distractors present in context, low-frequency vocabulary.",
        }
    )
    forbidden: list[str] = Field(
        default_factory=lambda: [
            "any grammatical terminology in the prompt",
            "any instruction naming case, tense, or mood",
            "the answer appearing elsewhere in the sentence",
            "unnatural or convoluted German phrasing",
        ]
    )
    # 01-foundation.md's solvability rule / stage-04-pilot-2026-08-15.md D1:
    # applies to every topic, not only ones that keep cloze_free -- it names
    # the specific element in the carrier that forces the gap's answer (a
    # temporal anchor, a governing preposition, an antecedent, the first half
    # of a two-part connector...), and generation must require it to be
    # present. ``None`` is still valid for topics where the item type alone
    # already guarantees solvability (paragraph_cloze/transformation carry
    # their own forcing context structurally) or where no forcing_element has
    # been authored for this topic yet.
    forcing_element: ForcingElement | None = None
    gold_examples: list[GoldExample] = Field(default_factory=list)


def load_spec(path: Path | str) -> TopicSpec:
    """Load a TopicSpec from a YAML file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Spec file not found: {p}")

    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return TopicSpec.model_validate(data)


def save_spec(spec: TopicSpec, path: Path | str) -> None:
    """Save a TopicSpec to a YAML file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        yaml.dump(spec.model_dump(), f, sort_keys=False, allow_unicode=True)


def build_spec_for_topic(topic: Topic) -> TopicSpec:
    """Derive a complete TopicSpec with 3 gold examples from a Topic model."""
    gold_examples: list[GoldExample] = []

    # Derive gold examples from intro_card worked examples where available
    if (
        topic.intro_card
        and not isinstance(topic.intro_card, str)
        and topic.intro_card.worked_examples
    ):
        for ex in topic.intro_card.worked_examples:
            # Mask the key element to create cloze prompts
            parts = ex.split()
            if len(parts) >= 3:
                masked_prompt = (
                    f"{parts[0]} ___ {' '.join(parts[2:])}" if len(parts) > 2 else f"{parts[0]} ___"
                )
                gold_examples.append(
                    GoldExample(
                        prompt=masked_prompt,
                        accepted_answers=[parts[1].strip(".,?!")],
                        difficulty=1,
                    )
                )

    # Ensure minimum 3 gold examples per spec sheet
    while len(gold_examples) < 3:
        idx = len(gold_examples) + 1
        gold_examples.append(
            GoldExample(
                prompt=f"Hier steht Beispielsatz Nummer {idx} mit ___ Lücke.",
                accepted_answers=["einer"],
                difficulty=1,
            )
        )

    # Context-requiring topics should only request paragraph_cloze / error_correction
    item_types = list(topic.eligible_types)
    if topic.requires_context:
        item_types = [t for t in item_types if t in ["paragraph_cloze", "error_correction"]]
        if not item_types:
            item_types = ["paragraph_cloze"]

    max_tokens = 14 if topic.cefr == "A1" else (18 if topic.cefr in ["A2", "B1"] else 24)

    return TopicSpec(
        topic_id=topic.id,
        cefr=topic.cefr,
        target_form=topic.description,
        item_types=item_types,
        max_tokens_per_sentence=max_tokens,
        vocabulary_ceiling=topic.cefr,
        gold_examples=gold_examples[:3],
    )
