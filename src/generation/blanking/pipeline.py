"""Orchestrates tag -> select -> blank across every in-scope topic and every
generated sentence, and reports what happened -- sentences tagged, items
produced per topic, and skips by reason -- so a caller (``scripts.
step6_blank_pilot``, or a test) never has to re-derive that bookkeeping.

"Generate" itself (asking an LLM for plain sentences) lives in
``sentence_source.py`` and is deliberately NOT called from here: this module
takes already-generated sentence strings, so it can be exercised in tests
with a fixed, hand-written corpus and no LLM involved at all.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from src.contracts import CandidateItem, Difficulty
from src.generation.blanking import sentence_tagger
from src.generation.blanking.blanker import blank_candidate
from src.generation.blanking.selectors import SELECTORS

TOPIC_IDS: tuple[str, ...] = tuple(SELECTORS)


@dataclass
class BlankingReport:
    """Everything a human running the pilot script (or a test) needs to
    audit what the pipeline did with a batch of sentences."""

    sentences_requested: int = 0
    sentences_tagged: int = 0
    items_by_topic: dict[str, int] = field(default_factory=dict)
    skips_by_reason: Counter[str] = field(default_factory=Counter)
    items: list[CandidateItem] = field(default_factory=list)

    @property
    def total_items(self) -> int:
        return sum(self.items_by_topic.values())


def blank_sentences(sentences: list[str], difficulty: Difficulty = 1) -> BlankingReport:
    """Run every sentence in ``sentences`` through tag -> select -> blank for
    every topic in ``SELECTORS``.

    Degrades cleanly with an explicit, countable skip reason when spaCy is
    unavailable (``"spacy_unavailable"``) or a given sentence fails to parse
    (``"untaggable_sentence"``) -- never raises, matching every degrade
    contract in this package.

    At most one item is produced per (sentence, topic) pair even when a
    selector finds several qualifying tokens (requirement: never two items
    from the same sentence for the same topic); the first token position a
    selector returns is used, and the sentence's other matches for that same
    topic are simply not spent on a second item.
    """
    report = BlankingReport(sentences_requested=len(sentences))
    if not sentence_tagger.analysis_available():
        report.skips_by_reason["spacy_unavailable"] += len(sentences) * len(SELECTORS)
        return report

    for raw in sentences:
        tagged = sentence_tagger.tag_sentence(raw)
        if tagged is None:
            report.skips_by_reason["untaggable_sentence"] += len(SELECTORS)
            continue
        report.sentences_tagged += 1

        for topic_id, selector in SELECTORS.items():
            candidates = selector(tagged)
            if not candidates:
                report.skips_by_reason["no_candidate_for_topic"] += 1
                continue
            outcome = blank_candidate(topic_id, tagged, candidates[0], difficulty=difficulty)
            if outcome.item is None:
                reason = outcome.skip_reason or "unknown_skip_reason"
                report.skips_by_reason[reason] += 1
                continue
            report.items.append(outcome.item)
            report.items_by_topic[topic_id] = report.items_by_topic.get(topic_id, 0) + 1

    return report
