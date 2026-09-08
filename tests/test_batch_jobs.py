"""Unit tests for detached batch submission. TODO.md item 1.

No network. The store is a file format and the collector is a state machine;
both are testable with a fake SDK client, and a test that needed a real batch
job would take minutes and cost money.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from src.llm.batch_jobs import (
    DEFAULT_BATCH_JOB_STORE,
    STORE_FORMAT_VERSION,
    BatchJobStore,
    PendingBatchJob,
    record_submission,
)


def _job(name: str = "batches/abc", prompts: list[str] | None = None) -> PendingBatchJob:
    return PendingBatchJob(
        job_name=name,
        model="gemini-3.7-flash",
        purpose="item_verification",
        prompts=prompts if prompts is not None else ["prompt one", "prompt two"],
    )


def test_store_round_trips_through_disk(tmp_path: Path) -> None:
    path = tmp_path / "pending.json"
    store = BatchJobStore()
    store.add(_job())
    store.save(path)

    loaded = BatchJobStore.load(path)
    assert len(loaded.jobs) == 1
    assert loaded.jobs[0].job_name == "batches/abc"
    assert loaded.jobs[0].prompts == ["prompt one", "prompt two"]


def test_prompt_order_survives_the_file(tmp_path: Path) -> None:
    """Google returns inlined responses positionally, so pairing them with the
    wrong prompts would poison the cache with confidently wrong entries."""
    path = tmp_path / "pending.json"
    store = BatchJobStore()
    store.add(_job(prompts=["first", "second", "third"]))
    store.save(path)
    assert BatchJobStore.load(path).jobs[0].prompts == ["first", "second", "third"]


def test_a_missing_store_is_an_empty_store_not_an_error(tmp_path: Path) -> None:
    """ "Nothing is outstanding" is the normal state and must not need a file."""
    assert BatchJobStore.load(tmp_path / "absent.json").jobs == []


def test_a_corrupt_store_reads_empty_and_is_not_deleted(tmp_path: Path) -> None:
    """An operator must still be able to read the job names out by hand."""
    path = tmp_path / "pending.json"
    path.write_text("{not json", encoding="utf-8")
    assert BatchJobStore.load(path).jobs == []
    assert path.exists()


def test_a_future_version_store_reads_empty(tmp_path: Path) -> None:
    path = tmp_path / "pending.json"
    path.write_text(json.dumps({"version": STORE_FORMAT_VERSION + 1, "jobs": []}), encoding="utf-8")
    assert BatchJobStore.load(path).jobs == []


def test_add_replaces_an_earlier_entry_of_the_same_name() -> None:
    store = BatchJobStore()
    store.add(_job(prompts=["old"]))
    store.add(_job(prompts=["new"]))
    assert len(store.jobs) == 1
    assert store.jobs[0].prompts == ["new"]


def test_remove_drops_only_the_named_job() -> None:
    store = BatchJobStore()
    store.add(_job("batches/one"))
    store.add(_job("batches/two"))
    store.remove("batches/one")
    assert [j.job_name for j in store.jobs] == ["batches/two"]


def test_remove_is_silent_about_a_job_that_is_not_there() -> None:
    """Collection removes after caching, so a retried sweep removes twice."""
    store = BatchJobStore()
    store.add(_job("batches/one"))
    store.remove("batches/absent")
    assert len(store.jobs) == 1


def test_save_leaves_no_partial_file(tmp_path: Path) -> None:
    path = tmp_path / "pending.json"
    BatchJobStore(jobs=[_job()]).save(path)
    assert not list(tmp_path.glob("*.partial"))


def test_record_submission_writes_immediately(tmp_path: Path) -> None:
    """The window between "Google accepted it" and "we wrote it down" is the
    window in which billable work can be lost."""
    path = tmp_path / "pending.json"
    record_submission(
        job_name="batches/xyz",
        model="gemini-3.7-flash",
        purpose="item_verification",
        prompts=["p"],
        path=path,
    )
    assert BatchJobStore.load(path).jobs[0].job_name == "batches/xyz"


def test_record_submission_appends_rather_than_replacing(tmp_path: Path) -> None:
    """A run that queues several chunks must end with several jobs recorded."""
    path = tmp_path / "pending.json"
    for name in ("batches/a", "batches/b", "batches/c"):
        record_submission(
            job_name=name,
            model="m",
            purpose="item_verification",
            prompts=["p"],
            path=path,
        )
    assert len(BatchJobStore.load(path).jobs) == 3


def test_describe_says_nothing_outstanding_when_empty() -> None:
    assert "no batch jobs outstanding" in BatchJobStore().describe()


def test_describe_counts_jobs_and_prompts() -> None:
    store = BatchJobStore()
    store.add(_job("batches/one", prompts=["a", "b"]))
    store.add(_job("batches/two", prompts=["c"]))
    text = store.describe()
    assert "2 job(s)" in text
    assert "3 prompt(s)" in text


def test_default_store_path_is_under_the_cache_directory() -> None:
    """It is derived state, so it belongs with the cache and not in the repo."""
    assert DEFAULT_BATCH_JOB_STORE.parts[0] == ".cache"


def test_size_counts_prompts() -> None:
    assert _job(prompts=["a", "b", "c"]).size == 3


@pytest.mark.parametrize("prompts", [[], ["only one"]])
def test_a_job_of_any_size_round_trips(tmp_path: Path, prompts: list[str]) -> None:
    path = tmp_path / "pending.json"
    BatchJobStore(jobs=[_job(prompts=prompts)]).save(path)
    assert BatchJobStore.load(path).jobs[0].prompts == prompts


def test_json_is_readable_by_a_person(tmp_path: Path) -> None:
    """An operator whose store is corrupt needs to read the job names out."""
    path = tmp_path / "pending.json"
    BatchJobStore(jobs=[_job()]).save(path)
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    assert raw["jobs"][0]["job_name"] == "batches/abc"


# ---------------------------------------------------------------------------
# The collector. A state machine over jobs that may be running, finished,
# failed or unreachable, driven by a fake SDK client.


class _CollectorBatches:
    """Fake ``client.batches`` for collection: ``get`` returns a scripted job."""

    def __init__(self, jobs_by_name: dict[str, Any], error: Exception | None = None) -> None:
        self._jobs = jobs_by_name
        self._error = error
        self.get_calls: list[str] = []

    def get(self, *, name: str) -> Any:
        self.get_calls.append(name)
        if self._error is not None:
            raise self._error
        return self._jobs[name]


class _CollectorSdk:
    def __init__(self, batches: _CollectorBatches) -> None:
        self.batches = batches


def _succeeded_job(texts: list[str]) -> Any:
    from google.genai import types as genai_types

    return genai_types.BatchJob(
        name="batches/one",
        state=genai_types.JobState.JOB_STATE_SUCCEEDED,
        dest=genai_types.BatchJobDestination(
            inlined_responses=[
                genai_types.InlinedResponse(
                    response=genai_types.GenerateContentResponse(
                        candidates=[
                            genai_types.Candidate(
                                content=genai_types.Content(parts=[genai_types.Part(text=text)])
                            )
                        ],
                        usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
                            prompt_token_count=1,
                            candidates_token_count=1,
                            total_token_count=2,
                        ),
                    )
                )
                for text in texts
            ]
        ),
    )


def _job_in_state(state: Any, name: str = "batches/one") -> Any:
    from google.genai import types as genai_types

    return genai_types.BatchJob(name=name, state=state)


def _collector_client(tmp_path: Path, sdk: _CollectorSdk) -> Any:
    from src.llm.client import GeminiLlmClient

    client = GeminiLlmClient(
        paid_api_key="k",
        free_api_key="k",
        cost_log_path=tmp_path / "cost.jsonl",
        cache_dir=tmp_path / "llm",
        batch_job_store_path=tmp_path / "pending.json",
    )
    client._get_sdk_client = lambda lane: sdk  # type: ignore[method-assign]
    return client


def test_collect_caches_a_finished_jobs_responses(tmp_path: Path) -> None:
    """The point of collecting: the next run finds the work already done."""
    store_path = tmp_path / "pending.json"
    BatchJobStore(
        jobs=[PendingBatchJob(job_name="batches/one", model="m", purpose="p", prompts=["a", "b"])]
    ).save(store_path)
    sdk = _CollectorSdk(_CollectorBatches({"batches/one": _succeeded_job(["A", "B"])}))
    client = _collector_client(tmp_path, sdk)

    report = client.collect_pending_batches()

    assert report.collected_jobs == 1
    assert report.cached_responses == 2
    assert client.cache.get(model="m", prompt="a") == "A"
    assert client.cache.get(model="m", prompt="b") == "B"
    assert BatchJobStore.load(store_path).jobs == []


def test_collect_leaves_a_running_job_alone(tmp_path: Path) -> None:
    """Still running means look again next time, not wait."""
    from google.genai import types as genai_types

    store_path = tmp_path / "pending.json"
    BatchJobStore(
        jobs=[PendingBatchJob(job_name="batches/one", model="m", purpose="p", prompts=["a"])]
    ).save(store_path)
    sdk = _CollectorSdk(
        _CollectorBatches({"batches/one": _job_in_state(genai_types.JobState.JOB_STATE_RUNNING)})
    )
    report = _collector_client(tmp_path, sdk).collect_pending_batches()

    assert report.still_running == 1
    assert report.collected_jobs == 0
    assert len(BatchJobStore.load(store_path).jobs) == 1


def test_collect_drops_a_terminally_failed_job(tmp_path: Path) -> None:
    """Re-polling something Google has finished with forever is a slow way of
    never finishing."""
    from google.genai import types as genai_types

    store_path = tmp_path / "pending.json"
    BatchJobStore(
        jobs=[PendingBatchJob(job_name="batches/one", model="m", purpose="p", prompts=["a"])]
    ).save(store_path)
    sdk = _CollectorSdk(
        _CollectorBatches({"batches/one": _job_in_state(genai_types.JobState.JOB_STATE_FAILED)})
    )
    report = _collector_client(tmp_path, sdk).collect_pending_batches()

    assert len(report.failed) == 1
    assert BatchJobStore.load(store_path).jobs == []


def test_collect_keeps_a_job_it_could_not_reach(tmp_path: Path) -> None:
    """One unreachable job must not stop the sweep, and must not be lost."""
    store_path = tmp_path / "pending.json"
    BatchJobStore(
        jobs=[PendingBatchJob(job_name="batches/one", model="m", purpose="p", prompts=["a"])]
    ).save(store_path)
    sdk = _CollectorSdk(_CollectorBatches({}, error=RuntimeError("network down")))
    report = _collector_client(tmp_path, sdk).collect_pending_batches()

    assert len(report.errors) == 1
    assert len(BatchJobStore.load(store_path).jobs) == 1


def test_collect_drops_a_job_whose_response_count_is_wrong(tmp_path: Path) -> None:
    """Responses are positional; a length mismatch means the pairing cannot be
    trusted, so nothing is cached from it."""
    store_path = tmp_path / "pending.json"
    BatchJobStore(
        jobs=[PendingBatchJob(job_name="batches/one", model="m", purpose="p", prompts=["a", "b"])]
    ).save(store_path)
    sdk = _CollectorSdk(_CollectorBatches({"batches/one": _succeeded_job(["only one"])}))
    client = _collector_client(tmp_path, sdk)

    report = client.collect_pending_batches()

    assert len(report.failed) == 1
    assert report.cached_responses == 0
    assert client.cache.get(model="m", prompt="a") is None
    assert BatchJobStore.load(store_path).jobs == []


def test_collect_on_an_empty_store_contacts_nobody(tmp_path: Path) -> None:
    sdk = _CollectorSdk(_CollectorBatches({}))
    report = _collector_client(tmp_path, sdk).collect_pending_batches()
    assert report.examined == 0
    assert sdk.batches.get_calls == []


def test_collect_writes_a_cost_row_per_response(tmp_path: Path) -> None:
    """CLAUDE.md rule 4 is about visibility: collected batch work is real spend
    and must appear in the log, on the batch mode that earns the discount."""
    store_path = tmp_path / "pending.json"
    BatchJobStore(
        jobs=[PendingBatchJob(job_name="batches/one", model="m", purpose="verify", prompts=["a"])]
    ).save(store_path)
    sdk = _CollectorSdk(_CollectorBatches({"batches/one": _succeeded_job(["A"])}))
    client = _collector_client(tmp_path, sdk)
    client.collect_pending_batches()

    rows = [
        json.loads(line)
        for line in (tmp_path / "cost.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert len(rows) == 1
    assert rows[0]["mode"] == "batch"
    assert rows[0]["lane"] == "paid"
    assert rows[0]["purpose"] == "verify"
    assert rows[0]["call_id"] == "batches/one"


def test_a_job_remembers_its_cache_slot_and_an_old_record_defaults_to_none(tmp_path: Path) -> None:
    """The second verification pass submits under ``cache_namespace="pass2"``;
    collection must write into that slot, not the default one, or the two
    passes' jobs overwrite each other (measured 2026-09-08)."""
    from src.llm.batch_jobs import BatchJobStore, PendingBatchJob, record_submission

    path = tmp_path / "jobs.json"
    record_submission(
        job_name="batches/p2",
        model="m",
        purpose="item_verification",
        prompts=["a"],
        cache_namespace="pass2",
        path=path,
    )
    record_submission(
        job_name="batches/p1", model="m", purpose="item_verification", prompts=["a"], path=path
    )
    loaded = BatchJobStore.load(path)
    by_name = {job.job_name: job for job in loaded.jobs}
    assert by_name["batches/p2"].cache_namespace == "pass2"
    assert by_name["batches/p1"].cache_namespace is None
    # A record written before the field existed.
    old = PendingBatchJob.model_validate(
        {"job_name": "batches/old", "model": "m", "purpose": "x", "prompts": ["a"]}
    )
    assert old.cache_namespace is None


def test_cache_key_kwargs_leave_the_default_slot_untouched() -> None:
    from src.llm.cache import LlmCache, cache_key_kwargs

    assert cache_key_kwargs(None) == {}
    assert cache_key_kwargs("pass2") == {"namespace": "pass2"}
    cache = LlmCache.__new__(LlmCache)
    default_key = cache._compute_key("m", "p", **cache_key_kwargs(None))
    assert default_key == cache._compute_key("m", "p")
    assert default_key != cache._compute_key("m", "p", **cache_key_kwargs("pass2"))
