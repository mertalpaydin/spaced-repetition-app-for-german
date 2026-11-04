// The verb drill (owner, 2026-11-04): grading, its deck, and keeping its
// reviews out of the sentence deck's replay.
import test from "node:test";
import assert from "node:assert/strict";

import { createEngine } from "../../web/lib/engine.js";
import { deriveState, entriesForMode, makeReview } from "../../web/lib/log.js";
import { DEFAULT_SETTINGS, nextUnit } from "../../web/lib/session.js";
import { gradeVerb, verbDeck } from "../../web/lib/verbs.js";

const GEHEN = { unit_id: "pf:gehen", infinitive: "gehen", gloss_en: "to go", rank: 8, source_unit_id: "vb:gehen",
  praeteritum: ["ging"], auxiliary: ["ist"], partizip: ["gegangen"], weak: false };
const AUFSTEHEN = { unit_id: "pf:aufstehen", infinitive: "aufstehen", gloss_en: "to get up", rank: 90,
  source_unit_id: "sv:aufstehen", praeteritum: ["stand auf"], auxiliary: ["ist"], partizip: ["aufgestanden"], weak: false };
const FAHREN = { unit_id: "pf:fahren", infinitive: "fahren", gloss_en: "to drive", rank: 50, source_unit_id: "vb:fahren",
  praeteritum: ["fuhr"], auxiliary: ["ist", "hat"], partizip: ["gefahren"], weak: false };

test("all three right is good", () => {
  const r = gradeVerb(GEHEN, { praeteritum: "ging", auxiliary: "ist", partizip: "gegangen" });
  assert.equal(r.rating, "good");
  assert.deepEqual(r.gaps.map((g) => g.outcome), ["exact", "exact", "exact"]);
});

test("a typo is hard, a wrong form or auxiliary is again", () => {
  assert.equal(gradeVerb(GEHEN, { praeteritum: "ging", auxiliary: "ist", partizip: "gegnagen" }).rating, "hard");
  const weak = gradeVerb(GEHEN, { praeteritum: "gehte", auxiliary: "ist", partizip: "gegangen" });
  assert.equal(weak.rating, "again");
  assert.equal(weak.gaps[0].outcome, "wrong");
  const aux = gradeVerb(GEHEN, { praeteritum: "ging", auxiliary: "hat", partizip: "gegangen" });
  assert.equal(aux.rating, "again");
  assert.equal(aux.gaps[1].expected, "ist");
});

test("case, spacing and the infinitive of the auxiliary", () => {
  const r = gradeVerb(AUFSTEHEN, { praeteritum: "  Stand   auf ", auxiliary: "IST", partizip: "Aufgestanden" });
  assert.equal(r.rating, "good");
  // "sein" for "ist" is the right auxiliary in the wrong person
  const sein = gradeVerb(GEHEN, { praeteritum: "ging", auxiliary: "sein", partizip: "gegangen" });
  assert.equal(sein.gaps[1].outcome, "typo");
  assert.equal(sein.rating, "hard");
});

test("either auxiliary where both are standard; revealed is again", () => {
  assert.equal(gradeVerb(FAHREN, { praeteritum: "fuhr", auxiliary: "hat", partizip: "gefahren" }).rating, "good");
  const shown = gradeVerb(FAHREN, { praeteritum: null, auxiliary: null, partizip: null });
  assert.equal(shown.rating, "again");
  assert.deepEqual(shown.gaps.map((g) => g.outcome), ["revealed", "revealed", "revealed"]);
  assert.deepEqual(shown.gaps.map((g) => g.expected), ["fuhr", "ist", "gefahren"]);
});

test("the drill deck works with the scheduler and introduces by rank", () => {
  const deck = verbDeck({ verbs: [AUFSTEHEN, GEHEN, FAHREN] }, "vv1");
  const engine = createEngine();
  const state = deriveState([], engine);
  const first = nextUnit(deck, state, engine, { ...DEFAULT_SETTINGS, newPool: 1 }, new Date("2026-11-05T08:00:00Z"));
  assert.equal(first.unit_id, "pf:gehen");
  assert.equal(deck.cardsByUnit["pf:gehen"].length, 1);
});

test("each mode replays only its own entries; a reset belongs to both", () => {
  const t = new Date("2026-11-05T08:00:00Z");
  const log = [];
  log.push({ type: "reset", seq: 1, ts: t.toISOString(), note: "" });
  const fields = (unit) => ({ unit_id: unit, card_id: "c".repeat(12), rating: "good", outcome: "exact",
    answers: ["x"], expected: ["x"], elapsed_ms: 1, deck_version: "v" });
  log.push(makeReview(log, fields("vb:gehen"), new Date(t.getTime() + 60000)));
  log.push(makeReview(log, fields("pf:gehen"), new Date(t.getTime() + 120000)));
  assert.deepEqual(entriesForMode(log, "deck").map((e) => e.unit_id || ""), ["", "vb:gehen"]);
  assert.deepEqual(entriesForMode(log, "verbs").map((e) => e.unit_id || ""), ["", "pf:gehen"]);
  const state = deriveState(entriesForMode(log, "deck"), createEngine());
  assert.ok(!("pf:gehen" in state.records));
  assert.equal(state.reviewTimes.length, 1);
});
