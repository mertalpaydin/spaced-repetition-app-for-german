// The scheduler and stats, matching src/engine/session.py and stats.py.

import { entriesSinceReset } from "./log.js";

// newPool: a new unit is drawn at random from the next N by rank rather
// than always the very next one (feedback 2026-09-19). Matches
// src/engine/session.py Settings.
// relearnSpacing went from 3 to 8 and maxAsksPerSession arrived on
// 2026-10-06: at 3 a unit the learner kept getting wrong came back every
// fourth card, and nothing capped the total, so a sitting narrowed onto a
// handful of units. requestedPerDay is how many words added from the page
// jump the due queue. Matches Settings in src/engine/session.py.
export const DEFAULT_SETTINGS = { cardsPerDay: 40, newPerDay: null, learnAheadMinutes: 20, retention: 0.9, newPool: 50,
  relearnSpacing: 8, maxAsksPerSession: 3, sessionGapMinutes: 60, requestedPerDay: 5,
  requestedDelay: 10, extraCards: 10 };

// Stability bands for the Einheiten screen, in days. Matches
// STABILITY_BANDS in src/engine/session.py.
export const STABILITY_BANDS = [["fresh", 7], ["young", 21], ["mature", 90], ["solid", Infinity]];

export function stabilityBand(stability) {
  for (const [name, upper] of STABILITY_BANDS) if (stability < upper) return name;
  return STABILITY_BANDS[STABILITY_BANDS.length - 1][0];
}

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

// The order new units are introduced in. Three tiers: what the learner added
// from the page, newest last, because they asked for it a moment ago; then
// the units the owner asked for by hand in requested.yaml; then everything
// else by corpus frequency. Carried on the unit as requested_order rather than by rewriting
// rank, which is part of the deck's content hash and promises to mean
// frequency (owner, 2026-09-22). Matches introduction_order in
// src/engine/session.py; both suites pin the two against each other.
export function introductionOrder(deck, state = null) {
  const added = new Map((state?.requested || []).map((id, i) => [id, i]));
  return [...deck.units].sort((a, b) => {
    const aa = added.has(a.unit_id) ? added.get(a.unit_id) : Infinity;
    const ba = added.has(b.unit_id) ? added.get(b.unit_id) : Infinity;
    if (aa !== ba) return aa - ba;
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
// Units answered within the last relearnSpacing reviews of this sitting.
// Nothing is offered again until that many other units have gone by, in ANY
// tier. The spacing used to live in eligiblePendingUnits alone, so a unit
// answered wrong came back through the OVERDUE tier once its ten-minute FSRS
// due had passed, with no spacing at all (owner, 2026-10-07). Matches
// recently_shown in src/engine/session.py.
export function recentlyShown(state, settings, now) {
  const gap = settings.relearnSpacing ?? 0;
  if (gap <= 0) return new Set();
  const started = new Date(sessionStart(state.reviewTimes, now, (settings.sessionGapMinutes ?? 60) * 60000));
  const inSession = [];
  const order = state.reviewOrder || [];
  for (let i = 0; i < order.length; i++) {
    if (new Date(state.reviewTimes[i]) >= started) inSession.push(order[i]);
  }
  return new Set(inSession.slice(-gap));
}

// How often each unit has already been asked in this sitting. Matches
// asks_this_session in src/engine/session.py.
export function asksThisSession(state, settings, now) {
  const start = sessionStart(state.reviewTimes, now, (settings.sessionGapMinutes ?? 60) * 60000);
  const out = {};
  for (const [id, times] of Object.entries(state.reviewTimesByUnit || {})) {
    out[id] = times.filter((ts) => new Date(ts) >= new Date(start)).length;
  }
  return out;
}

// Words the learner added from the page that have never been reviewed. They
// jump the due queue: the learner typed the word a moment ago and the button
// said it would be taught next, which with a hundred cards due was false
// (owner, 2026-10-06). Matches requested_to_introduce in session.py.
export function requestedToIntroduce(deck, state, settings, now) {
  if (!state.requested?.length) return [];
  const today = dayOf(new Date(now).toISOString());
  const startedToday = state.requested.filter(
    (id) => state.firstReview[id] && dayOf(state.firstReview[id]) === today,
  ).length;
  const budget = (settings.requestedPerDay ?? 5) - startedToday;
  if (budget <= 0) return [];
  const out = [];
  for (const id of state.requested) {
    const unit = deck.byId[id];
    if (!unit || id in state.records || state.known.has(id)) continue;
    if (!isLearnable(deck, unit, state)) continue;
    // The learner looked this word up a moment ago, so a few other units go
    // by before it is asked. Counted in reviews since the request, so
    // closing the app does not skip the wait.
    const askedAt = state.requestedAt?.[id];
    if (askedAt) {
      const since = state.reviewTimes.filter((ts) => new Date(ts) > new Date(askedAt)).length;
      if (since < (settings.requestedDelay ?? 0)) continue;
    }
    out.push(unit);
    if (out.length >= budget) break;
  }
  return out;
}

export function nextUnit(deck, state, engine, settings, now, { overLimit = false, choose = null } = {}) {
  const last = state.lastUnit;
  const asks = asksThisSession(state, settings, now);
  // Spacing cannot stop a loop on its own: a unit the learner never gets
  // right stays eligible for ever, so the sitting narrows onto it. It keeps
  // its FSRS state and comes back tomorrow.
  const cap = settings.maxAsksPerSession ?? 0;
  const notExhausted = (units) => (cap <= 0 ? units : units.filter((u) => (asks[u.unit_id] || 0) < cap));
  const recent = recentlyShown(state, settings, now);
  const spaced = (units) => units.filter((u) => !recent.has(u.unit_id));

  const wanted = notLast(notExhausted(requestedToIntroduce(deck, state, settings, now)), last);
  if (wanted.length) return wanted[0];
  const overdue = notLast(spaced(notExhausted(dueUnits(deck, state, engine, now, 0))), last);
  if (overdue.length) return overdue[0];
  // A unit mid learning step comes back on spacing, not on the clock, and
  // ahead of any new unit: there is no point introducing more while the
  // learner is still getting this one wrong (owner, 2026-09-22).
  const pending = spaced(notExhausted(eligiblePendingUnits(deck, state, engine, settings, now))).filter((u) => u.unit_id !== last);
  if (pending.length) return pending[0];
  const withinBudget = overLimit || budgetLeft(state, settings, now) > 0;
  const underCap = settings.newPerDay === null || newUnitsStartedToday(state, now) < settings.newPerDay;
  if (withinBudget && (underCap || overLimit)) {
    const pool = [];
    for (const unit of introductionOrder(deck, state)) {
      if (unit.unit_id in state.records) continue;
      if (isLearnable(deck, unit, state)) {
        pool.push(unit);
        if (pool.length >= Math.max(settings.newPool || 1, 1)) break;
      }
    }
    if (pool.length) return pool[(choose ? choose(pool.length) : 0) % pool.length];
  }
  const ahead = spaced(notExhausted(dueUnits(deck, state, engine, now, settings.learnAheadMinutes * 60000))).filter((u) => u.unit_id !== last);
  if (ahead.length) return ahead[0];
  // Nothing else to interleave with: show the pending unit again rather than
  // stopping. Drilling what the learner just got wrong is the only useful
  // work left, and the ten-minute wait this replaces was the complaint.
  const looping = notExhausted(eligiblePendingUnits(deck, state, engine, settings, now, true));
  return looping.length ? looping[0] : null;
}

// A finite past-tense form in the gaps: Präteritum is a B1 form, so a unit
// still being learnt is shown in the present, the perfect and the infinitive
// first (feedback 2026-09-13). Matches src/engine/session.py.
export function isPraeteritum(card) { return (card.form_key || "").startsWith("Fin|Past"); }

export function pickCard(deck, unit, state) {
  let cards = showableCards(deck.cardsByUnit[unit.unit_id] || []);
  // Präteritum is never shown (owner, 2026-10-06). The card stage no longer
  // selects one, so this only matters for a deck built before that, which a
  // client can be running from cache for weeks. The fallback keeps a unit
  // whose only cards are Präteritum answerable on an old deck rather than
  // letting it vanish. Matches pick_card in src/engine/session.py.
  const easier = cards.filter((c) => !isPraeteritum(c));
  cards = easier.length ? easier : cards;
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
  const groups = { known: [], deferred: [], learning: [] };
  for (const [name] of STABILITY_BANDS) groups[name] = [];
  for (const id of state.known) {
    if (!deck.byId[id]) continue;
    groups[state.knownSource[id] === "defer" ? "deferred" : "known"].push([deck.byId[id], null]);
  }
  for (const [id, r] of Object.entries(state.records)) {
    const unit = deck.byId[id];
    if (!unit || state.known.has(id)) continue;
    if (r.state !== "review" || r.stability === null) groups.learning.push([unit, r.due]);
    else groups[stabilityBand(r.stability)].push([unit, r.due]);
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
