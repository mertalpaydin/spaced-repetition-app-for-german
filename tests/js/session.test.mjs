// The session rules that mirror src/engine/session.py: Präteritum cards wait
// until the unit is in review, deferred units are listed apart, and the
// grader accepts a connector's near-synonyms in single-gap cards only.
import test from "node:test";
import assert from "node:assert/strict";

import { createEngine } from "../../web/lib/engine.js";
import { gradeCard } from "../../web/lib/grader.js";
import { deriveState } from "../../web/lib/log.js";
import { DEFAULT_SETTINGS, nextUnit, pickCard, unitsByStage } from "../../web/lib/session.js";

const T0 = "2026-09-01T08:00:00Z";
const unit = (unit_id, rank, display_de, extra = {}) => ({ unit_id, kind: "verb_prep", display_de, rank, trivial: false, gloss_en: null, also_accepted: [], ...extra });
const card = (card_id, unit_id, sentence_de, answers, form_key = "Fin|Pres") => {
  const gaps = []; let pos = 0;
  for (const a of answers) { const start = sentence_de.indexOf(a, pos); gaps.push({ start, end: start + a.length, answer: a }); pos = start + a.length; }
  return { card_id, unit_id, sentence_de, gaps, answers, form_key, gloss_en: "gloss", needs_context: false, context_de: null };
};
const deck = () => {
  const units = [unit("vp:warten_auf", 1, "warten auf"), unit("cn:trotzdem", 2, "trotzdem", { kind: "connector" })];
  const cardsByUnit = {
    "vp:warten_auf": [card("p1", "vp:warten_auf", "Er wartete auf den Bus.", ["wartete", "auf"], "Fin|Past|3|Sing"), card("w2", "vp:warten_auf", "Wir warten auf dich.", ["warten", "auf"])],
    "cn:trotzdem": [card("t1", "cn:trotzdem", "Trotzdem kam sie.", ["Trotzdem"], "initial")],
  };
  return { units, byId: Object.fromEntries(units.map((u) => [u.unit_id, u])), cardsByUnit };
};

test("Präteritum cards wait until the unit is in review", () => {
  const d = deck(); const engine = createEngine();
  let state = deriveState([], engine);
  assert.equal(pickCard(d, d.byId["vp:warten_auf"], state).card_id, "w2");
  state.lastCard["vp:warten_auf"] = "w2";
  assert.equal(pickCard(d, d.byId["vp:warten_auf"], state).card_id, "w2");
  const review = (seq, ts) => ({ type: "review", seq, ts, unit_id: "vp:warten_auf", card_id: "w2", rating: "good", outcome: "exact", answers: ["warten", "auf"], expected: ["warten", "auf"], elapsed_ms: 1, deck_version: "t" });
  state = deriveState([review(1, T0), review(2, "2026-09-01T08:11:00Z")], engine);
  assert.equal(state.records["vp:warten_auf"].state, "review");
  assert.equal(pickCard(d, d.byId["vp:warten_auf"], state).card_id, "p1");
});

test("a deferred unit is skipped and listed apart until relearned", () => {
  const d = deck(); const engine = createEngine();
  const defer = { type: "mark", seq: 1, ts: T0, unit_id: "vp:warten_auf", known: true, source: "defer" };
  let state = deriveState([defer], engine);
  assert.equal(nextUnit(d, state, engine, DEFAULT_SETTINGS, T0).unit_id, "cn:trotzdem");
  let groups = unitsByStage(d, state, T0);
  assert.deepEqual(groups.deferred.map(([u]) => u.unit_id), ["vp:warten_auf"]);
  assert.deepEqual(groups.known, []);
  state = deriveState([defer, { type: "mark", seq: 2, ts: "2026-09-01T09:00:00Z", unit_id: "vp:warten_auf", known: false, source: "practice" }], engine);
  assert.equal(nextUnit(d, state, engine, DEFAULT_SETTINGS, T0).unit_id, "vp:warten_auf");
  assert.deepEqual(unitsByStage(d, state, T0).deferred, []);
});

test("near-synonyms count as correct in single-gap cards only", () => {
  const c = card("d1", "cn:deshalb", "Deshalb kam er.", ["Deshalb"], "initial");
  for (const typed of ["deswegen", "Deswegen", "daher"]) assert.equal(gradeCard(c, [typed], ["deswegen", "daher"]).rating, "good", typed);
  assert.equal(gradeCard(c, ["darum"], ["deswegen"]).rating, "again");
  const two = card("w1", "vp:warten_auf", "Er wartet auf den Bus.", ["wartet", "auf"]);
  assert.equal(gradeCard(two, ["hofft", "auf"], ["hofft"]).rating, "again");
});
