"""Unit tests for the on-device LLM transport.

Every test here injects a fake transport. CLAUDE.md rule 7: a unit test that
makes a real API call is a defect, and that applies to localhost just as much
as to Google -- a test that needs `ollama serve` running is a test that fails
on CI for a reason that has nothing to do with the code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from src.llm.cache import LlmCache
from src.llm.local_client import (
    DEFAULT_NUM_CTX,
    LocalCallStats,
    LocalLlmClient,
    LocalTransportError,
    normalize_verdict_envelope,
    strip_reasoning,
)


class RecordingTransport:
    """A fake ollama /api/chat that records requests and replays canned replies.

    Mirrors the chat endpoint's envelope (``message.content`` plus an optional
    ``message.thinking``) rather than the completion endpoint's flat
    ``response``, because the chat endpoint is the one the client uses: it is
    what applies a model's chat template and so what separates a reasoning
    model's thinking from its answer.
    """

    def __init__(
        self, replies: list[str] | None = None, thinking: str = "", **counters: int
    ) -> None:
        self.replies = replies if replies is not None else ["ok"]
        self.thinking = thinking
        self.calls: list[dict[str, Any]] = []
        self.counters = {
            "prompt_eval_count": 11,
            "eval_count": 7,
            "total_duration": 2_000_000_000,
            "load_duration": 500_000_000,
            "prompt_eval_duration": 200_000_000,
            "eval_duration": 1_000_000_000,
        }
        self.counters.update(counters)

    def __call__(
        self,
        url: str,
        body: dict[str, Any],
        timeout_s: float,
        on_progress: Any = None,
    ) -> dict[str, Any]:
        self.calls.append({"url": url, "body": body, "timeout_s": timeout_s})
        index = min(len(self.calls) - 1, len(self.replies) - 1)
        return {
            "message": {
                "role": "assistant",
                "content": self.replies[index],
                "thinking": self.thinking,
            },
            "done_reason": "stop",
            **self.counters,
        }


def _client(tmp_path: Path, transport: Any, **kwargs: Any) -> LocalLlmClient:
    return LocalLlmClient(
        model="test-model",
        transport=transport,
        cost_log_path=tmp_path / "cost_log.jsonl",
        **kwargs,
    )


def _cost_rows(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "cost_log.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_generate_returns_the_models_text(tmp_path: Path) -> None:
    client = _client(tmp_path, RecordingTransport(["das Ergebnis"]))
    assert client.generate("prompt") == "das Ergebnis"


def test_generate_many_preserves_prompt_order(tmp_path: Path) -> None:
    transport = RecordingTransport(["first", "second", "third"])
    client = _client(tmp_path, transport)

    assert client.generate_many(["a", "b", "c"]) == ["first", "second", "third"]
    sent = [call["body"]["messages"][-1]["content"] for call in transport.calls]
    assert sent == ["a", "b", "c"]


def test_generate_many_dispatches_one_request_at_a_time(tmp_path: Path) -> None:
    """The whole point of the sequential loop: never two KV caches at once."""
    in_flight = 0
    peak = 0

    def counting_transport(
        url: str, body: dict[str, Any], timeout_s: float, on_progress: Any = None
    ) -> dict[str, Any]:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        in_flight -= 1
        return {
            "message": {"content": "ok"},
            "eval_count": 1,
            "eval_duration": 1_000_000_000,
        }

    client = _client(tmp_path, counting_transport)
    client.generate_many([f"p{i}" for i in range(8)])

    assert peak == 1


def test_generate_declares_the_small_context_window(tmp_path: Path) -> None:
    """A large declared window costs VRAM whether or not the tokens are used."""
    transport = RecordingTransport()
    client = _client(tmp_path, transport)
    client.generate("prompt")

    options = transport.calls[0]["body"]["options"]
    assert options["num_ctx"] == DEFAULT_NUM_CTX
    assert options["temperature"] == 0.0


def test_generate_omits_think_when_unset(tmp_path: Path) -> None:
    transport = RecordingTransport()
    _client(tmp_path, transport).generate("prompt")
    assert "think" not in transport.calls[0]["body"]


@pytest.mark.parametrize("think", [True, False])
def test_generate_forwards_think_when_set(tmp_path: Path, think: bool) -> None:
    """TODO.md item 0 compares a reasoning checkpoint against an instruct one,
    so whether reasoning ran must be explicit rather than inferred."""
    transport = RecordingTransport()
    _client(tmp_path, transport, think=think).generate("prompt")
    assert transport.calls[0]["body"]["think"] is think


def test_generate_forwards_the_system_prompt_when_given(tmp_path: Path) -> None:
    transport = RecordingTransport()
    _client(tmp_path, transport).generate("prompt", system_prompt="du bist ein Prüfer")
    messages = transport.calls[0]["body"]["messages"]
    assert messages[0] == {"role": "system", "content": "du bist ein Prüfer"}
    assert messages[-1] == {"role": "user", "content": "prompt"}


def test_generate_cache_hit_skips_the_transport(tmp_path: Path) -> None:
    cache = LlmCache(cache_dir=tmp_path / "llm")
    cache.set(model="test-model", prompt="prompt", response="verdict from cache")
    transport = RecordingTransport(["should not be reached"])
    client = _client(tmp_path, transport, cache=cache)

    assert client.generate("prompt") == "verdict from cache"
    assert transport.calls == []


def test_generate_cache_hit_logs_the_cache_lane(tmp_path: Path) -> None:
    """Hit rate is only measurable if a hit writes a row, per CLAUDE.md 9."""
    cache = LlmCache(cache_dir=tmp_path / "llm")
    cache.set(model="test-model", prompt="prompt", response="cached")
    client = _client(tmp_path, RecordingTransport(), cache=cache)
    client.generate("prompt")

    rows = _cost_rows(tmp_path)
    assert [row["lane"] for row in rows] == ["cache"]
    assert rows[0]["cost_usd"] == 0.0


def test_generate_use_cache_false_bypasses_a_stored_verdict(tmp_path: Path) -> None:
    """A second verification pass asks for a second sample, not a replay."""
    cache = LlmCache(cache_dir=tmp_path / "llm")
    cache.set(model="test-model", prompt="prompt", response="stale")
    transport = RecordingTransport(["fresh"])
    client = _client(tmp_path, transport, cache=cache)

    assert client.generate("prompt", use_cache=False) == "fresh"
    assert len(transport.calls) == 1


def test_generate_writes_a_zero_cost_row_on_the_local_lane(tmp_path: Path) -> None:
    """CLAUDE.md rule 4 is about visibility: a run verified locally must not
    read as a run that did no verification."""
    client = _client(tmp_path, RecordingTransport())
    client.generate("prompt", purpose="item_verification")

    rows = _cost_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["lane"] == "local"
    assert rows[0]["mode"] == "local"
    assert rows[0]["cost_usd"] == 0.0
    assert rows[0]["purpose"] == "item_verification"
    assert rows[0]["prompt_tokens"] == 11
    assert rows[0]["completion_tokens"] == 7


def test_generate_records_stats_for_every_call(tmp_path: Path) -> None:
    client = _client(tmp_path, RecordingTransport())
    client.generate_many(["a", "b"])

    assert len(client.stats) == 2
    assert client.stats[0].completion_tokens == 7


def test_tokens_per_second_divides_output_tokens_by_eval_time() -> None:
    stats = LocalCallStats(
        model="m",
        prompt_tokens=10,
        completion_tokens=50,
        total_duration_ns=3_000_000_000,
        load_duration_ns=1_000_000_000,
        prompt_eval_duration_ns=500_000_000,
        eval_duration_ns=2_000_000_000,
    )
    assert stats.tokens_per_second == 25.0
    assert stats.wall_seconds == 3.0


def test_tokens_per_second_is_zero_when_nothing_was_generated() -> None:
    """Diagnostic figures must not raise inside a summary."""
    stats = LocalCallStats(
        model="m",
        prompt_tokens=10,
        completion_tokens=0,
        total_duration_ns=1,
        load_duration_ns=0,
        prompt_eval_duration_ns=0,
        eval_duration_ns=0,
    )
    assert stats.tokens_per_second == 0.0


def test_throughput_report_excludes_the_model_load_run(tmp_path: Path) -> None:
    """TODO.md item 0 asks for speed with the initial run excluded, and for the
    load cost to be reported rather than merely dropped."""
    client = _client(tmp_path, RecordingTransport())
    client.generate_many(["a", "b", "c"])

    report = client.throughput_report()
    assert report["calls_total"] == 3.0
    assert report["calls_measured"] == 2.0
    assert report["first_call_load_seconds"] == 0.5
    assert report["completion_tokens"] == 14.0
    assert report["tokens_per_second"] == 7.0


def test_throughput_report_keeps_the_only_call_when_there_is_just_one(tmp_path: Path) -> None:
    client = _client(tmp_path, RecordingTransport())
    client.generate("a")
    assert client.throughput_report()["calls_measured"] == 1.0


def test_throughput_report_is_empty_before_any_call(tmp_path: Path) -> None:
    assert _client(tmp_path, RecordingTransport()).throughput_report() == {}


def test_generate_propagates_a_transport_failure(tmp_path: Path) -> None:
    """A runtime that is not up must not be reported as a model verdict."""

    def failing(
        url: str, body: dict[str, Any], timeout_s: float, on_progress: Any = None
    ) -> dict[str, Any]:
        raise LocalTransportError("could not reach ollama")

    with pytest.raises(LocalTransportError):
        _client(tmp_path, failing).generate("prompt")


def test_generate_many_absorbs_the_gemini_only_keyword_arguments(tmp_path: Path) -> None:
    """So the verification pass can be handed either client unchanged."""
    client = _client(tmp_path, RecordingTransport(["a", "b"]))
    result = client.generate_many(
        ["p1", "p2"], model="gemini-3.7-flash", is_user_content=False, force_lane="free"
    )
    assert result == ["a", "b"]


def test_generate_ignores_a_hosted_model_id_from_the_caller(tmp_path: Path) -> None:
    """A local client serves the model it was configured with. A call site
    written for Gemini passes ``model=MODEL_VERIFY``; honouring that silently
    would send a hosted model id to ollama and 404."""
    transport = RecordingTransport()
    client = _client(tmp_path, transport)
    client.generate("prompt", model="gemini-3.7-flash")
    assert transport.calls[0]["body"]["model"] == "test-model"


def test_generate_honours_an_explicit_local_model_override(tmp_path: Path) -> None:
    transport = RecordingTransport()
    client = _client(tmp_path, transport)
    client.generate("prompt", model="hf.co/unsloth/Qwen3.5-9B-GGUF:IQ4_XS")
    assert transport.calls[0]["body"]["model"] == "hf.co/unsloth/Qwen3.5-9B-GGUF:IQ4_XS"


# ---------------------------------------------------------------------------
# Reasoning removal. The shapes below are the ones the candidate models were
# actually observed producing, not hypothetical ones.


def test_strip_reasoning_removes_a_tagged_block() -> None:
    text = '<think>Ich überlege noch.</think>\n{"verdicts": []}'
    assert strip_reasoning(text) == '{"verdicts": []}'


@pytest.mark.parametrize("tag", ["think", "thinking", "reasoning", "SCRATCHPAD"])
def test_strip_reasoning_removes_every_supported_tag(tag: str) -> None:
    text = f'<{tag}>hmm</{tag}> {{"verdicts": []}}'
    assert strip_reasoning(text) == '{"verdicts": []}'


def test_strip_reasoning_drops_an_unclosed_block_entirely() -> None:
    """Thinking that hit the token cap has no answer behind it to keep."""
    assert strip_reasoning("<think>I ran out of budget mid-thought") == ""


def test_strip_reasoning_unwraps_a_json_code_fence() -> None:
    assert strip_reasoning('```json\n{"verdicts": []}\n```') == '{"verdicts": []}'


def test_strip_reasoning_extracts_json_after_untagged_prose() -> None:
    """Granite 4.2's real shape: English prose, no markers, then the answer."""
    text = (
        "We need to evaluate each task individually. The tasks are numbered 1-5.\n"
        'For each we must answer four questions.\n{"verdicts": [{"index": 1}]}'
    )
    assert strip_reasoning(text) == '{"verdicts": [{"index": 1}]}'


def test_strip_reasoning_keeps_the_last_json_not_a_mid_thought_sketch() -> None:
    text = 'Maybe the shape is {"verdicts": []} but actually {"verdicts": [{"index": 2}]}'
    assert strip_reasoning(text) == '{"verdicts": [{"index": 2}]}'


def test_strip_reasoning_is_not_confused_by_braces_inside_german_text() -> None:
    payload = '{"verdicts": [{"reason": "Der Satz \\"Er kommt}\\" ist gut"}]}'
    assert strip_reasoning(f"Ich denke nach. {payload}") == payload


def test_strip_reasoning_returns_the_text_when_no_json_survives() -> None:
    """Never fabricate. A model that did not answer must not read as a model
    that approved everything."""
    assert strip_reasoning("I could not decide.") == "I could not decide."


def test_strip_reasoning_passes_through_bare_json_untouched() -> None:
    assert strip_reasoning('{"verdicts": []}') == '{"verdicts": []}'


def test_generate_strips_reasoning_by_default(tmp_path: Path) -> None:
    transport = RecordingTransport(['<think>hmm</think>{"verdicts": []}'])
    assert _client(tmp_path, transport).generate("p") == '{"verdicts": []}'


def test_generate_can_return_the_raw_reply_when_stripping_is_off(tmp_path: Path) -> None:
    raw = '<think>hmm</think>{"verdicts": []}'
    client = _client(tmp_path, RecordingTransport([raw]), strip_reasoning=False)
    assert client.generate("p") == raw


def test_generate_caches_the_stripped_reply_not_the_raw_one(tmp_path: Path) -> None:
    """The useful artefact is the payload; a cached run must not have to strip
    again, and must not replay a thought."""
    cache = LlmCache(cache_dir=tmp_path / "llm")
    client = _client(
        tmp_path, RecordingTransport(['<think>hmm</think>{"verdicts": []}']), cache=cache
    )
    client.generate("p")
    assert cache.get(model="test-model", prompt="p") == '{"verdicts": []}'


def test_generate_posts_to_the_chat_endpoint_not_the_completion_one(tmp_path: Path) -> None:
    """The chat endpoint applies the model's chat template, which is what makes
    a reasoning model separate its thinking instead of inlining it as prose."""
    transport = RecordingTransport()
    _client(tmp_path, transport).generate("prompt")
    assert transport.calls[0]["url"].endswith("/api/chat")


def test_generate_ignores_separated_thinking_and_keeps_the_content(tmp_path: Path) -> None:
    transport = RecordingTransport(['{"verdicts": []}'], thinking="a long deliberation")
    client = _client(tmp_path, transport)
    assert client.generate("p") == '{"verdicts": []}'
    assert client.stats[0].thinking_chars == len("a long deliberation")


def test_stats_flag_a_call_the_token_cap_ended(tmp_path: Path) -> None:
    """A truncated call did not finish, so its silence is not a verdict."""
    transport = RecordingTransport([""])
    transport.counters["done_reason"] = "length"  # type: ignore[assignment]
    client = _client(tmp_path, transport)
    client.generate("p")
    assert client.stats[0].truncated is True
    assert client.throughput_report(skip_first=False)["truncated_calls"] == 1.0


def test_stats_do_not_flag_a_call_the_model_ended(tmp_path: Path) -> None:
    client = _client(tmp_path, RecordingTransport())
    client.generate("p")
    assert client.stats[0].truncated is False


def test_http_transport_reassembles_a_streamed_reply(tmp_path: Path) -> None:
    """Streaming must produce the same envelope a single reply would, so the
    only observable difference is that progress is visible while it happens."""
    chunks = [
        {"message": {"thinking": "erst "}},
        {"message": {"thinking": "denken"}},
        {"message": {"content": '{"verdicts": '}},
        {"message": {"content": "[]}"}, "done": True, "done_reason": "stop", "eval_count": 4},
    ]
    body = {"stream": True}
    seen: list[tuple[int, str]] = []

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def __iter__(self) -> Any:
            return iter([(json.dumps(c) + "\n").encode("utf-8") for c in chunks])

    import src.llm.local_client as mod

    original = mod.urllib.request.urlopen
    mod.urllib.request.urlopen = lambda *a, **k: FakeResponse()  # type: ignore[assignment]
    try:
        result = mod._http_transport(
            "http://x/api/chat", body, 5.0, lambda n, p: seen.append((n, p))
        )
    finally:
        mod.urllib.request.urlopen = original  # type: ignore[assignment]

    assert result["message"]["content"] == '{"verdicts": []}'
    assert result["message"]["thinking"] == "erst denken"
    assert result["done_reason"] == "stop"


def test_generate_streams_by_default(tmp_path: Path) -> None:
    transport = RecordingTransport()
    _client(tmp_path, transport).generate("p")
    assert transport.calls[0]["body"]["stream"] is True


def test_normalize_envelope_wraps_a_bare_verdict_array() -> None:
    """Ministral 3 14B answers with the array alone; the parser wants an object."""
    out = normalize_verdict_envelope('[{"index": 1, "valid": true}]')
    assert json.loads(out) == {"verdicts": [{"index": 1, "valid": True}]}


def test_normalize_envelope_leaves_a_proper_object_alone() -> None:
    payload = '{"verdicts": [{"index": 1}]}'
    assert normalize_verdict_envelope(payload) == payload


def test_normalize_envelope_leaves_unparseable_text_alone() -> None:
    assert normalize_verdict_envelope("[not json") == "[not json"


def test_generate_normalizes_a_bare_array(tmp_path: Path) -> None:
    transport = RecordingTransport(['```json\n[{"index": 1, "valid": true}]\n```'])
    result = _client(tmp_path, transport).generate("p")
    assert json.loads(result) == {"verdicts": [{"index": 1, "valid": True}]}
