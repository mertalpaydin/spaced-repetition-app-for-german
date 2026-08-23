"""Unit tests for German-to-English sentence translation (src/llm/translation.py).

None of these tests touch the network (CLAUDE.md 158):

- ``AzureTranslator`` calls ``urllib.request.urlopen`` directly, so every test
  that exercises it monkeypatches that one function and inspects the
  ``urllib.request.Request`` object it was given, exactly as instructed.
- ``GeminiTranslator`` goes through an injected ``GeminiLlmClient``. Rather than
  faking the SDK transport two layers down (as ``tests/test_llm_client.py``
  does for the client's own tests), these tests build a real
  ``GeminiLlmClient`` pointed at a ``tmp_path`` cost log and cache, then
  replace its ``generate`` method with a small recording fake -- honest about
  what is and is not exercised: ``GeminiTranslator``'s contract with the
  client (one call per sentence, first-line-stripped, ``purpose="translation"``),
  not the client's own transport/cache/budget machinery, which
  ``test_llm_client.py`` already covers.
- ``AzureTranslator``'s cost-log tests use a real ``GeminiLlmClient`` too, for
  the same reason, but call nothing on it except ``_log_cost`` (via
  ``AzureTranslator._log``), which never touches a network client.
- No test ever points ``GeminiLlmClient`` at the real
  ``.cache/cost_log.jsonl``; every client built here is given an explicit
  ``cost_log_path`` under ``tmp_path``.
"""

import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from email.message import Message
from pathlib import Path
from typing import Any

import pytest
from src.llm.client import GeminiLlmClient
from src.llm.translation import (
    AZURE_COST_LOG_MODEL,
    AZURE_GLOBAL_HOST,
    AZURE_MAX_BATCH,
    AzureTranslator,
    FallbackTranslator,
    GeminiTranslator,
    TranslationError,
    TranslationProtocolError,
    _parse_azure_payload,
    azure_from_env,
)

# ----------------------------------------------------------------------
# Shared fakes
# ----------------------------------------------------------------------


class _FakeAzureResponse:
    """Stands in for the ``http.client.HTTPResponse`` a real ``urlopen``
    returns: a context manager whose ``read()`` yields a JSON body."""

    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeAzureResponse":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def _echo_urlopen(
    captured: list[urllib.request.Request],
) -> Callable[..., _FakeAzureResponse]:
    """A fake ``urlopen`` that records every ``Request`` it receives and
    echoes back one translation per input sentence (prefixed ``EN:``), so
    batching and ordering tests can tell which output came from which input
    without needing a fixed canned payload sized to a specific batch."""

    def fake_urlopen(
        request: urllib.request.Request, timeout: float | None = None
    ) -> _FakeAzureResponse:
        captured.append(request)
        body = json.loads(request.data.decode("utf-8"))  # type: ignore[union-attr]
        payload = [{"translations": [{"text": f"EN:{item['Text']}"}]} for item in body]
        return _FakeAzureResponse(payload)

    return fake_urlopen


def _refusing_urlopen(
    request: urllib.request.Request, timeout: float | None = None
) -> _FakeAzureResponse:
    """A fake ``urlopen`` that fails the test if it is ever called, for
    assertions that a request must not be made at all (budget exceeded,
    empty input)."""
    raise AssertionError("urlopen should not have been called")


def _make_client(tmp_path: Path) -> GeminiLlmClient:
    """A real ``GeminiLlmClient`` pointed at a throwaway cost log and cache
    under ``tmp_path``, never at ``.cache/cost_log.jsonl``, and never given an
    API key: nothing in these tests calls its real ``generate()`` against a
    network client -- only ``_log_cost`` directly, or a faked ``generate``."""
    return GeminiLlmClient(
        free_api_key=None,
        paid_api_key=None,
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
    )


# ----------------------------------------------------------------------
# AzureTranslator: request shape
# ----------------------------------------------------------------------


def test_azure_translator_translate_url_has_from_de_to_en(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[urllib.request.Request] = []
    monkeypatch.setattr(urllib.request, "urlopen", _echo_urlopen(captured))
    translator = AzureTranslator(api_key="secret-key")

    translator.translate(["Hallo Welt."])

    assert len(captured) == 1
    assert "from=de&to=en" in captured[0].full_url


def test_azure_translator_translate_sends_subscription_key_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[urllib.request.Request] = []
    monkeypatch.setattr(urllib.request, "urlopen", _echo_urlopen(captured))
    translator = AzureTranslator(api_key="secret-key")

    translator.translate(["Hallo Welt."])

    # ``Request.get_header`` does not normalize its argument itself (only
    # ``add_header``, used to build the stored dict, capitalizes); pass the
    # already-capitalized form ``Request`` actually stores it under.
    assert captured[0].get_header("Ocp-apim-subscription-key") == "secret-key"


def test_azure_translator_translate_includes_region_header_when_region_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[urllib.request.Request] = []
    monkeypatch.setattr(urllib.request, "urlopen", _echo_urlopen(captured))
    translator = AzureTranslator(api_key="secret-key", region="westeurope")

    translator.translate(["Hallo Welt."])

    assert captured[0].get_header("Ocp-apim-subscription-region") == "westeurope"


def test_azure_translator_translate_omits_region_header_when_region_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Global-resource key has no region at all. Sending the header with an
    empty value is not the same as never sending it, and Azure's Global
    endpoint does not expect it -- so an empty ``region`` must suppress the
    header entirely, not send it blank."""
    captured: list[urllib.request.Request] = []
    monkeypatch.setattr(urllib.request, "urlopen", _echo_urlopen(captured))
    translator = AzureTranslator(api_key="secret-key", region="")

    translator.translate(["Hallo Welt."])

    assert captured[0].get_header("Ocp-Apim-Subscription-Region") is None


def test_azure_translator_translate_sends_sentences_as_text_array(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[urllib.request.Request] = []
    monkeypatch.setattr(urllib.request, "urlopen", _echo_urlopen(captured))
    translator = AzureTranslator(api_key="k")

    translator.translate(["Guten Tag.", "Auf Wiedersehen."])

    body = json.loads(captured[0].data.decode("utf-8"))  # type: ignore[union-attr]
    assert body == [{"Text": "Guten Tag."}, {"Text": "Auf Wiedersehen."}]


def test_azure_translator_translate_empty_input_returns_empty_without_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", _refusing_urlopen)
    translator = AzureTranslator(api_key="k")

    assert translator.translate([]) == []


# ----------------------------------------------------------------------
# AzureTranslator: batching and order
# ----------------------------------------------------------------------


def test_azure_translator_translate_splits_over_max_batch_into_multiple_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[urllib.request.Request] = []
    monkeypatch.setattr(urllib.request, "urlopen", _echo_urlopen(captured))
    translator = AzureTranslator(api_key="k", monthly_character_budget=10**9)
    sentence_count = AZURE_MAX_BATCH + 30
    sentences = [f"Satz Nummer {i}." for i in range(sentence_count)]

    translator.translate(sentences)

    assert len(captured) == 2
    first_body = json.loads(captured[0].data.decode("utf-8"))  # type: ignore[union-attr]
    second_body = json.loads(captured[1].data.decode("utf-8"))  # type: ignore[union-attr]
    assert len(first_body) == AZURE_MAX_BATCH
    assert len(second_body) == sentence_count - AZURE_MAX_BATCH


def test_azure_translator_translate_preserves_order_across_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The property that actually matters: a batch split that comes back
    concatenated out of order attaches a plausible-looking, entirely wrong
    English gloss to a German sentence, and nothing downstream would notice
    since every individual translation still looks correct on its own. This
    checks the result lines up with the input index-for-index across a
    request boundary, not just that the right count of results came back."""
    captured: list[urllib.request.Request] = []
    monkeypatch.setattr(urllib.request, "urlopen", _echo_urlopen(captured))
    translator = AzureTranslator(api_key="k", monthly_character_budget=10**9)
    sentence_count = AZURE_MAX_BATCH + 30
    sentences = [f"Satz Nummer {i}." for i in range(sentence_count)]

    result = translator.translate(sentences)

    assert result == [f"EN:{s}" for s in sentences]


# ----------------------------------------------------------------------
# AzureTranslator: character budget
# ----------------------------------------------------------------------


def test_azure_translator_translate_raises_before_request_when_over_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", _refusing_urlopen)
    translator = AzureTranslator(api_key="k", monthly_character_budget=5, characters_used=0)

    with pytest.raises(TranslationError):
        translator.translate(["Hallo Welt"])  # 10 characters > budget of 5


def test_azure_translator_translate_budget_error_names_the_numbers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", _refusing_urlopen)
    translator = AzureTranslator(api_key="k", monthly_character_budget=5, characters_used=2)

    with pytest.raises(TranslationError) as exc_info:
        translator.translate(["Hallo Welt"])  # 10 characters

    message = str(exc_info.value)
    assert "2" in message  # characters_used
    assert "10" in message  # characters this call would add
    assert "5" in message  # monthly_character_budget


# ----------------------------------------------------------------------
# AzureTranslator: cost logging
# ----------------------------------------------------------------------


def test_azure_translator_translate_success_logs_one_cost_log_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: list[urllib.request.Request] = []
    monkeypatch.setattr(urllib.request, "urlopen", _echo_urlopen(captured))
    client = _make_client(tmp_path)
    translator = AzureTranslator(api_key="k", llm_client=client)
    sentences = ["Hallo Welt.", "Wie geht es dir?"]
    expected_characters = sum(len(s) for s in sentences)

    translator.translate(sentences)

    assert len(client.cost_records) == 1
    row = client.cost_records[0]
    assert row.model == AZURE_COST_LOG_MODEL
    assert row.lane == "free"
    assert row.cost_usd == 0.0
    assert row.purpose == "translation"
    assert row.prompt_tokens == expected_characters

    on_disk = (tmp_path / "cost_log.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(on_disk) == 1


def test_azure_translator_translate_with_no_llm_client_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[urllib.request.Request] = []
    monkeypatch.setattr(urllib.request, "urlopen", _echo_urlopen(captured))
    translator = AzureTranslator(api_key="k", llm_client=None)

    result = translator.translate(["Hallo Welt."])

    assert result == ["EN:Hallo Welt."]


# ----------------------------------------------------------------------
# AzureTranslator: transport failures
# ----------------------------------------------------------------------


def test_azure_translator_translate_http_error_raises_translation_error_with_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(
        request: urllib.request.Request, timeout: float | None = None
    ) -> _FakeAzureResponse:
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            Message(),
            io.BytesIO(b"quota exceeded"),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    translator = AzureTranslator(api_key="k")

    with pytest.raises(TranslationError) as exc_info:
        translator.translate(["Hallo Welt."])

    assert "403" in str(exc_info.value)


def test_azure_translator_translate_url_error_raises_translation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(
        request: urllib.request.Request, timeout: float | None = None
    ) -> _FakeAzureResponse:
        raise urllib.error.URLError("Name or service not known")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    translator = AzureTranslator(api_key="k")

    with pytest.raises(TranslationError) as exc_info:
        translator.translate(["Hallo Welt."])

    assert "unreachable" in str(exc_info.value).lower()


# ----------------------------------------------------------------------
# _parse_azure_payload: safety-critical response validation
#
# Every case here would otherwise silently misalign a translation with the
# wrong German sentence rather than fail: a short, malformed or
# wrong-shaped response still produces *some* string for each entry it does
# have, and a caller that zipped it against the input list positionally
# would attach a plausible, correct-looking English sentence to the wrong
# carrier, with nothing downstream able to tell the difference. An error
# here is strictly better than that: it stops the batch instead of quietly
# corrupting an unbounded number of glosses after the point of divergence.
# ----------------------------------------------------------------------


def test_parse_azure_payload_not_a_list_raises() -> None:
    """A dict (or any non-list) in place of Azure's documented JSON array
    would otherwise need to be coerced or iterated defensively somewhere
    downstream; either path either drops results silently or raises a
    confusing, unrelated error far from the actual cause."""
    with pytest.raises(TranslationError):
        _parse_azure_payload({"error": "nope"}, expected=2)


def test_parse_azure_payload_wrong_length_raises() -> None:
    """One entry short of what was requested is the textbook misalignment
    case: zipped positionally against the input sentences, every entry after
    the gap would become the gloss for the *previous* sentence, silently,
    for the rest of the batch."""
    payload = [{"translations": [{"text": "one"}]}]
    with pytest.raises(TranslationError):
        _parse_azure_payload(payload, expected=2)


def test_parse_azure_payload_entry_not_object_raises() -> None:
    """A bare string or number where Azure's documented per-sentence object
    belongs would otherwise raise deep inside a caller that assumes dict
    access works (or be skipped by one written defensively) -- either way
    losing the 1:1 alignment between this entry's position and its sentence
    without saying so."""
    payload = ["not-an-object", {"translations": [{"text": "ok"}]}]
    with pytest.raises(TranslationError):
        _parse_azure_payload(payload, expected=2)


def test_parse_azure_payload_missing_translations_key_raises() -> None:
    """An entry with no ``translations`` field at all (for example, only a
    ``detectedLanguage`` block) has literally no English text to return.
    Accepting it would require inventing a placeholder gloss, and a
    plausible-looking placeholder is worse than an absent one: a learner (or
    a later QA pass) cannot tell it apart from a real translation."""
    payload = [{"detectedLanguage": {"language": "de"}}]
    with pytest.raises(TranslationError):
        _parse_azure_payload(payload, expected=1)


def test_parse_azure_payload_empty_translations_list_raises() -> None:
    """Same failure as a missing key, reached a different way: the field is
    present but carries nothing to index into."""
    payload: list[dict[str, list[object]]] = [{"translations": []}]
    with pytest.raises(TranslationError):
        _parse_azure_payload(payload, expected=1)


def test_parse_azure_payload_text_not_a_string_raises() -> None:
    """Any field reached from an HTTP response is untrusted regardless of
    what Azure's documentation promises. Coercing a non-string ``text`` with
    ``str()`` would silently store a plausible but wrong gloss (``str(None)``
    is the literal word ``'None'``) with no trace that anything failed."""
    payload = [{"translations": [{"text": None}]}]
    with pytest.raises(TranslationError):
        _parse_azure_payload(payload, expected=1)


def test_parse_azure_payload_valid_returns_texts_in_order() -> None:
    payload = [
        {"translations": [{"text": "Hello world."}]},
        {"translations": [{"text": "How are you?"}]},
    ]

    assert _parse_azure_payload(payload, expected=2) == ["Hello world.", "How are you?"]


# ----------------------------------------------------------------------
# GeminiTranslator
# ----------------------------------------------------------------------


def _fake_generate(replies: list[str]) -> tuple[Callable[..., str], list[dict[str, str]]]:
    """A recording fake for ``GeminiLlmClient.generate``, matching the exact
    call shape ``GeminiTranslator`` uses: positional ``prompt``, keyword
    ``purpose`` and ``model``."""
    calls: list[dict[str, str]] = []
    remaining = list(replies)

    def fake_generate(
        prompt: str, *, purpose: str = "generation", model: str = "", **kwargs: Any
    ) -> str:
        calls.append({"prompt": prompt, "purpose": purpose, "model": model})
        return remaining.pop(0)

    return fake_generate, calls


def test_gemini_translator_translate_sends_one_call_per_sentence(tmp_path: Path) -> None:
    """A batched prompt invites the model to merge, reorder or drop lines
    (see the class docstring); one call per sentence is the whole point."""
    client = _make_client(tmp_path)
    fake_generate, calls = _fake_generate(["Hello.", "Goodbye."])
    client.generate = fake_generate  # type: ignore[method-assign]
    translator = GeminiTranslator(llm_client=client, model="gemini-test")

    result = translator.translate(["Hallo.", "Auf Wiedersehen."])

    assert len(calls) == 2
    assert result == ["Hello.", "Goodbye."]
    assert "Hallo." in calls[0]["prompt"]
    assert "Auf Wiedersehen." in calls[1]["prompt"]


def test_gemini_translator_translate_uses_first_line_stripped(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    fake_generate, _calls = _fake_generate(
        ["  Cats sleep a lot.  \nExtra second line that should be dropped.\n"]
    )
    client.generate = fake_generate  # type: ignore[method-assign]
    translator = GeminiTranslator(llm_client=client, model="gemini-test")

    result = translator.translate(["Katzen schlafen viel."])

    assert result == ["Cats sleep a lot."]


def test_gemini_translator_translate_raises_on_empty_reply(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    fake_generate, _calls = _fake_generate(["   \n  "])
    client.generate = fake_generate  # type: ignore[method-assign]
    translator = GeminiTranslator(llm_client=client, model="gemini-test")

    with pytest.raises(TranslationError):
        translator.translate(["Leerer Satz."])


def test_gemini_translator_translate_passes_purpose_translation_and_model(
    tmp_path: Path,
) -> None:
    client = _make_client(tmp_path)
    fake_generate, calls = _fake_generate(["Hello."])
    client.generate = fake_generate  # type: ignore[method-assign]
    translator = GeminiTranslator(llm_client=client, model="gemini-3.5-flash-lite")

    translator.translate(["Hallo."])

    assert calls[0]["purpose"] == "translation"
    assert calls[0]["model"] == "gemini-3.5-flash-lite"


# ----------------------------------------------------------------------
# FallbackTranslator
# ----------------------------------------------------------------------


class _StubTranslator:
    """A minimal ``Translator`` that either returns a fixed result or raises
    a fixed error, and records every call it received."""

    def __init__(self, result: list[str] | None = None, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[Sequence[str]] = []

    def translate(self, sentences: Sequence[str]) -> list[str]:
        self.calls.append(sentences)
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def test_fallback_translator_uses_fallback_when_primary_raises_translation_error() -> None:
    primary = _StubTranslator(error=TranslationError("Azure unreachable: timed out"))
    fallback = _StubTranslator(result=["Hello world."])
    translator = FallbackTranslator(primary=primary, fallback=fallback)

    result = translator.translate(["Hallo Welt."])

    assert result == ["Hello world."]
    assert fallback.calls == [["Hallo Welt."]]


def test_fallback_translator_records_failure_message() -> None:
    primary = _StubTranslator(error=TranslationError("Azure unreachable: timed out"))
    fallback = _StubTranslator(result=["Hello world."])
    translator = FallbackTranslator(primary=primary, fallback=fallback)

    translator.translate(["Hallo Welt."])

    assert translator.failures == ["Azure unreachable: timed out"]


def test_fallback_translator_returns_primary_result_untouched_when_it_succeeds() -> None:
    primary = _StubTranslator(result=["Hello world."])
    fallback = _StubTranslator(result=["should never be seen"])
    translator = FallbackTranslator(primary=primary, fallback=fallback)

    result = translator.translate(["Hallo Welt."])

    assert result == ["Hello world."]


def test_fallback_translator_successful_primary_never_calls_fallback() -> None:
    primary = _StubTranslator(result=["Hello world."])
    fallback = _StubTranslator(result=["should never be seen"])
    translator = FallbackTranslator(primary=primary, fallback=fallback)

    translator.translate(["Hallo Welt."])

    assert fallback.calls == []


def test_fallback_translator_falls_back_on_malformed_response_error_too() -> None:
    """Pins the ACTUAL current behaviour, which contradicts the class's own
    docstring. ``FallbackTranslator``'s docstring claims it "deliberately
    does NOT fall back on a malformed-response error, only on a transport,
    HTTP or quota failure", reasoning that a malformed response means the
    parsing contract is wrong and a second provider would paper over it. But
    the implementation is ``except TranslationError``, and
    ``_parse_azure_payload`` raises that exact same exception type for a
    malformed response -- there is no subclass or attribute distinguishing
    "transport/HTTP/quota" TranslationErrors from "parsing contract broken"
    ones. So a malformed Azure payload is, today, caught and silently papered
    over by the Gemini fallback, exactly what the docstring says must not
    happen. This is a real bug/bad contract, reported rather than fixed
    (module left unmodified per instructions): either the docstring is wrong
    and should be corrected, or the code needs a distinct exception (or a
    flag on TranslationError) so FallbackTranslator can actually tell the
    two failure kinds apart."""
    primary = _StubTranslator(error=TranslationError("Azure result entry carries no translations."))
    fallback = _StubTranslator(result=["patched over"])
    translator = FallbackTranslator(primary=primary, fallback=fallback)

    result = translator.translate(["Hallo Welt."])

    assert result == ["patched over"]
    assert fallback.calls == [["Hallo Welt."]]


# ----------------------------------------------------------------------
# azure_from_env
# ----------------------------------------------------------------------


def test_azure_from_env_returns_none_when_no_key() -> None:
    """Deliberate: a nightly job must degrade (skip Azure, use the fallback,
    or skip translation) rather than crash on a missing optional credential."""
    assert azure_from_env(env={}) is None


def test_azure_from_env_returns_none_when_key_is_blank() -> None:
    assert azure_from_env(env={"AZURE_TRANSLATOR_KEY": "   "}) is None


def test_azure_from_env_reads_key_region_and_endpoint(tmp_path: Path) -> None:
    client = _make_client(tmp_path)
    env = {
        "AZURE_TRANSLATOR_KEY": "secret-key",
        "AZURE_TRANSLATOR_REGION": "westeurope",
        "AZURE_TRANSLATOR_ENDPOINT": "https://custom.example.com",
    }

    translator = azure_from_env(llm_client=client, env=env)

    assert translator is not None
    assert translator.api_key == "secret-key"
    assert translator.region == "westeurope"
    assert translator.host == "https://custom.example.com"
    assert translator.llm_client is client


def test_azure_from_env_falls_back_to_global_host_when_no_endpoint() -> None:
    translator = azure_from_env(env={"AZURE_TRANSLATOR_KEY": "secret-key"})

    assert translator is not None
    assert translator.host == AZURE_GLOBAL_HOST


def test_azure_from_env_reads_from_os_environ_when_env_arg_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "from-os-environ")
    monkeypatch.delenv("AZURE_TRANSLATOR_REGION", raising=False)
    monkeypatch.delenv("AZURE_TRANSLATOR_ENDPOINT", raising=False)

    translator = azure_from_env()

    assert translator is not None
    assert translator.api_key == "from-os-environ"


def test_azure_translator_non_json_body_is_a_protocol_error_not_a_fallback() -> None:
    """A 200 whose body is not JSON is real in practice: a proxy or captive
    portal answers with an HTML error page and the right status code. That is
    the parsing contract failing, not a retryable outage, so it must raise
    ``TranslationProtocolError`` and must NOT send the batch to a second
    provider, which would hide a bug affecting every future response."""

    class _Body:
        def read(self) -> bytes:
            return b"<html>Proxy error</html>"

        def __enter__(self) -> "_Body":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    translator = AzureTranslator(api_key="k", urlopen=lambda *a, **k: _Body())
    with pytest.raises(TranslationProtocolError):
        translator.translate(["Hallo."])


def test_azure_translator_unexpected_exception_becomes_a_translation_error() -> None:
    """Everything a Translator raises must be a ``TranslationError``. An
    unforeseen transport exception escaping raw would crash the six-week
    unattended backfill before it writes its store; the backfill script had
    to add its own blanket guard to work around this gap before it was
    closed here."""

    def _boom(*args: object, **kwargs: object) -> object:
        raise OSError("socket exploded")

    translator = AzureTranslator(api_key="k", urlopen=_boom)
    with pytest.raises(TranslationError, match="transport failure"):
        translator.translate(["Hallo."])


def test_fallback_translator_does_not_fall_back_on_a_protocol_error() -> None:
    """The distinction the two exception types exist for. An earlier version
    of this module documented this behaviour without implementing it: both
    cases raised one type, so a malformed response was quietly papered over
    by the fallback, which is the opposite of what its docstring promised."""

    class _Malformed:
        def translate(self, sentences: object) -> list[str]:
            raise TranslationProtocolError("shape is wrong")

    class _NeverCalled:
        def translate(self, sentences: object) -> list[str]:
            raise AssertionError("the fallback must not run on a protocol error")

    fallback = FallbackTranslator(primary=_Malformed(), fallback=_NeverCalled())
    with pytest.raises(TranslationProtocolError):
        fallback.translate(["Hallo."])
    assert fallback.failures == []


def test_gemini_translator_transport_failure_becomes_a_translation_error() -> None:
    """The Gemini SDK raises its own httpx and proxy exceptions that callers
    of this module cannot be expected to know about. Reproduced live: a
    proxy 403 crashed a backfill mid-run before it could write anything."""

    class _Client:
        def generate(self, prompt: str, **kwargs: object) -> str:
            raise ConnectionError("proxy said no")

    translator = GeminiTranslator(llm_client=_Client(), model="m")  # type: ignore[arg-type]
    with pytest.raises(TranslationError, match="Gemini transport failure"):
        translator.translate(["Hallo."])
