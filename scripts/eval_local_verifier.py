"""TODO.md item 0: measure a local model as the verification backstop.

## What this measures, and against what

The same two golden fixtures ``scripts/eval_verifier.py`` uses, through the
same ``verify_items`` pass, with the transport swapped for
``src.llm.local_client.LocalLlmClient``. Nothing about the instruction, the
batching or the parsing changes, which is the point: a difference in the
result is then a difference in the model, not in the harness.

Gemini's figures on this same instrument, measured 2026-08-28 and recorded in
``docs/project-state.md``, are the baseline every local model is read against:

    recall            60.5%  pooled over 38 adversarial records
    recall (visible)  15/20  on records whose defect the model can actually see
    false positives   12.9%  on 31 hand-confirmed clean records

## Why there are two recall numbers, and why the second is the real one

CLAUDE.md rule 2 forbids an item from naming the grammar topic it tests, and
the verification instruction honours that: the model is shown the prompt, the
cue and the answer, and is never told the ``topic_id``. Eighteen of the 38
adversarial records are correct German with exactly one right answer, and are
defects **only** relative to the topic they were filed under -- a reflexive
routed to the wrong case for its topic, a Futur I item whose carrier is
actually a passive participle, an item misfiled under a topic its sentence
does not exercise at all. No model that is not told the topic can identify
those as defects, and it would be a rule-2 violation to tell it.

So a pooled recall figure over all 38 is measuring, in part, an impossibility.
``TOPIC_DEPENDENT_DEFECT_CLASSES`` below names the 18, and this script reports
recall over the other 20 alongside the pooled figure.

**That classification is derived here, not authored upstream.** It was
obtained by reading each ``defect_class`` in the fixture against
``docs/known-defects.md`` 2.10 to 2.14, and it reproduces the 18/20 split
``docs/project-state.md`` states independently, which is corroboration rather
than proof. It is written out as a constant, rather than inlined, so a future
reader can disagree with a specific class instead of with a number.

## Known fixture caveat, carried into the output

TODO.md item 2.7b: fixture records ``c08_04`` and ``c08_05`` are filed as
capitalisation traps but their carriers were reconstructed with lowercase
``ihrer``/``ihren``, which makes them ordinary correct sentences. They sit in
the *visible* 20, so the visible-recall figure has at most two records that no
correct verifier should reject. This script prints the caveat next to the
number rather than silently adjusting it, because the fixture is golden and
correcting it needs the owner's say-so.

## The cache is off by default here

``verify_items`` caches on the full request, and a cached verdict replays for
free. That is right for a bank build and wrong for this: a second run would
report the first run's verdicts at thousands of tokens per second and measure
nothing. ``--use-cache`` exists for the case where you deliberately want to
re-score an already-collected run without re-generating it.

## Usage

    uv run python -m scripts.eval_local_verifier \\
        --model hf.co/ibm-granite/granite-4.2-3b-GGUF:Q5_K_M \\
        --output docs/audits/data/local-verifier-granite-4.2-3b.json

One model per invocation, on purpose: two models resident at once is exactly
the thing the 6 GB budget forbids.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.generation.blanking.model_verification import (
    DEFAULT_VERIFICATION_BATCH_SIZE,
    VerificationReport,
    verify_items,
)
from src.llm.local_client import (
    DEFAULT_NUM_CTX,
    DEFAULT_NUM_PREDICT,
    LocalCallStats,
    LocalLlmClient,
    LocalTransportError,
)

from scripts.eval_verifier import (
    DEFAULT_ADVERSARIAL_PATH,
    DEFAULT_KNOWN_CLEAN_PATH,
    AdversarialRecord,
    _to_bank_item,
    load_adversarial_fixture,
    load_known_clean_fixture,
)

#: The 18 adversarial records whose defect exists only relative to the topic
#: the item was filed under. The verifier is never told the topic (CLAUDE.md
#: rule 2), so it cannot identify these as defects, and a pooled recall figure
#: that includes them is measuring an impossibility. See the module docstring
#: on how this list was derived and why it is written out rather than inlined.
TOPIC_DEPENDENT_DEFECT_CLASSES = frozenset(
    {
        # A reflexive pronoun routed to the wrong case for its topic. The
        # sentence is correct German either way; only the topic makes it wrong.
        "reflexive_case_routing_wrong_direction",
        "reflexive_case_routing_wrong_direction_akk_expected",
        "reflexive_case_routing_wrong_direction_dat_expected",
        # A Nullartikel topic firing on a carrier that does have a determiner.
        "nullartikel_fires_with_determiner_present",
        # Filed as Futur I; the carrier is actually a passive participle.
        "futur_i_matches_passive_participle",
        # Filed under a topic whose grammar the sentence does not exercise.
        "wrong_case_topic_misfiling",
        "wrong_passive_type_topic_misfiling",
        "not_reflexive_misfiling",
        "topic_misfiling_no_comparison_present",
        "topic_misfiling_wrong_konjunktiv_tense",
        "not_a_passive_copula_adjective",
        "wrong_slot_possessive_as_adjective",
    }
)

#: TODO.md 2.7b. These two records do not encode the defect they are filed
#: under, and they sit in the visible subset, so visible recall has at most two
#: records a correct verifier is right to pass.
MISFILED_FIXTURE_IDS = ("c08_04", "c08_05")

#: Gemini on this same instrument, 2026-08-28 (docs/project-state.md).
GEMINI_BASELINE = {
    "recall_pooled": 0.605,
    "recall_visible_caught": 15,
    "recall_visible_total": 20,
    "false_positive_rate": 0.129,
}


def _sample_gpu_used_mib() -> int | None:
    """Current GPU memory in use, or ``None`` if nvidia-smi is unavailable.

    Deliberately total-used rather than per-process: what the 6 GB budget is
    really about is whether the card has room, and a desktop compositor's
    share counts against that just as much as the model's.
    """
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = out.stdout.strip().splitlines()
    return int(line[0].strip()) if line else None


@dataclass
class VramSampler:
    """Polls GPU memory on a background thread for the duration of a run.

    A peak taken before and after would miss it: the model is loaded, used and
    (after the keep-alive window) unloaded inside the run, so the maximum is
    somewhere in the middle.
    """

    interval_s: float = 1.0
    peak_mib: int | None = None
    baseline_mib: int | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def __enter__(self) -> VramSampler:
        self.baseline_mib = _sample_gpu_used_mib()
        self.peak_mib = self.baseline_mib
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            current = _sample_gpu_used_mib()
            if current is not None:
                self.peak_mib = current if self.peak_mib is None else max(self.peak_mib, current)

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def peak_attributable_mib(self) -> int | None:
        """Peak minus what was already in use when the run started."""
        if self.peak_mib is None or self.baseline_mib is None:
            return None
        return max(0, self.peak_mib - self.baseline_mib)


def _ollama_processor(model: str) -> str | None:
    """What ``ollama ps`` says is running the model: GPU, CPU, or a split.

    This is the single most important line of the whole run. A model that
    silently spilled to system RAM still produces recall numbers, and they are
    real, but its speed figure describes a configuration nobody would ship.
    """
    try:
        out = subprocess.run(
            ["ollama", "ps"], capture_output=True, text=True, timeout=15, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.stdout.splitlines()[1:]:
        if line.strip() and model.split(":")[0] in line:
            return " ".join(line.split())
    return None


def _recall(records: list[AdversarialRecord], report: VerificationReport) -> tuple[int, int, int]:
    """``(caught, judged, not_run)`` over ``records``."""
    caught = judged = not_run = 0
    for _record, verdict in zip(records, report.verdicts, strict=True):
        if verdict.outcome == "not_run":
            not_run += 1
            continue
        judged += 1
        if verdict.outcome == "rejected":
            caught += 1
    return caught, judged, not_run


def _visible_recall(
    records: list[AdversarialRecord], report: VerificationReport
) -> tuple[int, int, list[str]]:
    """``(caught, judged, missed_ids)`` over the records whose defect the model
    is actually shown enough to see."""
    caught = judged = 0
    missed: list[str] = []
    for record, verdict in zip(records, report.verdicts, strict=True):
        if record.defect_class in TOPIC_DEPENDENT_DEFECT_CLASSES:
            continue
        if verdict.outcome == "not_run":
            continue
        judged += 1
        if verdict.outcome == "rejected":
            caught += 1
        else:
            missed.append(f"{record.id} ({record.defect_class})")
    return caught, judged, missed


def _fmt_rate(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "COULD NOT BE MEASURED (nothing judged)"
    return f"{numerator / denominator:.1%}  ({numerator}/{denominator})"


def run_eval(
    *,
    model: str,
    batch_size: int,
    num_ctx: int,
    num_predict: int,
    think: bool | str | None,
    probe: bool,
    use_cache: bool,
    adversarial_path: Path,
    clean_path: Path,
) -> dict[str, Any]:
    """Score one local model and return the full result as a plain dict."""
    adversarial = load_adversarial_fixture(adversarial_path)
    clean = load_known_clean_fixture(clean_path)

    adversarial_items = [
        _to_bank_item(id_=r.id, topic_id=r.topic_id, prompt=r.prompt, answer=r.answer, cue=r.cue)
        for r in adversarial
    ]
    clean_items = [
        _to_bank_item(id_=r.id, topic_id=r.topic_id, prompt=r.prompt, answer=r.answer, cue=r.cue)
        for r in clean
    ]

    call_started = time.monotonic()

    def _heartbeat(tokens: int, phase: str) -> None:
        elapsed = time.monotonic() - call_started
        rate = tokens / elapsed if elapsed > 0 else 0.0
        print(
            f"      ... {tokens:5d} tok  {elapsed:6.1f}s  {rate:5.1f} tok/s  ({phase})",
            flush=True,
        )

    def _progress(index: int, call: LocalCallStats) -> None:
        nonlocal call_started
        flag = "  TRUNCATED" if call.truncated else ""
        print(
            f"  [call {index:2d}] {call.wall_seconds:6.1f}s  "
            f"out={call.completion_tokens:5d} tok  "
            f"think={call.thinking_chars:6d} ch  "
            f"{call.tokens_per_second:5.1f} tok/s{flag}",
            flush=True,
        )
        call_started = time.monotonic()

    client = LocalLlmClient(
        model=model,
        num_ctx=num_ctx,
        num_predict=num_predict,
        think=think,
        on_call=_progress,
        on_progress=_heartbeat,
    )

    print(f"Model:      {model}")
    print(f"num_ctx:    {num_ctx}   num_predict: {num_predict}   batch_size: {batch_size}")
    print(
        f"think:      {think}   (None = the model's own default; reasoning is stripped from output)"
    )
    print(f"Cache:      {'ON (re-scoring)' if use_cache else 'OFF (measuring the model)'}")
    print(f"Fixtures:   {len(adversarial)} adversarial, {len(clean)} clean")
    print("Running. The first call also pays the model load.\n")

    if probe:
        # One batch, before committing to the other fourteen. A model that
        # cannot produce a parseable verdict for five items will not produce
        # one for thirty-eight, and finding that out costs an hour at the token
        # caps a reasoning model needs. Granite 4.2 3B burned exactly that:
        # every call returned the full cap and judged nothing, three times, at
        # three different caps.
        print("Pre-flight probe: one batch, to check the model can answer at all.")
        probe_report = verify_items(
            adversarial_items[:batch_size], client, batch_size=batch_size, use_cache=False
        )
        probe_stats = client.stats[-1] if client.stats else None
        judged_probe = probe_report.rejected_count + probe_report.verified_count
        truncated = probe_stats.truncated if probe_stats else False
        out_tokens = probe_stats.completion_tokens if probe_stats else 0
        think_chars = probe_stats.thinking_chars if probe_stats else 0
        print(
            f"  judged {judged_probe}/{min(batch_size, len(adversarial_items))}, "
            f"output {out_tokens} tokens, thinking {think_chars} chars, "
            f"truncated={truncated}"
        )
        if judged_probe == 0:
            print(
                "\nPROBE FAILED: the model judged nothing on its first batch."
                + (
                    "\n  Cause: it hit the token cap without finishing. Either raise"
                    "\n  --num-predict, or conclude the model does not converge on this task."
                    if truncated
                    else "\n  Cause: it finished but produced no parseable verdict."
                )
                + "\n  Skipping the full run. Pass --no-probe to force it anyway."
            )
            return {
                "model": model,
                "probe_failed": True,
                "probe_truncated": truncated,
                "probe_output_tokens": out_tokens,
                "probe_thinking_chars": think_chars,
                "num_predict": num_predict,
                "num_ctx": num_ctx,
                "batch_size": batch_size,
                "think": think,
            }
        print("  Probe passed. Running the full fixtures.\n")
        client.stats.clear()

    started = time.monotonic()
    with VramSampler() as vram:
        adversarial_report = verify_items(
            adversarial_items, client, batch_size=batch_size, use_cache=use_cache
        )
        processor = _ollama_processor(model)
        clean_report = verify_items(clean_items, client, batch_size=batch_size, use_cache=use_cache)
    wall_s = time.monotonic() - started

    caught, judged, not_run = _recall(adversarial, adversarial_report)
    vis_caught, vis_judged, vis_missed = _visible_recall(adversarial, adversarial_report)
    fp = clean_report.rejected_count
    fp_judged = clean_report.rejected_count + clean_report.verified_count
    throughput = client.throughput_report()

    result: dict[str, Any] = {
        "model": model,
        "batch_size": batch_size,
        "num_ctx": num_ctx,
        "num_predict": num_predict,
        "think": think,
        "use_cache": use_cache,
        "recall_pooled": {"caught": caught, "judged": judged, "not_run": not_run},
        "recall_visible": {
            "caught": vis_caught,
            "judged": vis_judged,
            "missed": vis_missed,
            "caveat": (
                f"{', '.join(MISFILED_FIXTURE_IDS)} do not encode the defect they name "
                "(TODO.md 2.7b), so at most two of these are records a correct verifier "
                "is right to pass."
            ),
        },
        "false_positives": {
            "rejected": fp,
            "judged": fp_judged,
            "not_run": clean_report.not_run_count,
        },
        "throughput": throughput,
        "vram": {
            "peak_mib": vram.peak_mib,
            "baseline_mib": vram.baseline_mib,
            "peak_attributable_mib": vram.peak_attributable_mib,
            "ollama_ps": processor,
        },
        "wall_seconds": wall_s,
        "gemini_baseline": GEMINI_BASELINE,
    }

    print("=" * 72)
    print(f"RESULT  {model}")
    print("=" * 72)
    print(f"  recall, pooled over {len(adversarial)}:  {_fmt_rate(caught, judged)}")
    print("    Gemini baseline:               60.5%  (23/38)")
    print(f"  recall, visible defects only:    {_fmt_rate(vis_caught, vis_judged)}")
    print("    Gemini baseline:               75.0%  (15/20)   <-- the comparable one")
    print(f"  false positives on clean:        {_fmt_rate(fp, fp_judged)}")
    print("    Gemini baseline:               12.9%            <-- rank on this first")
    if not_run or clean_report.not_run_count:
        print(f"\n  WARNING: {not_run + clean_report.not_run_count} items were never judged.")
    if vis_missed:
        print(f"\n  Missed, visible defects ({len(vis_missed)}):")
        for miss in vis_missed:
            print(f"    - {miss}")
    print("\n  Speed (first call excluded, it pays the model load):")
    if throughput:
        print(f"    tokens/sec:            {throughput['tokens_per_second']:.1f}")
        print(f"    seconds/call:          {throughput['seconds_per_call']:.1f}")
        print(f"    first-call load:       {throughput['first_call_load_seconds']:.1f}s")
        print(f"    calls:                 {int(throughput['calls_total'])}")
        truncated_calls = int(throughput.get("truncated_calls", 0))
        if truncated_calls:
            print(
                f"    TRUNCATED:             {truncated_calls}"
                f" of {int(throughput['calls_total'])}"
                " calls hit the token cap"
            )
            print(
                "      A truncated call did not finish, so its items are not_run"
                " rather than missed."
            )
            print("      Raise --num-predict before reading any recall figure.")
        print(f"    thinking chars:        {int(throughput.get('thinking_chars', 0))}")
    print("\n  VRAM:")
    print(f"    peak total:            {vram.peak_mib} MiB")
    print(f"    peak above baseline:   {vram.peak_attributable_mib} MiB")
    print(f"    ollama ps:             {processor or 'not captured'}")
    print(f"\n  Wall clock: {wall_s:.0f}s")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure a local model as the verification backstop (TODO.md item 0)."
    )
    parser.add_argument(
        "--model", required=True, help="ollama model tag, e.g. hf.co/org/repo:QUANT"
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_VERIFICATION_BATCH_SIZE)
    parser.add_argument("--num-ctx", type=int, default=DEFAULT_NUM_CTX)
    parser.add_argument("--num-predict", type=int, default=DEFAULT_NUM_PREDICT)
    parser.add_argument(
        "--no-probe",
        dest="probe",
        action="store_false",
        help="Skip the one-batch pre-flight check and run the full fixtures regardless.",
    )
    parser.add_argument(
        "--think",
        choices=("on", "off", "low", "medium", "high"),
        default=None,
        help="Reasoning: on/off, or a level (low/medium/high) that keeps reasoning but "
        "bounds it. Omit to leave the model's own default alone.",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        help="Re-score from cached verdicts instead of generating. Makes the speed figure "
        "meaningless; only for re-reading an already-collected run.",
    )
    parser.add_argument("--adversarial", default=str(DEFAULT_ADVERSARIAL_PATH))
    parser.add_argument("--known-clean", default=str(DEFAULT_KNOWN_CLEAN_PATH))
    parser.add_argument("--output", default=None, help="Write the full result as JSON here.")
    args = parser.parse_args()

    # A level is passed through as a string; on/off stay booleans. Bounding
    # reasoning is not the same as suppressing it: two models measured here
    # spent their entire token budget thinking and never answered at all, which
    # is not a better measurement than a bounded one, it is no measurement.
    if args.think is None:
        think: bool | str | None = None
    elif args.think in ("on", "off"):
        think = args.think == "on"
    else:
        think = args.think

    try:
        result = run_eval(
            model=args.model,
            batch_size=args.batch_size,
            num_ctx=args.num_ctx,
            num_predict=args.num_predict,
            think=think,
            probe=args.probe,
            use_cache=args.use_cache,
            adversarial_path=Path(args.adversarial),
            clean_path=Path(args.known_clean),
        )
    except LocalTransportError as exc:
        print(f"\nTransport failed, so nothing was measured: {exc}", file=sys.stderr)
        return 2

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
