"""Step 6: generate-then-blank pilot for the 49 computable grammar topics.

docs/audits/generation-track-plan.md's cycle-2 architecture: ask the model
only for plain, natural German sentences (no gap, no answer, no grammar
topic), then tag, select, and blank them in code. This script runs that
whole flow end to end and persists the result: accepted items to a JSONL
file in the same shape as ``data/pilot_review.jsonl`` (the LLM-direct
pipeline's review file), and every skipped/rejected/dropped candidate, with
its reason, to a second file mirroring ``data/pilot_rejected.jsonl``.

## What "generate" means here

Sentences come from ``sentence_source.generate_sentence_pool``, not one flat
request: many small batches, each nudged toward a different theme,
grammatical person, time frame, register, and sentence structure, so the
pool actually has the person/tense variety a single uniform request cannot
produce (see that module's own docstring for the pilot skew this fixes --
44 items of ``pronomen_personal_nom`` and 38 of ``verb_praesens_regelm``
against 1 each for three other topics, from a ~60-sentence, single-narrative
pool). Every sentence in the pool has also passed
``carrier_validation.validate_carrier`` before this script ever sees it --
carrier validation gates the pool, not just this script's own bookkeeping;
a sentence it rejects never reaches a selector, because
``generate_sentence_pool`` never puts it in ``pool.sentences``.

## What "blank" additionally does now

``pipeline.blank_sentences`` deduplicates on (prompt, answer) across the
whole run (no item accepted under two topics at once -- see its own
docstring for why that corrupts FSRS review state) and caps how many items
one topic, or one source sentence, can contribute, so a run's item counts
reflect coverage decisions rather than which grammar happens to be common in
first-person prose. Both are reported separately from ordinary quality
skips (``--max-items-per-topic``/``--max-items-per-sentence`` are exposed as
flags precisely so a cap is a visible, deliberate choice, not a silent one).

It also runs the uniqueness gate (docs/audits/cycle-04-report.md's own
finding): a blanked token being correct does not mean it is the only
grammatically possible filler of its slot -- a modal verb or a free-choice
object pronoun very often is not. ``src.generation.blanking.uniqueness``
rejects a correctly-built item under those conditions and this script
reports the skip under its own reason, distinct from both an ordinary
quality skip and a balance drop (``report.skips_by_uniqueness``).

Run this from a terminal:

    .venv/bin/python -m scripts.step6_blank_pilot --sentences 300

With no API key configured it runs entirely offline against the
deterministic mock sentence pool (never a live call by accident); with
GEMINI_FREE_API_KEY/GEMINI_PAID_API_KEY/GEMINI_API_KEY set it generates for
real through ``src.llm.client.GeminiLlmClient``. Not wired into the bank or
the verification chain yet -- that is a later cycle.
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
from src.generation.blanking import sentence_source, sentence_tagger
from src.generation.blanking.pipeline import (
    DEFAULT_MAX_ITEMS_PER_SENTENCE,
    DEFAULT_MAX_ITEMS_PER_TOPIC,
    TOPIC_IDS,
    BlankingReport,
    DroppedItem,
    UniquenessSkip,
    blank_sentences,
)
from src.generation.pilot import _write_rejected_file, _write_review_file
from src.llm.env import load_env_file
from src.taxonomy.facets import derive_facet
from src.taxonomy.loader import load_taxonomy

_VALID_CEFR: tuple[CEFR, ...] = ("A1", "A2", "B1", "B2")

DEFAULT_REVIEW_PATH = Path("data/blank_pilot_review.jsonl")
DEFAULT_REJECTED_PATH = Path("data/blank_pilot_rejected.jsonl")


@dataclass(frozen=True)
class _DetailedSkip:
    """One (sentence-or-prompt, topic) pair a skip, or a dropped item,
    happened for, and why. ``BlankingReport``'s own counters only aggregate
    a count per reason; the rejected file (docs/audits/
    stage-04-pilot-2026-08-14.md's "the pilot does not persist rejected
    items, so the cause cannot be diagnosed from this run") needs the
    sentence/prompt and topic too. ``proposed_answer`` is empty for a pure
    skip (no candidate was ever successfully built) and the item's real
    answer for a cap/duplicate drop (a candidate WAS built; it just was not
    kept -- see ``pipeline.DroppedItem``)."""

    topic_id: str
    sentence: str
    reason: str
    proposed_answer: str = ""


def _blank_sentences_with_skip_detail(
    sentences: list[str], difficulty: Difficulty = 1
) -> tuple[BlankingReport, list[_DetailedSkip]]:
    """Thin adapter over ``pipeline.blank_sentences`` for this script's own
    ``_DetailedSkip``/rejected-file shape.

    Used to be a full second copy of the tag -> select -> blank loop (see
    git history), kept only because ``BlankingReport`` did not yet expose
    per-skip detail. It now does (``BlankingReport.skip_details``), so this
    is exactly the thin wrapper that duplication's own docstring said should
    replace it once that happened -- one loop, not two, so the two can never
    quietly diverge.
    """
    report = blank_sentences(sentences, difficulty=difficulty)
    skips = [_DetailedSkip(d.topic_id, d.sentence, d.reason) for d in report.skip_details]
    return report, skips


def _blank_pilot_run_id(sentences: list[str], cefr: str, theme: str) -> str:
    """Content-addressed id for this run, stamped onto every review/rejected
    row (mirroring ``_pilot_batch_id`` in the LLM-direct pipeline's files),
    so the whole run is one comparable audit unit. ``theme`` is a label for
    the run, not necessarily a single theme handed to the generator -- the
    pool cycles many themes per run (module docstring); callers pass
    whatever best identifies the run (a single theme when one was pinned via
    ``--theme``, else a fixed label for "the full varied theme set")."""
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
    pipeline into the real bank is a later cycle's job, per this script's own
    module docstring.

    ``facet`` and ``confusion_group`` are derived the same way
    ``scripts/step2_build_item_bank.py``'s ``ingest_items`` stamps them for
    the LLM-direct pipeline (``derive_facet``, ``Topic.confusion_group``), so
    the two review files are directly comparable, not just same-shaped.
    Returns ``None`` (and lets the caller record it as an ``unknown_topic``
    skip) if the item's ``topic_id`` is not in the loaded taxonomy at all --
    should not happen for any in-scope topic, but is not assumed.
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
    ``data/pilot_rejected.jsonl`` uses. There is no verification-chain layer
    here (a later cycle's job), so ``layer_failed`` is always ``None``."""
    return RejectedCandidateRecord(
        topic_id=skip.topic_id,
        type="cloze_free",
        difficulty=1,
        prompt=skip.sentence,
        proposed_answer=skip.proposed_answer,
        layer_failed=None,
        error_type=skip.reason,
        reason=skip.reason,
    )


def _dropped_items_to_skips(dropped: list[DroppedItem]) -> list[_DetailedSkip]:
    """``pipeline.DroppedItem`` (a cap/duplicate drop, a real item that
    existed but was not kept) onto this script's ``_DetailedSkip`` shape, so
    it can be persisted through the same rejected-file machinery as an
    ordinary skip -- distinguishable afterwards by ``reason``
    (``"cross_topic_duplicate"``, ``"topic_cap"``, ``"sentence_cap"`` versus
    every quality-judgment reason ``blanker.py``/selectors produce)."""
    return [
        _DetailedSkip(d.topic_id, d.prompt, d.reason, proposed_answer=d.proposed_answer)
        for d in dropped
    ]


def _uniqueness_skips_to_skips(skips: list[UniquenessSkip]) -> list[_DetailedSkip]:
    """``pipeline.UniquenessSkip`` (a correct item rejected because another
    member of its own closed class would also have fit the slot) onto this
    script's ``_DetailedSkip`` shape, same posture as
    ``_dropped_items_to_skips`` above -- persisted through the same
    rejected-file machinery, distinguishable afterwards by ``reason``
    (``"modal_verb_interchangeable"``, ``"personal_pronoun_unanchored"``,
    ``"plural_noun_open_class"``)."""
    return [
        _DetailedSkip(s.topic_id, s.prompt, s.reason, proposed_answer=s.proposed_answer)
        for s in skips
    ]


def _print_report(
    report: BlankingReport, pool: sentence_source.SentencePool, ran_live: bool
) -> None:
    print("==========================================================")
    print("  Step 6: generate-then-blank pilot")
    print("==========================================================")
    live_note = "yes" if ran_live else "no (no API key configured; ran the offline mock pool)"
    print(f"  Live model calls:                 {live_note}")
    print(f"  Sentence generation batches run:  {pool.batches_run}")
    print(f"  Sentences requested:              {pool.requested}")
    print(f"  Raw sentences generated:          {pool.raw_generated}")
    print(f"  Duplicate sentences skipped:      {pool.duplicates_skipped}")
    rejected_total = sum(pool.rejected_by_reason.values())
    print(f"  Rejected by carrier validation:   {rejected_total}")
    if not pool.rejected_by_reason:
        print("    (none)")
    for reason, count in sorted(pool.rejected_by_reason.items(), key=lambda kv: -kv[1]):
        print(f"    - {reason}: {count}")
    print(f"  Sentences that survived:          {pool.accepted_count}")
    print(f"  Sentences tagged:                 {report.sentences_tagged}")
    print(f"  Items produced (final, kept):     {report.total_items}")
    print("  Items by topic:")
    for topic_id in TOPIC_IDS:
        print(f"    - {topic_id}: {report.items_by_topic.get(topic_id, 0)}")

    print("  Items dropped as cross-topic duplicates (balance, not quality;")
    print("  keyed by the topic whose item was dropped, another topic kept it):")
    if not report.cross_topic_duplicates_dropped:
        print("    (none)")
    for topic_id, count in sorted(
        report.cross_topic_duplicates_dropped.items(), key=lambda kv: -kv[1]
    ):
        print(f"    - {topic_id}: {count}")

    print("  Items dropped to the per-topic cap (balance, not quality):")
    if not report.items_dropped_by_topic_cap:
        print("    (none)")
    for topic_id, count in sorted(report.items_dropped_by_topic_cap.items(), key=lambda kv: -kv[1]):
        print(f"    - {topic_id}: {count}")

    print("  Items dropped to the per-source-sentence cap (balance, not quality):")
    if not report.items_dropped_by_sentence_cap:
        print("    (none)")
    for topic_id, count in sorted(
        report.items_dropped_by_sentence_cap.items(), key=lambda kv: -kv[1]
    ):
        print(f"    - {topic_id}: {count}")

    print("  Skips by reason (a quality judgment: no candidate, or a candidate")
    print("  that failed paradigm reconstruction -- never a balance decision):")
    if not report.skips_by_reason:
        print("    (none)")
    for reason, count in sorted(report.skips_by_reason.items(), key=lambda kv: -kv[1]):
        print(f"    - {reason}: {count}")

    print("  Skips by uniqueness reason (the item was correct and built cleanly,")
    print("  but another member of the blanked token's own closed class would")
    print("  also have been grammatical there -- not a quality defect in the item")
    print("  itself, and not a balance decision, see pipeline.py's own docstring):")
    if not report.skips_by_uniqueness:
        print("    (none)")
    for reason, count in sorted(report.skips_by_uniqueness.items(), key=lambda kv: -kv[1]):
        print(f"    - {reason}: {count}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 6 generate-then-blank pilot.")
    parser.add_argument(
        "--sentences",
        type=int,
        default=sentence_source.DEFAULT_POOL_SIZE,
        help=(
            "Target size of the generated sentence pool "
            f"(default {sentence_source.DEFAULT_POOL_SIZE}, per "
            "sentence_source.generate_sentence_pool)."
        ),
    )
    parser.add_argument(
        "--cefr", type=str, default="A2", choices=list(_VALID_CEFR), help="CEFR level to request."
    )
    parser.add_argument(
        "--theme",
        type=str,
        default=None,
        help=(
            "Pin the pool to a single theme handed to the model (never a grammar "
            "topic). Default: cycle the full varied theme set "
            "(sentence_source.DEFAULT_THEMES), which is what fixes the topic skew "
            "a single-theme pool produces."
        ),
    )
    parser.add_argument(
        "--max-items-per-topic",
        type=int,
        default=DEFAULT_MAX_ITEMS_PER_TOPIC,
        help=(
            "Cap on items kept for any one topic in this run "
            f"(default {DEFAULT_MAX_ITEMS_PER_TOPIC}); prevents a high-frequency "
            "topic (e.g. pronomen_personal_nom) from dominating the run."
        ),
    )
    parser.add_argument(
        "--max-items-per-sentence",
        type=int,
        default=DEFAULT_MAX_ITEMS_PER_SENTENCE,
        help=(
            "Cap on items kept from any one source sentence across all topics "
            f"(default {DEFAULT_MAX_ITEMS_PER_SENTENCE}); several topics sharing "
            "one good carrier sentence is fine, meeting the same carrier many "
            "times over is not."
        ),
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
            "Where to write every skipped/rejected/dropped candidate, with its "
            "reason (same shape as data/pilot_rejected.jsonl)."
        ),
    )
    args = parser.parse_args()

    # The two lane keys live in a gitignored .env per docs/01-foundation.md;
    # nothing else in the process reads that file.
    load_env_file()

    llm_client = sentence_source.client_from_env()
    ran_live = llm_client is not None
    generator = sentence_source.build_sentence_generator(llm_client)

    themes = (args.theme,) if args.theme else sentence_source.DEFAULT_THEMES
    theme_label = args.theme or "multi_theme_pool"

    # ``validate=True`` (the default) is what actually gates blanking: a
    # sentence carrier_validation rejects is never placed in
    # ``pool.sentences``, so it never reaches ``blank_sentences`` or any
    # selector at all -- see this module's own docstring.
    pool = sentence_source.generate_sentence_pool(
        generator, args.cefr, total=args.sentences, themes=themes
    )
    sentences = pool.sentences
    if not sentences:
        print(
            "No sentences survived generation and carrier validation "
            "(empty/unparseable model output, or every candidate was rejected "
            "as unsound German); nothing to do."
        )
        return 1

    if not sentence_tagger.analysis_available():
        print(
            "spaCy's de_core_news_sm model is not installed; no items can be "
            "produced (degrading cleanly, not crashing). Install it "
            "(`python -m spacy download de_core_news_sm`) and re-run."
        )
        print(f"  Sentences requested:   {pool.requested}")
        print(f"  Sentences that survived carrier validation: {pool.accepted_count}")
        print(f"  Topics in scope:       {len(TOPIC_IDS)}")
        return 0

    report, skips = _blank_sentences_with_skip_detail(sentences)
    _print_report(report, pool, ran_live=ran_live)

    batch_id = _blank_pilot_run_id(sentences, args.cefr, theme_label)
    topics_by_id = {t.id: t for t in load_taxonomy()}

    accepted_items: list[BankItem] = []
    unknown_topic_skips: list[_DetailedSkip] = []
    for item in report.items:
        bank_item = _to_bank_item(item, topics_by_id)
        if bank_item is None:
            unknown_topic_skips.append(
                _DetailedSkip(
                    item.topic_id,
                    item.prompt,
                    "unknown_topic",
                    proposed_answer=item.proposed_answer,
                )
            )
            continue
        accepted_items.append(bank_item)

    rejected_records = [
        _to_rejected_record(skip)
        for skip in [
            *skips,
            *_dropped_items_to_skips(report.dropped_details),
            *_uniqueness_skips_to_skips(report.uniqueness_skips),
            *unknown_topic_skips,
        ]
    ]

    review_path = Path(args.review_file)
    rejected_path = Path(args.rejected_file)
    _write_review_file(review_path, accepted_items, batch_id)
    _write_rejected_file(rejected_path, rejected_records, batch_id)

    print(f"  Review file:           {review_path}")
    print(f"  Rejected file:         {rejected_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
