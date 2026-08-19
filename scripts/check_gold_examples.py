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
7. If the spec declares a ``forcing_element``, each gold example is checked
   against it (stage-04-pilot-2026-08-15.md D7). Two ``kind``s
   (``unambiguous_antecedent``, ``clause_relation``) have no mechanical check
   per D6's own table; those are counted as "unverifiable" and reported
   explicitly rather than silently passed, so the summary line cannot claim a
   check that never ran.

Run: python -m scripts.check_gold_examples [--topic <id>] [--quiet]
Exit code 0 when every spec passes, 1 otherwise.
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from src.contracts import BankItem, Topic
from src.generation.blanking.paradigms import TEMPORAL_ANCHOR_LEMMAS as _TEMPORAL_ANCHORS
from src.generation.prompt_builder import PromptBuilder
from src.taxonomy.facets import derive_facet, facet_space
from src.taxonomy.loader import load_taxonomy
from src.verification.layer3_solver import CORRELATIVE_TRIGGERS

SPEC_DIR = Path("data/specs")
PLACEHOLDER_MARKERS = ("Hier steht Beispielsatz", "Beispielsatz Nummer")

# D6: closed list a temporal_anchor gold example must draw from. Deliberately
# limited to unambiguous time expressions -- words that are ALWAYS temporal,
# never also e.g. a determiner or preposition, so a hit cannot be a false
# positive from an unrelated part of the sentence. Now the single canonical
# copy, in ``src.generation.blanking.paradigms`` -- docs/audits/cycle-06-report.md's
# next-cycle task 1 reuses this exact list for the blanking pipeline's own
# auxiliary-tense-anchor check rather than redeclaring it a second time.

# D6/D2: an anteriority_anchor is either an explicit nachdem/bevor clause, or
# a second past-tense finite verb the gap's action precedes -- the latter
# cannot be checked without a full parse, so this only checks the former,
# which is the mechanically-checkable half.
_ANTERIORITY_CONNECTORS: frozenset[str] = frozenset({"nachdem", "bevor"})

_SUBJECT_PRONOUNS_D6: frozenset[str] = frozenset(
    {"ich", "du", "er", "sie", "es", "wir", "ihr", "man"}
)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-zÄÖÜäöüß]+", text.lower())


def _note_tokens(note: str) -> set[str]:
    """Extract candidate closed-set members the author wrote into ``note``.

    Words wrapped in single/double/curly quotes are the author's explicit
    closed list; falling back to all capitalised-looking tokens would catch
    ordinary sentence-initial capitalisation too, so only quoted tokens count.
    """
    return {m.lower() for m in re.findall(r"['\"„“]([A-Za-zÄÖÜäöüß]+)['\"”]", note)}


def _check_temporal_anchor(prompt: str, note: str) -> bool:
    return bool(set(_tokens(prompt)) & _TEMPORAL_ANCHORS)


def _check_correlative_first_half(prompt: str, note: str) -> bool:
    return bool(set(_tokens(prompt)) & CORRELATIVE_TRIGGERS)


def _check_anteriority_anchor(prompt: str, note: str) -> bool:
    return bool(set(_tokens(prompt)) & _ANTERIORITY_CONNECTORS)


def _check_subject_person_marker(prompt: str, note: str) -> bool:
    tokens = _tokens(prompt)
    return bool(set(tokens) & _SUBJECT_PRONOUNS_D6) or any(t[0].isupper() for t in prompt.split())


def _check_discourse_referent(prompt: str, note: str) -> bool:
    # Bucket 1: the carrier must contain a preceding sentence to refer back
    # to. Sentence count, not shared-noun overlap -- proving co-reference
    # would need real coreference resolution, out of scope here.
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", prompt.strip()) if s]
    return len(sentences) >= 2


def _check_declared_token(prompt: str, note: str) -> bool | None:
    """Generic check for kinds whose closed set IS whatever the spec author
    quoted in ``note`` (governing_word, governing_preposition,
    motion_or_location_verb): at least one quoted token must appear in the
    carrier. ``None`` if the author quoted nothing to check against."""
    declared = _note_tokens(note)
    if not declared:
        return None
    return bool(set(_tokens(prompt)) & declared)


# D6's table. ``None`` marks a kind D6 itself declares has no mechanical
# check (unambiguous_antecedent, clause_relation): always reported as
# unverifiable, never silently passed.
FORCING_ELEMENT_CHECKS: dict[str, Any] = {
    "temporal_anchor": _check_temporal_anchor,
    "motion_or_location_verb": _check_declared_token,
    "governing_word": _check_declared_token,
    "governing_preposition": _check_declared_token,
    "correlative_first_half": _check_correlative_first_half,
    "unambiguous_antecedent": None,
    "clause_relation": None,
    "anteriority_anchor": _check_anteriority_anchor,
    "subject_person_marker": _check_subject_person_marker,
    "discourse_referent": _check_discourse_referent,
}


def _all_unk(facet: str | None, space: tuple[str, ...]) -> bool:
    if facet is None:
        return True
    return set(facet.split("|")) == {f"{dim}=Unk" for dim in space}


def check_topic(topic: Topic, spec: dict[str, Any]) -> tuple[list[str], int]:
    """Return (failure strings, unverifiable forcing-element check count).

    Empty failures means the topic passes; the count is reported separately
    so a clean pass cannot be mistaken for "every check actually ran"
    (stage-04-pilot-2026-08-15.md D7).
    """
    failures: list[str] = []
    unverifiable = 0
    golds: list[dict[str, Any]] = spec.get("gold_examples") or []
    if len(golds) < 3:
        failures.append(f"only {len(golds)} gold examples, expected at least 3")

    blocklist = PromptBuilder.get_grammar_blocklist()
    space = facet_space(topic)
    facets: set[str | None] = set()
    forcing_element: dict[str, Any] | None = spec.get("forcing_element")

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

        if forcing_element:
            kind = forcing_element.get("kind")
            note = forcing_element.get("note") or ""
            checker = FORCING_ELEMENT_CHECKS.get(kind)
            if checker is None:
                unverifiable += 1
            else:
                result = checker(prompt, note)
                if result is None:
                    unverifiable += 1
                elif not result:
                    failures.append(
                        f"{label}: does not satisfy declared forcing_element "
                        f"(kind={kind!r}, note={note!r})"
                    )

    if space and len({f for f in facets if not _all_unk(f, space)}) < 2:
        failures.append(
            f"gold set shows fewer than 2 distinct facet values over {space}; "
            "the examples do not demonstrate the topic varying"
        )

    return failures, unverifiable


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
    unverifiable_total = 0

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
        problems, unverifiable = check_topic(topic, spec)
        unverifiable_total += unverifiable
        if problems:
            failing += 1
            if not args.quiet:
                print(f"\nFAIL {topic_id}")
                for problem in problems:
                    print(f"    - {problem}")

    print(f"\nchecked {checked} specs: {checked - failing} pass, {failing} fail")
    if unverifiable_total:
        print(
            f"{unverifiable_total} gold example(s) declared a forcing_element kind "
            "with no mechanical check (D6: unambiguous_antecedent, clause_relation, "
            "or a governing_word/preposition/verb note with no quoted token) -- "
            "counted as unverifiable, not passed."
        )
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
