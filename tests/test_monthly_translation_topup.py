"""Tests for the standing monthly Azure F0 top-up.

``scripts/monthly_translation_topup.py`` plus ``src/llm/translation_ledger.py``.

CLAUDE.md section 7: unit tests never touch the network. Every test drives
``run_topup`` (or ``main`` with the corpus reader and translator construction
monkeypatched) with a fake ``Translator``, an in-memory carrier set and an
injected clock. The store and the ledger are real files under ``tmp_path``,
because those two files ARE the mechanism under test: the store is the position
pointer that makes consecutive months advance, and the ledger is the
month-to-date total that makes two runs in one month safe.

The clock is injected rather than frozen with a library because month rollover
is this job's single most important behaviour and the only other way to test it
is to wait until the first of the month.
"""

from __future__ import annotations

import io
import json
import os
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from typing import Any

import pytest
from scripts import monthly_translation_topup as topup
from scripts.build_translations import TranslationRecord, _write_store_atomic
from scripts.corpus_reading import SOURCE_LEIPZIG, CorpusLine
from scripts.monthly_translation_topup import (
    DISTRUSTED_STORE_SOURCES,
    MonthlyTopupReport,
    main,
    prioritise,
    run_topup,
)
from src.llm.translation import AzureTranslator, TranslationError
from src.llm.translation_ledger import (
    LedgerVersionError,
    MonthlySpend,
    TranslationLedger,
    estimate_months_remaining,
    load_ledger,
    month_key,
    save_ledger_atomic,
)

# Every carrier below is exactly this long, so a character budget in a test is
# a carrier count and the arithmetic in the assertions is checkable by eye.
CARRIER_CHARS = 10


def _sentence(index: int) -> str:
    text = f"Satz {index:04d}."
    assert len(text) == CARRIER_CHARS
    return text


def _carriers(count: int, start: int = 0) -> dict[str, CorpusLine]:
    out: dict[str, CorpusLine] = {}
    for index in range(start, start + count):
        text = _sentence(index)
        out[text] = CorpusLine(line_id=str(index), text=text, source=SOURCE_LEIPZIG)
    return out


def _seed_store(path: Path, glosses: dict[str, str]) -> None:
    """``glosses`` maps German text to the stored record's ``source``."""
    store = {
        german: TranslationRecord(
            german=german,
            english=f"stored {german}",
            source=source,  # type: ignore[arg-type]
            written_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        for german, source in glosses.items()
    }
    _write_store_atomic(path, store)


@dataclass
class FakeClock:
    """A clock a test can walk across a month boundary."""

    moment: datetime

    def __call__(self) -> datetime:
        return self.moment

    def set(self, moment: datetime) -> None:
        self.moment = moment


@dataclass
class FakeTranslator:
    """Records every batch, never touches the network. Satisfies the
    ``Translator`` protocol structurally."""

    calls: list[list[str]] = field(default_factory=list)

    def translate(self, sentences: Sequence[str]) -> list[str]:
        self.calls.append(list(sentences))
        return [f"EN {s}" for s in sentences]

    @property
    def translated(self) -> list[str]:
        return [text for call in self.calls for text in call]


class _AzureWithQuota:
    """A fake Azure endpoint that answers until ``limit`` characters have
    been sent in this process, then answers HTTP 403 with Azure's own quota
    message. Driven through a REAL ``AzureTranslator`` so the refusal takes the
    exact path a live one does. This is the month boundary now: the job stops
    on Azure saying no, not on its own count."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.sent = 0
        self.calls: list[list[str]] = []

    def __call__(self, request: urllib.request.Request, timeout: float | None = None) -> Any:
        body = json.loads(request.data.decode("utf-8"))  # type: ignore[union-attr]
        texts = [entry["Text"] for entry in body]
        characters = sum(len(t) for t in texts)
        if self.sent + characters > self.limit:
            raise urllib.error.HTTPError(
                request.full_url,
                403,
                "Forbidden",
                Message(),
                io.BytesIO(
                    b'{"error":{"code":403001,"message":"The operation isn\'t allowed '
                    b'because the subscription has exceeded its free quota."}}'
                ),
            )
        self.sent += characters
        self.calls.append(texts)
        return _AzureBody([{"translations": [{"text": f"EN {t}"}]} for t in texts])

    @property
    def translated(self) -> list[str]:
        return [text for call in self.calls for text in call]


class _AzureBody:
    def __init__(self, payload: object) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> _AzureBody:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _azure_with_quota(limit: int, tmp_path: Path) -> tuple[AzureTranslator, _AzureWithQuota]:
    endpoint = _AzureWithQuota(limit)
    translator = AzureTranslator(
        api_key="k",
        urlopen=endpoint,
        characters_per_minute=0,
        sleep=lambda _s: None,
        cost_log_path=tmp_path / "cost_log.jsonl",
    )
    return translator, endpoint


@dataclass
class FailingTranslator:
    """Both providers refused. What a real outage looks like from here."""

    calls: list[list[str]] = field(default_factory=list)

    def translate(self, sentences: Sequence[str]) -> list[str]:
        self.calls.append(list(sentences))
        raise TranslationError("simulated provider outage")


@dataclass
class CrashAfterNBatches:
    """Succeeds ``n`` times, then raises a ``BaseException`` that nothing in
    the pipeline catches.

    ``KeyboardInterrupt`` rather than a plain ``Exception`` on purpose:
    ``_run_machine_translation`` deliberately swallows every ``Exception`` so an
    unattended job cannot be killed by one bad batch, so an ``Exception`` here
    would test the degradation path, not the crash path. This is the real
    "the process died mid-run" case.
    """

    n: int
    calls: list[list[str]] = field(default_factory=list)

    def translate(self, sentences: Sequence[str]) -> list[str]:
        if len(self.calls) >= self.n:
            raise KeyboardInterrupt("simulated process kill")
        self.calls.append(list(sentences))
        return [f"EN {s}" for s in sentences]


def _run(
    tmp_path: Path,
    *,
    carriers: dict[str, CorpusLine],
    translator: Any,
    clock: FakeClock,
    monthly_budget: int,
    max_characters: int | None = None,
    batch_size: int = 1,
    checkpoint_every: int = 1,
    headroom_characters: int = 0,
) -> MonthlyTopupReport:
    return run_topup(
        carriers=carriers,
        store_path=tmp_path / "de_en.jsonl",
        ledger_path=tmp_path / "azure_f0_ledger.json",
        translator=translator,
        translator_mode="azure_only",
        monthly_budget=monthly_budget,
        headroom_characters=headroom_characters,
        max_characters=max_characters,
        batch_size=batch_size,
        checkpoint_every=checkpoint_every,
        clock=clock,
    )


# ==============================================================================
# The ledger itself
# ==============================================================================


def test_month_key_uses_utc_not_local_time() -> None:
    # 23:30 on 31 August in UTC+2 is still August in UTC. A local-time key would
    # call it September on a European machine and August on an American one, and
    # the quota this counts against is metered globally.
    from datetime import timedelta, timezone

    berlin = timezone(timedelta(hours=2))
    assert month_key(datetime(2026, 9, 1, 1, 30, tzinfo=berlin)) == "2026-08"
    assert month_key(datetime(2026, 8, 31, 23, 30, tzinfo=UTC)) == "2026-08"


def test_month_key_treats_a_naive_datetime_as_utc() -> None:
    assert month_key(datetime(2026, 8, 31, 23, 30)) == "2026-08"


def test_ledger_spend_for_an_unseen_month_is_zero_with_no_human_action() -> None:
    ledger = TranslationLedger()
    assert ledger.spend_for("2026-09") == MonthlySpend()
    assert ledger.remaining("2026-09", 2_000_000) == 2_000_000


def test_ledger_remaining_never_goes_negative_on_an_overspent_month() -> None:
    ledger = TranslationLedger(months={"2026-08": MonthlySpend(azure_characters=2_500_000)})
    assert ledger.remaining("2026-08", 2_000_000) == 0


def test_ledger_record_batch_keeps_azure_and_gemini_characters_apart() -> None:
    ledger = TranslationLedger()
    ledger.record_batch("2026-08", azure_characters=500)
    ledger.record_batch("2026-08", gemini_characters=300)
    spend = ledger.spend_for("2026-08")
    assert spend.azure_characters == 500
    assert spend.gemini_characters == 300
    assert spend.batches == 2
    # Only Azure characters consume the F0 allowance.
    assert ledger.remaining("2026-08", 1_000) == 500


def test_ledger_roundtrips_through_an_atomic_write(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    ledger = TranslationLedger()
    ledger.record_batch("2026-08", azure_characters=56_275, at=datetime(2026, 8, 27, tzinfo=UTC))
    save_ledger_atomic(path, ledger)

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["version"] == 1
    assert on_disk["months"]["2026-08"]["azure_characters"] == 56_275

    assert load_ledger(path).spend_for("2026-08").azure_characters == 56_275
    # No temp file left behind next to it.
    assert [p.name for p in tmp_path.iterdir()] == ["ledger.json"]


def test_load_ledger_missing_file_is_a_first_run_not_an_error(tmp_path: Path) -> None:
    assert load_ledger(tmp_path / "nope.json").months == {}


def test_load_ledger_corrupt_file_degrades_rather_than_crashing_a_scheduled_job(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.json"
    path.write_text("{not json at all", encoding="utf-8")
    assert load_ledger(path).months == {}


def test_load_ledger_refuses_a_future_version_rather_than_guessing_a_spend(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({"version": 99, "months": {}}), encoding="utf-8")
    with pytest.raises(LedgerVersionError):
        load_ledger(path)


# ==============================================================================
# The months-remaining arithmetic
# ==============================================================================


def test_months_remaining_matches_the_owners_measured_13_4_month_figure() -> None:
    # The measured situation, 2026-08-27: 449,251 carriers with no trusted
    # machine translation, 26,933,263 characters of corpus, Azure F0's
    # 2,000,000 a month.
    estimate = estimate_months_remaining(
        characters_remaining=26_933_263,
        carriers_remaining=449_251,
        monthly_budget=2_000_000,
    )
    assert estimate.months == pytest.approx(13.4666315)
    # 13.4 is that number truncated, which is how the owner wrote it down.
    assert f"{estimate.months:.1f}" == "13.5"
    # Thirteen and a half months of work needs fourteen monthly runs.
    assert estimate.whole_months == 14
    assert estimate.mean_characters_per_carrier == pytest.approx(59.95, abs=0.01)


def test_months_remaining_is_zero_and_says_so_when_the_corpus_is_finished() -> None:
    estimate = estimate_months_remaining(
        characters_remaining=0, carriers_remaining=0, monthly_budget=2_000_000
    )
    assert estimate.months == 0.0
    assert estimate.whole_months == 0
    assert "No untrusted carriers remain" in estimate.sentence()


def test_months_remaining_sentence_carries_its_own_assumptions() -> None:
    sentence = estimate_months_remaining(
        characters_remaining=26_933_263,
        carriers_remaining=449_251,
        monthly_budget=2_000_000,
    ).sentence()
    # The figure must never be quotable without the caveat attached to it.
    assert "449,251" in sentence
    assert "13.5 months" in sentence
    assert "not a promise" in sentence


def test_months_remaining_rejects_a_nonpositive_budget() -> None:
    with pytest.raises(ValueError):
        estimate_months_remaining(characters_remaining=100, carriers_remaining=1, monthly_budget=0)


def test_run_report_months_remaining_is_right_for_a_known_store_and_corpus(
    tmp_path: Path,
) -> None:
    # Ten 10-character carriers; two already have a trusted azure gloss, so
    # eight are untrusted, 80 characters. Azure accepts 45 characters and then
    # refuses: four land, the fifth batch is the refusal, leaving four carriers
    # and 40 characters: 40 / 45 = 0.888... months, so one more monthly run.
    carriers = _carriers(10)
    texts = sorted(carriers)
    _seed_store(tmp_path / "de_en.jsonl", {texts[0]: "azure", texts[1]: "gemini"})
    translator, _endpoint = _azure_with_quota(45, tmp_path)

    report = _run(
        tmp_path,
        carriers=carriers,
        translator=translator,
        clock=FakeClock(datetime(2026, 8, 27, tzinfo=UTC)),
        monthly_budget=45,
    )

    assert report.carriers_trusted_before == 2
    assert report.carriers_ungossed_before == 8
    assert report.translated == 4
    assert report.azure_quota_rejected
    assert report.carriers_untrusted_after == 4
    assert report.characters_untrusted_after == 40
    assert report.months_remaining == pytest.approx(40 / 45)
    assert report.months_remaining_whole == 1


# ==============================================================================
# The monthly budget across runs
# ==============================================================================


def test_two_runs_in_one_month_do_not_exceed_the_monthly_budget_between_them(
    tmp_path: Path,
) -> None:
    # The month is bounded by Azure, not by the count (owner's instruction,
    # 2026-09-08: the count cannot see what other scripts sent). The first run
    # is bounded by --max-characters; the second keeps sending until Azure
    # refuses, and that refusal is what the ledger remembers.
    carriers = _carriers(20)
    clock = FakeClock(datetime(2026, 8, 10, tzinfo=UTC))

    first = _run(
        tmp_path,
        carriers=carriers,
        translator=FakeTranslator(),
        clock=clock,
        monthly_budget=45,
        max_characters=25,
    )
    clock.set(datetime(2026, 8, 24, tzinfo=UTC))
    # Azure has 25 of its 45 left after the first run's 20.
    second_translator, _endpoint = _azure_with_quota(25, tmp_path)
    second = _run(
        tmp_path,
        carriers=carriers,
        translator=second_translator,
        clock=clock,
        monthly_budget=45,
    )

    assert first.azure_characters == 20  # two 10-character carriers
    assert second.azure_characters == 20  # two more, then Azure says no
    assert second.azure_quota_rejected
    assert not first.azure_quota_rejected
    total = first.azure_characters + second.azure_characters
    assert total == 40
    assert second.month_remaining_after == 5

    ledger = load_ledger(tmp_path / "azure_f0_ledger.json")
    assert ledger.spend_for("2026-08").azure_characters == 40
    assert ledger.spend_for("2026-08").runs == 2
    assert ledger.quota_rejected("2026-08")
    assert ledger.spend_for("2026-08").azure_quota_rejected_at == datetime(2026, 8, 24, tzinfo=UTC)


def test_a_third_run_in_a_spent_month_translates_nothing_and_says_why(
    tmp_path: Path,
) -> None:
    carriers = _carriers(20)
    clock = FakeClock(datetime(2026, 8, 10, tzinfo=UTC))
    refused, _endpoint = _azure_with_quota(40, tmp_path)
    _run(tmp_path, carriers=carriers, translator=refused, clock=clock, monthly_budget=40)

    clock.set(datetime(2026, 8, 28, tzinfo=UTC))
    translator = FakeTranslator()
    report = _run(
        tmp_path, carriers=carriers, translator=translator, clock=clock, monthly_budget=40
    )

    # Not one call: the ledger's flag, not a count, says the month is spent.
    assert translator.calls == []
    assert report.translated == 0
    assert report.run_budget == 0
    assert any("refused month 2026-08 on quota" in w for w in report.warnings)


def test_a_count_past_the_allowance_does_not_stop_a_run_azure_has_not_refused(
    tmp_path: Path,
) -> None:
    """The 2026-09-08 case exactly: the ledger read 1,999,321 of 2,000,000 and a
    pilot had put 66,000 more through Azure unrecorded, yet Azure kept
    answering. The count is an estimate; only the refusal stops a run."""
    carriers = _carriers(20)
    clock = FakeClock(datetime(2026, 8, 10, tzinfo=UTC))
    _run(
        tmp_path,
        carriers=carriers,
        translator=FakeTranslator(),
        clock=clock,
        monthly_budget=40,
        max_characters=40,
    )
    ledger = load_ledger(tmp_path / "azure_f0_ledger.json")
    assert ledger.remaining("2026-08", 40) == 0
    assert not ledger.quota_rejected("2026-08")

    clock.set(datetime(2026, 8, 20, tzinfo=UTC))
    translator = FakeTranslator()
    report = _run(
        tmp_path, carriers=carriers, translator=translator, clock=clock, monthly_budget=40
    )

    assert report.translated == 16  # everything left, Azure never said no
    assert not report.azure_quota_rejected
    assert any("has not refused a call yet" in w for w in report.warnings)


def test_a_run_in_a_new_month_starts_from_zero(tmp_path: Path) -> None:
    carriers = _carriers(20)
    clock = FakeClock(datetime(2026, 8, 28, tzinfo=UTC))
    refused, _endpoint = _azure_with_quota(40, tmp_path)
    august = _run(tmp_path, carriers=carriers, translator=refused, clock=clock, monthly_budget=40)
    assert august.month_remaining_after == 0
    assert august.azure_quota_rejected

    # Rollover is automatic: nobody edits anything, the month key just changes,
    # and August's refusal does not carry into September.
    clock.set(datetime(2026, 9, 1, tzinfo=UTC))
    fresh, _endpoint = _azure_with_quota(40, tmp_path)
    september = _run(tmp_path, carriers=carriers, translator=fresh, clock=clock, monthly_budget=40)

    assert september.month == "2026-09"
    assert september.month_to_date_before == 0
    assert september.run_budget == topup.UNBOUNDED_RUN_CHARACTERS
    assert september.translated == 4

    ledger = load_ledger(tmp_path / "azure_f0_ledger.json")
    assert ledger.spend_for("2026-08").azure_characters == 40
    assert ledger.spend_for("2026-09").azure_characters == 40
    assert ledger.quota_rejected("2026-08")
    assert ledger.quota_rejected("2026-09")


def test_a_refused_month_skips_the_validity_filter_entirely(tmp_path: Path) -> None:
    """The daily task was paying 35 minutes of spaCy on every run of an
    already-spent month. Nothing is sent, so nothing needs validating."""
    carriers = _carriers(20)
    clock = FakeClock(datetime(2026, 8, 28, tzinfo=UTC))
    refused, _endpoint = _azure_with_quota(40, tmp_path)
    _run(tmp_path, carriers=carriers, translator=refused, clock=clock, monthly_budget=40)
    calls: list[str] = []

    def counting_validator(text: str) -> bool:
        calls.append(text)
        return True

    fresh, _endpoint = _azure_with_quota(40, tmp_path)
    report = run_topup(
        carriers=carriers,
        store_path=tmp_path / "de_en.jsonl",
        ledger_path=tmp_path / "azure_f0_ledger.json",
        translator=fresh,
        translator_mode="azure_only",
        monthly_budget=40,
        is_carrier_valid=counting_validator,
        clock=clock,
    )
    assert report.run_budget == 0
    assert calls == []


def test_headroom_shapes_the_estimate_but_no_longer_gates_the_run(tmp_path: Path) -> None:
    carriers = _carriers(20)
    report = _run(
        tmp_path,
        carriers=carriers,
        translator=FakeTranslator(),
        clock=FakeClock(datetime(2026, 8, 10, tzinfo=UTC)),
        monthly_budget=100,
        headroom_characters=65,
    )
    # Headroom used to stop the run at 35 characters. Since 2026-09-08 only
    # Azure's refusal stops a run; headroom survives in the printed allowance
    # and the months-remaining arithmetic, where it is still 100 - 65.
    assert report.run_budget == topup.UNBOUNDED_RUN_CHARACTERS
    assert report.translated == 20
    assert report.monthly_budget - report.headroom_characters == 35


# ==============================================================================
# Priority order and forward progress
# ==============================================================================


def test_prioritise_puts_ungossed_before_tatoeba_replacements_and_counts_trusted() -> None:
    carriers = _carriers(6)
    texts = sorted(carriers)
    store = {
        texts[0]: TranslationRecord(
            german=texts[0], english="x", source="tatoeba", written_at=datetime.now(UTC)
        ),
        texts[1]: TranslationRecord(
            german=texts[1], english="x", source="azure", written_at=datetime.now(UTC)
        ),
    }
    prioritised = prioritise(carriers, store, seed=7)

    assert prioritised.trusted == 1
    assert [line.text for line in prioritised.replacements] == [texts[0]]
    assert len(prioritised.ungossed) == 4
    assert prioritised.todo[:4] == prioritised.ungossed
    assert prioritised.carriers_untrusted == 5
    assert prioritised.characters_untrusted == 5 * CARRIER_CHARS


def test_prioritise_order_is_stable_when_the_corpus_grows(tmp_path: Path) -> None:
    # The reason ordering is a keyed hash and not random.shuffle: a shuffle is a
    # permutation of a LIST, so one new corpus line repermutes everything and
    # next month's run re-draws a slice it has already done.
    small = prioritise(_carriers(20), {}, seed=7)
    large = prioritise(_carriers(40), {}, seed=7)
    small_order = [line.text for line in small.ungossed]
    large_order = [line.text for line in large.ungossed if line.text in set(small_order)]
    assert small_order == large_order


def test_prioritise_order_changes_with_the_seed() -> None:
    a = [line.text for line in prioritise(_carriers(40), {}, seed=7).ungossed]
    b = [line.text for line in prioritise(_carriers(40), {}, seed=8).ungossed]
    assert a != b
    assert sorted(a) == sorted(b)


def test_ungossed_carriers_are_exhausted_before_any_tatoeba_replacement(
    tmp_path: Path,
) -> None:
    carriers = _carriers(10)
    texts = sorted(carriers)
    tatoeba = set(texts[:6])
    _seed_store(tmp_path / "de_en.jsonl", dict.fromkeys(tatoeba, "tatoeba"))
    ungossed = set(texts[6:])
    assert len(ungossed) == 4

    translator = FakeTranslator()
    # Room for exactly the four ungossed carriers and not one more.
    report = _run(
        tmp_path,
        carriers=carriers,
        translator=translator,
        clock=FakeClock(datetime(2026, 8, 10, tzinfo=UTC)),
        monthly_budget=4 * CARRIER_CHARS,
        max_characters=4 * CARRIER_CHARS,
    )

    assert set(translator.translated) == ungossed
    assert not set(translator.translated) & tatoeba
    assert report.translated_from_ungossed == 4
    assert report.translated_from_replacements == 0
    assert report.carriers_tatoeba_replacements_before == 6


def test_tatoeba_replacements_start_only_after_the_ungossed_are_gone(
    tmp_path: Path,
) -> None:
    carriers = _carriers(10)
    texts = sorted(carriers)
    tatoeba = set(texts[:6])
    _seed_store(tmp_path / "de_en.jsonl", dict.fromkeys(tatoeba, "tatoeba"))

    clock = FakeClock(datetime(2026, 8, 10, tzinfo=UTC))
    _run(
        tmp_path,
        carriers=carriers,
        translator=FakeTranslator(),
        clock=clock,
        monthly_budget=4 * CARRIER_CHARS,
        max_characters=4 * CARRIER_CHARS,
    )

    clock.set(datetime(2026, 9, 10, tzinfo=UTC))
    translator = FakeTranslator()
    report = _run(
        tmp_path,
        carriers=carriers,
        translator=translator,
        clock=clock,
        monthly_budget=3 * CARRIER_CHARS,
        max_characters=3 * CARRIER_CHARS,
    )

    assert set(translator.translated) <= tatoeba
    assert report.translated_from_replacements == 3
    assert report.translated_from_ungossed == 0
    # The replaced records really were overwritten, not left as Tatoeba's.
    store_lines = (tmp_path / "de_en.jsonl").read_text(encoding="utf-8").splitlines()
    records = [TranslationRecord.model_validate_json(line) for line in store_lines]
    replaced = [r for r in records if r.german in translator.translated]
    assert len(replaced) == 3
    assert {r.source for r in replaced} == {"azure"}
    # And the six-minus-three Tatoeba records that were not reached are intact,
    # because 5.3's corpus must never have a hole punched in it.
    assert sum(1 for r in records if r.source == "tatoeba") == 3


def test_consecutive_runs_advance_through_the_carrier_set_instead_of_repeating_it(
    tmp_path: Path,
) -> None:
    # Thirteen months of runs must not re-draw the same slice. The store is the
    # position pointer: a translated carrier leaves the ungossed group and never
    # enters the replacement group.
    carriers = _carriers(30)
    clock = FakeClock(datetime(2026, 8, 1, tzinfo=UTC))
    seen: list[set[str]] = []

    for month in range(1, 6):
        clock.set(datetime(2026, month + 7, 1, tzinfo=UTC))
        translator = FakeTranslator()
        _run(
            tmp_path,
            carriers=carriers,
            translator=translator,
            clock=clock,
            monthly_budget=4 * CARRIER_CHARS,
            max_characters=4 * CARRIER_CHARS,
        )
        seen.append(set(translator.translated))

    assert all(len(batch) == 4 for batch in seen)
    union: set[str] = set()
    for batch in seen:
        assert not (batch & union), "a later run repeated carriers an earlier run did"
        union |= batch
    assert len(union) == 20


def test_two_runs_in_one_month_also_advance_rather_than_repeat(tmp_path: Path) -> None:
    carriers = _carriers(30)
    clock = FakeClock(datetime(2026, 8, 5, tzinfo=UTC))
    first = FakeTranslator()
    _run(
        tmp_path,
        carriers=carriers,
        translator=first,
        clock=clock,
        monthly_budget=8 * CARRIER_CHARS,
        max_characters=4 * CARRIER_CHARS,
    )
    clock.set(datetime(2026, 8, 20, tzinfo=UTC))
    second = FakeTranslator()
    _run(
        tmp_path,
        carriers=carriers,
        translator=second,
        clock=clock,
        monthly_budget=8 * CARRIER_CHARS,
        max_characters=4 * CARRIER_CHARS,
    )

    assert len(first.translated) == 4
    assert len(second.translated) == 4
    assert not set(first.translated) & set(second.translated)


# ==============================================================================
# Crash and degradation
# ==============================================================================


def test_a_crash_after_one_batch_leaves_that_batchs_characters_recorded(
    tmp_path: Path,
) -> None:
    carriers = _carriers(10)
    translator = CrashAfterNBatches(n=1)

    with pytest.raises(KeyboardInterrupt):
        _run(
            tmp_path,
            carriers=carriers,
            translator=translator,
            clock=FakeClock(datetime(2026, 8, 10, tzinfo=UTC)),
            monthly_budget=100,
        )

    ledger = load_ledger(tmp_path / "azure_f0_ledger.json")
    spend = ledger.spend_for("2026-08")
    assert spend.azure_characters == CARRIER_CHARS
    assert spend.batches == 1
    # The run itself is still recorded, so "it fired and died" is distinguishable
    # from "it never fired".
    assert spend.runs == 1


def test_a_crash_after_one_batch_leaves_that_batchs_gloss_on_disk(tmp_path: Path) -> None:
    # The store checkpoint runs BEFORE the ledger write, so anything the month
    # was charged for is already durable. The reverse order would bill for work
    # that was thrown away.
    carriers = _carriers(10)
    translator = CrashAfterNBatches(n=1)
    with pytest.raises(KeyboardInterrupt):
        _run(
            tmp_path,
            carriers=carriers,
            translator=translator,
            clock=FakeClock(datetime(2026, 8, 10, tzinfo=UTC)),
            monthly_budget=100,
            checkpoint_every=1,
        )

    lines = (tmp_path / "de_en.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert TranslationRecord.model_validate_json(lines[0]).german == translator.calls[0][0]


def test_a_crash_resumes_from_where_it_stopped_on_the_next_run(tmp_path: Path) -> None:
    carriers = _carriers(10)
    crashed = CrashAfterNBatches(n=2)
    clock = FakeClock(datetime(2026, 8, 10, tzinfo=UTC))
    with pytest.raises(KeyboardInterrupt):
        _run(
            tmp_path,
            carriers=carriers,
            translator=crashed,
            clock=clock,
            monthly_budget=100,
        )
    done = {text for call in crashed.calls for text in call}

    resumed = FakeTranslator()
    clock.set(datetime(2026, 8, 11, tzinfo=UTC))
    _run(tmp_path, carriers=carriers, translator=resumed, clock=clock, monthly_budget=100)

    assert not set(resumed.translated) & done
    assert len(resumed.translated) == 8


def test_a_provider_failure_degrades_and_still_writes_the_ledger_and_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    carriers = _carriers(4)
    store_path = tmp_path / "de_en.jsonl"
    ledger_path = tmp_path / "azure_f0_ledger.json"
    report_path = tmp_path / "report.json"
    translator = FailingTranslator()

    monkeypatch.setattr(topup, "load_env_file", lambda: None)
    monkeypatch.setattr(topup, "client_from_env", lambda **kwargs: None)
    monkeypatch.setattr(topup, "_read_corpora", lambda args: carriers)
    # "Satz 0001." has no finite verb; the validity filter is not under test here.
    monkeypatch.setattr(topup, "_carrier_is_usable", lambda text: True)
    monkeypatch.setattr(topup, "translator_from_env", lambda client: (translator, "azure_only"))

    exit_code = main(
        [
            "--store",
            str(store_path),
            "--ledger",
            str(ledger_path),
            "--report-file",
            str(report_path),
            "--monthly-budget",
            "1000",
            "--batch-size",
            "1",
        ]
    )

    # Degraded, not crashed, and loud about it: this is the silent-no-op case a
    # scheduled job is most likely to have and least likely to be noticed.
    assert exit_code == 1
    assert report_path.exists()
    assert ledger_path.exists()

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["this_run"]["translated"] == 0
    assert report["this_run"]["failed"] == 4
    assert report["this_run"]["failure_examples"]
    assert report["remaining"]["carriers_untrusted"] == 4

    ledger = load_ledger(ledger_path)
    # Nothing was charged, because nothing landed, but the run was recorded.
    assert ledger.spend_for(report["run"]["month"]).azure_characters == 0
    assert ledger.spend_for(report["run"]["month"]).runs == 1


def test_no_translator_configured_degrades_and_reports_rather_than_crashing(
    tmp_path: Path,
) -> None:
    report = run_topup(
        carriers=_carriers(4),
        store_path=tmp_path / "de_en.jsonl",
        ledger_path=tmp_path / "ledger.json",
        translator=None,
        translator_mode="none",
        monthly_budget=1000,
        batch_size=1,
        clock=FakeClock(datetime(2026, 8, 10, tzinfo=UTC)),
    )
    assert report.translated == 0
    assert any("NO TRANSLATOR CONFIGURED" in w for w in report.warnings)
    assert report.carriers_untrusted_after == 4


def test_the_gemini_circuit_breaker_stops_a_month_of_paid_fallback(tmp_path: Path) -> None:
    # If Azure is down all month, every scheduled run would otherwise fall
    # through to the paid provider and translate the corpus with an LLM.
    ledger_path = tmp_path / "ledger.json"
    ledger = TranslationLedger(months={"2026-08": MonthlySpend(gemini_characters=150_000)})
    save_ledger_atomic(ledger_path, ledger)

    translator = FakeTranslator()
    report = run_topup(
        carriers=_carriers(4),
        store_path=tmp_path / "de_en.jsonl",
        ledger_path=ledger_path,
        translator=translator,
        translator_mode="gemini_only",
        monthly_budget=1000,
        max_gemini_characters_per_month=100_000,
        batch_size=1,
        clock=FakeClock(datetime(2026, 8, 10, tzinfo=UTC)),
    )

    assert translator.calls == []
    assert report.translated == 0
    assert any("circuit breaker" in w for w in report.warnings)


def test_main_returns_zero_when_the_month_is_legitimately_spent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A weekly schedule against a monthly allowance means three runs in four are
    # meant to be no-ops. Those must not look like failures to Task Scheduler.
    carriers = _carriers(4)
    ledger_path = tmp_path / "ledger.json"
    save_ledger_atomic(
        ledger_path,
        TranslationLedger(
            months={month_key(datetime.now(UTC)): MonthlySpend(azure_characters=999)}
        ),
    )
    monkeypatch.setattr(topup, "load_env_file", lambda: None)
    monkeypatch.setattr(topup, "client_from_env", lambda **kwargs: None)
    monkeypatch.setattr(topup, "_read_corpora", lambda args: carriers)
    # "Satz 0001." has no finite verb; the validity filter is not under test here.
    monkeypatch.setattr(topup, "_carrier_is_usable", lambda text: True)
    monkeypatch.setattr(
        topup, "translator_from_env", lambda client: (FakeTranslator(), "azure_only")
    )

    exit_code = main(
        [
            "--store",
            str(tmp_path / "de_en.jsonl"),
            "--ledger",
            str(ledger_path),
            "--report-file",
            str(tmp_path / "report.json"),
            "--monthly-budget",
            "999",
        ]
    )
    assert exit_code == 0


def test_main_refuses_headroom_at_or_above_the_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(topup, "load_env_file", lambda: None)
    with pytest.raises(SystemExit):
        main(["--monthly-budget", "1000", "--headroom-characters", "1000"])


def test_the_distrusted_sources_are_exactly_tatoeba() -> None:
    # Pinned deliberately. Widening this set silently re-translates records the
    # project already paid for; narrowing it to empty turns the whole job into a
    # no-op once every carrier has some gloss.
    assert DISTRUSTED_STORE_SOURCES == frozenset({"tatoeba"})


def test_a_budget_below_one_whole_batch_is_reported_as_normal_not_as_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found by a smoke run, not by reasoning: a 3,000-character budget with a
    batch size of 100 and 71.5-character carriers wanted 7,150 for its first
    batch, translated nothing, and looked exactly like a broken job.

    ``run_backfill`` never sends a partial batch to use up a remainder, so a
    per-run cap below one batch buys nothing. That is normal and must not exit
    1, or a schedule cries wolf and the exit code stops being read. (Since
    2026-09-08 the month itself is not a count, so the case arrives through
    --max-characters rather than through a nearly spent month.)"""
    carriers = _carriers(10)
    translator = FakeTranslator()
    monkeypatch.setattr(topup, "load_env_file", lambda: None)
    monkeypatch.setattr(topup, "client_from_env", lambda **kwargs: None)
    monkeypatch.setattr(topup, "_read_corpora", lambda args: carriers)
    # "Satz 0001." has no finite verb; the validity filter is not under test here.
    monkeypatch.setattr(topup, "_carrier_is_usable", lambda text: True)
    monkeypatch.setattr(topup, "translator_from_env", lambda client: (translator, "azure_only"))

    report_path = tmp_path / "report.json"
    exit_code = main(
        [
            "--store",
            str(tmp_path / "de_en.jsonl"),
            "--ledger",
            str(tmp_path / "ledger.json"),
            "--report-file",
            str(report_path),
            "--max-characters",
            "25",
            "--batch-size",
            "5",
        ]
    )

    assert translator.calls == []
    assert exit_code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["run"]["next_batch_characters"] == 50
    assert any("smaller than the next whole batch" in w for w in report["warnings"])


def test_the_tail_of_a_month_is_at_most_one_batch_of_wasted_allowance(
    tmp_path: Path,
) -> None:
    # Azure accepts 45 characters, 10-character carriers, batches of 4: one
    # batch of 40 lands, the second is refused, and the run stops there. The
    # refused batch spent nothing and stays queued for next month; nothing
    # after it was attempted, so at most one batch's worth of calls is wasted.
    translator, endpoint = _azure_with_quota(45, tmp_path)
    report = _run(
        tmp_path,
        carriers=_carriers(20),
        translator=translator,
        clock=FakeClock(datetime(2026, 8, 10, tzinfo=UTC)),
        monthly_budget=45,
        batch_size=4,
    )
    assert report.translated == 4
    assert report.failed == 4
    assert report.azure_quota_rejected
    assert len(endpoint.calls) == 1
    assert report.month_remaining_after == 5


# ---------------------------------------------------------------------------
# The Windows replace retry. Regression test for the failure that killed the
# first real run of the scheduled job on 2026-08-28, at 27% of the month's
# allowance: os.replace raised WinError 5 because another process was reading
# the ledger at that instant, and the whole run died with it.


def _os_error(winerror: int) -> OSError:
    exc = OSError(winerror, "Access is denied")
    exc.winerror = winerror  # type: ignore[attr-defined]
    return exc


def test_save_ledger_retries_when_the_destination_is_briefly_in_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reader holding the file open must cost a retry, not the run. The
    allowance a crashed run does not spend expires with the month."""
    import src.atomic_write as atomic_mod

    path = tmp_path / "ledger.json"
    attempts: list[int] = []
    real_replace = os.replace

    def flaky(src: Any, dst: Any) -> None:
        attempts.append(1)
        if len(attempts) < 3:
            raise _os_error(5)
        real_replace(src, dst)

    monkeypatch.setattr(atomic_mod.os, "replace", flaky)
    monkeypatch.setattr(atomic_mod.time, "sleep", lambda _s: None)

    ledger = TranslationLedger()
    ledger.record_batch("2026-08", azure_characters=10)
    save_ledger_atomic(path, ledger)

    assert len(attempts) == 3
    assert load_ledger(path).spend_for("2026-08").azure_characters == 10


@pytest.mark.parametrize("winerror", [5, 32])
def test_both_windows_sharing_errors_are_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, winerror: int
) -> None:
    """5 is ERROR_ACCESS_DENIED and 32 is ERROR_SHARING_VIOLATION. Antivirus
    scanning the file produces one, an editor with it open the other."""
    import src.atomic_write as atomic_mod

    attempts: list[int] = []
    real_replace = os.replace

    def flaky(src: Any, dst: Any) -> None:
        attempts.append(1)
        if len(attempts) < 2:
            raise _os_error(winerror)
        real_replace(src, dst)

    monkeypatch.setattr(atomic_mod.os, "replace", flaky)
    monkeypatch.setattr(atomic_mod.time, "sleep", lambda _s: None)
    save_ledger_atomic(tmp_path / "ledger.json", TranslationLedger())
    assert len(attempts) == 2


def test_a_non_transient_error_is_raised_without_retrying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read-only directory is not transient, and retrying it six times only
    delays the report of a real problem."""
    import src.atomic_write as atomic_mod

    attempts: list[int] = []

    def always_fails(src: Any, dst: Any) -> None:
        attempts.append(1)
        raise _os_error(13)

    monkeypatch.setattr(atomic_mod.os, "replace", always_fails)
    monkeypatch.setattr(atomic_mod.time, "sleep", lambda _s: None)

    with pytest.raises(OSError):
        save_ledger_atomic(tmp_path / "ledger.json", TranslationLedger())
    assert len(attempts) == 1


def test_a_permanently_locked_destination_still_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retrying is not pretending. A file locked forever is still an error."""
    import src.atomic_write as atomic_mod

    def always_locked(src: Any, dst: Any) -> None:
        raise _os_error(5)

    monkeypatch.setattr(atomic_mod.os, "replace", always_locked)
    monkeypatch.setattr(atomic_mod.time, "sleep", lambda _s: None)

    with pytest.raises(OSError):
        save_ledger_atomic(tmp_path / "ledger.json", TranslationLedger())
    assert not list(tmp_path.glob("*.tmp"))


# ---------------------------------------------------------------------------
# Pass 0 and the carrier-validity filter. Added 2026-08-29 after a real phase B
# found 462 of its 1,224 carriers with no gloss at all and only 79 with an
# Azure one, because the job's ordering had no notion of which sentences ever
# become exercises.


def _line(text: str) -> CorpusLine:
    return CorpusLine(line_id=text[:8], text=text, source=SOURCE_LEIPZIG)


def test_exercise_carriers_are_translated_before_the_rest_of_the_corpus() -> None:
    """Pass 0. A 1,225-item bank needs 3.4% of one month; the corpus at large
    is 13.4 months. Without this the bank waits on coincidence."""
    carriers = {t: _line(t) for t in ("aaa", "bbb", "ccc", "ddd")}
    result = topup.prioritise(carriers, {}, seed=7, exercise_carriers=frozenset({"ccc", "ddd"}))
    assert {line.text for line in result.exercises} == {"ccc", "ddd"}
    assert {line.text for line in result.ungossed} == {"aaa", "bbb"}
    # And the concatenated order really does put them first.
    assert [line.text for line in result.todo][:2] == [line.text for line in result.exercises]


def test_an_exercise_carrier_with_a_tatoeba_gloss_is_still_pass_zero() -> None:
    """A distrusted gloss is work to do, and it is work to do FIRST when the
    carrier is an actual exercise."""
    carriers = {t: _line(t) for t in ("aaa", "bbb")}
    store = {
        "aaa": TranslationRecord(
            german="aaa",
            english="A",
            source="tatoeba",
            written_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    }
    result = topup.prioritise(carriers, store, seed=7, exercise_carriers=frozenset({"aaa"}))
    assert [line.text for line in result.exercises] == ["aaa"]
    assert result.replacements == []


def test_an_exercise_carrier_that_is_already_trusted_is_not_retranslated() -> None:
    """Pass 0 jumps the queue; it does not spend allowance on work already done."""
    carriers = {"aaa": _line("aaa")}
    store = {
        "aaa": TranslationRecord(
            german="aaa",
            english="A",
            source="azure",
            written_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    }
    result = topup.prioritise(carriers, store, seed=7, exercise_carriers=frozenset({"aaa"}))
    assert result.exercises == []
    assert result.trusted == 1


def test_carriers_that_cannot_host_an_exercise_are_skipped_entirely() -> None:
    """129,469 of 450,502 real corpus lines fail carrier validation and were
    previously queued for allowance they can never repay."""
    carriers = {t: _line(t) for t in ("good one", "junk", "also good")}
    result = topup.prioritise(carriers, {}, seed=7, is_carrier_valid=lambda text: text != "junk")
    assert {line.text for line in result.ungossed} == {"good one", "also good"}
    assert result.carrier_invalid == 1
    assert "junk" not in {line.text for line in result.todo}


def test_an_exercise_carrier_is_exempt_from_the_validity_filter() -> None:
    """It demonstrably hosts an exercise: it is wanted by the deck. Whatever a re-run
    of the validator says about it today, the allowance is already committed."""
    carriers = {"odd but sampled": _line("odd but sampled")}
    result = topup.prioritise(
        carriers,
        {},
        seed=7,
        exercise_carriers=frozenset({"odd but sampled"}),
        is_carrier_valid=lambda _text: False,
    )
    assert [line.text for line in result.exercises] == ["odd but sampled"]
    assert result.carrier_invalid == 0


def test_no_validity_predicate_keeps_every_carrier() -> None:
    """The default for the pure function: a caller that has already filtered
    must not have spaCy imposed on it."""
    carriers = {t: _line(t) for t in ("a", "b")}
    result = topup.prioritise(carriers, {}, seed=7)
    assert len(result.ungossed) == 2
    assert result.carrier_invalid == 0


def test_load_exercise_carriers_reads_a_wanted_carriers_file(tmp_path: Path) -> None:
    carriers_path = tmp_path / "wanted_carriers.txt"
    carriers_path.write_text(
        "leipzig\t1\tErster Satz.\ntatoeba\t2\tZweiter Satz.\n\nDritter Satz.\n",
        encoding="utf-8",
    )

    texts, notes = topup.load_exercise_carriers(carriers_path)
    assert texts == frozenset({"Erster Satz.", "Zweiter Satz.", "Dritter Satz."})
    assert any("3 carrier(s)" in note for note in notes)


def test_load_exercise_carriers_is_not_an_error_when_there_is_no_file(
    tmp_path: Path,
) -> None:
    """A machine that has never built the deck simply has no pass 0."""
    texts, notes = topup.load_exercise_carriers(tmp_path / "absent.txt")
    assert texts == frozenset()
    assert any("no wanted-carriers file" in note for note in notes)


def test_main_wires_the_wanted_carriers_into_pass_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scheduled job, not just ``run_topup``, must translate pass 0 first.

    Regression: the September 2026 run printed the pass 0 notes and then spent
    the whole 2,000,000-character month in corpus order, because ``main`` loaded
    the priority list and never handed it to ``run_topup``.
    """
    carriers = _carriers(40)
    # Two carriers from deep in the corpus order are wanted by the deck.
    wanted = [_sentence(37), _sentence(23)]
    carriers_path = tmp_path / "wanted_carriers.txt"
    carriers_path.write_text(
        "".join(f"leipzig\t{i}\t{text}\n" for i, text in enumerate(wanted)), encoding="utf-8"
    )
    translator = FakeTranslator()
    invalid_calls: list[str] = []

    def rejecting_validator(text: str) -> bool:
        invalid_calls.append(text)
        return text != _sentence(0)

    monkeypatch.setattr(topup, "load_env_file", lambda: None)
    monkeypatch.setattr(topup, "client_from_env", lambda **kwargs: None)
    monkeypatch.setattr(topup, "_read_corpora", lambda args: carriers)
    monkeypatch.setattr(topup, "translator_from_env", lambda client: (translator, "azure_only"))
    monkeypatch.setattr(topup, "_carrier_is_usable", rejecting_validator)

    exit_code = main(
        [
            "--store",
            str(tmp_path / "de_en.jsonl"),
            "--ledger",
            str(tmp_path / "ledger.json"),
            "--report-file",
            str(tmp_path / "report.json"),
            "--carriers-file",
            str(carriers_path),
            "--monthly-budget",
            "100000",
            "--batch-size",
            "2",
        ]
    )

    assert exit_code == 0
    # Pass 0 is the first batch, whatever the hash order says.
    assert set(translator.calls[0]) == set(wanted)
    # And the validity filter is wired too: the rejected carrier never ships.
    assert invalid_calls
    assert _sentence(0) not in translator.translated


def test_ledger_quota_rejection_roundtrips_and_is_per_month(tmp_path: Path) -> None:
    ledger = TranslationLedger()
    assert not ledger.quota_rejected("2026-09")
    ledger.record_quota_rejection("2026-09", datetime(2026, 9, 8, 12, tzinfo=UTC))
    save_ledger_atomic(tmp_path / "ledger.json", ledger)

    loaded = load_ledger(tmp_path / "ledger.json")
    assert loaded.quota_rejected("2026-09")
    assert loaded.spend_for("2026-09").azure_quota_rejected_at == datetime(
        2026, 9, 8, 12, tzinfo=UTC
    )
    assert not loaded.quota_rejected("2026-10")


def test_a_ledger_written_before_the_flag_existed_still_loads(tmp_path: Path) -> None:
    """The field is additive: every ledger on disk today lacks it."""
    (tmp_path / "ledger.json").write_text(
        json.dumps(
            {
                "version": 1,
                "months": {"2026-09": {"azure_characters": 1999321, "batches": 285, "runs": 2}},
            }
        ),
        encoding="utf-8",
    )
    ledger = load_ledger(tmp_path / "ledger.json")
    assert ledger.spend_for("2026-09").azure_characters == 1999321
    assert not ledger.quota_rejected("2026-09")


def test_a_401001_is_recorded_as_the_month_spent_with_azures_words(tmp_path: Path) -> None:
    """A month with characters recorded, then a 401001: that is the allowance
    spent, and the ledger says so with Azure's own words attached."""
    carriers = _carriers(20)
    clock = FakeClock(datetime(2026, 8, 10, tzinfo=UTC))
    _run(
        tmp_path,
        carriers=carriers,
        translator=FakeTranslator(),
        clock=clock,
        monthly_budget=40,
        max_characters=20,
    )

    class _Unauthorized:
        def __call__(self, request: urllib.request.Request, timeout: float | None = None) -> Any:
            raise urllib.error.HTTPError(
                request.full_url,
                401,
                "Unauthorized",
                Message(),
                io.BytesIO(
                    b'{"error":{"code":401001,"message":"credentials are missing or invalid."}}'
                ),
            )

    azure = AzureTranslator(
        api_key="k",
        urlopen=_Unauthorized(),
        characters_per_minute=0,
        cost_log_path=tmp_path / "cost_log.jsonl",
    )
    clock.set(datetime(2026, 8, 11, tzinfo=UTC))
    report = _run(tmp_path, carriers=carriers, translator=azure, clock=clock, monthly_budget=40)

    assert report.azure_quota_rejected
    assert report.translated == 0
    ledger = load_ledger(tmp_path / "azure_f0_ledger.json")
    assert ledger.quota_rejected("2026-08")
    detail = ledger.spend_for("2026-08").azure_quota_rejected_detail
    assert detail is not None and "401001" in detail
