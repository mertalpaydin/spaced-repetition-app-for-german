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
    firstReview: {}, reviewTimes: [], lastUnit: null,
  };
}

export function deriveState(entries, engine) {
  let state = freshState();
  for (const e of sortEntries(entries)) {
    // A restart: everything before it is history, the replay starts over.
    if (e.type === "reset") { state = freshState(); continue; }
    if (e.type === "mark") {
      state.triaged.add(e.unit_id);
      if (e.known) { state.known.add(e.unit_id); state.knownSource[e.unit_id] = e.source; }
      else { state.known.delete(e.unit_id); delete state.knownSource[e.unit_id]; }
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
