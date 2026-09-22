// The session rules that mirror src/engine/session.py: Präteritum cards wait
// until the unit is in review, deferred units are listed apart, and the
// grader accepts a connector's near-synonyms in single-gap cards only.
import test from "node:test";
import assert from "node:assert/strict";

import { createEngine } from "../../web/lib/engine.js";
import { gradeCard } from "../../web/lib/grader.js";
import { deriveState, entryKey, makeReset, mergeEntries, parseJsonl, toJsonl } from "../../web/lib/log.js";
import { DEFAULT_SETTINGS, computeStats, eligiblePendingUnits, nextUnit, pickCard, sessionStart, unitsByStage } from "../../web/lib/session.js";

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

test("a reset starts the replay over, and the entry survives a round trip", () => {
  const d = deck(); const engine = createEngine();
  const review = { type: "review", seq: 1, ts: T0, unit_id: "vp:warten_auf", card_id: "w2", rating: "good", outcome: "exact", answers: ["warten", "auf"], expected: ["warten", "auf"], elapsed_ms: 1, deck_version: "t" };
  const mark = { type: "mark", seq: 2, ts: "2026-09-01T08:01:00Z", unit_id: "cn:trotzdem", known: true, source: "triage" };
  const before = deriveState([review, mark], engine);
  assert.ok(Object.keys(before.records).length && before.known.size);

  const reset = makeReset([review, mark], "restart", "2026-09-01T08:02:00Z");
  assert.equal(reset.type, "reset");
  const after = deriveState([review, mark, reset], engine);
  assert.deepEqual(after.records, {});
  assert.equal(after.known.size, 0);
  assert.equal(after.reviewTimes.length, 0);
  assert.equal(after.lastUnit, null);
  assert.equal(nextUnit(d, after, engine, DEFAULT_SETTINGS, "2026-09-01T08:03:00Z").unit_id, "vp:warten_auf");

  // it crosses the gist like any other line, and merges once
  const text = toJsonl([review, mark, reset]);
  assert.equal(parseJsonl(text).length, 3);
  assert.equal(entryKey(reset), `reset||${reset.ts}`);
  assert.equal(mergeEntries(parseJsonl(text), [reset]).length, 3);
});

test("a new unit is drawn from the next pool by rank", () => {
  const d = deck(); const engine = createEngine();
  const state = deriveState([], engine);
  assert.equal(nextUnit(d, state, engine, DEFAULT_SETTINGS, T0).unit_id, "vp:warten_auf");
  assert.equal(nextUnit(d, state, engine, DEFAULT_SETTINGS, T0, { choose: () => 1 }).unit_id, "cn:trotzdem");
  const tight = { ...DEFAULT_SETTINGS, newPool: 1 };
  assert.equal(nextUnit(d, state, engine, tight, T0, { choose: () => 1 }).unit_id, "vp:warten_auf");
});

test("a reset puts the counters back to zero", () => {
  const d = deck(); const engine = createEngine();
  const review = { type: "review", seq: 1, ts: T0, unit_id: "vp:warten_auf", card_id: "w2", rating: "good", outcome: "exact", answers: ["warten", "auf"], expected: ["warten", "auf"], elapsed_ms: 1, deck_version: "t" };
  const before = computeStats(d, deriveState([review], engine), [review], engine, T0);
  assert.equal(before.reviews_total, 1);
  assert.equal(before.reviews_today, 1);
  const entries = [review, makeReset([review], "restart", "2026-09-01T08:01:00Z")];
  const after = computeStats(d, deriveState(entries, engine), entries, engine, T0);
  assert.equal(after.reviews_total, 0);
  assert.equal(after.reviews_today, 0);
  assert.equal(after.streak_days, 0);
  assert.equal(after.retention_30d, null);
});

// -- sessions instead of minutes (owner, 2026-09-22) --------------------------

const rev = (seq, ts, unit_id, rating) => ({
  type: "review", seq, ts, unit_id, card_id: "c1", rating,
  outcome: rating === "again" ? "wrong" : "exact",
  answers: ["x"], expected: ["x"], elapsed_ms: 1, deck_version: "t",
});

test("sessionStart walks back while the gaps are small", () => {
  const gap = 60 * 60000;
  assert.equal(sessionStart([], T0, gap), new Date(T0).getTime());
  const recent = ["2026-09-01T07:30:00Z", "2026-09-01T07:40:00Z", "2026-09-01T07:55:00Z"];
  assert.equal(sessionStart(recent, T0, gap), new Date(recent[0]).getTime());
  // a long pause ends the sitting
  assert.equal(sessionStart(["2026-09-01T03:00:00Z", ...recent], T0, gap), new Date(recent[0]).getTime());
  assert.equal(sessionStart(["2026-08-31T23:00:00Z"], T0, gap), new Date(T0).getTime());
});

test("a pending unit waits for other cards, not for the clock", () => {
  const d = deck(); const engine = createEngine();
  const settings = { ...DEFAULT_SETTINGS, relearnSpacing: 3 };
  const entries = [rev(1, T0, "vp:warten_auf", "again")];
  let state = deriveState(entries, engine);
  const soon = "2026-09-01T08:00:30Z";
  assert.notEqual(state.records["vp:warten_auf"].state, "review");
  assert.deepEqual(eligiblePendingUnits(d, state, engine, settings, soon).map((u) => u.unit_id), []);

  entries.push(rev(2, "2026-09-01T08:00:20Z", "cn:trotzdem", "good"));
  entries.push(rev(3, "2026-09-01T08:00:30Z", "cn:trotzdem", "good"));
  entries.push(rev(4, "2026-09-01T08:00:40Z", "cn:trotzdem", "good"));
  state = deriveState(entries, engine);
  const later = "2026-09-01T08:01:00Z";
  assert.deepEqual(eligiblePendingUnits(d, state, engine, settings, later).map((u) => u.unit_id), ["vp:warten_auf"]);
  assert.ok(new Date(later).getTime() < new Date(state.records["vp:warten_auf"].due).getTime());
});

test("a unit pending from an earlier sitting skips the spacing", () => {
  const d = deck(); const engine = createEngine();
  const settings = { ...DEFAULT_SETTINGS, relearnSpacing: 3 };
  const state = deriveState([rev(1, T0, "vp:warten_auf", "again")], engine);
  const tomorrow = "2026-09-02T08:00:00Z";
  assert.deepEqual(eligiblePendingUnits(d, state, engine, settings, tomorrow).map((u) => u.unit_id), ["vp:warten_auf"]);
});

test("only the first retry is free of the day's budget", () => {
  const engine = createEngine();
  const entries = [rev(1, T0, "vp:warten_auf", "again")];
  let state = deriveState(entries, engine);
  assert.equal(state.budgetReviewTimes.length, 1);

  entries.push(rev(2, "2026-09-01T08:00:30Z", "vp:warten_auf", "again"));
  state = deriveState(entries, engine);
  assert.equal(state.reviewTimes.length, 2);
  assert.equal(state.budgetReviewTimes.length, 1);

  entries.push(rev(3, "2026-09-01T08:01:00Z", "vp:warten_auf", "again"));
  state = deriveState(entries, engine);
  assert.equal(state.reviewTimes.length, 3);
  assert.equal(state.budgetReviewTimes.length, 2);
});
