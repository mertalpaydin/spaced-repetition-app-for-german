"""German to English sentence translation, for the learner-facing gloss.

TODO.md 5.1 step 2. The owner's decision is that every exercise shows its
English translation. Tatoeba supplies one for 61.7% of its own carriers
(measured, `scripts/eval_tatoeba_translation_quality.py`); everything else,
which is all of Leipzig plus the other 38% of Tatoeba, is translated here.

## Why not an LLM

This is machine translation of one short sentence, which is exactly what a
dedicated engine is built for and an LLM is not. Azure Translator's free F0
tier gives 2,000,000 characters a month, permanently, with no card. The
whole backfill is about 2.9 million characters and is one-off, so a nightly
job inside the free tier finishes it in roughly six weeks at zero cost. An
LLM would cost real money for a worse result.

`gemini-3.5-live-translate-preview` was considered and does not apply: it is
audio only, over the Live API websocket, and rejects text input outright.

## Rule 4 and the budget

CLAUDE.md rule 4 exists so that no external model call is invisible to the
spend ceiling. A second provider is exactly the shape of thing that rule
guards against, so every call here writes a ``CostLogRow`` through the same
client and the same log file as every Gemini call. Azure F0 rows are logged
on the ``free`` lane because they genuinely cost nothing, which is the same
treatment the unbilled Gemini project already gets; no new lane value is
introduced, so no contract changes.

The Gemini fallback is not a second provider in that sense: it goes through
``GeminiLlmClient`` itself and is therefore already accounted for. It exists
for the case where Azure errors or its monthly quota runs out, so that a
nightly job degrades instead of stopping.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from src.llm.client import CostLogRow, GeminiLlmClient

#: Azure's own global endpoint. A resource created with Region "Global" needs
#: no region header; a region-locked one needs ``Ocp-Apim-Subscription-Region``
#: and its own custom host. Both shapes are supported: see ``AzureTranslator``.
AZURE_GLOBAL_HOST = "https://api.cognitive.microsofttranslator.com"

#: Azure's own documented cap for one request. Sentences here average 49 to 77
#: characters, so a batch of 100 is far inside every limit Azure publishes
#: (50,000 characters and 1,000 array elements per request) while keeping any
#: single failure cheap to retry.
AZURE_MAX_BATCH = 100

#: The F0 tier's monthly character allowance. Tracked locally as well as by
#: Azure so a nightly job can stop cleanly at its own budget rather than
#: discovering the ceiling as a 403 halfway through a batch.
AZURE_F0_MONTHLY_CHARACTERS = 2_000_000

#: What a translation row is called in ``cost_log.jsonl``. Not a Gemini model
#: string, deliberately: reading the log should make it obvious which rows are
#: machine translation and which are generation or verification.
AZURE_COST_LOG_MODEL = "azure-translator-f0"


class TranslationError(RuntimeError):
    """A translation provider failed in a way the caller must handle.

    Raised rather than returning a partial or empty result, so a backfill can
    never silently store an empty gloss and count the sentence as done.

    This is the RETRYABLE kind: a transport failure, an HTTP error, a spent
    quota. Another provider may well succeed, so ``FallbackTranslator``
    catches it.
    """


class TranslationProtocolError(TranslationError):
    """The provider answered, but not in the shape this module expects.

    Split out from ``TranslationError`` because the two need opposite
    handling and an earlier version of this module got that wrong: its
    ``FallbackTranslator`` docstring promised not to fall back on a malformed
    response, while its code caught the one shared exception type and did
    exactly that. A malformed response means the parsing contract is broken,
    and quietly asking a second provider instead would hide a bug that
    affects every future response rather than surfacing it once.
    """


class Translator(Protocol):
    """Anything that turns German sentences into English ones."""

    def translate(self, sentences: Sequence[str]) -> list[str]:
        """One English translation per input, same order, same length."""
        ...


@dataclass
class AzureTranslator:
    """Azure Translator, the primary provider.

    ``region`` is required for a region-locked resource and ignored for a
    Global one; passing it always is harmless and is what the setup
    instructions tell the owner to record, so it is not made conditional here.

    ``characters_used`` is this process's own running count, not Azure's. It
    exists so a nightly job can stop at ``monthly_character_budget`` on its own
    terms. It does not survive a restart, so a caller running more than once a
    month must persist its own total; ``scripts/build_translations.py`` does.
    """

    api_key: str
    region: str = ""
    host: str = AZURE_GLOBAL_HOST
    llm_client: GeminiLlmClient | None = None
    monthly_character_budget: int = AZURE_F0_MONTHLY_CHARACTERS
    characters_used: int = 0
    timeout_seconds: float = 30.0

    #: The network call, injected so a caller can replace it (CLAUDE.md
    #: section 8: anything touching the network is an injected dependency).
    #: An earlier version declared this field and never read it, which left
    #: the seam advertised but not wired.
    #:
    #: Defaults to ``None`` rather than to ``urllib.request.urlopen`` itself,
    #: and resolves at call time. Binding the function as a default value
    #: captures it at import, which silently defeats a test that
    #: monkeypatches the module-level name, and that is the more common way
    #: to fake this. Both approaches work now.
    urlopen: Callable[..., Any] | None = None

    def translate(self, sentences: Sequence[str]) -> list[str]:
        if not sentences:
            return []
        if len(sentences) > AZURE_MAX_BATCH:
            out: list[str] = []
            for start in range(0, len(sentences), AZURE_MAX_BATCH):
                out.extend(self.translate(sentences[start : start + AZURE_MAX_BATCH]))
            return out

        characters = sum(len(s) for s in sentences)
        if self.characters_used + characters > self.monthly_character_budget:
            raise TranslationError(
                f"Azure F0 character budget would be exceeded: "
                f"{self.characters_used:,} used, {characters:,} more requested, "
                f"{self.monthly_character_budget:,} allowed. Stop and resume next month, "
                f"or pass a larger --monthly-character-budget if this resource is not F0."
            )

        url = f"{self.host}/translate?api-version=3.0&from=de&to=en&textType=plain"
        headers = {
            "Ocp-Apim-Subscription-Key": self.api_key,
            "Content-Type": "application/json; charset=UTF-8",
        }
        if self.region:
            headers["Ocp-Apim-Subscription-Region"] = self.region

        body = json.dumps([{"Text": s} for s in sentences]).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        opener = self.urlopen if self.urlopen is not None else urllib.request.urlopen
        try:
            with opener(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise TranslationError(f"Azure returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise TranslationError(f"Azure unreachable: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            # A 200 whose body is not JSON. Real in practice: a proxy or
            # captive portal can answer with an HTML error page and the
            # correct status code. This is the parsing contract failing, not
            # a retryable outage, so it does NOT fall back to another
            # provider.
            raise TranslationProtocolError(f"Azure response was not JSON: {exc}") from exc
        except TranslationError:
            raise
        except Exception as exc:  # noqa: BLE001 -- see below
            # Everything a Translator raises must be a TranslationError, so
            # that one unforeseen transport exception cannot crash a
            # six-week unattended backfill before it writes its store. Found
            # while building scripts/build_translations.py, which had to add
            # its own blanket guard to work around this gap.
            raise TranslationError(f"Azure transport failure: {exc}") from exc

        translations = _parse_azure_payload(payload, expected=len(sentences))
        self.characters_used += characters
        self._log(characters)
        return translations

    def _log(self, characters: int) -> None:
        """Write a ``cost_log`` row so this provider is visible to the budget.

        Logged on the ``free`` lane at zero cost, which is literally true of
        F0 and is the same treatment the unbilled Gemini project gets.
        Characters, not tokens, are what Azure meters, so they go in
        ``prompt_tokens``: the field is the request-side size in the unit the
        provider bills, and inventing a token estimate would put a fiction in
        an audit record.
        """
        if self.llm_client is None:
            return
        self.llm_client._log_cost(  # noqa: SLF001 -- the one writer for this log
            CostLogRow(
                timestamp=datetime.now(UTC),
                model=AZURE_COST_LOG_MODEL,
                lane="free",
                prompt_tokens=characters,
                completion_tokens=0,
                cost_usd=0.0,
                purpose="translation",
            )
        )


def _parse_azure_payload(payload: object, *, expected: int) -> list[str]:
    """Azure's response shape, validated rather than trusted.

    A short or malformed response would otherwise silently misalign every
    translation after it with the wrong sentence, which is far worse than an
    error: the glosses would all be plausible and all attached to the wrong
    German.
    """
    if not isinstance(payload, list) or len(payload) != expected:
        raise TranslationProtocolError(
            f"Azure returned {type(payload).__name__} of unexpected shape; "
            f"expected a list of {expected} results."
        )
    out: list[str] = []
    for entry in payload:
        if not isinstance(entry, dict):
            raise TranslationProtocolError("Azure result entry is not an object.")
        translations = entry.get("translations")
        if not isinstance(translations, list) or not translations:
            raise TranslationProtocolError("Azure result entry carries no translations.")
        first = translations[0]
        if not isinstance(first, dict) or not isinstance(first.get("text"), str):
            raise TranslationProtocolError("Azure translation entry carries no text.")
        out.append(first["text"])
    return out


@dataclass
class GeminiTranslator:
    """The fallback, for when Azure errors or its quota is spent.

    Goes through ``GeminiLlmClient``, so it is already inside the budget and
    the spend ceiling with no special handling. Quality is lower than a
    dedicated engine for this task and it is not free on the paid lane, which
    is exactly why it is the fallback and not the default.

    One sentence per call rather than a batch: a batched prompt invites the
    model to merge, reorder or drop lines, and a misaligned gloss attaches
    correct-looking English to the wrong German, which no downstream check
    would catch.
    """

    llm_client: GeminiLlmClient
    model: str

    def translate(self, sentences: Sequence[str]) -> list[str]:
        out: list[str] = []
        for sentence in sentences:
            prompt = (
                "Translate this German sentence into natural English. "
                "Reply with the translation and nothing else, on one line.\n\n"
                f"{sentence}"
            )
            try:
                text = self.llm_client.generate(prompt, purpose="translation", model=self.model)
            except TranslationError:
                raise
            except Exception as exc:  # noqa: BLE001 -- see below
                # The Gemini SDK raises its own transport exceptions (httpx
                # errors, proxy failures) that this module's callers cannot
                # be expected to know about. Leaving them unwrapped crashed a
                # nightly backfill mid-run before it could write its store,
                # found while building `scripts/build_translations.py`. Every
                # failure out of a Translator is a TranslationError.
                raise TranslationError(f"Gemini transport failure: {exc}") from exc
            cleaned = text.strip().splitlines()[0].strip() if text.strip() else ""
            if not cleaned:
                raise TranslationError(f"Gemini returned no translation for: {sentence!r}")
            out.append(cleaned)
        return out


@dataclass
class FallbackTranslator:
    """Azure first, Gemini when Azure will not answer.

    Deliberately does NOT fall back on ``TranslationProtocolError``, only on
    the retryable ``TranslationError``. A malformed response means the
    parsing contract is wrong, and a second provider would paper over a bug
    that affects every future response. The two exception types exist for
    exactly this distinction; an earlier version of this class documented the
    behaviour without implementing it, because both cases raised one type.
    """

    primary: Translator
    fallback: Translator
    failures: list[str] = field(default_factory=list)

    def translate(self, sentences: Sequence[str]) -> list[str]:
        try:
            return self.primary.translate(sentences)
        except TranslationProtocolError:
            raise
        except TranslationError as exc:
            self.failures.append(str(exc))
            return self.fallback.translate(sentences)


def azure_from_env(
    llm_client: GeminiLlmClient | None = None,
    env: dict[str, str] | None = None,
) -> AzureTranslator | None:
    """Build an ``AzureTranslator`` from the environment, or ``None``.

    Returns ``None`` rather than raising when no key is configured, so a
    caller can degrade to the fallback or skip translation entirely instead
    of crashing a nightly job on a missing optional credential.
    """
    source = env if env is not None else dict(os.environ)
    key = source.get("AZURE_TRANSLATOR_KEY", "").strip()
    if not key:
        return None
    return AzureTranslator(
        api_key=key,
        region=source.get("AZURE_TRANSLATOR_REGION", "").strip(),
        host=source.get("AZURE_TRANSLATOR_ENDPOINT", "").strip() or AZURE_GLOBAL_HOST,
        llm_client=llm_client,
    )
