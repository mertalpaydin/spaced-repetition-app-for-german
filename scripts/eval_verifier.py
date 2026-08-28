"""TODO.md 3.3: measure the model verification pass's recall instead of
guessing it.

## Why this exists

``src.generation.blanking.model_verification.verify_items`` (the model
backstop over finished, already-accepted items -- see that module's own
docstring) has never had its recall measured. docs/audits/cycle-08-report.md
and cycle-09-report.md both record that it caught real defects and missed
others of the same class in the same run, but until this script existed
"the verifier is unreliable" was, in TODO.md 3.3's own words, "something we
both believe and neither of us can act on."

## The instrument

This script runs ``verify_items`` over two golden fixtures under
``data/fixtures/verification/`` (CLAUDE.md section 7: versioned, not
regenerated casually):

- ``blanking_model_verifier_adversarial.jsonl`` -- 38 hand-confirmed
  defects, one record per defect, quoted from docs/audits/cycle-07-report.md
  (14), cycle-08-report.md (7) and cycle-09-report.md (17). **Recall** is
  how many of these the pass rejects.
- ``blanking_model_verifier_known_clean.jsonl`` -- 31 hand-confirmed clean
  items, sampled from the topic families both cycle-08-report.md and
  cycle-09-report.md name as audited item by item with nothing found.
  **False-positive rate** is how many of these the pass wrongly rejects.

Both fixtures carry a leading ``{"_meta": true, ...}`` record documenting
their own versioning contract; every future audit cycle that hand-confirms a
new defect appends a record to the adversarial fixture rather than editing
or replacing an existing one (TODO.md 3.3's own instruction, restated in
each fixture's own ``_meta`` record so it is not only in this script).

## TODO.md 3.4: batch size as a flag, not a fix

Items are verified ``DEFAULT_VERIFICATION_BATCH_SIZE`` (20) to a prompt, and
attention is plausibly not uniform across a 20-item list -- an item late in
a batch may get less scrutiny than one at the top. That is a theory to be
tested, not a fix already decided on, so this script takes ``--batch-size``
as a flag rather than changing the default: run

    .venv/bin/python -m scripts.eval_verifier --batch-size 5
    .venv/bin/python -m scripts.eval_verifier --batch-size 20

and compare the two recall numbers. ``DEFAULT_VERIFICATION_BATCH_SIZE``
itself is untouched by this script.

## Degrading honestly, exactly as the pilot does

This script needs a real ``GeminiLlmClient`` (``sentence_source.
client_from_env``, the same lane policy ``scripts/step6_blank_pilot.py``
already uses: ``forbid_batch=True``, ``forbid_paid_lane=False``) to make any
model call at all. With no API key configured -- the default state of this
container, and of CI -- it makes zero network calls and prints, honestly,
that recall and false-positive rate could not be measured this run, mirroring
``model_verification.verify_items(items, llm_client=None)``'s own "not_run"
outcome and ``scripts/step6_blank_pilot.py``'s own refusal to exit 0 when the
verification backstop did not execute (see that script's own ``main()``).
This script exits non-zero in exactly that situation, and non-zero again if
either fixture's run leaves any item ``not_run`` for any other reason
(a malformed response, a transport failure ``verify_items`` itself caught and
degraded cleanly, or a budget ceiling) -- a run that could not judge every
item is not a valid recall measurement and must never be reported as if the
two numbers below were real.

A second, distinct degrade path exists for exactly this development
container's own state: a real (if possibly stale) key IS configured in
``.env`` here, so ``client_from_env`` returns a real client, but outbound
calls hit a proxy 403 before ever reaching Gemini -- a transport failure
shaped nothing like the five exceptions ``verify_items`` itself catches
(``BudgetExceeded``, ``ServerUnavailableError``, ``PaidLaneForbiddenError``,
``BatchForbiddenError``, ``MissingApiKeyError``), so it propagates out of
``generate_many`` unmodified. This script's own ``main()`` catches that broadly, around the
``verify_items`` calls, and reports it exactly as honestly as the no-key
case above, rather than letting a raw traceback stand in for "could not
run" -- ``src/llm/client.py`` is explicitly off-limits for this task (TODO.md
section 5's owner-pinned constants live there), so the catch belongs at
this script's own boundary instead of a change to the transport layer.

## Converging over several days, and being able to see it

On ``--free-lane-only``, this eval does not necessarily finish in one day.
Google's free tier gives ``MODEL_VERIFY`` a daily request allowance in the low
tens, most attempts come back 503, and 69 items at ``--batch-size 5`` need 15
successful requests. What makes it work anyway is the local content-addressed
cache (CLAUDE.md 9): each day's successful batches land in it, and the next
day's identical command replays them for free and spends the day's quota only
on what is still missing.

That only helps if the operator can see it happening. An incomplete run
therefore prints a PROGRESS block -- how many of the items now have a cached
model verdict, how many are still missing, and that re-running the same
command tomorrow replays the former at zero cost -- so three days of the same
command reads as convergence rather than as three identical failures. The
count comes from the cache (``model_verification.cache_coverage``), not from
this run's own verified-plus-rejected total, because the two disagree exactly
when it matters: a run that lands five batches and is then refused by the
daily quota degrades every item to ``not_run`` and reports zero judged, while
the cache genuinely gained those five batches. See ``CacheCoverage``'s own
docstring.

None of this lowers the bar. A run with any ``not_run`` item still refuses to
report recall as measured and still exits non-zero; the progress block is
visibility into an incomplete run, not a partial pass.

Run this from a terminal:

    .venv/bin/python -m scripts.eval_verifier
    .venv/bin/python -m scripts.eval_verifier --batch-size 5
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
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
from src.llm.client import GeminiLlmClient
from src.llm.env import load_env_file

DEFAULT_ADVERSARIAL_PATH = Path(
    "data/fixtures/verification/blanking_model_verifier_adversarial.jsonl"
)
DEFAULT_KNOWN_CLEAN_PATH = Path(
    "data/fixtures/verification/blanking_model_verifier_known_clean.jsonl"
)


@dataclass(frozen=True)
class AdversarialRecord:
    """One hand-confirmed defect from the adversarial fixture, kept as its
    own small type (rather than a raw dict) so the rest of this script
    never guesses at a JSONL field's presence or type."""

    id: str
    source_cycle: int
    topic_id: str
    prompt: str
    answer: str
    cue: str | None
    defect_class: str
    defect_description: str


@dataclass(frozen=True)
class CleanRecord:
    """One hand-confirmed clean item from the known-clean fixture."""

    id: str
    topic_id: str
    prompt: str
    answer: str
    cue: str | None


def _as_int(value: object) -> int:
    """``value`` narrowed to ``int`` for a JSON field that is always an
    integer in the fixture format. A plain ``int(value)`` call does not
    type-check under ``mypy --strict`` against an ``object``-typed dict
    value (no overload matches), so this asserts the shape instead of
    reaching for a ``# type: ignore``."""
    assert isinstance(value, int)
    return value


def _as_optional_str(value: object) -> str | None:
    """``value`` narrowed to ``str | None`` for a JSON field (``cue``) that
    is either a string or JSON ``null`` in the fixture format."""
    return None if value is None else str(value)


def _load_jsonl_records(path: Path) -> list[dict[str, object]]:
    """Every non-``_meta`` record in ``path``, as raw dicts -- the one place
    this script reads a fixture file, so a caller never has to remember to
    skip the leading metadata record itself."""
    records: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("_meta"):
                continue
            records.append(record)
    return records


def load_adversarial_fixture(path: Path = DEFAULT_ADVERSARIAL_PATH) -> list[AdversarialRecord]:
    return [
        AdversarialRecord(
            id=str(r["id"]),
            source_cycle=_as_int(r["source_cycle"]),
            topic_id=str(r["topic_id"]),
            prompt=str(r["prompt"]),
            answer=str(r["answer"]),
            cue=_as_optional_str(r["cue"]),
            defect_class=str(r["defect_class"]),
            defect_description=str(r["defect_description"]),
        )
        for r in _load_jsonl_records(path)
    ]


def load_known_clean_fixture(path: Path = DEFAULT_KNOWN_CLEAN_PATH) -> list[CleanRecord]:
    return [
        CleanRecord(
            id=str(r["id"]),
            topic_id=str(r["topic_id"]),
            prompt=str(r["prompt"]),
            answer=str(r["answer"]),
            cue=_as_optional_str(r["cue"]),
        )
        for r in _load_jsonl_records(path)
    ]


def _to_bank_item(
    *, id_: str, topic_id: str, prompt: str, answer: str, cue: str | None
) -> BankItem:
    """A minimal, valid ``BankItem`` for one fixture record. Only the four
    fields ``model_verification._format_item_block`` actually reads
    (``prompt``, ``cue``, ``accepted_answers``) carry the fixture's real
    content; every other required field is a fixed, arbitrary-but-valid
    placeholder, because CLAUDE.md rule 2 already means none of them ever
    reaches the model anyway (``_format_item_block``'s own docstring)."""
    return BankItem(
        id=id_,
        topic_id=topic_id,
        tag_id=topic_id,
        type="cloze_cued" if cue else "cloze_free",
        difficulty=1,
        cefr="A2",
        prompt=prompt,
        accepted_answers=[answer],
        cue=cue,
    )


@dataclass
class EvalResult:
    label: str
    total: int
    verified: int
    rejected: int
    not_run: int

    @property
    def judged(self) -> int:
        """Items the pass actually reached a verdict on (excludes
        ``not_run``) -- the honest denominator for a rate, since an item
        the pass never judged is neither a catch nor a miss."""
        return self.verified + self.rejected

    def rate(self) -> float | None:
        """``rejected / judged``, or ``None`` if nothing was judged at all
        (every item ``not_run``) -- never a silent ``0.0`` for a rate that
        was never actually measured."""
        if self.judged == 0:
            return None
        return self.rejected / self.judged


def _summarize(label: str, report: VerificationReport, total: int) -> EvalResult:
    return EvalResult(
        label=label,
        total=total,
        verified=report.verified_count,
        rejected=report.rejected_count,
        not_run=report.not_run_count,
    )


def _print_result(result: EvalResult, *, rate_name: str) -> None:
    print(f"  {result.label}: {result.total} items")
    print(f"    verified:  {result.verified}")
    print(f"    rejected:  {result.rejected}")
    print(f"    not_run:   {result.not_run}")
    rate = result.rate()
    if rate is None:
        print(f"    {rate_name}: COULD NOT BE MEASURED (every item was not_run)")
    else:
        print(f"    {rate_name}: {rate:.1%}  ({result.rejected} of {result.judged} judged items)")


def _print_recall_by_defect_class(
    adversarial: list[AdversarialRecord], report: VerificationReport
) -> None:
    caught: Counter[str] = Counter()
    total: Counter[str] = Counter()
    for record, verdict in zip(adversarial, report.verdicts, strict=True):
        total[record.defect_class] += 1
        if verdict.outcome == "rejected":
            caught[record.defect_class] += 1
    print("  Recall by defect class:")
    for defect_class in sorted(total):
        print(f"    - {defect_class}: {caught[defect_class]}/{total[defect_class]}")


def _combined_cache_coverage(
    item_groups: Sequence[Sequence[BankItem]],
    llm_client: GeminiLlmClient | None,
    *,
    batch_size: int,
) -> CacheCoverage:
    """One coverage figure over both fixtures. They are verified in two
    separate ``verify_items`` calls with their own batching, so each is
    measured on its own and the totals are added -- never by concatenating the
    two item lists, which would build batch prompts that straddle the fixtures
    and match nothing the run ever wrote."""
    total = 0
    cached = 0
    for group in item_groups:
        coverage = cache_coverage(group, llm_client, batch_size=batch_size)
        total += coverage.total_items
        cached += coverage.cached_items
    return CacheCoverage(total_items=total, cached_items=cached)


def _print_progress(coverage: CacheCoverage, *, batch_size: int) -> None:
    """The one block the operator reads to decide whether a multi-day
    free-lane run is converging or stuck. Printed only when the run was
    incomplete: a complete run's own numbers say everything, and a progress
    line under them would only muddy which of the two is the result."""
    print()
    print("  PROGRESS TOWARD A COMPLETE MEASUREMENT")
    print(
        f"    items with a cached model verdict: {coverage.cached_items} of {coverage.total_items}"
    )
    print(f"    items still missing a verdict:     {coverage.missing_items}")
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure the model verifier's recall and FPR.")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_VERIFICATION_BATCH_SIZE,
        help=(
            "Items per verification prompt, passed straight through to "
            "verify_items (TODO.md 3.4: a flag to test the theory that a "
            f"smaller batch raises recall, default {DEFAULT_VERIFICATION_BATCH_SIZE} "
            "-- unchanged by this script)."
        ),
    )
    parser.add_argument(
        "--adversarial-file",
        type=str,
        default=str(DEFAULT_ADVERSARIAL_PATH),
        help="Path to the adversarial (known-defect) fixture JSONL.",
    )
    parser.add_argument(
        "--known-clean-file",
        type=str,
        default=str(DEFAULT_KNOWN_CLEAN_PATH),
        help="Path to the known-clean fixture JSONL.",
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

    load_env_file()
    try:
        llm_client = sentence_source.client_from_env(free_lane_only=args.free_lane_only)
    except sentence_source.FreeLaneKeyMissingError as exc:
        print()
        print(f"  FAILING: {exc}")
        return 1

    adversarial = load_adversarial_fixture(Path(args.adversarial_file))
    known_clean = load_known_clean_fixture(Path(args.known_clean_file))

    adversarial_items = [
        _to_bank_item(id_=r.id, topic_id=r.topic_id, prompt=r.prompt, answer=r.answer, cue=r.cue)
        for r in adversarial
    ]
    clean_items = [
        _to_bank_item(id_=r.id, topic_id=r.topic_id, prompt=r.prompt, answer=r.answer, cue=r.cue)
        for r in known_clean
    ]

    print("==========================================================")
    print("  Verifier eval: recall and false-positive rate (TODO 3.3)")
    print("==========================================================")
    print(f"  Adversarial fixture: {args.adversarial_file} ({len(adversarial_items)} defects)")
    print(f"  Known-clean fixture: {args.known_clean_file} ({len(clean_items)} clean items)")
    print(f"  Batch size:          {args.batch_size}")

    if llm_client is None:
        print()
        print(
            "  NOT RUN: no API key configured (GEMINI_FREE_API_KEY / "
            "GEMINI_PAID_API_KEY / GEMINI_API_KEY). This is the honest, "
            "expected offline result -- see this module's own docstring, "
            "and model_verification.verify_items(items, llm_client=None), "
            "which this call mirrors exactly. Recall and false-positive "
            "rate could NOT be measured this run."
        )
        return 1

    try:
        adversarial_report = verify_items(adversarial_items, llm_client, batch_size=args.batch_size)
        clean_report = verify_items(clean_items, llm_client, batch_size=args.batch_size)
    except Exception as exc:  # noqa: BLE001 -- see the docstring: an unreachable
        # network (this container's own state -- an API key IS configured,
        # but outbound calls hit a proxy 403, an error shape none of
        # ``verify_items``'s own five caught transport/budget exceptions
        # cover, since it never reaches Gemini at all) must degrade exactly
        # as honestly as the documented no-key case above, never crash with
        # a raw traceback that looks like a bug in THIS script rather than
        # an environment that cannot reach the model.
        print()
        print(
            "  NOT RUN: a transport error prevented any model call from "
            f"completing ({type(exc).__name__}: {exc}). Recall and "
            "false-positive rate could NOT be measured this run -- this is "
            "expected in a container with no outbound network access, "
            "exactly as the no-API-key case above."
        )
        _print_progress(
            _combined_cache_coverage(
                [adversarial_items, clean_items], llm_client, batch_size=args.batch_size
            ),
            batch_size=args.batch_size,
        )
        return 1

    recall_result = _summarize("Adversarial (known defects)", adversarial_report, len(adversarial))
    fpr_result = _summarize("Known clean", clean_report, len(known_clean))

    print()
    _print_result(recall_result, rate_name="recall")
    print()
    _print_recall_by_defect_class(adversarial, adversarial_report)
    print()
    _print_result(fpr_result, rate_name="false-positive rate")

    not_run_total = recall_result.not_run + fpr_result.not_run
    if not_run_total > 0:
        print()
        print(
            f"  FAILING: {not_run_total} item(s) across both fixtures were not_run. "
            "A run where the pass did not judge every item is not a valid "
            "recall/false-positive measurement, whatever the rates above say."
        )
        # Name what actually happened rather than listing what might have.
        # The reason slugs and the degrading exception's own message are both
        # already on the reports (model_verification.VerificationReport), and
        # for the case this script hits most -- a --free-lane-only run refused
        # by the free tier's daily allowance -- the message names RPD and the
        # reset time in local time.
        for line in describe_not_run_cause([adversarial_report, clean_report]):
            print(f"    {line}")
        _print_progress(
            _combined_cache_coverage(
                [adversarial_items, clean_items], llm_client, batch_size=args.batch_size
            ),
            batch_size=args.batch_size,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
