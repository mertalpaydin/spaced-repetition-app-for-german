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

## Two different Azure ceilings

F0 has an ALLOWANCE (2,000,000 characters a month) and a THROUGHPUT limit
(2,000,000 characters an hour, metered as a sliding window, so roughly
33,300 a minute). They fail differently and must be handled differently.
Hitting the allowance is terminal for the month; hitting the throughput
limit is an HTTP 429 that clears on its own in seconds. The owner's first
real backfill hit the second one, sending about 56,275 characters in ten
back-to-back requests, and the run treated it as a provider outage: 100 of
its 1,000 sentences went to the paid Gemini fallback over a rate limit that
a short wait would have cleared. ``AzureTranslator`` therefore paces itself
under ``AZURE_F0_CHARACTERS_PER_MINUTE`` and retries a 429 in place. The
fallback stays what it is for: a genuinely spent quota, or an outage.

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
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from src.llm.client import (
    DEFAULT_COST_LOG_PATH,
    CostLogRow,
    GeminiLlmClient,
    append_cost_row,
)

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

#: The F0 tier's throughput ceiling, expressed per minute. Microsoft documents
#: F0 as 2,000,000 characters per HOUR, consumed as a sliding window, and its
#: own guidance is to spread that quota evenly rather than burst; 2,000,000/60
#: is 33,333, rounded down here. Measured need for this constant: the owner's
#: first real backfill sent about 56,275 characters in ten back-to-back
#: requests within seconds and Azure answered HTTP 429 (error code 429001,
#: "the client has exceeded request limits"). That is throughput, not a bad
#: key and not a spent monthly allowance, so it is paced against rather than
#: failed over.
AZURE_F0_CHARACTERS_PER_MINUTE = 33_300

#: The width of the pacing window, in seconds. Matches the unit
#: ``AZURE_F0_CHARACTERS_PER_MINUTE`` is expressed in.
_PACING_WINDOW_SECONDS = 60.0

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

    ``characters_per_minute`` is a different ceiling entirely, and the two are
    not interchangeable: the monthly one is an allowance, this one is
    THROUGHPUT. F0 meters characters against a sliding hourly window, so a run
    can be far inside its monthly allowance and still be refused with HTTP 429
    for sending too much too fast, which is exactly what happened on the first
    real backfill (see ``AZURE_F0_CHARACTERS_PER_MINUTE``). A 429 is retryable
    and must never cost a paid fallback call, so this class both paces itself
    below the limit and retries the 429s that pacing does not prevent.
    """

    api_key: str
    region: str = ""
    host: str = AZURE_GLOBAL_HOST
    llm_client: GeminiLlmClient | None = None
    monthly_character_budget: int = AZURE_F0_MONTHLY_CHARACTERS
    characters_used: int = 0
    timeout_seconds: float = 30.0

    #: Where a row goes when no ``llm_client`` was supplied to write it
    #: through. Same file the client itself uses, so one log holds every
    #: provider (CLAUDE.md rule 4).
    #:
    #: ``None`` means ``DEFAULT_COST_LOG_PATH``, resolved at call time rather
    #: than bound here as a default VALUE. Same reasoning as ``urlopen``
    #: below: binding it captures the path at import, which would leave a test
    #: no way to redirect the log away from the real ``.cache/cost_log.jsonl``
    #: and would have every offline test append rows to the repository's own
    #: audit file.
    cost_log_path: Path | None = None

    #: Throughput self-limit, enforced before every real HTTP call. Set to 0
    #: or below to disable pacing entirely (a non-F0 resource, or a test that
    #: is not exercising pacing).
    characters_per_minute: int = AZURE_F0_CHARACTERS_PER_MINUTE

    #: How many times a RETRYABLE HTTP status (429, or any 5xx) is tried
    #: again before the batch is finally reported as failed. Total attempts
    #: are therefore ``max_retries + 1``.
    max_retries: int = 5

    #: Flat wait between those attempts. Flat rather than exponential
    #: deliberately: the thing being waited out is a sliding-window quota
    #: refilling at a known rate, not a contended lock, so a fixed pause
    #: sized to the window is both predictable and enough.
    retry_backoff_seconds: float = 20.0

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

    #: The clock and the wait, injected for the same reason and in the same
    #: shape as ``urlopen`` above (CLAUDE.md section 8 lists the clock
    #: alongside the network). Both default to ``None`` and resolve at call
    #: time rather than binding ``time.sleep``/``time.monotonic`` as default
    #: VALUES, which would capture them at import and silently defeat a test
    #: that monkeypatches the module-level names instead. A test drives
    #: pacing and retry backoff through these with no real waiting at all.
    sleep: Callable[[float], None] | None = None
    monotonic: Callable[[], float] | None = None

    #: (timestamp, characters) for the sends inside the current pacing
    #: window. In-process only, like ``characters_used``: a fresh nightly
    #: process starts with an empty window, which is correct, since a window
    #: that old has long since drained.
    _recent_sends: list[tuple[float, int]] = field(default_factory=list, repr=False)

    def _sleep_fn(self) -> Callable[[float], None]:
        return self.sleep if self.sleep is not None else time.sleep

    def _monotonic_fn(self) -> Callable[[], float]:
        return self.monotonic if self.monotonic is not None else time.monotonic

    def _pace(self, characters: int) -> None:
        """Wait, if needed, so this batch stays inside ``characters_per_minute``.

        Keeps the last ``_PACING_WINDOW_SECONDS`` of sends and, when this
        batch would push the window over the limit, sleeps just long enough
        for as many of the OLDEST entries to age out as it takes to make
        room. Not a loop over the clock: the wait is computed once from the
        entries themselves, so a faked ``sleep`` that does not advance a
        faked ``monotonic`` cannot spin here.
        """
        if self.characters_per_minute <= 0:
            return
        monotonic = self._monotonic_fn()
        now = monotonic()
        self._recent_sends = [
            entry for entry in self._recent_sends if entry[0] > now - _PACING_WINDOW_SECONDS
        ]
        in_window = sum(chars for _, chars in self._recent_sends)
        if self._recent_sends and in_window + characters > self.characters_per_minute:
            needed = in_window + characters - self.characters_per_minute
            freed = 0
            wait = 0.0
            for timestamp, chars in self._recent_sends:  # oldest first
                freed += chars
                wait = timestamp + _PACING_WINDOW_SECONDS - now
                if freed >= needed:
                    break
            if wait > 0:
                self._sleep_fn()(wait)
                # A real monotonic clock has moved at least ``wait`` by now;
                # a faked one may not have, so take the later of the two
                # rather than trusting either alone.
                now = max(monotonic(), now + wait)
                self._recent_sends = [
                    entry for entry in self._recent_sends if entry[0] > now - _PACING_WINDOW_SECONDS
                ]
        self._recent_sends.append((now, characters))

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
        payload = self._post_with_retry(request, characters)

        translations = _parse_azure_payload(payload, expected=len(sentences))
        self.characters_used += characters
        self._log(characters)
        return translations

    def _post_with_retry(self, request: urllib.request.Request, characters: int) -> object:
        """One paced, retried HTTP POST, returning the decoded JSON payload.

        Retries HTTP 429 and any 5xx, ``max_retries`` times, with a flat
        ``retry_backoff_seconds`` between attempts. Every other status raises
        at once: a 401 or a 403 is a bad key, a wrong region or a genuinely
        spent allowance, and repeating it just wastes the run's time before
        failing the same way.

        A 429 in particular MUST be retried rather than fall through to
        ``FallbackTranslator``. The first real backfill spent 100 of its
        1,000 sentences on a paid Gemini call because of one transient rate
        limit, which is the opposite of what the fallback exists for.
        """
        opener = self.urlopen if self.urlopen is not None else urllib.request.urlopen
        attempt = 0
        while True:
            attempt += 1
            self._pace(characters)
            try:
                with opener(request, timeout=self.timeout_seconds) as response:
                    payload: object = json.loads(response.read().decode("utf-8"))
                return payload
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:400]
                retryable = exc.code == 429 or exc.code >= 500
                if retryable and attempt <= self.max_retries:
                    self._sleep_fn()(self.retry_backoff_seconds)
                    continue
                raise TranslationError(f"Azure returned HTTP {exc.code}: {detail}") from exc
            except urllib.error.URLError as exc:
                raise TranslationError(f"Azure unreachable: {exc.reason}") from exc
            except json.JSONDecodeError as exc:
                # A 200 whose body is not JSON. Real in practice: a proxy or
                # captive portal can answer with an HTML error page and the
                # correct status code. This is the parsing contract failing,
                # not a retryable outage, so it does NOT retry and does NOT
                # fall back to another provider.
                raise TranslationProtocolError(f"Azure response was not JSON: {exc}") from exc
            except TranslationError:
                raise
            except Exception as exc:  # noqa: BLE001 -- see below
                # Everything a Translator raises must be a TranslationError,
                # so that one unforeseen transport exception cannot crash a
                # six-week unattended backfill before it writes its store.
                # Found while building scripts/build_translations.py, which
                # had to add its own blanket guard to work around this gap.
                raise TranslationError(f"Azure transport failure: {exc}") from exc

    def _log(self, characters: int) -> None:
        """Write a ``cost_log`` row so this provider is visible to the budget.

        Logged on the ``free`` lane at zero cost, which is literally true of
        F0 and is the same treatment the unbilled Gemini project gets.
        Characters, not tokens, are what Azure meters, so they go in
        ``prompt_tokens``: the field is the request-side size in the unit the
        provider bills, and inventing a token estimate would put a fiction in
        an audit record.

        A row is written whether or not an ``llm_client`` was supplied. An
        earlier version returned early without one, which meant a run
        configured with an Azure key and no Gemini key translated real
        sentences and left no trace in the log at all. CLAUDE.md rule 4 is
        about visibility, not about money, so "it was free anyway" does not
        excuse the gap. With a client the row also lands in that client's own
        in-memory ``cost_records``, which is why that path is preferred when
        one is available.
        """
        row = CostLogRow(
            timestamp=datetime.now(UTC),
            model=AZURE_COST_LOG_MODEL,
            lane="free",
            # Azure's text API is a synchronous REST call; it has no batch
            # mode to distinguish, so the row says so rather than leaving the
            # field unrecorded.
            mode="sync",
            prompt_tokens=characters,
            completion_tokens=0,
            cost_usd=0.0,
            purpose="translation",
        )
        if self.llm_client is not None:
            self.llm_client._log_cost(row)  # noqa: SLF001 -- the one writer for this log
            return
        append_cost_row(row, self.cost_log_path or DEFAULT_COST_LOG_PATH)


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
