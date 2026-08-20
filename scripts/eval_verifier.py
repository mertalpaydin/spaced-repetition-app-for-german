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

Run this from a terminal:

    .venv/bin/python -m scripts.eval_verifier
    .venv/bin/python -m scripts.eval_verifier --batch-size 5
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from src.contracts import BankItem
from src.generation.blanking import sentence_source
from src.generation.blanking.model_verification import (
    DEFAULT_VERIFICATION_BATCH_SIZE,
    VerificationReport,
    verify_items,
)
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
    args = parser.parse_args()

    load_env_file()
    llm_client = sentence_source.client_from_env()

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
            f"  FAILING: {not_run_total} item(s) across both fixtures were not_run "
            "(malformed response, transport failure, or budget ceiling -- see "
            "the counts above). A run where the pass did not judge every item "
            "is not a valid recall/false-positive measurement."
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
