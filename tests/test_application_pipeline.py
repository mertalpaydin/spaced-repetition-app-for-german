"""Unit and system tests for Stage 10 automation deficits, caps, and Stage 11 live LLM features."""

import json
import sys
from pathlib import Path

import pytest
from src.contracts import BatchId, BatchStatus, CandidateItem, Distractor, GenerationRequest
from src.generation.batch_client import CostTracker, MockBatchClient, run_ingest, run_submit
from src.generation.deficits import (
    MIN_BATCH_THRESHOLD,
    NIGHTLY_ITEM_CAP,
    SAFETY_FACTOR,
    compute_deficit,
    compute_topic_deficits,
)
from src.generation.pilot import (
    ALL_VERIFICATION_CLASSES,
    DEFAULT_PILOT_CLASSES,
    build_pilot_requests,
    run_pilot,
    select_pilot_topics,
)
from src.llm.client import CostLogRow, GeminiLlmClient
from src.llm.minimal_pairs import MinimalPairGenerator
from src.llm.production_grader import ProductionGrader
from src.llm.provider import MockLlmClient
from src.taxonomy.loader import load_taxonomy


class FakeBatchClient:
    """Injectable fake ``BatchClient`` returning pre-built ``CandidateItem``s per
    topic, standing in for a real (or mock) transport so ingest/pilot wiring
    can be tested end to end without a live key -- deliberately including
    items the verification chain is expected to reject.
    """

    def __init__(self, candidates_by_topic: dict[str, list[CandidateItem]]) -> None:
        self._by_topic = candidates_by_topic
        self.submitted_batches: dict[BatchId, list[GenerationRequest]] = {}
        self.batch_statuses: dict[BatchId, BatchStatus] = {}

    def submit(self, requests: list[GenerationRequest]) -> BatchId:
        batch_id = f"fake_batch_{len(self.submitted_batches)}"
        if not self.submitted_batches:
            batch_id = "fake_batch_0"
        # Idempotent on content, matching the real clients' contract.
        for existing_id, existing_requests in self.submitted_batches.items():
            if existing_requests == requests:
                return existing_id
        self.submitted_batches[batch_id] = requests
        self.batch_statuses[batch_id] = "completed"
        return batch_id

    def poll(self, batch_id: BatchId) -> BatchStatus:
        return self.batch_statuses[batch_id]

    def retrieve(self, batch_id: BatchId) -> list[CandidateItem]:
        requests = self.submitted_batches[batch_id]
        items: list[CandidateItem] = []
        for req in requests:
            items.extend(self._by_topic.get(req.topic_id, []))
        return items


def _good_candidate(topic_id: str = "dativ_nach_praeposition") -> CandidateItem:
    """A candidate that should pass the full verification chain (mirrors
    ``data/fixtures/verification/known_good.jsonl``'s ``kg_a2_002``)."""
    return CandidateItem(
        topic_id=topic_id,
        type="cloze_free",
        difficulty=1,
        prompt="Das Buch liegt auf ___ Tisch.",
        proposed_answer="dem",
        distractors=[
            Distractor(text="den", implied_topic_id="kasus_akkusativ_formen"),
            Distractor(text="des", implied_topic_id="kasus_genitiv_formen"),
            Distractor(text="das", implied_topic_id="artikel_bestimmt_nom"),
        ],
        carrier_lemmas=["Buch", "liegen", "Tisch"],
    )


def _topic_leaking_candidate(topic_id: str = "dativ_nach_praeposition") -> CandidateItem:
    """A candidate the topic-leak layer must reject: names the grammar point."""
    return CandidateItem(
        topic_id=topic_id,
        type="cloze_free",
        difficulty=1,
        prompt="Setze den Satz in den Dativ: Das Buch liegt auf ___ Tisch.",
        proposed_answer="dem",
        distractors=[
            Distractor(text="den"),
            Distractor(text="des"),
            Distractor(text="das"),
        ],
    )


def test_deficit_zero_when_stock_exceeds_projected_demand() -> None:
    """When bank stock exceeds ceil(projected_demand * SAFETY_FACTOR), deficit is 0."""
    stock = 50
    projected_demand = 30
    assert compute_deficit(projected_demand, stock) == 0


def test_deficit_accounts_for_difficulty_tier_separately() -> None:
    """Deficits are computed across distinct difficulty tiers (1, 2, 3), independently."""
    demand_and_stock = {
        ("dativ_nach_praeposition", 1): (10, 20),
        ("dativ_nach_praeposition", 2): (10, 5),
        ("dativ_nach_praeposition", 3): (10, 0),
    }
    deficits = {
        d.difficulty: d.deficit for d in compute_topic_deficits(demand_and_stock, safety_factor=1.0)
    }

    assert deficits.get(1, 0) == 0
    assert deficits[2] == 5
    assert deficits[3] == 10


def test_monthly_spend_ceiling_blocks_generation(tmp_path: Path) -> None:
    """A cost log already at the ceiling blocks nightly submission: zero batches
    submitted, graceful log, exit 0. A budget stop is normal operation, not an
    error (docs/04-application.md)."""
    log_file = tmp_path / "cost_log.jsonl"
    llm_client = GeminiLlmClient(
        spend_ceiling_usd=0.50,
        cost_log_path=log_file,
        cache_dir=tmp_path / "cache",
    )
    llm_client._log_cost(
        CostLogRow(
            model="gemini-3.7-flash",
            lane="paid",
            prompt_tokens=5_000_000,
            completion_tokens=2_000_000,
            cost_usd=0.55,
        )
    )

    db_path = tmp_path / "bank.db"  # deliberately does not exist
    state_path = tmp_path / "generation_state.json"

    exit_code = run_submit(db_path=db_path, state_path=state_path, llm_client=llm_client)

    assert exit_code == 0
    assert not state_path.exists(), "spend ceiling must block submission before any batch is built"


def test_nightly_item_cap_enforced() -> None:
    """A deficit calculation returning far more than the cap generates at most CAP items."""
    demand_and_stock = {(f"topic_{i}", 1): (10_000, 0) for i in range(50)}
    deficits = compute_topic_deficits(demand_and_stock, safety_factor=SAFETY_FACTOR)
    from src.generation.deficits import build_generation_requests

    requests = build_generation_requests(
        deficits,
        eligible_types_by_topic={f"topic_{i}": ["cloze_free"] for i in range(50)},
    )

    total_requested = sum(r.count for r in requests)
    assert total_requested <= NIGHTLY_ITEM_CAP
    assert total_requested > MIN_BATCH_THRESHOLD


def test_ingest_on_pending_batch_exits_zero(tmp_path: Path) -> None:
    """Ingest on a batch that has not completed yet exits 0 (retry tomorrow), not a failure."""
    state_path = tmp_path / "generation_state.json"
    request = GenerationRequest(
        topic_id="dativ_nach_praeposition", count=6, difficulty=1, item_types=["cloze_free"]
    )
    batch_client = MockBatchClient()
    batch_id = batch_client.submit([request])
    batch_client.rate_limit_simulations.add(batch_id)  # simulate: still processing
    state_path.write_text(
        json.dumps({"batch_id": batch_id, "requests": [request.model_dump()]}), encoding="utf-8"
    )

    exit_code = run_ingest(state_path=state_path, batch_client=batch_client)
    assert exit_code == 0


def test_ingest_with_no_submitted_batch_exits_zero(tmp_path: Path) -> None:
    """Ingest before anything has ever been submitted is harmless, not a KeyError crash."""
    state_path = tmp_path / "generation_state.json"  # never written
    exit_code = run_ingest(state_path=state_path)
    assert exit_code == 0


def test_production_grading_dimensions_isolated() -> None:
    """Target structure detection is evaluated independently from naturalness/accuracy."""
    grader = ProductionGrader(provider=MockLlmClient())
    res = grader.grade_production(
        target_topic_id="nebensatz_weil_da",
        cefr="B1",
        prompt_instruction="Bilde einen Satz mit 'weil'.",
        student_submission="Ich lerne Deutsch, weil ich gern reise.",
    )

    assert res.target_structure_used is True
    assert res.grammatical_accuracy == 1.0
    assert res.naturalness == 1.0
    assert res.is_pass is True


def test_minimal_pair_members_share_confusion_group() -> None:
    """Minimal pair drills contrast confusable items from the same confusion group."""
    generator = MinimalPairGenerator()
    drill = generator.get_or_generate_drill("wechselpraepositionen")

    assert drill.confusion_group == "wechselpraepositionen"
    assert drill.target_a == "dem"
    assert drill.target_b == "den"
    assert "Dativ" in drill.explanation_a
    assert "Akkusativ" in drill.explanation_b


def test_cost_tracker_total_reflects_recorded_batches() -> None:
    """CostTracker sums recorded usage across batches (sanity check for the ceiling test above)."""
    tracker = CostTracker()
    tracker.record_usage("batch_a", "gemini-3.7-flash", 1_000_000, 500_000)
    tracker.record_usage("batch_b", "gemini-3.5-flash-lite", 1_000_000, 500_000)
    assert tracker.total_cost_usd == sum(r.estimated_cost_usd for r in tracker.records)
    assert len(tracker.records) == 2


# ==============================================================================
# BREAK 2: retrieved candidates must be verified and inserted, not discarded.
# ==============================================================================


def test_run_ingest_verifies_and_inserts_accepted_items_only(tmp_path: Path) -> None:
    """Accepted candidates land in the bank with ``facet`` and
    ``confusion_group`` populated; rejected candidates (here, a topic leak)
    never do."""
    from src.bank.storage import SqliteItemBank

    db_path = tmp_path / "bank.db"
    state_path = tmp_path / "state.json"

    request = GenerationRequest(
        topic_id="dativ_nach_praeposition", count=2, difficulty=1, item_types=["cloze_free"]
    )
    fake_client = FakeBatchClient(
        {"dativ_nach_praeposition": [_good_candidate(), _topic_leaking_candidate()]}
    )
    batch_id = fake_client.submit([request])
    state_path.write_text(
        json.dumps({"batch_id": batch_id, "requests": [request.model_dump()]}), encoding="utf-8"
    )

    exit_code = run_ingest(state_path=state_path, batch_client=fake_client, db_path=db_path)
    assert exit_code == 0

    bank = SqliteItemBank(db_path)
    all_items = bank.get_all_items()

    # Only the good candidate made it in; the topic-leaking one did not.
    assert len(all_items) == 1
    inserted = all_items[0]
    assert inserted.prompt == "Das Buch liegt auf ___ Tisch."
    assert inserted.accepted_answers == ["dem"]
    assert inserted.facet is not None
    assert inserted.confusion_group == "kasus_wechselpraeposition"

    for item in all_items:
        assert "dativ" not in item.prompt.lower() or "___" in item.prompt


def test_run_ingest_prints_honest_summary_with_rejection_reasons(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The printed summary reports retrieved, accepted, rejected by reason,
    inserted and duplicates -- not just a raw retrieved count."""
    db_path = tmp_path / "bank.db"
    state_path = tmp_path / "state.json"

    request = GenerationRequest(
        topic_id="dativ_nach_praeposition", count=2, difficulty=1, item_types=["cloze_free"]
    )
    fake_client = FakeBatchClient(
        {"dativ_nach_praeposition": [_good_candidate(), _topic_leaking_candidate()]}
    )
    batch_id = fake_client.submit([request])
    state_path.write_text(
        json.dumps({"batch_id": batch_id, "requests": [request.model_dump()]}), encoding="utf-8"
    )

    run_ingest(state_path=state_path, batch_client=fake_client, db_path=db_path)
    captured = capsys.readouterr()

    assert "Ingested 2 candidate items" in captured.out
    assert "Accepted by verification chain: 1" in captured.out
    assert "Rejected by verification chain: 1" in captured.out
    assert "topic_leak" in captured.out
    assert "Inserted into bank: 1" in captured.out
    assert "Duplicates" in captured.out

    # Re-ingesting the same batch a second time: the verification chain's own
    # deduplication layer now catches the good candidate against the item
    # already in the bank (a near-identical prompt), so nothing new is
    # inserted -- the honest summary reports it as a chain rejection
    # ("duplicate"), not silently as a no-op.
    run_ingest(state_path=state_path, batch_client=fake_client, db_path=db_path)
    captured2 = capsys.readouterr()
    assert "Inserted into bank: 0" in captured2.out
    assert "duplicate: 1" in captured2.out


def test_run_ingest_unknown_topic_is_rejected_not_silently_dropped(tmp_path: Path) -> None:
    """A candidate whose topic_id is not in the taxonomy is counted as
    rejected (diagnosable), never inserted."""
    from src.bank.storage import SqliteItemBank

    db_path = tmp_path / "bank.db"
    state_path = tmp_path / "state.json"

    request = GenerationRequest(
        topic_id="totally_unknown_topic_zzz", count=1, difficulty=1, item_types=["cloze_free"]
    )
    unknown_item = CandidateItem(
        topic_id="totally_unknown_topic_zzz",
        type="cloze_free",
        difficulty=1,
        prompt="Ich sehe ___ Mann.",
        proposed_answer="den",
        distractors=[Distractor(text="der"), Distractor(text="dem"), Distractor(text="des")],
    )
    fake_client = FakeBatchClient({"totally_unknown_topic_zzz": [unknown_item]})
    batch_id = fake_client.submit([request])
    state_path.write_text(
        json.dumps({"batch_id": batch_id, "requests": [request.model_dump()]}), encoding="utf-8"
    )

    exit_code = run_ingest(state_path=state_path, batch_client=fake_client, db_path=db_path)
    assert exit_code == 0

    bank = SqliteItemBank(db_path)
    assert bank.get_all_items() == []


# ==============================================================================
# NEW: the stage-4 pilot command.
# ==============================================================================


def test_select_pilot_topics_spreads_across_cefr_bands() -> None:
    """Topic selection covers every CEFR band present in the taxonomy, not
    just whichever topics sort first overall."""
    topics = load_taxonomy()
    selected = select_pilot_topics(topics, topics_per_cefr=2)

    cefrs_present = {t.cefr for t in topics}
    cefrs_selected = {t.cefr for t in selected}
    assert cefrs_selected == cefrs_present

    for cefr in cefrs_present:
        count_available = len([t for t in topics if t.cefr == cefr])
        count_selected = len([t for t in selected if t.cefr == cefr])
        assert count_selected == min(2, count_available)


def test_select_pilot_topics_default_classes_excludes_semantic() -> None:
    """By default (no ``--classes`` override) a pilot never draws a 'semantic'
    topic: docs/audits/generation-track-plan.md 'Topic triage' establishes
    that class has no mechanically checkable answer, so it must be an opt-in,
    not something a plain pilot run stumbles into."""
    topics = load_taxonomy()
    assert any(t.verification_class == "semantic" for t in topics), (
        "expected at least one semantic topic in the real taxonomy for this test to be meaningful"
    )

    selected = select_pilot_topics(topics, topics_per_cefr=100)  # every topic per band

    assert selected, "expected a non-empty selection"
    assert all(t.verification_class != "semantic" for t in selected)
    assert all(t.verification_class in DEFAULT_PILOT_CLASSES for t in selected)


def test_select_pilot_topics_classes_filter_selects_only_requested_classes() -> None:
    """``classes`` restricts the pool to exactly the requested
    ``verification_class`` values, nothing else."""
    topics = load_taxonomy()

    selected = select_pilot_topics(topics, topics_per_cefr=100, classes=("lexical_table",))

    assert selected, "expected at least one lexical_table topic"
    assert all(t.verification_class == "lexical_table" for t in selected)


def test_select_pilot_topics_classes_none_includes_semantic() -> None:
    """``classes=None`` disables the class filter entirely, so a caller can
    still explicitly opt into every verification class including 'semantic'."""
    topics = load_taxonomy()

    selected = select_pilot_topics(topics, topics_per_cefr=100, classes=None)

    assert any(t.verification_class == "semantic" for t in selected)
    assert {t.verification_class for t in selected} <= set(ALL_VERIFICATION_CLASSES)


def test_select_pilot_topics_classes_composes_with_cefr() -> None:
    """The ``classes`` and ``cefr`` filters compose: a run scoped to one CEFR
    band and one verification class only ever returns topics matching BOTH."""
    topics = load_taxonomy()

    selected = select_pilot_topics(topics, cefr="B2", classes=("semantic",))

    assert selected, "expected at least one B2 semantic topic (e.g. modalpartikeln)"
    assert all(t.cefr == "B2" and t.verification_class == "semantic" for t in selected)

    # And the same class filter against a band with no matching topics raises,
    # rather than silently returning an empty (and useless) pilot run.
    all_a1_classes = {t.verification_class for t in topics if t.cefr == "A1"}
    assert "semantic" not in all_a1_classes, (
        "expected A1 to have no semantic topics for this test to be meaningful"
    )
    with pytest.raises(ValueError, match="No topics at CEFR level"):
        select_pilot_topics(topics, cefr="A1", classes=("semantic",))


def test_select_pilot_topics_all_topics_ignores_topics_per_cefr() -> None:
    """``--topics-per-cefr`` defaults to 3, so a plain pilot run only ever
    covers 12 topics (3 per band x 4 bands) out of the whole taxonomy.
    ``all_topics=True`` selects EVERY topic passing the class filter in
    every band, regardless of ``topics_per_cefr``."""
    topics = load_taxonomy()

    selected = select_pilot_topics(topics, topics_per_cefr=3, all_topics=True)

    expected = [t for t in topics if t.verification_class in DEFAULT_PILOT_CLASSES]
    assert {t.id for t in selected} == {t.id for t in expected}
    assert len(selected) > 12, "expected far more than the topics_per_cefr=3 sample size"

    for cefr in {t.cefr for t in expected}:
        count_available = len([t for t in expected if t.cefr == cefr])
        count_selected = len([t for t in selected if t.cefr == cefr])
        assert count_selected == count_available


def test_select_pilot_topics_all_topics_composes_with_classes() -> None:
    """``all_topics`` composes with ``classes``: it widens the topics-per-band
    sampling, not the class filter, which still applies first."""
    topics = load_taxonomy()

    selected = select_pilot_topics(topics, classes=("lexical_table",), all_topics=True)

    assert selected, "expected at least one lexical_table topic"
    assert all(t.verification_class == "lexical_table" for t in selected)
    expected = [t for t in topics if t.verification_class == "lexical_table"]
    assert {t.id for t in selected} == {t.id for t in expected}


def test_select_pilot_topics_all_topics_composes_with_cefr() -> None:
    """When ``cefr`` is also given, ``all_topics`` has nothing further to add:
    a CEFR-scoped run already selects every topic in that one band."""
    topics = load_taxonomy()

    without_all_topics = select_pilot_topics(topics, cefr="B1")
    with_all_topics = select_pilot_topics(topics, cefr="B1", all_topics=True)

    assert [t.id for t in with_all_topics] == [t.id for t in without_all_topics]


def test_step5_all_topics_flag_is_wired_to_select_pilot_topics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``scripts/step5_pilot_generation.py --all-topics`` must actually reach
    ``select_pilot_topics``, not just parse -- assert on the real argument
    passed through ``run_pilot``, not on the parser alone."""
    import scripts.step5_pilot_generation as step5

    captured: dict[str, object] = {}

    def fake_run_pilot(**kwargs: object) -> object:
        captured.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setattr(step5, "run_pilot", fake_run_pilot)
    monkeypatch.setattr(sys, "argv", ["step5_pilot_generation.py", "--all-topics"])

    with pytest.raises(SystemExit):
        step5.main()

    assert captured.get("all_topics") is True


def test_step5_parse_classes_accepts_the_default_and_rejects_garbage() -> None:
    """scripts/step5_pilot_generation.py's --classes parser mirrors
    --difficulties: valid comma-separated verification classes parse to a
    tuple, and anything outside the allowed set exits loudly instead of being
    passed through unvalidated."""
    from scripts.step5_pilot_generation import _parse_classes

    assert _parse_classes(",".join(DEFAULT_PILOT_CLASSES)) == DEFAULT_PILOT_CLASSES
    assert _parse_classes("semantic") == ("semantic",)
    assert _parse_classes("computable, structural") == ("computable", "structural")

    with pytest.raises(SystemExit, match="Invalid --classes value"):
        _parse_classes("not_a_real_class")


def test_build_pilot_requests_spreads_across_topics_not_one_topic() -> None:
    """A pilot request set must not concentrate every item on a single topic:
    every sampled topic contributes at least one request."""
    topics = load_taxonomy()
    pilot_topics = select_pilot_topics(topics, topics_per_cefr=2)

    requests = build_pilot_requests(pilot_topics, item_count=40)

    assert sum(r.count for r in requests) == 40
    topics_requested = {r.topic_id for r in requests}
    # With only 40 items spread over >= 8 topics x 3 difficulties, every
    # sampled topic should appear at least once (round-robin coverage).
    assert len(topics_requested) >= min(len(pilot_topics), 40)


def test_build_pilot_requests_never_exceeds_item_count() -> None:
    """The total requested item count matches exactly what was asked for,
    never more (a hard bound the pilot cap check depends on)."""
    topics = load_taxonomy()[:5]
    requests = build_pilot_requests(topics, item_count=13)
    assert sum(r.count for r in requests) == 13


def test_run_pilot_refuses_to_exceed_the_item_cap(tmp_path: Path) -> None:
    """Asking for more items than the cap must refuse outright, before any
    batch client is even constructed."""
    with pytest.raises(ValueError, match="exceeds the item cap"):
        run_pilot(
            item_count=500,
            item_cap=200,
            db_path=tmp_path / "bank.db",
            review_path=tmp_path / "review.jsonl",
            batch_client=FakeBatchClient({}),
        )


def _write_pilot_taxonomy(path: Path) -> list[dict[str, object]]:
    """A tiny two-topic, two-CEFR-band taxonomy (both topics share
    ``dativ_nach_praeposition``'s real ``morph_spec``/``syntax_tags``, so the
    same hand-built good/bad candidate pair is valid evidence for both) so the
    pilot's end-to-end acceptance/rejection outcome is deterministic without
    depending on which real topics the full 87-topic taxonomy happens to sort
    first."""
    import yaml

    topics = [
        {
            "id": "pilot_topic_a1",
            "name_de": "Pilot-Testthema A1",
            "cefr": "A1",
            "description": "Pilot test topic, A1 band.",
            "confusion_group": "pilot_confusion_a1",
            "eligible_types": ["cloze_free"],
            "requires_context": False,
            "morph_spec": {"Case": "Dat"},
            "verification_class": "computable",
            "syntax_tags": {"Pos": "Prep"},
        },
        {
            "id": "pilot_topic_a2",
            "name_de": "Pilot-Testthema A2",
            "cefr": "A2",
            "description": "Pilot test topic, A2 band.",
            "confusion_group": "pilot_confusion_a2",
            "eligible_types": ["cloze_free"],
            "requires_context": False,
            "morph_spec": {"Case": "Dat"},
            "verification_class": "computable",
            "syntax_tags": {"Pos": "Prep"},
        },
    ]
    path.write_text(yaml.safe_dump(topics, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return topics


def test_run_pilot_respects_item_cap_and_writes_review_file(tmp_path: Path) -> None:
    """A pilot run at or under the cap generates the requested spread across
    topics and CEFR levels, inserts accepted items, and writes a reviewable
    file of exactly the accepted set."""
    from src.bank.storage import SqliteItemBank

    taxonomy_path = tmp_path / "taxonomy.yaml"
    raw_topics = _write_pilot_taxonomy(taxonomy_path)
    topic_ids = [str(t["id"]) for t in raw_topics]

    # Every sampled topic yields one good, verifiable item plus one that the
    # chain must reject, so the pilot exercises both outcomes end to end.
    candidates_by_topic = {
        topic_id: [_good_candidate(topic_id), _topic_leaking_candidate(topic_id)]
        for topic_id in topic_ids
    }
    fake_client = FakeBatchClient(candidates_by_topic)

    db_path = tmp_path / "bank.db"
    review_path = tmp_path / "review.jsonl"

    report = run_pilot(
        item_count=len(topic_ids),  # exactly one request per topic
        topics_per_cefr=1,
        difficulties=(1,),
        db_path=db_path,
        taxonomy_path=taxonomy_path,
        review_path=review_path,
        item_cap=NIGHTLY_ITEM_CAP,
        batch_client=fake_client,
    )

    assert report.requested_item_count <= NIGHTLY_ITEM_CAP
    assert set(report.topics_used) == set(topic_ids)
    assert report.accepted == len(topic_ids)
    assert report.rejected == len(topic_ids)
    assert report.rejected_by_reason.get("topic_leak") == len(topic_ids)
    assert report.inserted == len(topic_ids)
    assert report.cost_usd_incurred == 0.0  # no llm_client: nothing billed
    assert report.ran_live is False

    bank = SqliteItemBank(db_path)
    all_items = bank.get_all_items()
    assert len(all_items) == len(topic_ids)
    for item in all_items:
        assert item.facet is not None
        assert item.confusion_group is not None

    assert review_path.exists()
    review_lines = [json.loads(line) for line in review_path.read_text().splitlines() if line]
    assert len(review_lines) == len(topic_ids)
    for row in review_lines:
        assert row["facet"] is not None
        assert row["confusion_group"] is not None
        assert row["_pilot_batch_id"] == report.batch_id


def test_run_pilot_reports_cost_from_the_cost_log_not_an_estimate(tmp_path: Path) -> None:
    """The reported cost is the delta the client's own cost log recorded
    across the run, not a synthesised estimate."""
    from src.generation.batch_client import GeminiBatchClient

    llm_client = GeminiLlmClient(
        free_api_key="test-key",
        cost_log_path=tmp_path / "cost_log.jsonl",
        cache_dir=tmp_path / "cache",
    )

    def fake_transport(
        *, model: str, prompt: str, lane: str, mode: str, purpose: str
    ) -> tuple[str, int, int]:
        return json.dumps({"items": []}), 10, 10

    llm_client._call_transport = fake_transport  # type: ignore[method-assign]
    batch_client = GeminiBatchClient(llm_client)

    report = run_pilot(
        item_count=3,
        topics_per_cefr=1,
        db_path=tmp_path / "bank.db",
        review_path=tmp_path / "review.jsonl",
        llm_client=llm_client,
        batch_client=batch_client,
    )

    # Free lane is unbilled by design, so the honest incurred cost is 0.0 --
    # but it must be read from the client's own spend delta, not guessed.
    spend_after = llm_client.get_month_to_date_spend()
    assert report.cost_usd_incurred == round(spend_after, 6)
