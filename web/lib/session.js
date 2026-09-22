// The scheduler and stats, matching src/engine/session.py and stats.py.

import { entriesSinceReset } from "./log.js";

// newPool: a new unit is drawn at random from the next N by rank rather
// than always the very next one (feedback 2026-09-19). Matches
// src/engine/session.py Settings.
export const DEFAULT_SETTINGS = { cardsPerDay: 40, newPerDay: null, learnAheadMinutes: 20, retention: 0.9, newPool: 50,
  relearnSpacing: 3, sessionGapMinutes: 60 };

const dayOf = (iso) => new Date(iso).toISOString().slice(0, 10);

export function showableCards(cards) {
  const glossed = cards.filter((c) => c.gloss_en !== null && c.gloss_en !== undefined);
  const withContext = glossed.filter((c) => !c.needs_context || c.context_de);
  return withContext.length ? withContext : glossed;
}

export function isLearnable(deck, unit, state) {
  return !unit.trivial && !state.known.has(unit.unit_id) && showableCards(deck.cardsByUnit[unit.unit_id] || []).length > 0;
}

export function reviewsToday(state, now) {
  const day = dayOf(now);
  return state.reviewTimes.filter((ts) => dayOf(ts) === day).length;
}

export function newUnitsStartedToday(state, now) {
  const day = dayOf(now);
  return Object.values(state.firstReview).filter((ts) => dayOf(ts) === day).length;
}

// What the day's budget has actually been charged for. Not the same as
// reviewsToday: the first retry of a unit mid step is left out, because
// finishing a step is not new work, so the two differ by the number of
// lapses (owner, 2026-09-22). Matches budget_spent_today in session.py.
export function budgetSpentToday(state, now) {
  const day = dayOf(now);
  return (state.budgetReviewTimes || []).filter((ts) => dayOf(ts) === day).length;
}

export function budgetLeft(state, settings, now) {
  return Math.max(settings.cardsPerDay - budgetSpentToday(state, now), 0);
}

export function dueUnits(deck, state, engine, now, horizonMs) {
  const limit = new Date(now).getTime() + horizonMs;
  const due = [];
  for (const [unitId, record] of Object.entries(state.records)) {
    const unit = deck.byId[unitId];
    if (!unit || state.known.has(unitId) || new Date(record.due).getTime() > limit) continue;
    due.push([engine.retrievability(record, now), unit.rank, unit]);
  }
  due.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  return due.map((d) => d[2]);
}

// When the current sitting began: walk the reviews back while the gaps
// between them stay under the session gap. Matches session_start in
// src/engine/session.py.
export function sessionStart(reviewTimes, now, gapMs) {
  let start = new Date(now).getTime();
  for (let i = reviewTimes.length - 1; i >= 0; i -= 1) {
    const ts = new Date(reviewTimes[i]).getTime();
    if (start - ts > gapMs) break;
    start = ts;
  }
  return start;
}

// Units mid learning or relearning step that may be shown again now. Their
// FSRS due is ten minutes out, and waiting for it is what let the day's
// budget strand a unit the learner had just failed (owner, 2026-09-22):
// eligibility is spacing instead of the clock. A unit left pending from an
// earlier sitting skips the wait. Matches eligible_pending_units in
// src/engine/session.py.
export function eligiblePendingUnits(deck, state, engine, settings, now, ignoreSpacing = false) {
  const gapMs = (settings.sessionGapMinutes ?? 60) * 60000;
  const spacing = settings.relearnSpacing ?? 3;
  const started = sessionStart(state.reviewTimes, now, gapMs);
  const out = [];
  for (const [unitId, record] of Object.entries(state.records)) {
    const unit = deck.byId[unitId];
    if (!unit || state.known.has(unitId) || record.state === "review") continue;
    const last = record.last_review ? new Date(record.last_review).getTime() : null;
    if (!ignoreSpacing && last !== null && last >= started) {
      const since = state.reviewTimes.filter((ts) => new Date(ts).getTime() > last).length;
      if (since < spacing) continue;
    }
    out.push([engine.retrievability(record, now), unit.rank, unit]);
  }
  out.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  return out.map((d) => d[2]);
}

// The order new units are introduced in: the units the owner asked for by
// hand first, in the order he listed them, then everything else by corpus
// frequency. Carried on the unit as requested_order rather than by rewriting
// rank, which is part of the deck's content hash and promises to mean
// frequency (owner, 2026-09-22). Matches introduction_order in
// src/engine/session.py; both suites pin the two against each other.
export function introductionOrder(deck) {
  return [...deck.units].sort((a, b) => {
    const ao = a.requested_order ?? Infinity;
    const bo = b.requested_order ?? Infinity;
    return ao - bo || a.rank - b.rank;
  });
}

function notLast(units, last) {
  const others = units.filter((u) => u.unit_id !== last);
  return others.length ? others : units;
}

// `choose(n)` picks an index in [0, n) among the new units on offer; the
// page passes a random one. The default picks the first, so a replay and
// the tests stay deterministic.
export function nextUnit(deck, state, engine, settings, now, { overLimit = false, choose = null } = {}) {
  const last = state.lastUnit;
  const overdue = notLast(dueUnits(deck, state, engine, now, 0), last);
  if (overdue.length) return overdue[0];
  // A unit mid learning step comes back on spacing, not on the clock, and
  // ahead of any new unit: there is no point introducing more while the
  // learner is still getting this one wrong (owner, 2026-09-22).
  const pending = eligiblePendingUnits(deck, state, engine, settings, now).filter((u) => u.unit_id !== last);
  if (pending.length) return pending[0];
  const withinBudget = overLimit || budgetLeft(state, settings, now) > 0;
  const underCap = settings.newPerDay === null || newUnitsStartedToday(state, now) < settings.newPerDay;
  if (withinBudget && (underCap || overLimit)) {
    const pool = [];
    for (const unit of introductionOrder(deck)) {
      if (unit.unit_id in state.records) continue;
      if (isLearnable(deck, unit, state)) {
        pool.push(unit);
        if (pool.length >= Math.max(settings.newPool || 1, 1)) break;
      }
    }
    if (pool.length) return pool[(choose ? choose(pool.length) : 0) % pool.length];
  }
  const ahead = dueUnits(deck, state, engine, now, settings.learnAheadMinutes * 60000).filter((u) => u.unit_id !== last);
  if (ahead.length) return ahead[0];
  // Nothing else to interleave with: show the pending unit again rather than
  // stopping. Drilling what the learner just got wrong is the only useful
  // work left, and the ten-minute wait this replaces was the complaint.
  const looping = eligiblePendingUnits(deck, state, engine, settings, now, true);
  return looping.length ? looping[0] : null;
}

// A finite past-tense form in the gaps: Präteritum is a B1 form, so a unit
// still being learnt is shown in the present, the perfect and the infinitive
// first (feedback 2026-09-13). Matches src/engine/session.py.
export function isPraeteritum(card) { return (card.form_key || "").startsWith("Fin|Past"); }

export function pickCard(deck, unit, state) {
  let cards = showableCards(deck.cardsByUnit[unit.unit_id] || []);
  const record = state.records[unit.unit_id];
  if (!record || record.state !== "review") {
    const easier = cards.filter((c) => !isPraeteritum(c));
    cards = easier.length ? easier : cards;
  }
  if (!cards.length) return null;
  if (cards.length === 1) return cards[0];
  const last = state.lastCard[unit.unit_id];
  const idx = cards.findIndex((c) => c.card_id === last);
  return cards[idx >= 0 ? (idx + 1) % cards.length : 0];
}

export function untriagedUnits(deck, state) {
  return deck.units.filter((u) => !u.trivial && !state.triaged.has(u.unit_id) && !(u.unit_id in state.records));
}

export function unitsByStage(deck, state, now) {
  const groups = { known: [], deferred: [], learning: [], young: [], mature: [] };
  for (const id of state.known) {
    if (!deck.byId[id]) continue;
    groups[state.knownSource[id] === "defer" ? "deferred" : "known"].push([deck.byId[id], null]);
  }
  for (const [id, r] of Object.entries(state.records)) {
    const unit = deck.byId[id];
    if (!unit || state.known.has(id)) continue;
    if (r.state !== "review" || r.stability === null) groups.learning.push([unit, r.due]);
    else if (r.stability < 21) groups.young.push([unit, r.due]);
    else groups.mature.push([unit, r.due]);
  }
  for (const items of Object.values(groups)) {
    items.sort((a, b) => ((a[1] || now) < (b[1] || now) ? -1 : (a[1] || now) > (b[1] || now) ? 1 : a[0].rank - b[0].rank));
  }
  return groups;
}

export function computeStats(deck, state, entries, engine, now) {
  const s = { known: state.known.size, learning: 0, young: 0, mature: 0, due_now: 0, new_remaining: 0, reviews_today: 0, reviews_total: 0, streak_days: 0, retention_30d: null, coverage: [] };
  const nowMs = new Date(now).getTime();
  for (const [id, r] of Object.entries(state.records)) {
    if (state.known.has(id) || !deck.byId[id]) continue;
    if (r.state !== "review" || r.stability === null) s.learning++;
    else if (r.stability < 21) s.young++;
    else s.mature++;
    if (new Date(r.due).getTime() <= nowMs) s.due_now++;
  }
  s.new_remaining = deck.units.filter((u) => !(u.unit_id in state.records) && isLearnable(deck, u, state)).length;
  const reviews = entriesSinceReset(entries).filter((e) => e.type === "review");
  s.reviews_total = reviews.length;
  const days = new Map();
  for (const e of reviews) days.set(dayOf(e.ts), (days.get(dayOf(e.ts)) || 0) + 1);
  const today = dayOf(now);
  s.reviews_today = days.get(today) || 0;
  let d = new Date(today);
  if (!days.has(today)) d = new Date(d.getTime() - 86400000);
  while (days.has(d.toISOString().slice(0, 10))) { s.streak_days++; d = new Date(d.getTime() - 86400000); }
  const recent = reviews.filter((e) => new Date(e.ts).getTime() >= nowMs - 30 * 86400000);
  if (recent.length) s.retention_30d = Math.round((recent.filter((e) => e.rating !== "again").length / recent.length) * 1000) / 1000;
  const bands = new Map();
  for (const u of deck.units) {
    const band = Math.floor((u.rank - 1) / 500) * 500 + 1;
    const b = bands.get(band) || { band, seen: 0, total: 0 };
    b.total++;
    if (u.unit_id in state.records || state.known.has(u.unit_id)) b.seen++;
    bands.set(band, b);
  }
  s.coverage = [...bands.values()].sort((a, b) => a.band - b.band);
  return s;
}
