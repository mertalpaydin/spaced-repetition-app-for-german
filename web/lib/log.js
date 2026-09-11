// The review log and its replay, matching src/engine/review_log.py. Entries
// are the same JSON objects the Python client writes, so a log from either
// side replays here.

import { newRecord } from "./engine.js";

export function entryKey(e) { return `${e.type}|${e.unit_id}|${e.ts}`; }

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
      if (e && (e.type === "review" || e.type === "mark") && e.unit_id && e.ts) out.push(e);
    } catch (err) { /* a corrupt line never hides the rest */ }
  }
  return out;
}

export function toJsonl(entries) {
  return sortEntries(entries).map((e) => JSON.stringify(e)).join("\n") + (entries.length ? "\n" : "");
}

export function deriveState(entries, engine) {
  const state = {
    records: {}, known: new Set(), triaged: new Set(), lastCard: {}, ratings: {},
    firstReview: {}, reviewTimes: [], lastUnit: null,
  };
  for (const e of sortEntries(entries)) {
    if (e.type === "mark") {
      state.triaged.add(e.unit_id);
      if (e.known) state.known.add(e.unit_id); else state.known.delete(e.unit_id);
      continue;
    }
    const record = state.records[e.unit_id] || newRecord(e.unit_id, e.ts);
    state.records[e.unit_id] = engine.schedule(record, e.rating, e.ts);
    state.lastCard[e.unit_id] = e.card_id;
    (state.ratings[e.unit_id] ||= []).push(e.rating);
    if (!(e.unit_id in state.firstReview)) state.firstReview[e.unit_id] = e.ts;
    state.reviewTimes.push(e.ts);
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

export function makeMark(entries, unitId, known, source, now) {
  return { type: "mark", seq: nextSeq(entries), ts: new Date(now).toISOString(), unit_id: unitId, known, source };
}
