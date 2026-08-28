"""TODO.md 2.2: does the verifier notice that an item's English gloss is WRONG?

## Why this exists

Since TODO.md 5.1 step 4, the model verification pass reads each item's
English gloss and RELAXES its uniqueness judgment against it: an alternative
answer the translation rules out no longer counts as a second correct answer
(``model_verification._format_item_block``, and the ``Die Lernenden sehen zu
jeder Aufgabe auch die englische Uebersetzung`` paragraph of
``_INSTRUCTION_DE_LIVE``). That relaxation is load-bearing. It turned 44
tense rejections into 6 and 376 accepted items into 437 in one cycle.

Nobody has ever checked whether the verifier notices a gloss that is wrong.
If it does not, the pass is relaxing a real judgment on evidence it never
validated, which is worse than not reading the gloss at all. This script is
the measurement, and the fixture under it is the safety net's own test.

## What is being measured, honestly

**No question in the live instruction asks whether the gloss is correct.**
The four questions are: is the completed sentence good German, is the stated
answer the only filler, does every word exist, and is the cue the right
citation form. The gloss enters only question 2, and only as a reason to
rule an alternative OUT. So a catch here is INCIDENTAL: it happens when a
wrong gloss makes the stated answer look like the wrong filler, and the
model says so under question 2.

That is deliberately not fixed before measuring. The number this script
produces is the recall of the pass as it actually ships today. If the recall
is poor, adding a fifth question ("does the translation match the German
sentence?") is the obvious next move, and this same fixture measures whether
it helped.

## The fixture

``data/fixtures/adversarial/wrong_glosses.jsonl``: 36 matched pairs, 72 rows,
drawn from the owner's own last corpus pilot (430 accepted items). Every
German prompt, answer and cue is copied verbatim from that pilot; only the
wrong glosses are hand-authored. Each pair contributes:

- a ``wrong`` row carrying a deliberately defective gloss, labelled with one
  of six defect kinds, six pairs each: ``wrong_tense``, ``wrong_person``,
  ``wrong_number``, ``wrong_definiteness``, ``wrong_polarity``, ``unrelated``
- a ``correct`` row carrying the item's own real gloss, unchanged

The matched design is what makes the numbers mean something. A rejection on
the wrong row that does NOT also happen on that pair's correct row is
attributable to the gloss; a rejection on both is the verifier disliking the
item itself, and this script reports the two separately rather than counting
the second as a catch. The two arms are verified in SEPARATE calls so the
model never sees a pair's two glosses side by side.

## What we expect, written down before the run so the result can surprise us

The project owner's prediction, recorded verbatim in the brief that
commissioned this script: **wrong polarity and completely unrelated are the
two he most expects it to catch, and wrong definiteness the one he most
expects it to miss.** This module agrees, and adds the mechanism:

- ``unrelated`` should be near total. The gloss shares no content with the
  German at all, and question 2 asks the model to reason from the prompt to
  the answer with the translation in hand; a translation about a completely
  different event cannot support any filler.
- ``wrong_polarity`` should be high but is the interesting one. In four of
  the six pairs the dropped negation sits OUTSIDE the gap, so the stated
  answer stays perfectly correct German and only the gloss is wrong. Nothing
  in the four questions asks about that. If those four are missed while the
  two ``kein`` pairs are caught, the pass is not reading the gloss against
  the German; it is only noticing when the gloss changes what belongs in the
  gap.
- ``wrong_tense``, ``wrong_person`` and ``wrong_number`` should be caught
  where the answer itself carries that morphology, because there the wrong
  gloss points at a different, real German form for the same gap.
- ``wrong_definiteness`` is expected to be the weakest. "I am sending the
  invoice by fax" and "I am sending an invoice by fax" are both ordinary
  English, the German sentence reads naturally either way, and the
  instruction explicitly tells the model a translation settles definiteness,
  which cuts both ways: it licenses the model to accept whichever article
  the gloss implies rather than to object.

A result that contradicts any of the above is worth more than one that
confirms it. Nothing in this script is tuned to produce the expected shape.

**One property of the fixture to keep in mind when reading the per-kind
numbers.** ``wrong_person`` is the easiest kind here, easier than the
prediction above implies, because most of its carriers state their subject
outright ("Du ___ meine Frage noch nicht beantwortet." glossed "He hasn't
answered my question yet."). The model can see the gloss contradict the
visible "Du" without reading the gap at all. A high ``wrong_person`` recall
is therefore weaker evidence than a high ``wrong_tense`` one, where the only
thing that gives the defect away is the verb form in the gap. This is a
property of real German sentences, not something the fixture could have
avoided while still drawing every carrier from real accepted items.

## Refusing to invent numbers

With no API key configured this script makes zero network calls and prints
that recall and false-positive rate were NOT measured, then exits non-zero.
It never reports a zero recall for a run that did not happen. The same
applies to a transport failure and to any item left ``not_run`` for any other
reason, exactly as ``scripts/eval_verifier.py`` already does.

## Converging over several days, and being able to see it

On ``--free-lane-only`` this eval does not necessarily finish in one day: the
free tier's daily request allowance for ``MODEL_VERIFY`` is in the low tens,
most attempts come back 503, and 72 rows in two separate arms need more
successful requests than one day usually grants. The local content-addressed
cache (CLAUDE.md 9) is what makes the same command, run again tomorrow,
cheaper rather than merely repeated: every batch that landed is replayed for
free and only the missing ones spend quota.

So an incomplete run prints a PROGRESS block -- rows with a cached model
verdict, rows still missing, and that re-running replays the former at zero
cost -- and names the actual cause of the ``not_run`` rows instead of listing
possible ones. The count comes from the cache
(``model_verification.cache_coverage``), not from this run's own
verified-plus-rejected total, because a run that lands several batches and is
then refused by the daily quota degrades every row to ``not_run`` and reports
zero judged while the cache genuinely gained those batches.

This changes nothing about what counts as a measurement: any ``not_run`` row
still means neither number is reported as measured, and the run still exits
non-zero.

## Why this is not a flag on scripts/eval_verifier.py

That script measures a different thing on a differently shaped fixture: 69
unpaired items whose GERMAN is known good or known bad, with no glosses at
all, and tests that pin its record counts. This fixture is paired, its rows
differ only in the gloss, and the arms must not share a batch. One script per
measurement is clearer than one script with two fixture formats.

Run this from a terminal, with a key configured:

    uv run python -m scripts.eval_gloss_adversarial
    uv run python -m scripts.eval_gloss_adversarial --batch-size 5
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from src.contracts import BankItem
from src.generation.blanking import sentence_source
from src.generation.blanking.model_verification import (
    DEFAULT_VERIFICATION_BATCH_SIZE,
    CacheCoverage,
    VerificationReport,
    cache_coverage,
    describe_not_run_cause,
    verify_items,
)
from src.generation.gloss_validation import (
    english_analysis_available,
    validate_gloss_consistency,
)
from src.llm.client import GeminiLlmClient
from src.llm.env import load_env_file
from src.taxonomy.loader import load_taxonomy

DEFAULT_FIXTURE_PATH = Path("data/fixtures/adversarial/wrong_glosses.jsonl")

# The six defect kinds the fixture labels its wrong rows with, in the order
# they are reported. Fixed here rather than derived from the file so a kind
# that silently disappears from the fixture shows up as an empty row in the
# report instead of vanishing from it.
DEFECT_KINDS: tuple[str, ...] = (
    "wrong_tense",
    "wrong_person",
    "wrong_number",
    "wrong_definiteness",
    "wrong_polarity",
    "unrelated",
)


@dataclass(frozen=True)
class GlossRecord:
    """One fixture row, kept as its own small type so nothing downstream
    guesses at a JSONL field's presence or type."""

    id: str
    pair_id: str
    source_item_id: str
    topic_id: str
    prompt: str
    answer: str
    cue: str | None
    arm: str
    gloss_en: str
    defect_kind: str | None
    defect_description: str | None


def _as_optional_str(value: object) -> str | None:
    """``value`` narrowed to ``str | None`` for a JSON field that is either a
    string or JSON ``null`` in the fixture format."""
    return None if value is None else str(value)


def load_fixture(path: Path = DEFAULT_FIXTURE_PATH) -> list[GlossRecord]:
    """Every non-``_meta`` row in ``path``, in file order. The leading
    metadata record is skipped here so no caller has to remember to."""
    records: list[GlossRecord] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw: dict[str, object] = json.loads(line)
            if raw.get("_meta"):
                continue
            records.append(
                GlossRecord(
                    id=str(raw["id"]),
                    pair_id=str(raw["pair_id"]),
                    source_item_id=str(raw["source_item_id"]),
                    topic_id=str(raw["topic_id"]),
                    prompt=str(raw["prompt"]),
                    answer=str(raw["answer"]),
                    cue=_as_optional_str(raw["cue"]),
                    arm=str(raw["arm"]),
                    gloss_en=str(raw["gloss_en"]),
                    defect_kind=_as_optional_str(raw["defect_kind"]),
                    defect_description=_as_optional_str(raw["defect_description"]),
                )
            )
    return records


def wrong_rows(records: list[GlossRecord]) -> list[GlossRecord]:
    return [r for r in records if r.arm == "wrong"]


def correct_rows(records: list[GlossRecord]) -> list[GlossRecord]:
    return [r for r in records if r.arm == "correct"]


def to_bank_item(record: GlossRecord) -> BankItem:
    """A minimal, valid ``BankItem`` for one fixture row. Only the four
    fields ``model_verification._format_item_block`` actually reads
    (``prompt``, ``cue``, ``gloss_en``, ``accepted_answers``) carry the
    fixture's real content; every other required field is a fixed,
    arbitrary-but-valid placeholder, because none of them ever reaches the
    model (CLAUDE.md rule 2, and that helper's own docstring).

    ``topic_id`` is carried anyway, unused by the prompt, purely so a
    rejection can be traced back to its source item when reading the report.
    """
    return BankItem(
        id=record.id,
        topic_id=record.topic_id,
        tag_id=record.topic_id,
        type="cloze_cued" if record.cue else "cloze_free",
        difficulty=1,
        cefr="A2",
        prompt=record.prompt,
        accepted_answers=[record.answer],
        cue=record.cue,
        gloss_en=record.gloss_en,
    )


@dataclass(frozen=True)
class ArmResult:
    """One arm's raw counts. ``judged`` excludes ``not_run``, because an item
    the pass never judged is neither a catch nor a miss."""

    label: str
    total: int
    verified: int
    rejected: int
    not_run: int

    @property
    def judged(self) -> int:
        return self.verified + self.rejected

    def rate(self) -> float | None:
        """``rejected / judged``, or ``None`` when nothing was judged at all
        -- never a silent ``0.0`` for a rate that was never measured."""
        if self.judged == 0:
            return None
        return self.rejected / self.judged


def summarize_arm(label: str, report: VerificationReport, total: int) -> ArmResult:
    return ArmResult(
        label=label,
        total=total,
        verified=report.verified_count,
        rejected=report.rejected_count,
        not_run=report.not_run_count,
    )


@dataclass(frozen=True)
class KindResult:
    """One defect kind's recall, split three ways so a rejection the verifier
    would have made anyway is never counted as a catch.

    - ``attributable``: the wrong row was rejected and its pair's correct row
      was verified. The gloss is what changed the verdict.
    - ``rejected_both``: both rows were rejected. A catch of the ITEM, not of
      the gloss, and reported separately for that reason.
    - ``missed``: the wrong row was verified. The gloss went through.
    """

    kind: str
    total: int
    attributable: int
    rejected_both: int
    missed: int
    unresolved: int


def kind_results(
    records: list[GlossRecord],
    wrong_report: VerificationReport,
    correct_report: VerificationReport,
) -> list[KindResult]:
    """Per-kind recall, computed by pairing each wrong row's verdict with its
    own pair's correct-row verdict. ``unresolved`` counts pairs where either
    verdict was ``not_run``; ``main()`` already refuses to report a run with
    any of those, so it exists to make such a run visibly broken rather than
    quietly mis-attributed."""
    wrong = wrong_rows(records)
    correct = correct_rows(records)
    wrong_verdict = {r.id: v.outcome for r, v in zip(wrong, wrong_report.verdicts, strict=True)}
    correct_by_pair = {
        r.pair_id: v.outcome for r, v in zip(correct, correct_report.verdicts, strict=True)
    }

    results: list[KindResult] = []
    for kind in DEFECT_KINDS:
        rows = [r for r in wrong if r.defect_kind == kind]
        attributable = 0
        rejected_both = 0
        missed = 0
        unresolved = 0
        for row in rows:
            outcome = wrong_verdict[row.id]
            paired = correct_by_pair.get(row.pair_id)
            if outcome == "not_run" or paired is None or paired == "not_run":
                unresolved += 1
            elif outcome == "rejected" and paired == "verified":
                attributable += 1
            elif outcome == "rejected":
                rejected_both += 1
            else:
                missed += 1
        results.append(
            KindResult(
                kind=kind,
                total=len(rows),
                attributable=attributable,
                rejected_both=rejected_both,
                missed=missed,
                unresolved=unresolved,
            )
        )
    return results


def _print_arm(result: ArmResult, *, rate_name: str) -> None:
    print(f"  {result.label}: {result.total} rows")
    print(f"    verified:  {result.verified}")
    print(f"    rejected:  {result.rejected}")
    print(f"    not_run:   {result.not_run}")
    rate = result.rate()
    if rate is None:
        print(f"    {rate_name}: COULD NOT BE MEASURED (every row was not_run)")
    else:
        print(f"    {rate_name}: {rate:.1%}  ({result.rejected} of {result.judged} judged rows)")


def _print_kinds(results: list[KindResult]) -> None:
    print("  Recall by defect kind (attributable / total):")
    for r in results:
        extra = []
        if r.rejected_both:
            extra.append(f"{r.rejected_both} rejected in both arms")
        if r.unresolved:
            extra.append(f"{r.unresolved} unresolved")
        suffix = f"   [{'; '.join(extra)}]" if extra else ""
        print(f"    - {r.kind:<20} {r.attributable}/{r.total}   missed {r.missed}{suffix}")


def _print_missed(records: list[GlossRecord], wrong_report: VerificationReport) -> None:
    """Every wrong gloss that went through, printed in full. The counts say
    how bad it is; these lines say what got past, which is what a reader
    needs in order to decide what to do next."""
    wrong = wrong_rows(records)
    missed = [
        r for r, v in zip(wrong, wrong_report.verdicts, strict=True) if v.outcome == "verified"
    ]
    if not missed:
        print("  No wrong gloss was accepted.")
        return
    print(f"  Wrong glosses the pass accepted ({len(missed)}):")
    for r in missed:
        print(f"    - [{r.defect_kind}] {r.prompt}   ({r.answer})")
        print(f"        gloss shown: {r.gloss_en}")


def _print_false_positives(records: list[GlossRecord], correct_report: VerificationReport) -> None:
    """Every CORRECT gloss the pass rejected. These are the false positives,
    and each one costs a good item, so they are named rather than counted."""
    correct = correct_rows(records)
    rejected = [
        r for r, v in zip(correct, correct_report.verdicts, strict=True) if v.outcome == "rejected"
    ]
    if not rejected:
        print("  No correct gloss was rejected.")
        return
    print(f"  Correct glosses the pass rejected ({len(rejected)}):")
    reason_by_prompt = {rej.prompt: rej.reason for rej in correct_report.rejections}
    for r in rejected:
        print(f"    - {r.prompt}   ({r.answer})")
        print(f"        gloss shown: {r.gloss_en}")
        reason = reason_by_prompt.get(r.prompt)
        if reason:
            print(f"        reason: {reason}")


def _combined_cache_coverage(
    row_groups: Sequence[Sequence[GlossRecord]],
    llm_client: GeminiLlmClient | None,
    *,
    batch_size: int,
) -> CacheCoverage:
    """One coverage figure over both arms. The two arms are verified in two
    separate ``verify_items`` calls, deliberately (a pair's two glosses must
    never share a batch), so each is measured on its own batching and the
    totals are added -- never by concatenating the rows, which would build
    batch prompts that straddle the arms and match nothing the run wrote."""
    total = 0
    cached = 0
    for group in row_groups:
        coverage = cache_coverage(
            [to_bank_item(r) for r in group], llm_client, batch_size=batch_size
        )
        total += coverage.total_items
        cached += coverage.cached_items
    return CacheCoverage(total_items=total, cached_items=cached)


def _print_progress(coverage: CacheCoverage, *, batch_size: int) -> None:
    """The one block the operator reads to decide whether a multi-day
    free-lane run is converging or stuck. Printed only when the run was
    incomplete: a complete run's own numbers are the result, and a progress
    line under them would only muddy which of the two to read."""
    print()
    print("  PROGRESS TOWARD A COMPLETE MEASUREMENT")
    print(
        f"    rows with a cached model verdict: {coverage.cached_items} of {coverage.total_items}"
    )
    print(f"    rows still missing a verdict:     {coverage.missing_items}")
    print(
        "    Re-running this exact command replays those "
        f"{coverage.cached_items} cached verdict(s) from the local cache at "
        f"zero cost and spends quota only on the remaining "
        f"{coverage.missing_items}. The measurement completes on the first run "
        f"that reaches {coverage.total_items} of {coverage.total_items}."
    )
    print(
        f"    Keep --batch-size at {batch_size} across runs: the cache is keyed "
        "on the exact batch prompt, so changing it discards this progress."
    )


def _run_deterministic(records: list[GlossRecord]) -> int:
    """Score the DETERMINISTIC gloss check instead of the model verifier.

    docs/known-defects.md 2.15 is closed by a rule, not by a fifth question in
    the verification instruction, so the model-backed arm of this script cannot
    measure it: it would report the model's own recall, which was 0 of 6 and is
    exactly the number the rule exists to replace. This arm calls
    ``gloss_validation.validate_gloss_consistency`` directly.

    It costs nothing, needs no API key and touches no network, which is also
    why it is worth having: the number can be re-checked on every commit
    instead of once per billing decision.
    """
    wrong = wrong_rows(records)
    correct = correct_rows(records)

    print("==========================================================")
    print("  Deterministic gloss check (no model, no key, no cost)")
    print("  docs/known-defects.md 2.15 / TODO.md item 1")
    print("==========================================================")
    print(f"  Wrong rows:   {len(wrong)}")
    print(f"  Correct rows: {len(correct)}")
    if not english_analysis_available():
        print()
        print("  FAILING: en_core_web_sm did not load, so nothing was measured.")
        return 1

    # Pass the real Topic, exactly as the pipeline does. The tense dimension
    # reads it (``_german_tense_bucket`` prefers the topic's own ``morph_spec``
    # over the answer token's FEATS), and with ``None`` a Perfekt formed with
    # "sein" is read off its present-tense auxiliary as present tense, which
    # rejects four correct past-tense glosses. That is a false positive
    # belonging to the harness, not to the check.
    topics_by_id = {topic.id: topic for topic in load_taxonomy()}

    def rejects(record: GlossRecord) -> tuple[bool, str]:
        result = validate_gloss_consistency(
            record.prompt,
            record.answer,
            record.gloss_en,
            topics_by_id.get(record.topic_id),
        )
        return (not result.consistent), (result.reason or "")

    by_kind: dict[str, list[tuple[GlossRecord, bool, str]]] = {}
    for record in wrong:
        caught, reason = rejects(record)
        by_kind.setdefault(record.defect_kind or "unknown", []).append((record, caught, reason))

    print()
    print("  Recall by defect kind:")
    for kind in sorted(by_kind):
        rows = by_kind[kind]
        hits = sum(1 for _r, caught, _why in rows if caught)
        marker = "  <-- 2.15" if kind == "wrong_number" else ""
        print(f"    {kind:20} {hits}/{len(rows)}{marker}")

    number_rows = by_kind.get("wrong_number", [])
    number_hits = sum(1 for _r, caught, _why in number_rows if caught)

    false_positives: list[tuple[GlossRecord, str]] = []
    for record in correct:
        caught, reason = rejects(record)
        if caught:
            false_positives.append((record, reason))

    print()
    print(f"  wrong_number caught:  {number_hits}/{len(number_rows)}")
    print(f"  False positives:      {len(false_positives)}/{len(correct)}")
    for record, reason in false_positives:
        print(f"    {record.id}: {reason}")
        print(f"      gloss: {record.gloss_en}")

    print()
    if number_rows and number_hits > 0 and not false_positives:
        print("  PASS: 2.15 is caught and no correct gloss was rejected.")
        return 0
    print("  FAIL: see TODO.md item 1 for the bar this has to clear.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure whether the model verifier notices a wrong English gloss."
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_VERIFICATION_BATCH_SIZE,
        help=(
            "Items per verification prompt, passed straight through to "
            f"verify_items (default {DEFAULT_VERIFICATION_BATCH_SIZE}, unchanged "
            "by this script)."
        ),
    )
    parser.add_argument(
        "--fixture",
        type=str,
        default=str(DEFAULT_FIXTURE_PATH),
        help="Path to the adversarial gloss fixture JSONL.",
    )
    parser.add_argument(
        "--deterministic",
        action="store_true",
        help=(
            "Score the deterministic gloss check instead of the model "
            "verifier. No API key, no network, no cost. This is the arm that "
            "measures docs/known-defects.md 2.15, which is closed by a rule "
            "rather than by a model question."
        ),
    )
    parser.add_argument(
        "--free-lane-only",
        action="store_true",
        help=(
            "Forbid the paid lane outright for this run: it measures the "
            "verifier on the unbilled project or not at all. Requires "
            "GEMINI_FREE_API_KEY to be set explicitly (the run refuses to "
            "start otherwise, rather than falling back to GEMINI_API_KEY, "
            "which may be a billed key). Off by default; without it this "
            "script behaves exactly as it did before the flag existed."
        ),
    )
    args = parser.parse_args()

    if args.deterministic:
        return _run_deterministic(load_fixture(Path(args.fixture)))

    load_env_file()
    try:
        llm_client = sentence_source.client_from_env(free_lane_only=args.free_lane_only)
    except sentence_source.FreeLaneKeyMissingError as exc:
        print()
        print(f"  FAILING: {exc}")
        return 1

    records = load_fixture(Path(args.fixture))
    wrong = wrong_rows(records)
    correct = correct_rows(records)

    print("==========================================================")
    print("  Adversarial gloss eval: does a WRONG gloss get caught?")
    print("  TODO.md 2.2")
    print("==========================================================")
    print(f"  Fixture:     {args.fixture}")
    print(f"  Wrong rows:  {len(wrong)}")
    print(f"  Correct rows:{len(correct)}")
    print(f"  Batch size:  {args.batch_size}")

    if not wrong or not correct:
        print()
        print("  FAILING: the fixture must carry both arms. Nothing was measured.")
        return 1

    if llm_client is None:
        print()
        print(
            "  NOT RUN: no API key configured (GEMINI_FREE_API_KEY / "
            "GEMINI_PAID_API_KEY / GEMINI_API_KEY). This is the honest, "
            "expected offline result. Recall and false-positive rate could "
            "NOT be measured this run, and are reported as nothing rather "
            "than as zero."
        )
        return 1

    try:
        # Two separate calls, deliberately: a pair's wrong row and its
        # correct row must never share a batch, or the model would be
        # comparing two glosses of one German sentence instead of judging
        # each item the way a learner sees it.
        wrong_report = verify_items(
            [to_bank_item(r) for r in wrong], llm_client, batch_size=args.batch_size
        )
        correct_report = verify_items(
            [to_bank_item(r) for r in correct], llm_client, batch_size=args.batch_size
        )
    except Exception as exc:  # noqa: BLE001 -- an unreachable network (a key IS
        # configured, but outbound calls never reach Gemini) is a failure shape
        # none of verify_items's own caught transport exceptions covers, and it
        # must degrade exactly as honestly as the no-key case above rather than
        # crash with a traceback that reads like a bug in this script.
        print()
        print(
            "  NOT RUN: a transport error prevented any model call from "
            f"completing ({type(exc).__name__}: {exc}). Recall and "
            "false-positive rate could NOT be measured this run."
        )
        _print_progress(
            _combined_cache_coverage([wrong, correct], llm_client, batch_size=args.batch_size),
            batch_size=args.batch_size,
        )
        return 1

    recall_arm = summarize_arm("Wrong glosses", wrong_report, len(wrong))
    fpr_arm = summarize_arm("Correct glosses", correct_report, len(correct))

    print()
    _print_arm(recall_arm, rate_name="raw catch rate")
    print()
    _print_kinds(kind_results(records, wrong_report, correct_report))
    print()
    _print_missed(records, wrong_report)
    print()
    _print_arm(fpr_arm, rate_name="false-positive rate")
    print()
    _print_false_positives(records, correct_report)

    not_run_total = recall_arm.not_run + fpr_arm.not_run
    if not_run_total > 0:
        print()
        print(
            f"  FAILING: {not_run_total} row(s) across both arms were not_run. "
            "A run where the pass did not judge every row is not a valid "
            "measurement of either number, whatever the rates above say."
        )
        # Name what actually happened rather than listing what might have:
        # both the reason slugs and the degrading exception's own message are
        # already on the reports, and for the case this script hits most -- a
        # --free-lane-only run refused by the free tier's daily allowance --
        # that message names RPD and the reset time in local time.
        for line in describe_not_run_cause([wrong_report, correct_report]):
            print(f"    {line}")
        _print_progress(
            _combined_cache_coverage([wrong, correct], llm_client, batch_size=args.batch_size),
            batch_size=args.batch_size,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
