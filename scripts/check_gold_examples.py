"""Objective quality gate for the gold examples in data/specs/*.yaml.

Gold examples are the few-shot anchor for generation (docs/02-content-pipeline.md
stage 3), so a wrong one propagates into every item a topic ever produces. This
script is the automated bar they must clear. It is deliberately mechanical: it
cannot judge whether a sentence is idiomatic, but it can prove that an example
tests the morpheme its topic claims to test.

A gold example passes when all of the following hold:

1. It has a prompt containing exactly one gap, and a non-empty accepted-answers
   list.
2. It is not the fabricated placeholder that build_spec_for_topic used to emit.
3. The prompt contains no grammatical terminology, using the same blocklist the
   generation prompt and verification layer 1 use. An example that names its own
   topic teaches the generator to leak.
4. The answer does not appear elsewhere in the prompt.
5. For a topic with a non-empty facet space, the answer resolves to a facet that
   is not entirely Unk. This is the load-bearing check: it is what distinguishes
   "the answer IS the tested morpheme" from "the answer is some other word in a
   sentence about the topic". A noun answer under a dative-preposition topic
   fails here, which is exactly the defect this script exists to catch.
6. Across the topic's whole gold set, at least two DISTINCT facet values appear,
   so the examples demonstrate the topic varying rather than one frozen cell.

Run: python -m scripts.check_gold_examples [--topic <id>] [--quiet]
Exit code 0 when every spec passes, 1 otherwise.
"""

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml
from src.contracts import BankItem, Topic
from src.generation.prompt_builder import PromptBuilder
from src.taxonomy.facets import derive_facet, facet_space
from src.taxonomy.loader import load_taxonomy

SPEC_DIR = Path("data/specs")
PLACEHOLDER_MARKERS = ("Hier steht Beispielsatz", "Beispielsatz Nummer")


def _all_unk(facet: str | None, space: tuple[str, ...]) -> bool:
    if facet is None:
        return True
    return set(facet.split("|")) == {f"{dim}=Unk" for dim in space}


def check_topic(topic: Topic, spec: dict[str, Any]) -> list[str]:
    """Return a list of failure strings for one topic. Empty means it passes."""
    failures: list[str] = []
    golds: list[dict[str, Any]] = spec.get("gold_examples") or []
    if len(golds) < 3:
        failures.append(f"only {len(golds)} gold examples, expected at least 3")

    blocklist = PromptBuilder.get_grammar_blocklist()
    space = facet_space(topic)
    facets: set[str | None] = set()

    for idx, gold in enumerate(golds):
        label = f"gold[{idx}]"
        prompt = (gold or {}).get("prompt") or ""
        answers = [str(a) for a in ((gold or {}).get("accepted_answers") or [])]

        if any(marker in prompt for marker in PLACEHOLDER_MARKERS):
            failures.append(f"{label}: fabricated placeholder example")
            continue
        if prompt.count("___") != 1:
            failures.append(f"{label}: expected exactly one gap, found {prompt.count('___')}")
            continue
        if not answers:
            failures.append(f"{label}: no accepted answers")
            continue

        lowered = prompt.lower()
        hits = [term for term in blocklist if term.lower() in lowered]
        if hits:
            failures.append(f"{label}: prompt names grammar terminology {hits}")

        head, _, tail = prompt.partition("___")
        rest = f"{head} {tail}".lower()
        for answer in answers:
            if answer.lower() in rest.split():
                failures.append(f"{label}: answer {answer!r} also appears in the prompt")
                break

        if space:
            item = BankItem(
                id=f"gold_{topic.id}_{idx}",
                topic_id=topic.id,
                type="cloze_free",
                difficulty=int(gold.get("difficulty") or 1),  # type: ignore[arg-type]
                cefr=topic.cefr,
                prompt=prompt,
                accepted_answers=answers,
            )
            facet = derive_facet(item, topic)
            facets.add(facet)
            if _all_unk(facet, space):
                failures.append(
                    f"{label}: answer {answers[0]!r} yields an all-Unk facet over {space}, "
                    "so it does not exercise the tested morpheme"
                )

    if space and len({f for f in facets if not _all_unk(f, space)}) < 2:
        failures.append(
            f"gold set shows fewer than 2 distinct facet values over {space}; "
            "the examples do not demonstrate the topic varying"
        )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate gold examples in data/specs/.")
    parser.add_argument("--topic", type=str, default=None, help="Check a single topic id.")
    parser.add_argument("--quiet", action="store_true", help="Only print the summary.")
    args = parser.parse_args()

    taxonomy = load_taxonomy()
    topics = taxonomy if isinstance(taxonomy, list) else list(taxonomy.values())
    by_id = {t.id: t for t in topics}

    targets = [args.topic] if args.topic else sorted(by_id)
    failing = 0
    checked = 0

    for topic_id in targets:
        topic = by_id.get(topic_id)
        if topic is None:
            print(f"UNKNOWN TOPIC {topic_id}")
            failing += 1
            continue
        spec_path = SPEC_DIR / f"{topic_id}.yaml"
        if not spec_path.exists():
            print(f"MISSING SPEC {topic_id}")
            failing += 1
            continue
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
        checked += 1
        problems = check_topic(topic, spec)
        if problems:
            failing += 1
            if not args.quiet:
                print(f"\nFAIL {topic_id}")
                for problem in problems:
                    print(f"    - {problem}")

    print(f"\nchecked {checked} specs: {checked - failing} pass, {failing} fail")
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
