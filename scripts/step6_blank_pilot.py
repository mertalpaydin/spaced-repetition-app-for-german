"""Step 6: generate-then-blank pilot for article and adjective declension.

docs/audits/generation-track-plan.md's cycle-2 architecture: ask the model
only for plain, natural German sentences (no gap, no answer, no grammar
topic), then tag, select, and blank them in code. This script runs that
whole flow end to end, prints a report -- sentences requested, sentences
tagged, items produced per topic, and skips by reason -- for the 15 article
and adjective declension topics this cycle covers
(``src.generation.blanking.selectors.SELECTORS``), and persists the result:
accepted items to a JSONL file in the same shape as ``data/pilot_review.jsonl``
(the LLM-direct pipeline's review file), and every skipped/rejected candidate,
with its reason, to a second file mirroring ``data/pilot_rejected.jsonl``.
Before this, the accepted set was printed as a count and then discarded, so
the new pipeline's items could not be audited at all
(docs/audits cycle-2 blocking gap).

Run this from a terminal:

    .venv/bin/python -m scripts.step6_blank_pilot --sentences 60

With no API key configured it runs entirely offline against the
deterministic mock sentence pool (never a live call by accident); with
GEMINI_FREE_API_KEY/GEMINI_PAID_API_KEY/GEMINI_API_KEY set it generates for
real through ``src.llm.client.GeminiLlmClient``. Not wired into the bank or
the verification chain yet -- that is cycle 3.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from src.contracts import CEFR, BankItem, CandidateItem, Difficulty, Topic
from src.generation.batch_client import RejectedCandidateRecord
from src.generation.blanking import sentence_tagger
from src.generation.blanking.blanker import blank_candidate
from src.generation.blanking.pipeline import TOPIC_IDS, BlankingReport
from src.generation.blanking.selectors import SELECTORS
from src.generation.blanking.sentence_source import build_sentence_generator, client_from_env
from src.generation.pilot import _write_rejected_file, _write_review_file
from src.llm.env import load_env_file
from src.taxonomy.facets import derive_facet
from src.taxonomy.loader import load_taxonomy

_VALID_CEFR: tuple[CEFR, ...] = ("A1", "A2", "B1", "B2")

DEFAULT_REVIEW_PATH = Path("data/blank_pilot_review.jsonl")
DEFAULT_REJECTED_PATH = Path("data/blank_pilot_rejected.jsonl")


@dataclass(frozen=True)
class _DetailedSkip:
    """One (sentence, topic) pair a skip happened for, and why. ``BlankingReport.
    skips_by_reason`` only aggregates a count per reason; auditing which
    candidate a skip actually was (docs/audits/stage-04-pilot-2026-08-14.md's
    "the pilot does not persist rejected items, so the cause cannot be
    diagnosed from this run") needs the sentence and topic too."""

    topic_id: str
    sentence: str
    reason: str


def _blank_sentences_with_skip_detail(
    sentences: list[str], difficulty: Difficulty = 1
) -> tuple[BlankingReport, list[_DetailedSkip]]:
    """A faithful mirror of ``src.generation.blanking.pipeline.blank_sentences``
    -- same tag -> select -> blank loop, same functions, in the same order --
    that additionally records which (topic_id, sentence) a skip belongs to.

    ``src.generation.blanking.pipeline`` is owned by another agent this cycle
    and ``BlankingReport`` does not expose skip detail itself, only the
    aggregate ``skips_by_reason`` counter; this duplicates that loop rather
    than modifying that module. If ``BlankingReport`` ever grows a
    per-skip-record field, this function (and the duplication) should be
    deleted in favour of calling ``blank_sentences`` directly.
    """
    report = BlankingReport(sentences_requested=len(sentences))
    skips: list[_DetailedSkip] = []

    if not sentence_tagger.analysis_available():
        report.skips_by_reason["spacy_unavailable"] += len(sentences) * len(SELECTORS)
        return report, skips

    for raw in sentences:
        tagged = sentence_tagger.tag_sentence(raw)
        if tagged is None:
            report.skips_by_reason["untaggable_sentence"] += len(SELECTORS)
            skips.extend(
                _DetailedSkip(topic_id, raw, "untaggable_sentence") for topic_id in SELECTORS
            )
            continue
        report.sentences_tagged += 1

        for topic_id, selector in SELECTORS.items():
            candidates = selector(tagged)
            if not candidates:
                report.skips_by_reason["no_candidate_for_topic"] += 1
                skips.append(_DetailedSkip(topic_id, raw, "no_candidate_for_topic"))
                continue
            outcome = blank_candidate(topic_id, tagged, candidates[0], difficulty=difficulty)
            if outcome.item is None:
                reason = outcome.skip_reason or "unknown_skip_reason"
                report.skips_by_reason[reason] += 1
                skips.append(_DetailedSkip(topic_id, raw, reason))
                continue
            report.items.append(outcome.item)
            report.items_by_topic[topic_id] = report.items_by_topic.get(topic_id, 0) + 1

    return report, skips


def _blank_pilot_run_id(sentences: list[str], cefr: str, theme: str) -> str:
    """Content-addressed id for this run, stamped onto every review/rejected
    row (mirroring ``_pilot_batch_id`` in the LLM-direct pipeline's files),
    so the whole run is one comparable audit unit."""
    payload = json.dumps({"sentences": sentences, "cefr": cefr, "theme": theme}, sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
    return f"blank_batch_{digest}"


def _blank_item_id(item: CandidateItem) -> str:
    """Content-addressed id, same scheme as ``src.generation.batch_client.
    _candidate_item_id`` (topic/type/difficulty/prompt/answer), with a
    ``blank_`` prefix distinguishing generate-then-blank items from the
    LLM-direct pipeline's ``gen_`` ids in a combined audit view."""
    payload = json.dumps(
        {
            "topic_id": item.topic_id,
            "type": item.type,
            "difficulty": item.difficulty,
            "prompt": item.prompt,
            "proposed_answer": item.proposed_answer,
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"blank_{digest}"


def _to_bank_item(item: CandidateItem, topics_by_id: dict[str, Topic]) -> BankItem | None:
    """Map a generate-then-blank ``CandidateItem`` onto the exact field set
    ``data/pilot_review.jsonl`` uses (``BankItem``), so the same audit
    tooling reads both files. Not a bank insert -- no ``id`` is stamped
    against the database, nothing is written to ``data/bank.db``: wiring this
    pipeline into the real bank is cycle 3's job, per this script's own
    module docstring.

    ``facet`` and ``confusion_group`` are derived the same way
    ``scripts/step2_build_item_bank.py``'s ``ingest_items`` stamps them for
    the LLM-direct pipeline (``derive_facet``, ``Topic.confusion_group``), so
    the two review files are directly comparable, not just same-shaped.
    Returns ``None`` (and lets the caller record it as an ``unknown_topic``
    skip) if the item's ``topic_id`` is not in the loaded taxonomy at all --
    should not happen for the 15 in-scope topics, but is not assumed.
    """
    topic = topics_by_id.get(item.topic_id)
    if topic is None:
        return None
    bank_item = BankItem(
        id=_blank_item_id(item),
        topic_id=item.topic_id,
        tag_id=item.topic_id,
        dimension="grammar",
        type=item.type,
        difficulty=item.difficulty,
        cefr=topic.cefr,
        prompt=item.prompt,
        accepted_answers=[item.proposed_answer],
        distractors=item.distractors,
        cue=item.cue,
        rule_hint=item.rule_hint,
        facet=item.facet,
        confusion_group=topic.confusion_group,
        domain=item.domain,
        carrier_lemmas=item.carrier_lemmas,
        source_sentence_id=item.source_sentence_id,
    )
    facet = derive_facet(bank_item, topic)
    if facet != bank_item.facet:
        bank_item = bank_item.model_copy(update={"facet": facet})
    return bank_item


def _to_rejected_record(skip: _DetailedSkip) -> RejectedCandidateRecord:
    """Map a ``_DetailedSkip`` onto the exact field set
    ``data/pilot_rejected.jsonl`` uses (``RejectedCandidateRecord``). There is
    no verification-chain layer here (cycle 3's job), so ``layer_failed`` is
    always ``None``; ``proposed_answer`` is empty since a skip means no token
    was ever successfully extracted as a candidate answer."""
    return RejectedCandidateRecord(
        topic_id=skip.topic_id,
        type="cloze_free",
        difficulty=1,
        prompt=skip.sentence,
        proposed_answer="",
        layer_failed=None,
        error_type=skip.reason,
        reason=skip.reason,
    )


def _print_report(report: BlankingReport, requested: int, ran_live: bool) -> None:
    print("==========================================================")
    print("  Step 6: generate-then-blank pilot (article/adjective declension)")
    print("==========================================================")
    live_note = "yes" if ran_live else "no (no API key configured; ran the offline mock pool)"
    print(f"  Live model calls:      {live_note}")
    print(f"  Sentences requested:   {requested}")
    print(f"  Sentences tagged:      {report.sentences_tagged}")
    print(f"  Items produced:        {report.total_items}")
    print("  Items by topic:")
    for topic_id in TOPIC_IDS:
        print(f"    - {topic_id}: {report.items_by_topic.get(topic_id, 0)}")
    print("  Skips by reason:")
    if not report.skips_by_reason:
        print("    (none)")
    for reason, count in sorted(report.skips_by_reason.items(), key=lambda kv: -kv[1]):
        print(f"    - {reason}: {count}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 6 generate-then-blank pilot.")
    parser.add_argument(
        "--sentences", type=int, default=60, help="Number of sentences to request (default 60)."
    )
    parser.add_argument(
        "--cefr", type=str, default="A2", choices=list(_VALID_CEFR), help="CEFR level to request."
    )
    parser.add_argument(
        "--theme",
        type=str,
        default="Alltag",
        help="Theme handed to the model (never a grammar topic).",
    )
    parser.add_argument(
        "--review-file",
        type=str,
        default=str(DEFAULT_REVIEW_PATH),
        help=(
            "Where to write accepted items for hand audit (same shape as data/pilot_review.jsonl)."
        ),
    )
    parser.add_argument(
        "--rejected-file",
        type=str,
        default=str(DEFAULT_REJECTED_PATH),
        help=(
            "Where to write every skipped/rejected candidate, with its reason "
            "(same shape as data/pilot_rejected.jsonl)."
        ),
    )
    args = parser.parse_args()

    # The two lane keys live in a gitignored .env per docs/01-foundation.md;
    # nothing else in the process reads that file.
    load_env_file()

    llm_client = client_from_env()
    ran_live = llm_client is not None
    generator = build_sentence_generator(llm_client)

    sentences = generator.generate(args.cefr, args.theme, args.sentences)
    if not sentences:
        print("No sentences were generated (empty or unparseable model output); nothing to do.")
        return 1

    if not sentence_tagger.analysis_available():
        print(
            "spaCy's de_core_news_sm model is not installed; no items can be "
            "produced (degrading cleanly, not crashing). Install it "
            "(`python -m spacy download de_core_news_sm`) and re-run."
        )
        print(f"  Sentences requested:   {len(sentences)}")
        print(f"  Topics in scope:       {len(TOPIC_IDS)}")
        return 0

    report, skips = _blank_sentences_with_skip_detail(sentences)
    _print_report(report, requested=len(sentences), ran_live=ran_live)

    batch_id = _blank_pilot_run_id(sentences, args.cefr, args.theme)
    topics_by_id = {t.id: t for t in load_taxonomy()}

    accepted_items: list[BankItem] = []
    unknown_topic_skips: list[_DetailedSkip] = []
    for item in report.items:
        bank_item = _to_bank_item(item, topics_by_id)
        if bank_item is None:
            unknown_topic_skips.append(_DetailedSkip(item.topic_id, item.prompt, "unknown_topic"))
            continue
        accepted_items.append(bank_item)

    rejected_records = [_to_rejected_record(skip) for skip in [*skips, *unknown_topic_skips]]

    review_path = Path(args.review_file)
    rejected_path = Path(args.rejected_file)
    _write_review_file(review_path, accepted_items, batch_id)
    _write_rejected_file(rejected_path, rejected_records, batch_id)

    print(f"  Review file:           {review_path}")
    print(f"  Rejected file:         {rejected_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
