// The FSRS engine, a direct port of the py-fsrs 6 scheduler as configured in
// src/engine/fsrs.py: default FSRS-6 parameters, retention 0.9, one learning
// step of ten minutes, one relearning step of ten minutes, no fuzz. Records
// have the same JSON shape as the Python FSRSRecord so a log replays to the
// identical state on both sides (tests/js/replay.test.mjs pins it against a
// Python fixture).
//
// It is a port rather than a wrapper around ts-fsrs because the two libraries
// differ in details that move due dates: py-fsrs measures elapsed time in
// whole 24-hour spans and ts-fsrs in calendar days, and ts-fsrs enforces an
// ordering between the hard, good and easy intervals that py-fsrs does not.

export const DEFAULT_PARAMETERS = [
  0.212, 1.2931, 2.3065, 8.2956, 6.4133, 0.8334, 3.0194, 0.001, 1.8722, 0.1666, 0.796,
  1.4835, 0.0614, 0.2629, 1.6483, 0.6014, 1.8729, 0.5425, 0.0912, 0.0658, 0.1542,
];

const STABILITY_MIN = 0.001;
const MIN_DIFFICULTY = 1.0;
const MAX_DIFFICULTY = 10.0;
const DAY_MS = 86400000;
const MINUTE_MS = 60000;
const RATING = { again: 1, hard: 2, good: 3, easy: 4 };

export function newRecord(unitId, due) {
  return {
    card_id: unitId, state: "learning", due: toIso(due), stability: null, difficulty: null,
    step: 0, reps: 0, lapses: 0, last_review: null,
  };
}

function toIso(d) { return (d instanceof Date ? d : new Date(d)).toISOString(); }

// Python rounds half to even; Math.round rounds half up.
function roundHalfEven(x) {
  const f = Math.floor(x);
  const diff = x - f;
  if (diff > 0.5) return f + 1;
  if (diff < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;
}

export function createEngine({
  retention = 0.9, learningStepsMin = [10], relearningStepsMin = [10], maximumInterval = 36500, w = DEFAULT_PARAMETERS,
} = {}) {
  const decay = -w[20];
  const factor = Math.pow(0.9, 1 / decay) - 1;
  const learningSteps = learningStepsMin.map((m) => m * MINUTE_MS);
  const relearningSteps = relearningStepsMin.map((m) => m * MINUTE_MS);

  const clampD = (d) => Math.min(Math.max(d, MIN_DIFFICULTY), MAX_DIFFICULTY);
  const clampS = (s) => Math.max(s, STABILITY_MIN);

  function initialStability(rating) { return clampS(w[rating - 1]); }
  function initialDifficulty(rating, clamp) {
    const d = w[4] - Math.exp(w[5] * (rating - 1)) + 1;
    return clamp ? clampD(d) : d;
  }
  function nextDifficulty(difficulty, rating) {
    const arg1 = initialDifficulty(RATING.easy, false);
    const delta = -(w[6] * (rating - 3));
    const arg2 = difficulty + (10.0 - difficulty) * delta / 9.0;
    return clampD(w[7] * arg1 + (1 - w[7]) * arg2);
  }
  function shortTermStability(stability, rating) {
    let inc = Math.exp(w[17] * (rating - 3 + w[18])) * Math.pow(stability, -w[19]);
    if (rating !== RATING.again) inc = Math.max(inc, 1.0);
    return clampS(stability * inc);
  }
  function nextForgetStability(difficulty, stability, r) {
    const longTerm = w[11] * Math.pow(difficulty, -w[12]) * (Math.pow(stability + 1, w[13]) - 1) * Math.exp((1 - r) * w[14]);
    const shortTerm = stability / Math.exp(w[17] * w[18]);
    return Math.min(longTerm, shortTerm);
  }
  function nextRecallStability(difficulty, stability, r, rating) {
    const hardPenalty = rating === RATING.hard ? w[15] : 1;
    const easyBonus = rating === RATING.easy ? w[16] : 1;
    return stability * (1 + Math.exp(w[8]) * (11 - difficulty) * Math.pow(stability, -w[9]) * (Math.exp((1 - r) * w[10]) - 1) * hardPenalty * easyBonus);
  }
  function nextStability(difficulty, stability, r, rating) {
    const s = rating === RATING.again ? nextForgetStability(difficulty, stability, r) : nextRecallStability(difficulty, stability, r, rating);
    return clampS(s);
  }
  function elapsedWholeDays(lastReview, now) {
    return Math.floor((now - lastReview) / DAY_MS);
  }
  function retrievabilityRaw(stability, lastReview, now) {
    const elapsed = Math.max(0, elapsedWholeDays(lastReview, now));
    return Math.pow(1 + factor * elapsed / stability, decay);
  }
  function intervalDays(stability) {
    const raw = (stability / factor) * (Math.pow(retention, 1 / decay) - 1);
    return Math.min(Math.max(roundHalfEven(raw), 1), maximumInterval);
  }

  // Learning and relearning share one step machine; only `steps` differs.
  function stepInterval(card, rating, steps) {
    if (steps.length === 0 || (card.step >= steps.length && rating !== RATING.again)) {
      card.state = "review"; card.step = 0;
      return intervalDays(card.stability) * DAY_MS;
    }
    if (rating === RATING.again) { card.step = 0; return steps[0]; }
    if (rating === RATING.hard) {
      if (card.step === 0 && steps.length === 1) return steps[0] * 1.5;
      if (card.step === 0 && steps.length >= 2) return (steps[0] + steps[1]) / 2;
      return steps[card.step];
    }
    if (rating === RATING.good) {
      if (card.step + 1 === steps.length) { card.state = "review"; card.step = 0; return intervalDays(card.stability) * DAY_MS; }
      card.step += 1; return steps[card.step];
    }
    card.state = "review"; card.step = 0;
    return intervalDays(card.stability) * DAY_MS;
  }

  function schedule(record, ratingName, now) {
    const rating = RATING[ratingName];
    const at = new Date(now).getTime();
    const last = record.last_review ? new Date(record.last_review).getTime() : null;
    const sameDay = last !== null && elapsedWholeDays(last, at) < 1;
    const card = { state: record.state || "learning", step: record.step || 0, stability: record.stability, difficulty: record.difficulty };
    let intervalMs;

    if (card.state === "learning" || card.state === "relearning") {
      if (card.stability === null || card.stability === undefined || card.difficulty === null || card.difficulty === undefined) {
        card.stability = initialStability(rating);
        card.difficulty = initialDifficulty(rating, true);
      } else if (sameDay) {
        card.stability = shortTermStability(card.stability, rating);
        card.difficulty = nextDifficulty(card.difficulty, rating);
      } else {
        const r = retrievabilityRaw(card.stability, last, at);
        card.stability = nextStability(card.difficulty, card.stability, r, rating);
        card.difficulty = nextDifficulty(card.difficulty, rating);
      }
      intervalMs = stepInterval(card, rating, card.state === "learning" ? learningSteps : relearningSteps);
    } else {
      if (sameDay) {
        card.stability = shortTermStability(card.stability, rating);
      } else {
        const r = retrievabilityRaw(card.stability, last, at);
        card.stability = nextStability(card.difficulty, card.stability, r, rating);
      }
      card.difficulty = nextDifficulty(card.difficulty, rating);
      if (rating === RATING.again && relearningSteps.length > 0) {
        card.state = "relearning"; card.step = 0;
        intervalMs = relearningSteps[0];
      } else {
        intervalMs = intervalDays(card.stability) * DAY_MS;
      }
    }

    return {
      card_id: record.card_id,
      state: card.state,
      due: new Date(at + intervalMs).toISOString(),
      stability: card.stability,
      difficulty: card.difficulty,
      step: card.step,
      reps: record.reps + 1,
      lapses: record.lapses + (rating === RATING.again ? 1 : 0),
      last_review: new Date(at).toISOString(),
    };
  }

  function retrievability(record, now) {
    if (record.stability === null || record.stability === undefined || !record.last_review) return 1;
    const r = retrievabilityRaw(record.stability, new Date(record.last_review).getTime(), new Date(now).getTime());
    return Math.round(r * 10000) / 10000;
  }

  return { schedule, retrievability };
}
