"""On-device LLM transport, so the verification pass can run on a model that
costs nothing, has no quota and never returns 503.

## Why this exists

The model-backed verification pass (``src/generation/blanking/
model_verification.py``) is the one place in this pipeline where a model's
judgement is worth buying, and it is also the place where the hosted lane
hurts most. Three measured facts, all in ``docs/project-state.md``:

- Its recall is 60.5% pooled, 15 of 20 on the records whose defect is visible
  in what the model is actually shown, and its false-positive rate is 12.9%.
  That last number throws away roughly one good candidate in eight, silently.
- ``MODEL_VERIFY`` gets roughly 25 to 36 requests a day on Google's free tier
  and most come back 503, so verifying a 1,225-item bank (245 requests at one
  pass, 490 at two) cannot finish in one sitting on the free lane.
- The paid route costs about $3.28 against a $7.50 monthly ceiling.

A local model removes all three constraints at once: no daily quota, no 503,
no spend ceiling, and no question about whether user text may be sent to a
provider that trains on it. What it costs instead is latency and quality, and
this module exists so both can be measured rather than assumed. **This pass is
off the learner's critical path by design (CLAUDE.md rule 3)**, so seconds per
item is an acceptable price where it would not be for explanations or
production grading.

## What this is not

It is not a general replacement for ``GeminiLlmClient``. It implements the one
interface the verification pass actually calls (``generate_many``, plus the
optional ``cache`` attribute that pass probes for a resumability estimate),
and nothing else. There is no batch lane, no free-to-paid failover, no spend
ceiling and no retry-on-quota, because none of those concepts exist on a model
running on the same machine.

## CLAUDE.md rule 4, and why this does not breach it

Rule 4 says every LLM call goes through ``src/llm/client.py`` because the
wrapper "handles retries, token accounting, cost logging, and the spend
ceiling. A call that bypasses it is invisible to the budget." A local call
cannot spend money, so the budget half is moot -- but the *visibility* half is
not, and ``client.append_cost_row`` exists precisely so "a non-Gemini provider
can append to the SAME file without owning a client" (its own docstring, added
when ``AzureTranslator`` turned out to be writing no rows at all).

So every call here writes a ``CostLogRow`` with ``lane="local"``,
``mode="local"`` and ``cost_usd=0.0``. Zero cost is the true figure, not a
placeholder. The point is that a run whose verification happened entirely on a
local model must not read, in the log, as a run that did no verification.

## One request at a time, and a deliberately small context

``generate_many`` dispatches **sequentially**, not concurrently, which is the
opposite of what ``GeminiLlmClient.generate_many`` does. The reason is that
concurrency buys throughput only when the bottleneck is network round-trip
latency. Here the bottleneck is a single GPU: two concurrent requests share
one set of weights and one compute unit, so they interleave rather than
overlap, and each one's KV cache is resident at the same time. On an 8 GB
laptop card that is how a run that fits becomes a run that spills to system
RAM and slows by an order of magnitude.

Because there is only ever one request in flight, ``num_ctx`` can be set to
just above the longest prompt this pass builds rather than to the model's
advertised maximum. That matters more than it sounds: ollama allocates the KV
cache for the *declared* context window whether or not the tokens are used, so
a default 32k window on a 12B model can cost more VRAM than the quantisation
saved. ``DEFAULT_NUM_CTX`` is sized for a 20-item verification batch with
headroom, and ``VerificationRunner`` in ``scripts/eval_local_verifier.py``
raises it only if a prompt actually measures longer.

## Timing, and the load run

``LocalCallStats`` records ``eval_count`` and ``eval_duration_ns`` per call,
which is what a tokens-per-second figure has to be computed from. ollama
reports ``load_duration_ns`` separately, so the cost of paging a model into
VRAM is *identified* rather than merely excluded by convention: the eval still
discards each model's first call, but it can also show what that first call
actually paid, which is the number that decides whether a local verifier is
usable interactively or only in a batch job.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.llm.cache import LlmCache
from src.llm.client import CostLogRow, append_cost_row

#: Where ollama listens unless told otherwise.
DEFAULT_BASE_URL = "http://localhost:11434"

#: Context window, in tokens, declared per request. Sized for a 20-item
#: verification batch (the pass's ``DEFAULT_VERIFICATION_BATCH_SIZE``) with
#: roughly 2x headroom, NOT for the model's advertised maximum -- see the
#: module docstring on why a large declared window costs real VRAM even when
#: the tokens go unused.
DEFAULT_NUM_CTX = 8192

#: Hard cap on generated tokens. Raised from 2048 after Granite 4.2 3B was
#: measured spending the entire 2048 on prose reasoning and never reaching the
#: JSON: a cap set below what a reasoning model needs does not measure the
#: model, it measures the cap. The cap still exists because the same model at
#: 8192 was observed in a repetition loop ("but maybe X is okay;" repeated to
#: the limit), so an uncapped run would hang rather than answer.
DEFAULT_NUM_PREDICT = 4096

#: Greedy decoding. A verifier that returns a different verdict on a re-run is
#: unmeasurable, and this project already carries one instability it cannot
#: explain (``pass_disagreements``, TODO.md item 1). Sampling would add a
#: second.
DEFAULT_TEMPERATURE = 0.0

#: Read timeout per request, in seconds. Generous because a 14B model at 2-bit
#: on a laptop GPU genuinely can take minutes on a 20-item batch, and because
#: the first call of a run additionally pays the model load.
DEFAULT_TIMEOUT_S = 900.0


#: Tag pairs a reasoning model may wrap its thinking in. Matched
#: case-insensitively and non-greedily, opening tag through closing tag.
REASONING_TAG_NAMES = ("think", "thinking", "reason", "reasoning", "scratchpad")

_REASONING_BLOCK_RE = re.compile(
    r"<\s*(" + "|".join(REASONING_TAG_NAMES) + r")\s*>.*?<\s*/\s*\1\s*>",
    re.DOTALL | re.IGNORECASE,
)

#: An unclosed opening tag, i.e. thinking that ran into the token cap. Everything
#: from the tag onward is thought that never finished, so there is no answer
#: after it to keep.
_UNCLOSED_REASONING_RE = re.compile(
    r"<\s*(" + "|".join(REASONING_TAG_NAMES) + r")\s*>.*\Z",
    re.DOTALL | re.IGNORECASE,
)


def _last_balanced_json(text: str) -> str | None:
    """The last complete JSON object or array in ``text``, or ``None``.

    Scans from each closing brace backwards for its matching opener, respecting
    string literals and escapes so a ``}`` inside a German sentence (or an
    escaped quote) does not end the scan early. Returns the LAST such value
    rather than the first, because a reasoning model that narrates its way to an
    answer frequently sketches a partial structure mid-thought and emits the
    real one at the end.
    """
    for end in range(len(text) - 1, -1, -1):
        closer = text[end]
        if closer not in "}]":
            continue
        opener = "{" if closer == "}" else "["
        depth = 0
        in_string = False
        escaped = False
        for start in range(end, -1, -1):
            char = text[start]
            if escaped:
                escaped = False
                continue
            # Walking backwards, a quote is escaped if preceded by an odd
            # number of backslashes; count them rather than peeking at one.
            if char == '"':
                backslashes = 0
                probe = start - 1
                while probe >= 0 and text[probe] == "\\":
                    backslashes += 1
                    probe -= 1
                if backslashes % 2 == 0:
                    in_string = not in_string
                continue
            if in_string:
                continue
            if char == closer:
                depth += 1
            elif char == opener:
                depth -= 1
                if depth == 0:
                    candidate = text[start : end + 1]
                    try:
                        json.loads(candidate)
                    except json.JSONDecodeError:
                        break
                    return candidate
    return None


def strip_reasoning(text: str) -> str:
    """Return ``text`` with a reasoning model's thinking removed.

    Three shapes, because three are what the candidate models actually produce:

    1. **Tagged.** ``<think>...</think>`` and friends, which are simply cut. An
       *unclosed* opening tag means thinking hit the token cap, so everything
       from it onward goes too; there is no answer behind it.
    2. **Fenced.** A `````json`` block, unwrapped.
    3. **Untagged prose, then the answer.** Granite 4.2 emits its reasoning as
       plain English prose with no markers at all and ollama's separate
       ``thinking`` field empty, so there is nothing to match on. The only
       reliable handle is the payload itself: take the last complete JSON value
       in the text.

    **Never fabricates.** If nothing JSON-shaped survives, the cleaned text is
    returned as-is so the caller's own parser records an honest parse failure.
    Manufacturing an empty verdict list here would turn "the model did not
    answer" into "the model approved everything", which is the worst possible
    direction for a verifier to fail in.
    """
    cleaned = _REASONING_BLOCK_RE.sub("", text)
    cleaned = _UNCLOSED_REASONING_RE.sub("", cleaned)
    cleaned = cleaned.strip()

    fenced = cleaned
    if fenced.startswith("```"):
        fenced = re.sub(r"^```[a-zA-Z]*\s*", "", fenced)
        fenced = re.sub(r"\s*```\s*\Z", "", fenced)
        fenced = fenced.strip()

    try:
        json.loads(fenced)
    except json.JSONDecodeError:
        pass
    else:
        return fenced

    payload = _last_balanced_json(fenced)
    return payload if payload is not None else fenced


def normalize_verdict_envelope(text: str) -> str:
    """Wrap a bare top-level JSON array into the ``{"verdicts": [...]}`` object
    the verification pass expects.

    ``model_verification._parse_batch_response`` requires an object with a
    ``"verdicts"`` list, and is deliberately strict because failing open on a
    parse defect is the exact failure this pipeline exists to avoid. Some
    models answer with the array alone: measured on Ministral 3 14B, which
    returns a correct, complete, correctly-keyed list of verdicts wrapped in
    nothing at all.

    Rejecting that would score the envelope rather than the judgement, so the
    array is rewrapped here, in the transport, where a per-runtime quirk
    belongs. The strict parser is left exactly as it is, and the hosted lane is
    untouched. **Only the wrapper is changed**: every verdict, key and value is
    passed through as the model wrote it, so a malformed or incomplete list
    still fails the parser as it should.

    Anything that is not a bare top-level array is returned unchanged.
    """
    stripped = text.strip()
    if not stripped.startswith("["):
        return text
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return text
    if not isinstance(parsed, list):
        return text
    return json.dumps({"verdicts": parsed}, ensure_ascii=False)


class LocalTransportError(RuntimeError):
    """A local model call failed at the transport, not at the model.

    Kept distinct from a model that answered badly: a connection refused
    (ollama not running), a 404 (model not pulled) and a read timeout are
    operator problems with an obvious fix, and reporting them as a verdict
    would silently turn "the runtime was not up" into "the model rejected
    every item".
    """


@dataclass(frozen=True)
class LocalCallStats:
    """What one local call actually cost in time and tokens.

    ``load_duration_ns`` is reported separately by ollama and is the cost of
    paging weights into VRAM. It is nonzero on the first call against a model
    (and again after the keep-alive window expires) and near zero afterwards,
    which is exactly the distinction a tokens-per-second figure has to make.
    """

    model: str
    prompt_tokens: int
    completion_tokens: int
    total_duration_ns: int
    load_duration_ns: int
    prompt_eval_duration_ns: int
    eval_duration_ns: int
    #: ollama's own reason the generation stopped. ``"length"`` means the token
    #: cap was hit, which is a categorically different failure from a wrong
    #: answer: the model did not finish, so its silence is not a verdict. A run
    #: that truncates must say so rather than scoring the truncation as a miss.
    done_reason: str = ""
    #: Characters of separated reasoning, from ``message.thinking``. Recorded
    #: because on a reasoning model this is where the token budget actually
    #: goes, and a model that thinks past its cap never reaches an answer.
    thinking_chars: int = 0

    @property
    def truncated(self) -> bool:
        """Whether the token cap, rather than the model, ended this call."""
        return self.done_reason == "length"

    @property
    def tokens_per_second(self) -> float:
        """Generation throughput, excluding load and prompt evaluation.

        Returns 0.0 rather than raising when a call generated nothing or
        reported no duration: this figure is diagnostic, and a run should not
        die computing its own summary.
        """
        if self.eval_duration_ns <= 0 or self.completion_tokens <= 0:
            return 0.0
        return self.completion_tokens / (self.eval_duration_ns / 1e9)

    @property
    def wall_seconds(self) -> float:
        """Total time for the call, load included."""
        return self.total_duration_ns / 1e9


#: Called during a streaming generation with ``(tokens_so_far, phase)``, where
#: phase is ``"thinking"`` or ``"content"``. Exists so a long call is not a
#: blank screen: without it a twenty-minute generation is indistinguishable
#: from a hung one, which is exactly how an hour got wasted on a model that was
#: never going to answer.
ProgressCallback = Callable[[int, str], None]

#: A transport takes a fully-formed request body and an optional progress
#: callback, and returns ollama's decoded JSON response. Injected so unit tests
#: can exercise every path in this module without a network or a GPU (CLAUDE.md
#: section 8: functions that touch the network take it as an injected
#: dependency).
Transport = Callable[[str, dict[str, Any], float, ProgressCallback | None], dict[str, Any]]

#: Emit a heartbeat every this many tokens while streaming.
PROGRESS_EVERY_TOKENS = 100


def _http_transport(
    url: str,
    body: dict[str, Any],
    timeout_s: float,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Post ``body`` to ``url`` and decode the reply.

    The only place in this module that touches the network. When
    ``body["stream"]`` is true the reply is newline-delimited JSON: each chunk
    carries a token or two, and the final one carries the counters. They are
    reassembled here into the same envelope a non-streaming call returns, so
    the caller sees no difference beyond the progress callback firing.
    """
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    streaming = bool(body.get("stream"))
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            if not streaming:
                decoded: dict[str, Any] = json.loads(response.read().decode("utf-8"))
                return decoded

            content: list[str] = []
            thinking: list[str] = []
            final: dict[str, Any] = {}
            tokens = 0
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                chunk = json.loads(line)
                message = chunk.get("message") or {}
                piece = str(message.get("content", "") or "")
                thought = str(message.get("thinking", "") or "")
                if piece:
                    content.append(piece)
                if thought:
                    thinking.append(thought)
                if piece or thought:
                    tokens += 1
                    if on_progress is not None and tokens % PROGRESS_EVERY_TOKENS == 0:
                        on_progress(tokens, "content" if piece else "thinking")
                if chunk.get("done"):
                    final = chunk
            final["message"] = {
                "role": "assistant",
                "content": "".join(content),
                "thinking": "".join(thinking),
            }
            return final
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise LocalTransportError(f"ollama returned HTTP {exc.code} for {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise LocalTransportError(
            f"could not reach ollama at {url} ({exc.reason}). Is `ollama serve` running?"
        ) from exc
    except TimeoutError as exc:
        raise LocalTransportError(
            f"ollama did not answer within {timeout_s:.0f}s for {url}"
        ) from exc


@dataclass
class LocalLlmClient:
    """An ollama-backed client exposing the slice of ``GeminiLlmClient`` that
    the verification pass calls.

    Duck-typed rather than inheriting: ``GeminiLlmClient`` is built around two
    lanes, a spend ceiling, a batch API and quota classification, none of which
    have a meaning on a model running on this machine, so inheriting would mean
    stubbing out most of the parent. What the verification pass actually needs
    is ``generate_many`` and an optional ``cache`` attribute, and that is what
    this provides.
    """

    model: str
    base_url: str = DEFAULT_BASE_URL
    num_ctx: int = DEFAULT_NUM_CTX
    num_predict: int = DEFAULT_NUM_PREDICT
    temperature: float = DEFAULT_TEMPERATURE
    timeout_s: float = DEFAULT_TIMEOUT_S
    #: ``None`` leaves the model's own default alone, which is the right
    #: default: suppressing a reasoning model's reasoning changes what is being
    #: measured, so this eval lets models think and removes the thinking from
    #: the *output* instead (``strip_reasoning``). Kept as an explicit knob
    #: because TODO.md item 0 compares a reasoning checkpoint against an
    #: instruct one and "did reasoning run" must not be a guess -- but note it
    #: is inert on a model like Granite 4.2, which reasons in untagged prose
    #: regardless of this flag.
    think: bool | str | None = None
    #: Remove a reasoning model's thinking from the reply before returning it.
    #: On by default: the verification pass parses JSON, and every candidate
    #: model that thinks emits prose around it. See ``strip_reasoning``.
    strip_reasoning: bool = True
    #: Rewrap a bare top-level JSON array as ``{"verdicts": [...]}``. See
    #: ``normalize_verdict_envelope``: this changes the wrapper only, never a
    #: verdict, and exists so a model is scored on its judgement rather than on
    #: which of two equivalent shapes it chose.
    normalize_envelope: bool = True
    #: Shared with ``GeminiLlmClient`` on purpose: a cached verdict is a
    #: verdict, whichever transport produced it, and the verification pass
    #: probes this attribute to estimate how much of a run is already done.
    cache: LlmCache | None = None
    cost_log_path: Path | str = ".cache/cost_log.jsonl"
    transport: Transport = _http_transport
    #: Every call this client has made, in order, for the throughput report.
    stats: list[LocalCallStats] = field(default_factory=list)
    #: Called after every completed call with that call's stats and the index
    #: (1-based) of the call. A long local run is otherwise entirely silent for
    #: an hour, which makes a hung model indistinguishable from a slow one.
    on_call: Callable[[int, LocalCallStats], None] | None = None
    #: Stream the reply so progress is observable while it is being generated.
    stream: bool = True
    #: Fired every ``PROGRESS_EVERY_TOKENS`` tokens during a streaming call.
    on_progress: ProgressCallback | None = None

    def generate(
        self,
        prompt: str,
        model: str | None = None,
        purpose: str = "generation",
        use_cache: bool = True,
        system_prompt: str | None = None,
    ) -> str:
        """Run one prompt and return the model's text.

        ``model`` is accepted and ignored beyond overriding ``self.model``, so
        that call sites written against ``GeminiLlmClient`` (which pass
        ``model=MODEL_VERIFY``) work unchanged. A local client is configured
        with the one model it serves; the caller does not get to pick a hosted
        model id and have it silently honoured.
        """
        target = model if model is not None and model.startswith("hf.co/") else self.model

        if use_cache and self.cache is not None:
            cached = self.cache.get(model=target, prompt=prompt)
            if cached is not None:
                self._log(target, purpose, prompt_tokens=0, completion_tokens=0, lane="cache")
                return cached

        messages: list[dict[str, str]] = []
        if system_prompt is not None:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        body: dict[str, Any] = {
            "model": target,
            "messages": messages,
            "stream": self.stream,
            "options": {
                "num_ctx": self.num_ctx,
                "num_predict": self.num_predict,
                "temperature": self.temperature,
            },
        }
        if self.think is not None:
            body["think"] = self.think

        started = time.monotonic()
        # ``/api/chat``, never ``/api/generate``. The chat endpoint applies the
        # model's own chat template, which is what makes a reasoning model put
        # its thinking in ``message.thinking`` instead of inlining it as
        # untagged prose in the reply. Measured on Granite 4.2: through
        # ``/api/generate`` the reasoning arrives as unmarked English prose with
        # ``thinking`` empty and no JSON anywhere; through ``/api/chat`` the
        # same model separates 15k characters of thought cleanly. Using the
        # completion endpoint would have scored every reasoning model on a
        # prompt format it was never trained for.
        response = self.transport(
            f"{self.base_url}/api/chat", body, self.timeout_s, self.on_progress
        )
        elapsed_ns = int((time.monotonic() - started) * 1e9)

        # ollama puts separated reasoning in ``message.thinking`` and leaves
        # ``message.content`` clean. ``strip_reasoning`` still runs over the
        # content, because a model may additionally inline ``<think>`` tags or
        # wrap its JSON in a fence, and neither is the chat template's job.
        message = response.get("message") or {}
        raw_text = str(message.get("content", "") or "")
        thinking = str(message.get("thinking", "") or "")
        text = strip_reasoning(raw_text) if self.strip_reasoning else raw_text
        if self.normalize_envelope:
            text = normalize_verdict_envelope(text)
        stats = LocalCallStats(
            model=target,
            prompt_tokens=int(response.get("prompt_eval_count", 0) or 0),
            completion_tokens=int(response.get("eval_count", 0) or 0),
            # ollama reports its own durations; fall back to the wall clock we
            # measured rather than to zero, so a runtime that omits them still
            # produces a usable, if coarser, throughput figure.
            total_duration_ns=int(response.get("total_duration", 0) or elapsed_ns),
            load_duration_ns=int(response.get("load_duration", 0) or 0),
            prompt_eval_duration_ns=int(response.get("prompt_eval_duration", 0) or 0),
            eval_duration_ns=int(response.get("eval_duration", 0) or 0),
            done_reason=str(response.get("done_reason", "") or ""),
            thinking_chars=len(thinking),
        )
        self.stats.append(stats)
        if self.on_call is not None:
            self.on_call(len(self.stats), stats)
        self._log(
            target,
            purpose,
            prompt_tokens=stats.prompt_tokens,
            completion_tokens=stats.completion_tokens,
            lane="local",
        )

        if use_cache and self.cache is not None:
            self.cache.set(model=target, prompt=prompt, response=text)
        return text

    def generate_many(
        self,
        prompts: list[str],
        model: str | None = None,
        purpose: str = "generation",
        use_cache: bool = True,
        **_ignored: Any,
    ) -> list[str]:
        """Run every prompt in order and return the replies in that order.

        Sequential on purpose. See the module docstring: on one GPU,
        concurrency does not overlap work, it just makes two KV caches resident
        at once, which is how a run that fits stops fitting.

        ``**_ignored`` absorbs the keyword arguments ``GeminiLlmClient``
        accepts and this transport has no meaning for (``is_user_content``,
        ``now``, ``force_lane``). Swallowing them is deliberate so that the
        verification pass can be handed either client unchanged; every one of
        them concerns lane routing or billing, and there is neither here.
        """
        return [
            self.generate(prompt, model=model, purpose=purpose, use_cache=use_cache)
            for prompt in prompts
        ]

    def _log(
        self,
        model: str,
        purpose: str,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        lane: str,
    ) -> None:
        """Write one audit row. Cost is genuinely zero, not a placeholder."""
        append_cost_row(
            CostLogRow(
                model=model,
                lane="cache" if lane == "cache" else "local",
                mode="cache" if lane == "cache" else "local",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=0.0,
                purpose=purpose,
            ),
            self.cost_log_path,
        )

    def throughput_report(self, *, skip_first: bool = True) -> dict[str, float]:
        """Summarise this client's calls.

        ``skip_first`` drops the first recorded call, which is the one that
        paid the model load. TODO.md item 0 asks for the speed figure with the
        initial run excluded; the load cost itself is preserved separately in
        ``first_call_load_seconds`` rather than discarded, because whether a
        local verifier is usable at all partly turns on it.
        """
        if not self.stats:
            return {}
        first_load_s = self.stats[0].load_duration_ns / 1e9
        measured = self.stats[1:] if skip_first and len(self.stats) > 1 else self.stats
        eval_ns = sum(s.eval_duration_ns for s in measured)
        out_tokens = sum(s.completion_tokens for s in measured)
        return {
            "calls_total": float(len(self.stats)),
            "calls_measured": float(len(measured)),
            "first_call_load_seconds": first_load_s,
            "prompt_tokens": float(sum(s.prompt_tokens for s in measured)),
            "completion_tokens": float(out_tokens),
            "tokens_per_second": (out_tokens / (eval_ns / 1e9)) if eval_ns > 0 else 0.0,
            "truncated_calls": float(sum(1 for s in self.stats if s.truncated)),
            "thinking_chars": float(sum(s.thinking_chars for s in measured)),
            "wall_seconds": sum(s.wall_seconds for s in measured),
            "seconds_per_call": (
                sum(s.wall_seconds for s in measured) / len(measured) if measured else 0.0
            ),
        }
