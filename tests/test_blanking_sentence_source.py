"""Tests for src/generation/blanking/sentence_source.py.

Unit tests never touch the network (CLAUDE.md 7): every test here either
exercises ``MockSentenceGenerator`` directly or feeds ``LiveSentenceGenerator``
a fake ``GeminiLlmClient``-shaped object whose ``generate`` is a plain
Python function, never a real client.
"""

import pytest
from src.generation.blanking.sentence_source import (
    LiveSentenceGenerator,
    MockSentenceGenerator,
    build_prompt,
    build_sentence_generator,
    client_from_env,
)


def test_build_prompt_never_names_a_grammar_topic() -> None:
    """The one invariant this whole architecture depends on: the generation
    prompt must never leak a grammar topic, an answer, or a gap."""
    prompt = build_prompt("A2", "Reisen", 10)
    lowered = prompt.lower()
    for forbidden in ("kasus", "artikel", "dativ", "akkusativ", "deklination", "___"):
        assert forbidden not in lowered
    assert "Reisen" in prompt
    assert "A2" in prompt


def test_mock_sentence_generator_is_deterministic() -> None:
    gen = MockSentenceGenerator()
    first = gen.generate("A2", "Alltag", 20)
    second = gen.generate("A2", "Alltag", 20)
    assert first == second
    assert len(first) == 20
    assert all(isinstance(s, str) and s for s in first)


def test_mock_sentence_generator_handles_zero_and_negative_count() -> None:
    gen = MockSentenceGenerator()
    assert gen.generate("A1", "Essen", 0) == []
    assert gen.generate("A1", "Essen", -3) == []


def test_mock_sentence_generator_never_produces_a_gap_or_grammar_terminology() -> None:
    gen = MockSentenceGenerator()
    for sentence in gen.generate("B1", "Alltag", 40):
        assert "___" not in sentence


class _FakeLlmClient:
    """A minimal stand-in for GeminiLlmClient.generate -- not a real client,
    never touches the network."""

    def __init__(self, response_text: str) -> None:
        self._response_text = response_text
        self.calls: list[dict[str, object]] = []

    def generate(self, prompt: str, **kwargs: object) -> str:
        self.calls.append({"prompt": prompt, **kwargs})
        return self._response_text


def test_live_sentence_generator_parses_plain_json() -> None:
    fake = _FakeLlmClient('{"sentences": ["Der Hund läuft.", "Die Katze schläft."]}')
    generator = LiveSentenceGenerator(fake)  # type: ignore[arg-type]
    sentences = generator.generate("A1", "Tiere", 2)
    assert sentences == ["Der Hund läuft.", "Die Katze schläft."]
    assert fake.calls[0]["purpose"] == "sentence_generation"
    assert fake.calls[0]["is_user_content"] is False


def test_live_sentence_generator_strips_a_markdown_code_fence() -> None:
    fake = _FakeLlmClient('```json\n{"sentences": ["Ein Beispiel."]}\n```')
    generator = LiveSentenceGenerator(fake)  # type: ignore[arg-type]
    assert generator.generate("A1", "Alltag", 1) == ["Ein Beispiel."]


def test_live_sentence_generator_returns_empty_list_on_malformed_json() -> None:
    fake = _FakeLlmClient("not json at all")
    generator = LiveSentenceGenerator(fake)  # type: ignore[arg-type]
    assert generator.generate("A1", "Alltag", 3) == []


def test_live_sentence_generator_returns_empty_list_when_sentences_key_missing() -> None:
    fake = _FakeLlmClient('{"oops": []}')
    generator = LiveSentenceGenerator(fake)  # type: ignore[arg-type]
    assert generator.generate("A1", "Alltag", 3) == []


def test_client_from_env_returns_none_without_any_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_FREE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_PAID_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert client_from_env() is None


def test_build_sentence_generator_falls_back_to_mock_when_no_client() -> None:
    generator = build_sentence_generator(None)
    assert isinstance(generator, MockSentenceGenerator)


def test_build_sentence_generator_wraps_a_real_client() -> None:
    fake = _FakeLlmClient('{"sentences": []}')
    generator = build_sentence_generator(fake)  # type: ignore[arg-type]
    assert isinstance(generator, LiveSentenceGenerator)
