// The session rules that mirror src/engine/session.py: Präteritum cards wait
// until the unit is in review, deferred units are listed apart, and the
// grader accepts a connector's near-synonyms in single-gap cards only.
import test from "node:test";
import assert from "node:assert/strict";

import { createEngine } from "../../web/lib/engine.js";
import { gradeCard } from "../../web/lib/grader.js";
import { deriveState, entryKey, makeRequest, makeReset, mergeEntries, parseJsonl, toJsonl } from "../../web/lib/log.js";
import { reserveLetter, withAdded } from "../../web/lib/deck.js";
import { DEFAULT_SETTINGS, asksThisSession, budgetLeft, budgetSpentToday, computeStats, dueUnits, eligiblePendingUnits, introductionOrder, nextUnit, pickCard, requestedToIntroduce, reviewsToday, sessionStart, stabilityBand, unitsByStage } from "../../web/lib/session.js";

const T0 = "2026-09-01T08:00:00Z";
const unit_ = (unit_id, rank, display_de, extra = {}) => ({ unit_id, kind: "verb_prep", display_de, rank, trivial: false, gloss_en: null, also_accepted: [], ...extra });
const card = (card_id, unit_id, sentence_de, answers, form_key = "Fin|Pres") => {
  const gaps = []; let pos = 0;
  for (const a of answers) { const start = sentence_de.indexOf(a, pos); gaps.push({ start, end: start + a.length, answer: a }); pos = start + a.length; }
  return { card_id, unit_id, sentence_de, gaps, answers, form_key, gloss_en: "gloss", needs_context: false, context_de: null };
};
const deck = () => {
  const units = [unit_("vp:warten_auf", 1, "warten auf"), unit_("cn:trotzdem", 2, "trotzdem", { kind: "connector" })];
  const cardsByUnit = {
    "vp:warten_auf": [card("p1", "vp:warten_auf", "Er wartete auf den Bus.", ["wartete", "auf"], "Fin|Past|3|Sing"), card("w2", "vp:warten_auf", "Wir warten auf dich.", ["warten", "auf"])],
    "cn:trotzdem": [card("t1", "cn:trotzdem", "Trotzdem kam sie.", ["Trotzdem"], "initial")],
  };
  return { units, byId: Object.fromEntries(units.map((u) => [u.unit_id, u])), cardsByUnit };
};

// Owner's instruction of 2026-10-06: Präteritum is never shown. It used to
// be held back only until the unit reached review, so the learner met it
// eventually. The Python side asserts the same in tests/test_phase2_engine.py.
test("a Präteritum card is never shown, even once the unit is in review", () => {
  const d = deck(); const engine = createEngine();
  let state = deriveState([], engine);
  assert.equal(pickCard(d, d.byId["vp:warten_auf"], state).card_id, "w2");
  state.lastCard["vp:warten_auf"] = "w2";
  assert.equal(pickCard(d, d.byId["vp:warten_auf"], state).card_id, "w2");
  const review = (seq, ts) => ({ type: "review", seq, ts, unit_id: "vp:warten_auf", card_id: "w2", rating: "good", outcome: "exact", answers: ["warten", "auf"], expected: ["warten", "auf"], elapsed_ms: 1, deck_version: "t" });
  state = deriveState([review(1, T0), review(2, "2026-09-01T08:11:00Z")], engine);
  assert.equal(state.records["vp:warten_auf"].state, "review");
  assert.equal(pickCard(d, d.byId["vp:warten_auf"], state).card_id, "w2");
});

test("a unit whose only card is Präteritum stays answerable on an old deck", () => {
  const d = deck();
  d.cardsByUnit["vp:warten_auf"] = [card("p1", "vp:warten_auf", "Er wartete auf den Bus.", ["wartete", "auf"], "Fin|Past|3|Sing")];
  const state = deriveState([], createEngine());
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

test("a pending unit yields to other work until its spacing is met", () => {
  const d = deck(); const engine = createEngine();
  const roomy = { ...DEFAULT_SETTINGS, cardsPerDay: 40, relearnSpacing: 3 };
  const entries = [rev(1, T0, "vp:warten_auf", "again")];
  let state = deriveState(entries, engine);
  // a new unit comes first: warten has had no other reviews to space it
  assert.equal(nextUnit(d, state, engine, roomy, "2026-09-01T08:00:30Z").unit_id, "cn:trotzdem");

  for (let i = 2; i <= 4; i += 1) entries.push(rev(i, `2026-09-01T08:00:${10 * i}Z`, "cn:trotzdem", "good"));
  state = deriveState(entries, engine);
  const spaced = "2026-09-01T08:01:00Z";
  assert.equal(nextUnit(d, state, engine, roomy, spaced).unit_id, "vp:warten_auf");
  assert.ok(new Date(spaced).getTime() < new Date(state.records["vp:warten_auf"].due).getTime());
});

test("with nothing else to show, the pending unit comes back at once", () => {
  const d = deck(); const engine = createEngine();
  const tight = { ...DEFAULT_SETTINGS, cardsPerDay: 1, relearnSpacing: 3 };
  const state = deriveState([rev(1, T0, "vp:warten_auf", "again")], engine);
  const soon = "2026-09-01T08:01:00Z";
  assert.equal(nextUnit(d, state, engine, tight, soon).unit_id, "vp:warten_auf");
  assert.ok(new Date(soon).getTime() < new Date(state.records["vp:warten_auf"].due).getTime());
});

test("the budget and the statistics count a retry differently", () => {
  const engine = createEngine();
  const settings = { ...DEFAULT_SETTINGS, cardsPerDay: 40 };
  const entries = [rev(1, T0, "vp:warten_auf", "again"), rev(2, "2026-09-01T08:00:30Z", "vp:warten_auf", "again")];
  let state = deriveState(entries, engine);
  assert.equal(reviewsToday(state, T0), 2);
  assert.equal(budgetSpentToday(state, T0), 1);
  assert.equal(budgetLeft(state, settings, T0), 39);

  entries.push(rev(3, "2026-09-01T08:01:00Z", "vp:warten_auf", "again"));
  state = deriveState(entries, engine);
  assert.equal(reviewsToday(state, T0), 3);
  assert.equal(budgetSpentToday(state, T0), 2);
  assert.equal(budgetLeft(state, settings, T0), 38);
});

test("a requested unit is introduced before any mined one", () => {
  // The twin of test_a_requested_unit_is_introduced_before_any_mined_one in
  // tests/test_phase2_engine.py: same deck, same answer. Nothing else guards
  // the two engines from drifting apart (owner, 2026-09-22).
  const d = deck(); const engine = createEngine();
  d.units = d.units.map((u) => (u.unit_id === "cn:trotzdem" ? { ...u, requested_order: 1 } : u));
  d.byId = Object.fromEntries(d.units.map((u) => [u.unit_id, u]));
  const state = deriveState([], engine);
  assert.deepEqual(introductionOrder(d).map((u) => u.unit_id), ["cn:trotzdem", "vp:warten_auf"]);
  assert.equal(nextUnit(d, state, engine, DEFAULT_SETTINGS, T0).unit_id, "cn:trotzdem");

  // without the request, rank order decides as before
  const plain = deck();
  assert.deepEqual(introductionOrder(plain).map((u) => u.unit_id), ["vp:warten_auf", "cn:trotzdem"]);
  assert.equal(nextUnit(plain, state, engine, DEFAULT_SETTINGS, T0).unit_id, "vp:warten_auf");
});

// ---------------------------------------------------------------------------
// Words the learner adds from the page. The Python side of each of these is
// in tests/test_phase2_engine.py; the two must agree or a word added on the
// phone is taught in a different order on the laptop.
// ---------------------------------------------------------------------------

test("a word the learner added is introduced before anything the corpus ranked", () => {
  const d = deck(); const engine = createEngine();
  const added = unit_("wn:hähnchen", 99999, "das Hähnchen", { kind: "noun" });
  const entries = [makeRequest([], { unitId: "wn:hähnchen", origin: "generated", unit: added, cards: [card("h1", "wn:hähnchen", "Das Hähnchen ist fertig.", ["Hähnchen"])] }, T0)];
  const state = deriveState(entries, engine);

  assert.deepEqual(state.requested, ["wn:hähnchen"]);
  const full = withAdded(d, state);
  assert.equal(introductionOrder(full, state)[0].unit_id, "wn:hähnchen");
  assert.equal(full.cardsByUnit["wn:hähnchen"][0].card_id, "h1");
  // and without the learner's state it falls back to rank, as before
  assert.equal(introductionOrder(full)[0].unit_id, "vp:warten_auf");
});

test("a reserve word carries no payload: both devices fetch the same shard", () => {
  const engine = createEngine();
  const state = deriveState([makeRequest([], { unitId: "wn:bügeln", origin: "reserve" }, T0)], engine);
  assert.deepEqual(state.requested, ["wn:bügeln"]);
  assert.deepEqual(state.addedUnits, {});
  assert.equal(withAdded(deck(), state).byId["wn:bügeln"], undefined);
});

test("the deck wins when a written word is later mined for real", () => {
  const d = deck(); const engine = createEngine();
  const invented = unit_("vp:warten_auf", 99999, "erfunden");
  const state = deriveState([makeRequest([], { unitId: "vp:warten_auf", origin: "generated", unit: invented, cards: [card("x9", "vp:warten_auf", "Erfundener Satz hier.", ["Satz"])] }, T0)], engine);
  const full = withAdded(d, state);
  assert.equal(full.byId["vp:warten_auf"].display_de, "warten auf");
  assert.ok(!full.cardsByUnit["vp:warten_auf"].some((c) => c.card_id === "x9"));
});

test("adding the same word twice does not queue it twice", () => {
  const engine = createEngine();
  const first = makeRequest([], { unitId: "wn:bügeln", origin: "reserve" }, T0);
  const second = makeRequest([first], { unitId: "wn:bügeln", origin: "reserve" }, "2026-09-01T09:00:00Z");
  assert.deepEqual(deriveState([first, second], engine).requested, ["wn:bügeln"]);
});

test("a reset forgets the added words like everything else", () => {
  const engine = createEngine();
  const req = makeRequest([], { unitId: "wn:bügeln", origin: "reserve" }, T0);
  const state = deriveState([req, makeReset([req], "", "2026-09-01T10:00:00Z")], engine);
  assert.deepEqual(state.requested, []);
});

test("an entry type this client does not know is skipped, not fatal", () => {
  const engine = createEngine();
  const entries = parseJsonl([
    JSON.stringify({ type: "mark", seq: 1, ts: T0, unit_id: "cn:trotzdem", known: true, source: "triage" }),
    JSON.stringify({ type: "something_new", seq: 2, ts: "2026-09-01T09:00:00Z" }),
  ].join("\n"));
  const state = deriveState(entries, engine);
  assert.ok(state.known.has("cn:trotzdem"));
});

test("the reserve shard for a word follows from its folded first letter", () => {
  assert.equal(reserveLetter("Ärger"), "a");
  assert.equal(reserveLetter("bügeln"), "b");
  assert.equal(reserveLetter("1990er"), "_");
  assert.equal(reserveLetter(""), "_");
});

// ---------------------------------------------------------------------------
// 2026-10-06. Each of these pins something that was wrong in practice.
// ---------------------------------------------------------------------------

test("a request entry survives parseJsonl, which is how the added words were lost", () => {
  // The regression: parseJsonl had an allow-list of review/mark/reset, so
  // every "request" line was dropped. sync() replaces the local log with the
  // merge and pushGist writes the merge back, so one parse on one device
  // deleted the owner's added words everywhere.
  const unit = unit_("nn:hähnchen", 99999, "das Hähnchen", { kind: "noun" });
  const entry = makeRequest([], { unitId: "nn:hähnchen", origin: "generated", unit, cards: [] }, T0);
  const round = parseJsonl(toJsonl([entry]));
  assert.equal(round.length, 1, "a request entry must survive a round trip");
  assert.equal(round[0].type, "request");
  assert.equal(round[0].unit.display_de, "das Hähnchen");
  // and it still reaches the state after the trip
  assert.deepEqual(deriveState(round, createEngine()).requested, ["nn:hähnchen"]);
});

test("an added word is shown before the due queue, not after it", () => {
  // The owner added words and never saw them: with a hundred cards due, the
  // new-unit tier is days away (2026-10-06).
  const d = deck(); const engine = createEngine();
  const added = unit_("nn:hähnchen", 99999, "das Hähnchen", { kind: "noun" });
  const addedCard = card("h1", "nn:hähnchen", "Das Hähnchen ist fertig.", ["Hähnchen"]);
  const req = makeRequest([], { unitId: "nn:hähnchen", origin: "generated", unit: added, cards: [addedCard] }, T0);
  // warten is overdue and would otherwise win
  const review = { type: "review", seq: 2, ts: T0, unit_id: "vp:warten_auf", card_id: "w2", rating: "again", outcome: "wrong", answers: ["x"], expected: ["warten", "auf"], elapsed_ms: 1, deck_version: "t" };
  const state = deriveState([req, review], engine);
  const full = withAdded(d, state);
  const later = "2026-09-03T08:00:00Z";
  assert.ok(dueUnits(full, state, engine, later, 0).some((u) => u.unit_id === "vp:warten_auf"));
  assert.equal(nextUnit(full, state, engine, DEFAULT_SETTINGS, later).unit_id, "nn:hähnchen");
});

test("adding many words at once does not cost the day's reviews", () => {
  const d = deck(); const engine = createEngine();
  const entries = [];
  for (let i = 0; i < 9; i++) {
    const u = unit_(`nn:wort${i}`, 90000 + i, `das Wort ${i}`, { kind: "noun" });
    entries.push(makeRequest(entries, { unitId: u.unit_id, origin: "generated", unit: u, cards: [card(`c${i}`, u.unit_id, `Das Wort ${i} ist da.`, ["Wort"])] }, T0));
  }
  const state = deriveState(entries, engine);
  const full = withAdded(d, state);
  assert.equal(requestedToIntroduce(full, state, DEFAULT_SETTINGS, T0).length, DEFAULT_SETTINGS.requestedPerDay);
});

test("one unit is not asked for ever in a single sitting", () => {
  const d = deck(); const engine = createEngine();
  const wrong = (seq, ts) => ({ type: "review", seq, ts, unit_id: "vp:warten_auf", card_id: "w2", rating: "again", outcome: "wrong", answers: ["x", "auf"], expected: ["warten", "auf"], elapsed_ms: 1, deck_version: "t" });
  const stamps = ["2026-09-01T08:00:00Z", "2026-09-01T08:02:00Z", "2026-09-01T08:04:00Z"];
  const state = deriveState(stamps.map((ts, i) => wrong(i + 1, ts)), engine);
  const now = "2026-09-01T08:06:00Z";
  assert.equal(asksThisSession(state, DEFAULT_SETTINGS, now)["vp:warten_auf"], 3);
  // Three asks is the cap, so the next card is something else, or nothing,
  // but never warten again.
  const next = nextUnit(d, state, engine, DEFAULT_SETTINGS, now);
  assert.notEqual(next?.unit_id, "vp:warten_auf");
  // A fresh sitting the next day offers it again: the cap is per sitting.
  const tomorrow = "2026-09-02T08:00:00Z";
  assert.equal(nextUnit(d, state, engine, DEFAULT_SETTINGS, tomorrow).unit_id, "vp:warten_auf");
});

test("stability lands a unit in the right band", () => {
  assert.equal(stabilityBand(3), "fresh");
  assert.equal(stabilityBand(7), "young");
  assert.equal(stabilityBand(20.9), "young");
  assert.equal(stabilityBand(21), "mature");
  assert.equal(stabilityBand(89), "mature");
  assert.equal(stabilityBand(400), "solid");
});
