// The review log and its replay, matching src/engine/review_log.py. Entries
// are the same JSON objects the Python client writes, so a log from either
// side replays here.

import { newRecord } from "./engine.js";

// A reset carries no unit, so the empty string stands in for one; the
// Python side builds the same key (src/engine/review_log.entry_key).
export function entryKey(e) { return `${e.type}|${e.unit_id || ""}|${e.ts}`; }

export function sortEntries(entries) {
  return [...entries].sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : a.seq - b.seq));
}

// Two devices' logs as one: the same entry (type, unit, time) counts once.
export function mergeEntries(...logs) {
  const seen = new Set();
  const out = [];
  for (const log of logs) {
    for (const e of log) {
      const k = entryKey(e);
      if (seen.has(k)) continue;
      seen.add(k);
      out.push(e);
    }
  }
  return sortEntries(out);
}

export function parseJsonl(text) {
  const out = [];
  for (const line of text.split("\n")) {
    const s = line.trim();
    if (!s) continue;
    try {
      const e = JSON.parse(s);
      const known = e && (e.type === "review" || e.type === "mark" || e.type === "reset");
      if (known && e.ts && (e.type === "reset" || e.unit_id)) out.push(e);
    } catch (err) { /* a corrupt line never hides the rest */ }
  }
  return out;
}

export function toJsonl(entries) {
  return sortEntries(entries).map((e) => JSON.stringify(e)).join("\n") + (entries.length ? "\n" : "");
}

function freshState() {
  return {
    records: {}, known: new Set(), knownSource: {}, triaged: new Set(), lastCard: {}, ratings: {},
    firstReview: {}, reviewTimes: [], budgetReviewTimes: [], retriesPending: {}, lastUnit: null,
    // Words the learner added from the page, oldest first, and the units and
    // cards that exist only here because no exported shard holds them.
    // Matches LearnerState in src/engine/review_log.py.
    requested: [], addedUnits: {}, addedCards: {},
  };
}

// Whether a review counts against the day's budget. A unit's first review
// and the one that lapses it both count: that is new work. The first retry
// while it is mid step does not, so the budget cannot strand a learner in the
// middle of relearning. Every retry after that counts again, so failing the
// same unit over and over drains the budget instead of piling new units on
// top of it (owner, 2026-09-22). Matches _spends_budget in review_log.py.
function spendsBudget(state, unitId, before) {
  if (!before || before.state === "review") { state.retriesPending[unitId] = 0; return true; }
  const retries = state.retriesPending[unitId] || 0;
  state.retriesPending[unitId] = retries + 1;
  return retries > 0;
}

export function deriveState(entries, engine) {
  let state = freshState();
  for (const e of sortEntries(entries)) {
    // A restart: everything before it is history, the replay starts over.
    if (e.type === "reset") { state = freshState(); continue; }
    // A word the learner added. The payload is only what the other device
    // cannot look up for itself: a reserve word with cards carries nothing,
    // because both devices fetch the same committed shard.
    if (e.type === "request") {
      if (!state.requested.includes(e.unit_id)) state.requested.push(e.unit_id);
      if (e.unit) state.addedUnits[e.unit_id] = e.unit;
      if (e.cards && e.cards.length) state.addedCards[e.unit_id] = e.cards;
      continue;
    }
    if (e.type === "mark") {
      state.triaged.add(e.unit_id);
      if (e.known) { state.known.add(e.unit_id); state.knownSource[e.unit_id] = e.source; }
      else { state.known.delete(e.unit_id); delete state.knownSource[e.unit_id]; }
      continue;
    }
    const before = state.records[e.unit_id];
    const record = before || newRecord(e.unit_id, e.ts);
    state.records[e.unit_id] = engine.schedule(record, e.rating, e.ts);
    state.lastCard[e.unit_id] = e.card_id;
    (state.ratings[e.unit_id] ||= []).push(e.rating);
    if (!(e.unit_id in state.firstReview)) state.firstReview[e.unit_id] = e.ts;
    state.reviewTimes.push(e.ts);
    if (spendsBudget(state, e.unit_id, before)) state.budgetReviewTimes.push(e.ts);
    if (state.records[e.unit_id].state === "review") delete state.retriesPending[e.unit_id];
    state.lastUnit = e.unit_id;
  }
  return state;
}

export function nextSeq(entries) {
  return entries.reduce((m, e) => Math.max(m, e.seq || 0), 0) + 1;
}

export function makeReview(entries, fields, now) {
  return { type: "review", seq: nextSeq(entries), ts: new Date(now).toISOString(), ...fields };
}

// What still counts: the entries after the last restart. Matches
// src/engine/review_log.entries_since_reset.
export function entriesSinceReset(entries) {
  const ordered = sortEntries(entries);
  for (let i = ordered.length - 1; i >= 0; i--) {
    if (ordered[i].type === "reset") return ordered.slice(i + 1);
  }
  return ordered;
}

export function makeReset(entries, note, now) {
  return { type: "reset", seq: nextSeq(entries), ts: new Date(now).toISOString(), note };
}

export function makeMark(entries, unitId, known, source, now) {
  return { type: "mark", seq: nextSeq(entries), ts: new Date(now).toISOString(), unit_id: unitId, known, source };
}

export function makeRequest(entries, { unitId, origin, unit = null, cards = [] }, now) {
  const entry = { type: "request", seq: nextSeq(entries), ts: new Date(now).toISOString(), unit_id: unitId, origin };
  if (unit) entry.unit = unit;
  if (cards.length) entry.cards = cards;
  return entry;
}
