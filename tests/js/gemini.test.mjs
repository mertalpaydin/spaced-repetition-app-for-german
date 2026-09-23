// The one model call outside src/llm/client.py (CLAUDE.md rule 4 names the
// exception). These tests are the compensating controls: the cache is
// checked first, every attempt is recorded, the two 429s are told apart, and
// the daily ceiling is refused rather than crossed. Nothing here touches the
// network; fetch is injected.
import test from "node:test";
import assert from "node:assert/strict";

import { MAX_CALLS_PER_DAY, QuotaExhaustedError, callSummary, isPerDayRefusal, quotaDay, toCard, writeCards } from "../../web/lib/gemini.js";

const UNIT = { unit_id: "nn:hähnchen", kind: "noun", lemma_key: "hähnchen", display_de: "das Hähnchen" };
const MODEL = "gemini-3.8-flash";
const T0 = Date.parse("2026-09-23T18:00:00Z");

function fakeStore() {
  const data = new Map();
  return {
    data,
    async get(name, key) { return data.get(`${name}:${key}`); },
    async put(name, key, value) { data.set(`${name}:${key}`, value); },
    async all(name) {
      return [...data.entries()].filter(([k]) => k.startsWith(`${name}:`)).map(([, v]) => v);
    },
  };
}

const answer = (cards) => ({
  ok: true, status: 200,
  text: async () => JSON.stringify({
    usageMetadata: { promptTokenCount: 30, candidatesTokenCount: 70 },
    candidates: [{ content: { parts: [{ text: JSON.stringify({ cards }) }] } }],
  }),
});

const GOOD = [{ de: "Das Hähnchen ist fertig.", en: "The chicken is ready.", surface: "Hähnchen" }];

test("a written card blanks the word where it actually occurs", async () => {
  const card = await toCard(UNIT, GOOD[0], MODEL);
  assert.equal(card.sentence_de.slice(card.gaps[0].start, card.gaps[0].end), "Hähnchen");
  assert.deepEqual(card.answers, ["Hähnchen"]);
  assert.equal(card.answers[0], card.gaps[0].answer);
  assert.equal(card.gloss_source, "gemini");
  assert.equal(card.card_id.length, 12);
});

test("a sentence that does not contain the word is dropped, not patched", async () => {
  assert.equal(await toCard(UNIT, { de: "Der Hund schläft.", en: "The dog sleeps.", surface: "Hähnchen" }, MODEL), null);
});

test("an English gloss that quotes the German is refused: it prints the answer", async () => {
  // CLAUDE.md rule 2: the card must not give its own answer away.
  const leaky = { de: "Das Hähnchen ist fertig.", en: "The Hähnchen is ready.", surface: "Hähnchen" };
  assert.equal(await toCard(UNIT, leaky, MODEL), null);
});

test("the cache is checked before the model and a hit costs no call", async () => {
  const store = fakeStore();
  let calls = 0;
  const fetchImpl = async () => { calls++; return answer(GOOD); };

  const first = await writeCards(UNIT, { apiKey: "k", model: MODEL, store, now: () => T0, fetchImpl });
  assert.equal(first.fromCache, false);
  assert.equal(calls, 1);

  const second = await writeCards(UNIT, { apiKey: "k", model: MODEL, store, now: () => T0, fetchImpl });
  assert.equal(second.fromCache, true);
  assert.equal(calls, 1);
  assert.deepEqual(second.cards, first.cards);

  const summary = await callSummary(store, () => T0);
  assert.equal(summary.today, 1);
  assert.equal(summary.cacheHits, 1);
  assert.equal(summary.tokens, 100);
});

test("a per-day 429 stops at once: retrying is how the next day is lost too", async () => {
  const store = fakeStore();
  let calls = 0;
  const fetchImpl = async () => {
    calls++;
    return {
      ok: false, status: 429,
      text: async () => JSON.stringify({ error: { message: "Quota exceeded for quota metric 'GenerateRequestsPerDay'" } }),
    };
  };
  await assert.rejects(
    writeCards(UNIT, { apiKey: "k", model: MODEL, store, now: () => T0, fetchImpl }),
    (err) => err instanceof QuotaExhaustedError && err.untilReset,
  );
  assert.equal(calls, 1, "a spent day must not be retried");
  const recorded = await store.all("geminiCalls");
  assert.equal(recorded[0].outcome, "quota_day");
});

test("a per-minute 429 backs off and then succeeds", async () => {
  const store = fakeStore();
  let calls = 0;
  const fetchImpl = async () => {
    calls++;
    if (calls === 1) {
      return { ok: false, status: 429, text: async () => JSON.stringify({ error: { message: "Resource exhausted: requests per minute" } }) };
    }
    return answer(GOOD);
  };
  const out = await writeCards(UNIT, { apiKey: "k", model: MODEL, store, now: () => T0, fetchImpl });
  assert.equal(out.cards.length, 1);
  assert.equal(calls, 2);
  // Both attempts recorded, not just the one that worked.
  const outcomes = (await store.all("geminiCalls")).map((c) => c.outcome).sort();
  assert.deepEqual(outcomes, ["ok", "quota_minute"]);
});

test("the two refusals are told apart by what Google says", () => {
  assert.equal(isPerDayRefusal("GenerateRequestsPerDayPerProject"), true);
  assert.equal(isPerDayRefusal("requests per minute"), false);
  // Ambiguous reads as per-day: the safer mistake is waiting a day we did
  // not need to, not burning tomorrow's quota on retries.
  assert.equal(isPerDayRefusal(""), false);
});

test("the daily ceiling is refused before the call, not after", async () => {
  const store = fakeStore();
  const day = quotaDay(T0);
  for (let i = 0; i < MAX_CALLS_PER_DAY; i++) {
    await store.put("geminiCalls", `old${i}`, { day, lane: "free", outcome: "ok", tokens: 0 });
  }
  let calls = 0;
  const fetchImpl = async () => { calls++; return answer(GOOD); };
  await assert.rejects(
    writeCards(UNIT, { apiKey: "k", model: MODEL, store, now: () => T0, fetchImpl }),
    QuotaExhaustedError,
  );
  assert.equal(calls, 0);
});

test("yesterday's calls do not count against today", async () => {
  const store = fakeStore();
  const yesterday = quotaDay(T0 - 24 * 3600 * 1000);
  for (let i = 0; i < MAX_CALLS_PER_DAY; i++) {
    await store.put("geminiCalls", `old${i}`, { day: yesterday, lane: "free", outcome: "ok", tokens: 0 });
  }
  const out = await writeCards(UNIT, { apiKey: "k", model: MODEL, store, now: () => T0, fetchImpl: async () => answer(GOOD) });
  assert.equal(out.cards.length, 1);
});

test("the day is Pacific, because that is when the free lane resets", () => {
  // 18:00 UTC on the 23rd is still the 23rd in Los Angeles; 08:00 UTC is
  // the 22nd there, and a call then belongs to the 22nd's quota.
  assert.equal(quotaDay(Date.parse("2026-09-23T18:00:00Z")), "2026-09-23");
  assert.equal(quotaDay(Date.parse("2026-09-23T04:00:00Z")), "2026-09-22");
});

test("no key is an error the learner can act on, and costs no call", async () => {
  await assert.rejects(
    writeCards(UNIT, { apiKey: "", model: MODEL, store: fakeStore(), fetchImpl: async () => answer(GOOD) }),
    /Einstellungen/,
  );
});

test("an answer with nothing usable in it is not cached as success", async () => {
  const store = fakeStore();
  const fetchImpl = async () => answer([{ de: "Der Hund schläft.", en: "The dog sleeps.", surface: "Hähnchen" }]);
  await assert.rejects(writeCards(UNIT, { apiKey: "k", model: MODEL, store, now: () => T0, fetchImpl }), /nothing usable/);
  assert.equal((await store.all("geminiCache")).length, 0);
});
