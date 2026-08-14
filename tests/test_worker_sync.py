"""Unit tests for Cloudflare Worker D1 sync protocol, schema, and client serialization."""

import json
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from src.contracts import TagStateModel, Topic
from src.engine.fsrs import FSRSRecord
from src.sync.client import ReviewLogEntry, SyncClient

NODE = shutil.which("node")
WORKER_ENTRY = (Path(__file__).resolve().parents[1] / "worker" / "src" / "index.js").as_posix()

requires_node = pytest.mark.skipif(NODE is None, reason="node is required to exercise the worker")


def test_worker_schema_and_config_exist() -> None:
    """Verify worker files, D1 schema, and wrangler.toml configuration."""
    worker_dir = Path("worker")
    assert (worker_dir / "wrangler.toml").exists()
    assert (worker_dir / "schema.sql").exists()
    assert (worker_dir / "src" / "index.js").exists()

    schema_text = (worker_dir / "schema.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS user_topic_states" in schema_text
    assert "CREATE TABLE IF NOT EXISTS user_fsrs_cards" in schema_text
    assert "CREATE TABLE IF NOT EXISTS review_logs" in schema_text
    # Idempotency key required by "duplicate review_log rows are idempotent"
    # (04-application.md stage 9).
    assert "UNIQUE (user_id, item_id, created_at)" in schema_text

    wrangler_text = (worker_dir / "wrangler.toml").read_text(encoding="utf-8")
    assert "SYNC_TOKEN" in wrangler_text
    # The placeholder must be documented but never a live, uncommented value.
    uncommented_lines = [
        line for line in wrangler_text.splitlines() if not line.strip().startswith("#")
    ]
    assert not any("SYNC_TOKEN" in line for line in uncommented_lines), (
        "wrangler.toml must not set a real SYNC_TOKEN value; it is a secret "
        "binding documented as a commented placeholder only"
    )


def test_sync_client_payload_serialization() -> None:
    """Test building outbound sync payload from TagStateModel and FSRSRecord."""
    now = datetime.now(UTC)
    topic_states = {
        "pronomen_personal_nom": TagStateModel(
            tag_id="pronomen_personal_nom",
            state="acquired",
            promotion_consecutive_passes=3,
            promotion_distinct_facets=["sg1", "sg2"],
            last_review_at=now,
        )
    }

    fsrs_records = {
        "item_001": FSRSRecord(
            card_id="item_001",
            state="review",
            due=now + timedelta(days=3),
            stability=3.5,
            difficulty=2.1,
            reps=2,
            lapses=0,
            last_review=now,
        )
    }

    payload = SyncClient.build_payload(
        user_id="user_test_123",
        topic_states=topic_states,
        fsrs_records=fsrs_records,
    )

    assert payload.user_id == "user_test_123"
    assert len(payload.topic_states) == 1
    assert payload.topic_states[0].topic_id == "pronomen_personal_nom"
    assert payload.topic_states[0].state == "acquired"
    assert len(payload.fsrs_cards) == 1
    assert payload.fsrs_cards[0].card_id == "item_001"
    assert payload.fsrs_cards[0].stability == 3.5


def test_sync_merge_review_logs_deduplicates_and_is_order_independent() -> None:
    """Union local and inbound review logs, deduped by (item_id, timestamp).

    review_log is append-only, so merging it must be a plain union: the
    same row submitted from both sides (a retried sync, or a duplicate
    push) collapses to one row, and the result does not depend on which
    side is passed as "local" vs "inbound".
    """
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    shared = ReviewLogEntry(
        item_id="item_1", topic_id="topic_a", is_correct=True, hint_level=0, timestamp=t0
    )
    local_only = ReviewLogEntry(
        item_id="item_2",
        topic_id="topic_a",
        is_correct=True,
        hint_level=0,
        timestamp=t0 + timedelta(minutes=1),
    )
    inbound_only = ReviewLogEntry(
        item_id="item_3",
        topic_id="topic_a",
        is_correct=False,
        hint_level=1,
        timestamp=t0 + timedelta(minutes=2),
    )
    # Same dedup key as `shared`, arriving as a duplicate submission from the
    # other side: must not produce a second row.
    duplicate = shared.model_copy()

    merged_a = SyncClient.merge_review_logs([shared, local_only], [duplicate, inbound_only])
    merged_b = SyncClient.merge_review_logs([inbound_only, duplicate], [local_only, shared])

    assert len(merged_a) == 3
    assert {e.dedup_key for e in merged_a} == {
        shared.dedup_key,
        local_only.dedup_key,
        inbound_only.dedup_key,
    }
    assert merged_a == merged_b


def test_sync_recompute_tag_states_from_merged_log_never_trusts_inbound_state() -> None:
    """tag_state is recomputed from the merged review_log, never merged directly.

    NOTE: this replaces the previous
    `test_sync_merge_inbound_conflict_resolution`, which asserted that
    `SyncClient.merge_inbound_topics` picks the topic record with the newer
    `last_review_at` timestamp and installs it as `tag_state` verbatim. That
    is exactly the pattern 04-application.md:363 forbids ("Never merge
    `tag_state` directly. It is derived data and merging derived data is how
    progress corrupts.") -- a peer could claim `state="acquired"` with a
    fabricated future timestamp and have it accepted with no evidence in
    `review_log` to support it. `merge_inbound_topics` has been removed;
    `SyncClient.recompute_tag_states` replaces it by rebuilding `tag_state`
    from a review_log of actual attempts, via the same `TopicStateManager`
    the rest of the engine uses. This test asserts the new behaviour is
    correct, per CLAUDE.md rule 7 (fix the test's expectation, do not delete
    it, when the old expectation encoded the bug).
    """
    topics = [
        Topic(
            id="topic_a",
            name_de="Topic A",
            cefr="A1",
            description="test topic",
            prereqs=[],
        )
    ]

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    # Three consecutive unhinted passes promote learning/ready -> acquired
    # (PROMOTION_CONSECUTIVE_PASSES). This is real evidence, unlike a bare
    # inbound state claim.
    review_log = [
        ReviewLogEntry(
            item_id=f"item_{i}",
            topic_id="topic_a",
            is_correct=True,
            hint_level=0,
            timestamp=t0 + timedelta(minutes=i),
        )
        for i in range(3)
    ]

    tag_states = SyncClient.recompute_tag_states(review_log, topics)

    assert tag_states["topic_a"].state == "acquired"
    assert tag_states["topic_a"].acquired_via == "earned"
    assert tag_states["topic_a"].attempts == 3
    assert tag_states["topic_a"].correct == 3

    # A peer merely *claiming* "acquired" with no supporting review_log rows
    # must never be trusted: recomputing from an empty/insufficient log
    # yields the taxonomy's own initial state, not whatever a file or peer
    # asserted.
    unsupported_states = SyncClient.recompute_tag_states([], topics)
    assert unsupported_states["topic_a"].state != "acquired"


def _run_worker_fetch(scenario_js: str) -> list[dict]:
    """Execute a scenario against the real worker module under Node and
    return the list of JSON-encoded results the scenario printed.

    There is no JS test runner in this repo, so this drives the actual
    `worker/src/index.js` module (not a reimplementation) through Node's
    built-in `fetch`/`Request`/`Response`, using a minimal in-memory fake of
    the D1 binding shaped like the real one (`prepare().bind()`, `batch()`).
    """
    assert NODE is not None
    harness = f"""
import worker from '{WORKER_ENTRY}';

class FakeStmt {{
  constructor(sql) {{ this.sql = sql; this.params = []; }}
  bind(...params) {{ this.params = params; return this; }}
}}

class FakeD1 {{
  constructor() {{ this.rows = []; }}
  prepare(sql) {{ return new FakeStmt(sql); }}
  async batch(stmts) {{
    for (const s of stmts) {{
      if (s.sql.includes('INSERT OR IGNORE INTO review_logs')) {{
        const key = JSON.stringify([s.params[0], s.params[1], s.params[9]]);
        if (this.rows.some(r => r.key === key)) continue;
        this.rows.push({{ key, params: s.params }});
      }}
    }}
    return {{ results: [] }};
  }}
}}

async function report(obj) {{
  console.log(JSON.stringify(obj));
}}

{scenario_js}
"""
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as f:
        f.write(harness)
        path = f.name
    try:
        proc = subprocess.run(
            [NODE, path],
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        Path(path).unlink(missing_ok=True)

    assert proc.returncode == 0, (
        f"worker harness failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


@requires_node
def test_worker_unauthenticated_request_is_rejected() -> None:
    """No Authorization header, and no configured token, must both be
    rejected with 401 before any D1 access -- the defect this fixes let any
    caller read or overwrite any user's rows by setting X-User-ID."""
    scenario = """
async function run() {
  const env = { SYNC_TOKEN: 'correct-secret', DB: new FakeD1() };

  const noHeader = await worker.fetch(
    new Request('https://example.com/sync', { method: 'POST', body: '{}' }), env, {}
  );
  await report({ case: 'no_header', status: noHeader.status, dbRows: env.DB.rows.length });

  const wrongToken = await worker.fetch(
    new Request('https://example.com/sync', {
      method: 'POST', headers: { Authorization: 'Bearer wrong' }, body: '{}',
    }), env, {}
  );
  await report({ case: 'wrong_token', status: wrongToken.status, dbRows: env.DB.rows.length });

  const unconfiguredEnv = { DB: new FakeD1() };
  const noSecretConfigured = await worker.fetch(
    new Request('https://example.com/sync', {
      method: 'POST', headers: { Authorization: 'Bearer anything' }, body: '{}',
    }), unconfiguredEnv, {}
  );
  await report({ case: 'no_secret_configured', status: noSecretConfigured.status });

  const overrideNoAuth = await worker.fetch(
    new Request('https://example.com/override', { method: 'POST' }), env, {}
  );
  await report({ case: 'override_no_auth', status: overrideNoAuth.status });
}
run();
"""
    results = _run_worker_fetch(scenario)
    by_case = {r["case"]: r for r in results}

    assert by_case["no_header"]["status"] == 401
    assert by_case["no_header"]["dbRows"] == 0
    assert by_case["wrong_token"]["status"] == 401
    assert by_case["wrong_token"]["dbRows"] == 0
    assert by_case["no_secret_configured"]["status"] == 401
    assert by_case["override_no_auth"]["status"] == 401


@requires_node
def test_worker_authenticated_request_is_accepted_and_health_is_public() -> None:
    """A correct bearer token is accepted, and the unauthenticated health
    check keeps working (it touches no user data or DB)."""
    scenario = """
async function run() {
  const env = { SYNC_TOKEN: 'correct-secret', DB: new FakeD1() };
  const authed = await worker.fetch(
    new Request('https://example.com/sync', {
      method: 'POST',
      headers: { Authorization: 'Bearer correct-secret' },
      body: JSON.stringify({ user_id: 'u1', review_events: [] }),
    }), env, {}
  );
  await report({ case: 'authed_sync', status: authed.status });

  const health = await worker.fetch(
    new Request('https://example.com/health', { method: 'GET' }), env, {}
  );
  await report({ case: 'health', status: health.status });
}
run();
"""
    results = _run_worker_fetch(scenario)
    by_case = {r["case"]: r for r in results}
    assert by_case["authed_sync"]["status"] == 200
    assert by_case["health"]["status"] == 200


@requires_node
def test_worker_method_guards_reject_wrong_verbs_on_every_route() -> None:
    """Every route accepts only its documented verb; a mismatch is 405, not
    a silent 200 from whatever branch happened to match the path."""
    scenario = """
async function run() {
  const env = { SYNC_TOKEN: 'correct-secret', DB: new FakeD1() };
  const auth = { Authorization: 'Bearer correct-secret' };

  const cases = [
    ['bank_delta_wrong_verb', '/bank/delta', 'POST'],
    ['override_wrong_verb', '/override', 'GET'],
    ['explain_wrong_verb', '/explain', 'GET'],
    ['grade_wrong_verb', '/grade', 'GET'],
    ['sync_wrong_verb', '/sync', 'GET'],
    ['sync_pull_wrong_verb', '/sync/pull', 'POST'],
    ['health_wrong_verb', '/health', 'POST'],
  ];

  for (const [name, path, method] of cases) {
    const res = await worker.fetch(
      new Request(`https://example.com${path}`, { method, headers: auth }), env, {}
    );
    await report({ case: name, status: res.status });
  }
}
run();
"""
    results = _run_worker_fetch(scenario)
    for r in results:
        assert r["status"] == 405, f"{r['case']} expected 405, got {r['status']}"


@requires_node
def test_worker_sync_writes_idempotent_review_logs() -> None:
    """Resubmitting the same review event (same item_id + client timestamp)
    inserts one review_logs row, not two -- the whole point of the
    (user_id, item_id, created_at) uniqueness key."""
    scenario = """
async function run() {
  const env = { SYNC_TOKEN: 'correct-secret', DB: new FakeD1() };
  const headers = { Authorization: 'Bearer correct-secret', 'Content-Type': 'application/json' };
  const event = {
    item_id: 'item_1', topic_id: 'topic_a', user_answer: 'dem',
    is_correct: true, hint_level: 0, fsrs_rating: 'good',
    timestamp: '2026-01-01T00:00:00Z',
  };

  const first = await worker.fetch(
    new Request('https://example.com/sync', {
      method: 'POST', headers, body: JSON.stringify({ user_id: 'u1', review_events: [event] }),
    }), env, {}
  );
  const second = await worker.fetch(
    new Request('https://example.com/sync', {
      method: 'POST', headers, body: JSON.stringify({ user_id: 'u1', review_events: [event] }),
    }), env, {}
  );

  await report({
    firstStatus: first.status,
    secondStatus: second.status,
    rowCount: env.DB.rows.length,
  });
}
run();
"""
    (result,) = _run_worker_fetch(scenario)
    assert result["firstStatus"] == 200
    assert result["secondStatus"] == 200
    assert result["rowCount"] == 1
