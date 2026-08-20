"""Step 6: generate-then-blank pilot for the 49 computable grammar topics.

docs/audits/generation-track-plan.md's cycle-2 architecture: ask the model
only for plain, natural German sentences (no gap, no answer, no grammar
topic), then tag, select, and blank them in code. This script runs that
whole flow end to end and persists the result: accepted items to a JSONL
file in the same shape as ``data/pilot_review.jsonl`` (the LLM-direct
pipeline's review file), and every skipped/rejected/dropped candidate, with
its reason, to a second file mirroring ``data/pilot_rejected.jsonl``.

## CONTRACT CHANGE (CLAUDE.md rule 8, flagged here rather than silently
## changed): this script no longer builds one general, round-robin sentence
## pool and harvests whatever topics fall out of it.

docs/audits/cycle-08-report.md found 18 of 49 topics ending a
pilot run at zero, every one of them for the same reason
(``no_candidate_for_topic``): the old ``generate_sentence_pool`` cycled
themes and a construction hint round-robin, never keyed to which topic
actually needed items, so a topic could go an entire run without a single
request aimed at it. The project owner's own words: "if we are not passing a
topic to the generator, what would happen during nightly top-up when we
needed perfekt_sein exercises? we were going to pray that it produces some
sentences with that topic."

This script now drives generation from ``src.generation.blanking.
topic_demand.compute_demand`` and ``src.generation.blanking.orchestrator.
run_demand_driven_generation``: for every topic that has demand, in demand
order, it requests sentences using THAT topic's own construction hint
(varying theme/person/tense/register/structure across the requests), counts
what the topic got, and retries (bounded) if it is still short -- see
``orchestrator``'s own module docstring for the full loop. Because this
script has no learner and (usually) no bank to read real stock from, its job
is COVERAGE, not top-up: it defaults to forcing every one of the 49 topics,
so a pilot run always shows what each topic's own construction hint
currently produces, even for a topic that would have zero organic deficit
against a real, stocked bank. ``--force-topics`` narrows this to a specific
list for a targeted run (e.g. against a bank that already has stock, passed
via ``--db``); ``--per-topic-target`` replaces the old ``--sentences`` flag
(previously an overall sentence-pool size hint) with a per-topic UNSEEN-item
target, because "how many sentences to request overall" is no longer a
meaningful number once requests are keyed per topic -- the loop requests as
many batches as each topic's own deficit and retry budget call for, not a
fixed pool size split across 49 topics.

Every sentence generated is still carrier-validated
(``carrier_validation.validate_carriers``) before it ever reaches a
selector, exactly as before -- see ``orchestrator``'s own docstring.

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

## The model verification backstop

After every structural check above, the surviving accepted items go through
one more pass: ``src.generation.blanking.model_verification.verify_items``,
the model-backed backstop docs/audits/cycle-06-report.md's own class G
defect ("treue" for "treffe", a non-word a mistagging spaCy pass let
through every structural check) argues for -- see that module's own
docstring for the full case. An item the model rejects is pulled out of the
accepted set here and moved to the rejected file with the model's own
reason attached; an item is never silently reported as verified when the
pass could not run at all (no API key configured, or a malformed model
response) -- see ``_print_verification_report`` and CLAUDE.md 12. This
script shares the SAME ``llm_client`` between sentence generation and this
pass (never builds a second one), so both draw from one cost log and one
rate-limiter state, and both are unconditionally forbidden from queuing a
real Batch API job (this script never takes a ``--batch`` opt-in at all) --
but NOT from the paid lane itself. The client
``sentence_source.client_from_env`` builds is ``forbid_batch=True``,
``forbid_paid_lane=False``: the project owner's own instruction is "no
batch api ... for pilot go to paid on demand api, if free lane is already
expired," not "no paid lane." So once the free lane's daily quota is spent
(exactly what happened in the run that motivated this: sentence generation
alone consumed the whole 50-call free-lane quota), both sentence generation
and this verification pass keep going, synchronously, on the paid lane,
instead of silently degrading every item to ``"not_run"`` the way an
unconditionally paid-lane-forbidden client used to. See ``main()``'s
exit-code handling below: a run where this pass did not execute must never
exit 0 again, regardless of why it did not execute.

Run this from a terminal:

    .venv/bin/python -m scripts.step6_blank_pilot
    .venv/bin/python -m scripts.step6_blank_pilot \
        --force-topics perfekt_sein,futur_i --db data/bank.db

With no API key configured it runs entirely offline against the
deterministic mock sentence pool (never a live call by accident); with
GEMINI_FREE_API_KEY/GEMINI_PAID_API_KEY/GEMINI_API_KEY set it generates for
real through ``src.llm.client.GeminiLlmClient``. Not wired into the bank or
the verification chain yet -- that is a later cycle (this script writes
review/rejected JSONL files, it does not call ``bank.insert``).

Before starting, the projected (worst-case) number of ``generate()`` calls
this run could make is printed -- 49 topics at one batch each is already
about 49 calls, and the free lane's daily quota is around 50 (CLAUDE.md 9),
so the operator sees a run that will spill onto the paid lane coming, rather
than discovering it in ``cost_log``.
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
from src.generation.blanking import orchestrator, sentence_source, sentence_tagger
from src.generation.blanking.model_verification import (
    DEFAULT_VERIFICATION_BATCH_SIZE,
    ModelRejection,
    VerificationReport,
    verify_items,
)
from src.generation.blanking.orchestrator import DemandRunReport
from src.generation.blanking.pipeline import (
    DEFAULT_MAX_ITEMS_PER_SENTENCE,
    DEFAULT_MAX_ITEMS_PER_TOPIC,
    TOPIC_IDS,
    BlankingReport,
    DroppedItem,
    UniquenessSkip,
    blank_sentences,
)
from src.generation.blanking.topic_demand import (
    DEFAULT_FORCED_FLOOR,
    DEFAULT_TARGET_UNSEEN,
    StockLookup,
    compute_demand,
)
from src.generation.pilot import _write_rejected_file, _write_review_file
from src.llm.env import load_env_file
from src.taxonomy.facets import derive_facet
from src.taxonomy.loader import load_taxonomy

_VALID_CEFR: tuple[CEFR, ...] = ("A1", "A2", "B1", "B2")
_VALID_DIFFICULTY: tuple[Difficulty, ...] = (1, 2, 3)

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


def _skip_details_from_report(report: BlankingReport) -> list[_DetailedSkip]:
    """``BlankingReport.skip_details`` onto this script's own
    ``_DetailedSkip`` shape -- the one place this mapping happens, so
    ``_blank_sentences_with_skip_detail`` (used directly by a fixed
    sentence list) and ``main()`` (used against a ``BlankingReport`` the
    demand-driven orchestrator already built) can never quietly diverge."""
    return [_DetailedSkip(d.topic_id, d.sentence, d.reason) for d in report.skip_details]


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
    quietly diverge. Not used by ``main()`` any more (the demand-driven loop
    builds its own ``BlankingReport`` via ``orchestrator.
    run_demand_driven_generation``), but kept as a real, tested utility for
    a caller that already has a fixed sentence list and wants the old
    all-topics-at-once behaviour (``topic_ids=None``) directly.
    """
    report = blank_sentences(sentences, difficulty=difficulty)
    return report, _skip_details_from_report(report)


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


def _model_rejection_to_record(rejection: ModelRejection) -> RejectedCandidateRecord:
    """One ``model_verification.ModelRejection`` onto the exact field set
    ``data/pilot_rejected.jsonl`` uses, with the model's own reason kept
    verbatim in ``reason`` (never discarded -- model_verification.py's own
    docstring on diagnosability) and ``error_type`` a fixed, machine-stable
    category distinct from that free text, so a rejected file can be
    grouped by ``error_type`` and still read the specific reason per row."""
    return RejectedCandidateRecord(
        topic_id=rejection.topic_id,
        type="cloze_free",
        difficulty=1,
        prompt=rejection.prompt,
        proposed_answer=" / ".join(rejection.accepted_answers),
        layer_failed=None,
        error_type="model_verification_rejected",
        reason=rejection.reason,
    )


def _apply_model_verification(
    items: list[BankItem], verification_report: VerificationReport
) -> list[BankItem]:
    """The items the model verification pass did NOT reject, in original
    order -- ``"verified"`` and ``"not_run"`` both stay in the accepted set
    (the rest of the pipeline already established these are structurally
    sound; a pass that could not run is not evidence AGAINST an item, only
    an absent additional confirmation), only ``"rejected"`` items are
    pulled out. ``verification_report.verdicts`` is aligned by position with
    ``items`` (``verify_items``'s own contract), so this zips the two
    directly rather than re-deriving an index."""
    return [
        item
        for item, verdict in zip(items, verification_report.verdicts, strict=True)
        if verdict.outcome != "rejected"
    ]


def _print_verification_report(verification_report: VerificationReport) -> None:
    """Print the model verification pass's outcome with its three counts
    kept visibly separate (this cycle's own brief: "Report the counts
    separately") -- verified, rejected by the model (with reasons grouped),
    and not verified because the pass could not run. Never prints a single
    combined "fine" number, and never lets a ``not_run`` item read as
    verified: the "NOT RUN" banner below is unconditional whenever
    ``verification_report.attempted`` is ``False``, which is exactly the
    no-API-key case CLAUDE.md 12 and this module's own docstring require to
    be reported honestly rather than silently passed."""
    print("  Model verification pass (backstop over items that already survived")
    print("  every structural check; see model_verification.py's own docstring):")
    if not verification_report.attempted:
        print("    NOT RUN: no LLM client configured (no API key). Every item below")
        print("    is UNVERIFIED by this pass -- it is not confirmed correct by it,")
        print("    only by the structural checks that ran before it.")
    print(f"    Items verified:                       {verification_report.verified_count}")
    print(f"    Items rejected by the model:          {verification_report.rejected_count}")
    if not verification_report.rejected_reasons:
        print("      (none)")
    for reason, count in sorted(
        verification_report.rejected_reasons.items(), key=lambda kv: -kv[1]
    ):
        print(f"      - {reason}: {count}")
    print(f"    Items not verified (pass did not run): {verification_report.not_run_count}")
    if not verification_report.not_run_reasons:
        print("      (none)")
    for reason, count in sorted(verification_report.not_run_reasons.items(), key=lambda kv: -kv[1]):
        print(f"      - {reason}: {count}")


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


def _print_projected_call_count(projected_calls: int, call_ceiling: int) -> None:
    """Printed BEFORE generation starts (this task's own brief: "the
    operator should see it coming rather than discover it in cost_log"): the
    worst-case number of ``generate()`` calls this run could make, and the
    explicit ceiling actually bounding it."""
    print(
        f"  Projected call count (worst case, every topic spending its full "
        f"retry budget): {projected_calls}"
    )
    print(f"  Call ceiling for this run:         {call_ceiling}")


def _print_demand_run_report(run_report: DemandRunReport, *, ran_live: bool) -> None:
    """Print the demand-driven loop's own honest, per-topic report (task 4):
    demand, items produced, retries used, and -- for every topic that fell
    short -- WHICH of the three distinct reasons applied, kept visibly
    separate rather than conflated into one "no items" line. A topic that
    ends at zero is named explicitly, never silently absent from the
    output."""
    print("==========================================================")
    print("  Step 6: generate-then-blank pilot (demand-driven)")
    print("==========================================================")
    live_note = "yes" if ran_live else "no (no API key configured; ran the offline mock pool)"
    print(f"  Live model calls:                 {live_note}")
    print(f"  Topics with demand this run:      {len(run_report.demands)}")
    print(f"  Calls made:                       {run_report.calls_made}")
    print(f"  Call ceiling hit:                 {run_report.call_ceiling_hit}")
    print(
        f"  Raw sentences requested (all topics, all attempts): "
        f"{sum(t.sentences_requested for t in run_report.topic_reports)}"
    )
    print(f"  Duplicate sentences skipped:      {run_report.duplicates_skipped}")
    rejected_total = sum(run_report.rejected_by_reason.values())
    print(f"  Rejected by carrier validation:   {rejected_total}")
    if not run_report.rejected_by_reason:
        print("    (none)")
    for reason, count in sorted(run_report.rejected_by_reason.items(), key=lambda kv: -kv[1]):
        print(f"    - {reason}: {count}")
    print(f"  Carrier-valid sentences collected: {len(run_report.sentences)}")
    print(f"  Items produced (final, kept):     {run_report.blanking_report.total_items}")

    print("  Per-topic demand report:")
    print("    topic_id                                  demand  items  retries  status")
    for t in run_report.topic_reports:
        status = "met demand" if t.met_demand else ", ".join(t.shortfall_reasons)
        print(
            f"    {t.topic_id:<42} {t.demand.demand:>6} {t.items_produced:>6} "
            f"{t.retries_used:>7}  {status}"
        )

    zero_topics = run_report.topics_with_zero_items
    print(f"  Topics that ended this run with ZERO items: {len(zero_topics)}")
    if not zero_topics:
        print("    (none)")
    for topic_id in zero_topics:
        t = next(r for r in run_report.topic_reports if r.topic_id == topic_id)
        print(f"    - {topic_id}: {', '.join(t.shortfall_reasons)}")

    report = run_report.blanking_report
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

    print("  Skips by uniqueness reason (the item was correct and built cleanly,")
    print("  but another member of the blanked token's own closed class would")
    print("  also have been grammatical there):")
    if not report.skips_by_uniqueness:
        print("    (none)")
    for reason, count in sorted(report.skips_by_uniqueness.items(), key=lambda kv: -kv[1]):
        print(f"    - {reason}: {count}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 6 generate-then-blank pilot.")
    parser.add_argument(
        "--per-topic-target",
        type=int,
        default=DEFAULT_TARGET_UNSEEN,
        help=(
            "Target UNSEEN-item stock per topic, fed to "
            "topic_demand.compute_demand as target_unseen "
            f"(default {DEFAULT_TARGET_UNSEEN}). Replaces the old "
            "--sentences flag (an overall sentence-pool size hint): "
            "generation is now driven per topic, not by one flat pool size, "
            "so 'how many sentences to request in total' is no longer a "
            "meaningful number -- see this module's own docstring."
        ),
    )
    parser.add_argument(
        "--cefr", type=str, default="A2", choices=list(_VALID_CEFR), help="CEFR level to request."
    )
    parser.add_argument(
        "--difficulty",
        type=int,
        default=1,
        choices=list(_VALID_DIFFICULTY),
        help="Difficulty tier to blank at and to read/compute stock for (default 1).",
    )
    parser.add_argument(
        "--theme",
        type=str,
        default=None,
        help=(
            "Pin every request to a single theme handed to the model (never a "
            "grammar topic). Default: cycle the full varied theme set "
            "(sentence_source.DEFAULT_THEMES) across a topic's own requests."
        ),
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help=(
            "Path to a real bank.db to read UNSEEN stock from (SqliteItemBank."
            "stock). Default: none -- this pilot has no learner and usually no "
            "bank, so every topic is treated as having zero stock. Pass this "
            "for a targeted run against a bank that already has stock (with "
            "--force-topics)."
        ),
    )
    parser.add_argument(
        "--force-topics",
        type=str,
        default=None,
        help=(
            "Comma-separated topic ids to force regardless of stock (demand at "
            "least a small floor even at/above target -- the owner's own "
            "instruction: 'during pilot you need to see exercises from "
            "problematic topics ... even if those problematic have exercises, "
            "ask sentences with those topics specifically'). Default: force "
            "EVERY topic in scope, since this pilot's job is coverage, not "
            "top-up. Pass an empty string to force nothing and rely purely on "
            "computed deficits (requires --db to mean anything)."
        ),
    )
    parser.add_argument(
        "--max-retries-per-topic",
        type=int,
        default=orchestrator.DEFAULT_MAX_RETRIES_PER_TOPIC,
        help=(
            "Per-topic retry budget: how many extra batches a topic gets if it "
            f"is still short of its demand (default "
            f"{orchestrator.DEFAULT_MAX_RETRIES_PER_TOPIC}). A topic that "
            "yields nothing across this budget stops being asked for the rest "
            "of this run."
        ),
    )
    parser.add_argument(
        "--call-ceiling",
        type=int,
        default=None,
        help=(
            "Hard ceiling on total generate() calls this run may make. Default: "
            "the projected worst case for this run's own demand list and retry "
            "budget (printed before generation starts) -- so an explicit "
            "ceiling is only needed to make a run STRICTER than that."
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

    # This pilot has no learner and (by default) no bank -- coverage, not
    # top-up, is its job (module docstring's CONTRACT CHANGE section). A
    # real ``--db`` opts into reading actual UNSEEN stock for a targeted run
    # against a bank that already has content.
    stock_lookup: StockLookup
    if args.db:
        from src.bank.storage import SqliteItemBank

        bank = SqliteItemBank(args.db)
        stock_lookup = bank.stock
        print(f"  Reading real stock from {args.db}.")
    else:

        def stock_lookup(topic_id: str, difficulty: Difficulty) -> int:
            return 0

        print("  No --db given: every topic is treated as having zero stock.")

    # ``--force-topics``, when given, also scopes the run to exactly that
    # topic list -- not just an addition on top of the full 49. Forcing a
    # topic against the default zero-stock lookup (no ``--db``) cannot be
    # distinguished from "restrict to these topics" anyway (every other
    # topic would ALSO show a positive deficit against zero stock, so it
    # would be requested too, defeating the point of naming a subset at
    # all); against a real ``--db`` this also means "a targeted run" means
    # what it says -- only the named topics, even if some other topic in the
    # bank happens to be understocked too.
    if args.force_topics is None:
        # Flag omitted: this pilot's own default (module docstring's
        # CONTRACT CHANGE section) -- coverage, not top-up, so every topic
        # is both in scope and forced.
        topic_universe: tuple[str, ...] = TOPIC_IDS
        forced_topics: set[str] = set(TOPIC_IDS)
    elif not args.force_topics.strip():
        # Explicitly empty: force nothing, but still consider all 49 topics
        # for their own computed deficit (meaningful only with --db; against
        # the default zero-stock lookup every topic will show a deficit and
        # this is equivalent to the default anyway).
        topic_universe = TOPIC_IDS
        forced_topics = set()
    else:
        # A specific list: scope the run to exactly these topics AND force
        # them -- see the comment above ``if args.force_topics is None``
        # for why forcing without scoping would not mean anything different
        # against the default zero-stock lookup.
        forced_topics = {t.strip() for t in args.force_topics.split(",") if t.strip()}
        unknown_forced = forced_topics - set(TOPIC_IDS)
        if unknown_forced:
            print(f"Unknown --force-topics id(s), not in scope: {sorted(unknown_forced)}")
            return 1
        topic_universe = tuple(sorted(forced_topics))

    demands = compute_demand(
        list(topic_universe),
        stock_lookup,
        difficulty=args.difficulty,
        target_unseen=args.per_topic_target,
        forced=forced_topics,
        forced_floor=DEFAULT_FORCED_FLOOR,
    )
    if not demands:
        print(
            "No topic has demand (every topic is at or above its target and "
            "none was forced); nothing to generate."
        )
        return 0

    if not sentence_tagger.analysis_available():
        print(
            "spaCy's de_core_news_sm model is not installed; no items can be "
            "produced (degrading cleanly, not crashing). Install it "
            "(`python -m spacy download de_core_news_sm`) and re-run."
        )
        print(f"  Topics with demand:    {len(demands)}")
        print(f"  Topics in scope:       {len(TOPIC_IDS)}")
        return 0

    projected_calls = orchestrator.projected_call_count(
        demands, max_retries_per_topic=args.max_retries_per_topic
    )
    call_ceiling = args.call_ceiling if args.call_ceiling is not None else projected_calls
    _print_projected_call_count(projected_calls, call_ceiling)

    run_report = orchestrator.run_demand_driven_generation(
        generator,
        args.cefr,
        demands,
        max_retries_per_topic=args.max_retries_per_topic,
        call_ceiling=args.call_ceiling,
        max_items_per_topic=args.max_items_per_topic,
        max_items_per_sentence=args.max_items_per_sentence,
        difficulty=args.difficulty,
        themes=themes,
    )
    if not run_report.sentences:
        print(
            "No sentences survived generation and carrier validation "
            "(empty/unparseable model output, or every candidate was rejected "
            "as unsound German); nothing to do."
        )
        return 1

    report = run_report.blanking_report
    skips = _skip_details_from_report(report)
    _print_demand_run_report(run_report, ran_live=ran_live)

    theme_label = args.theme or "multi_theme_pool"
    batch_id = _blank_pilot_run_id(run_report.sentences, args.cefr, theme_label)
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

    # The model verification backstop (module docstring's own section):
    # runs LAST, over items that already survived every structural check
    # above. Shares the SAME ``llm_client`` sentence generation just used
    # (never builds a second one), so both draw from one cost log and one
    # rate-limiter, and both are unconditionally forbidden from queuing a
    # real Batch API job, but NOT from the paid lane itself (forbid_batch=True,
    # forbid_paid_lane=False -- see sentence_source.client_from_env). With no
    # client configured (``ran_live`` is ``False``) this is a documented
    # no-op -- every item is reported "not_run", never silently "verified".
    # main() below turns a nonzero not_run_count into a nonzero exit code so
    # a skipped backstop can never look like a successful run again.
    verification_report = verify_items(
        accepted_items, llm_client, batch_size=DEFAULT_VERIFICATION_BATCH_SIZE
    )
    final_items = _apply_model_verification(accepted_items, verification_report)

    rejected_records = [
        _to_rejected_record(skip)
        for skip in [
            *skips,
            *_dropped_items_to_skips(report.dropped_details),
            *_uniqueness_skips_to_skips(report.uniqueness_skips),
            *unknown_topic_skips,
        ]
    ] + [_model_rejection_to_record(r) for r in verification_report.rejections]

    review_path = Path(args.review_file)
    rejected_path = Path(args.rejected_file)
    _write_review_file(review_path, final_items, batch_id)
    _write_rejected_file(rejected_path, rejected_records, batch_id)

    _print_verification_report(verification_report)

    print(f"  Review file:           {review_path}")
    print(f"  Rejected file:         {rejected_path}")

    if verification_report.not_run_count > 0:
        # The model verification backstop is the whole point of this
        # script's "generate then blank then verify" architecture (module
        # docstring's "model verification backstop" section). A run whose
        # backstop did not execute -- budget ceiling, missing key, a
        # transport failure, or a batch-forbidden client that somehow still
        # hit that path -- must never look like a successful run: exit 0
        # here is exactly what let the run that consumed the whole
        # free-lane quota on sentence generation report zero
        # item_verification calls in cost_log.jsonl while still reporting
        # success. Items the model actually rejected are NOT a failure of
        # this run; only items the pass never got to judge are.
        print()
        print(
            "  FAILING: the model verification backstop did not run for "
            f"{verification_report.not_run_count} item(s) (see 'Items not "
            "verified' above for why). A run where this backstop did not "
            "execute is not a valid pilot run."
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
