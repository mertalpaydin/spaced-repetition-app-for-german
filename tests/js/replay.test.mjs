// The JavaScript engine must replay a log to the same state as the Python
// engine: data/fixtures/fsrs/replay_fixture.json was produced by
// src/engine (see the generator in tests/test_web.py).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { createEngine } from "../../web/lib/engine.js";
import { deriveState, mergeEntries, parseJsonl, toJsonl } from "../../web/lib/log.js";

const here = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(readFileSync(join(here, "..", "..", "data", "fixtures", "fsrs", "replay_fixture.json"), "utf-8"));

test("a log replays to the Python engine's state", () => {
  const engine = createEngine();
  const state = deriveState(fixture.log, engine);
  assert.deepEqual([...state.known].sort(), fixture.known);
  for (const [unitId, exp] of Object.entries(fixture.expected)) {
    const got = state.records[unitId];
    assert.ok(got, unitId);
    assert.equal(got.state, exp.state, `${unitId} state`);
    assert.equal(new Date(got.due).getTime(), new Date(exp.due).getTime(), `${unitId} due`);
    assert.ok(Math.abs(got.stability - exp.stability) < 1e-6, `${unitId} stability ${got.stability} vs ${exp.stability}`);
    assert.ok(Math.abs(got.difficulty - exp.difficulty) < 1e-6, `${unitId} difficulty`);
    assert.equal(got.reps, exp.reps, `${unitId} reps`);
    assert.equal(got.lapses, exp.lapses, `${unitId} lapses`);
    assert.ok(Math.abs(engine.retrievability(got, fixture.now) - exp.retrievability) < 1e-3, `${unitId} retrievability`);
  }
});

test("jsonl round trip and merge dedupe by type, unit and time", () => {
  const text = toJsonl(fixture.log);
  const back = parseJsonl(text + "{broken\n\n");
  assert.equal(back.length, fixture.log.length);
  const merged = mergeEntries(back, fixture.log.slice(0, 10));
  assert.equal(merged.length, fixture.log.length);
});
