// Writing a card for a word the deck does not have.
//
// This is the ONE place outside src/llm/client.py that calls a model, by the
// owner's decision of 2026-09-23 (CLAUDE.md rule 4 names the exception and
// its bounds). Python cannot serve this call: the page runs on GitHub Pages
// with no backend, and a learner adding a word on the phone should not wait
// for a laptop to be switched on. In exchange this module owes what the
// Python client provides:
//
//   * a content-addressed cache, checked before every call, which is also
//     what makes a retry after a half-finished add cost nothing;
//   * a record of every ATTEMPT, not every success, so a failed call is
//     still visible when the day's quota is counted;
//   * the two 429s told apart: per-minute means back off and try again,
//     per-day means the free lane is closed until it resets and trying
//     again is how the next day's quota gets burned too;
//   * a ceiling it refuses to cross, here a call count rather than a dollar
//     figure, because the free lane bills nothing and what actually runs out
//     is requests.
//
// The key is the owner's own, typed into the page's settings beside the
// GitHub token that already lives there. It is never in the artifact.

const ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models";

// The free lane's own limits are the reason for each of these. A word needs
// one call, so a learner adding words by hand cannot reach the cap by
// accident; a runaway loop hits it immediately.
export const MAX_CALLS_PER_DAY = 40;
export const MAX_ATTEMPTS = 3;
// Gemini answers a per-minute refusal in seconds, so the wait is short and
// jittered; two devices adding a word at once must not retry in lockstep.
const BACKOFF_MS = [1200, 4000];

const STORE_CACHE = "geminiCache";
const STORE_CALLS = "geminiCalls";

// ---------------------------------------------------------------------------
// Cache and call record. IndexedDB through the page's own store helper, so
// there is one database and one upgrade path.
// ---------------------------------------------------------------------------

export async function cacheKey(payload) {
  const canonical = JSON.stringify(payload, Object.keys(payload).sort());
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}

// Pacific midnight is when the free lane's daily quota resets, so that is the
// day a call count belongs to, not the learner's local day.
export function quotaDay(now) {
  return new Date(now).toLocaleDateString("en-CA", { timeZone: "America/Los_Angeles" });
}

export class QuotaExhaustedError extends Error {
  constructor(message, { untilReset = true } = {}) {
    super(message);
    this.name = "QuotaExhaustedError";
    this.untilReset = untilReset;
  }
}

function is429(status) {
  return status === 429;
}

// Per-day and per-minute both arrive as 429. Google names the per-day one in
// the violation's quota id, so the text is what tells them apart; when it is
// ambiguous the safer reading is per-day, because retrying a spent day is
// what loses the next one.
export function isPerDayRefusal(body) {
  const text = typeof body === "string" ? body : JSON.stringify(body || {});
  return /per\s*day|PerDay|daily|RequestsPerDay/i.test(text);
}

// ---------------------------------------------------------------------------
// The prompt
// ---------------------------------------------------------------------------

// The card must not give its own answer away: the English the learner sees
// is the sentence's translation, and rule 2 forbids it quoting the German it
// translates. The model is told the word, so it is told not to leave it in.
function promptFor(unit, count) {
  const kind = { noun: "noun", verb: "verb", adjective: "adjective", adverb: "adverb" }[unit.kind] || unit.kind;
  return [
    `Write ${count} short German sentences for a vocabulary flashcard.`,
    `The word being taught is "${unit.display_de}" (${kind}).`,
    "",
    "Rules:",
    `- Every sentence must contain the word "${unit.lemma_key}" in some inflected form.`,
    "- Everyday register, 5 to 14 words, one clause or two, ending in . ! or ?",
    "- Use a different grammatical form of the word in each sentence.",
    "- Nothing offensive, no proper names, no brand names.",
    "- Give the English translation of each sentence.",
    `- The English must NOT contain the German word "${unit.lemma_key}" or any German at all.`,
    `- Give the exact surface form of "${unit.lemma_key}" as it appears in your sentence.`,
    "",
    "Answer as JSON only, no prose, no code fence:",
    '{"cards":[{"de":"...","en":"...","surface":"..."}]}',
  ].join("\n");
}

// ---------------------------------------------------------------------------
// Turning an answer into cards
// ---------------------------------------------------------------------------

async function cardIdFor(unitId, sentence) {
  const digest = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(`${unitId}\n${sentence}`));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 12);
}

// Rule 6: answers is a list, one per gap, in gap order, and each gap's text
// slices back out of sentence_de exactly. A sentence whose surface form does
// not actually occur in it is dropped rather than patched, because a card
// whose blank does not line up teaches nothing.
export async function toCard(unit, { de, en, surface }, modelName) {
  const sentence = (de || "").trim();
  const english = (en || "").trim();
  const form = (surface || "").trim();
  if (!sentence || !english || !form) return null;
  const start = sentence.indexOf(form);
  if (start < 0) return null;
  if (english.toLowerCase().includes(unit.lemma_key.toLowerCase())) return null;
  const tokenIndex = sentence.slice(0, start).split(/\s+/).filter(Boolean).length;
  return {
    card_id: await cardIdFor(unit.unit_id, sentence),
    unit_id: unit.unit_id,
    kind: unit.kind,
    sentence_de: sentence,
    gloss_en: english,
    gloss_source: "gemini",
    gaps: [{ start, end: start + form.length, answer: form, token_index: tokenIndex }],
    answers: [form],
    form_key: `written|${form.toLowerCase()}`,
    corpus_source: "written",
    corpus_line_id: modelName,
    needs_context: false,
    context_de: null,
  };
}

// ---------------------------------------------------------------------------
// The call
// ---------------------------------------------------------------------------

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * Cards for one unit, from the cache if it has them and from the model if
 * not. `store` is the page's IndexedDB helper: get(store, key) /
 * put(store, key, value) / all(store).
 */
export async function writeCards(unit, {
  apiKey,
  model,
  store,
  count = 3,
  now = () => Date.now(),
  fetchImpl = fetch,
} = {}) {
  if (!apiKey) throw new Error("no Gemini key: add one in Einstellungen");
  if (!model) throw new Error("no model named: the deck manifest is too old");

  const key = await cacheKey({ unit: unit.unit_id, display: unit.display_de, count, model });
  const cached = await store.get(STORE_CACHE, key);
  if (cached) {
    await store.put(STORE_CALLS, `${key}:cache:${now()}`, {
      ts: new Date(now()).toISOString(), day: quotaDay(now()), unit_id: unit.unit_id,
      model, lane: "cache", outcome: "hit", attempt: 0, tokens: 0,
    });
    return { cards: cached.cards, fromCache: true };
  }

  const calls = await store.all(STORE_CALLS);
  const today = quotaDay(now());
  const spent = calls.filter((c) => c.day === today && c.lane === "free").length;
  if (spent >= MAX_CALLS_PER_DAY) {
    throw new QuotaExhaustedError(
      `${MAX_CALLS_PER_DAY} Aufrufe heute erreicht. Morgen wieder, oder baue das Deck neu.`,
    );
  }

  const url = `${ENDPOINT}/${encodeURIComponent(model)}:generateContent?key=${encodeURIComponent(apiKey)}`;
  const body = {
    contents: [{ role: "user", parts: [{ text: promptFor(unit, count) }] }],
    // No temperature, top_p or top_k. Google deprecated all three: since
    // Gemini 3.6 Flash they have been pinned to defaults and had no effect
    // on output, and upcoming models return an error for requests that set
    // them (deprecation notice, 2026-10-07). responseMimeType stays, which
    // is what actually makes the answer parseable.
    generationConfig: { responseMimeType: "application/json" },
  };

  let lastError = null;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    const record = {
      ts: new Date(now()).toISOString(), day: quotaDay(now()), unit_id: unit.unit_id,
      model, lane: "free", attempt, tokens: 0, outcome: "error",
    };
    let res;
    try {
      res = await fetchImpl(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch (err) {
      // The network, not the model. Recorded all the same: an attempt that
      // never arrived still looks like a gap in the day's count otherwise.
      record.outcome = "network";
      await store.put(STORE_CALLS, `${key}:${attempt}:${now()}`, record);
      lastError = err;
      if (attempt < MAX_ATTEMPTS) { await sleep(BACKOFF_MS[attempt - 1] || 4000); continue; }
      throw err;
    }

    const text = await res.text();
    if (is429(res.status)) {
      const perDay = isPerDayRefusal(text);
      record.outcome = perDay ? "quota_day" : "quota_minute";
      await store.put(STORE_CALLS, `${key}:${attempt}:${now()}`, record);
      if (perDay) {
        throw new QuotaExhaustedError(
          "Das Tageskontingent der kostenlosen Gemini-Ebene ist aufgebraucht. " +
          "Es wird um Mitternacht (Pazifik) zurückgesetzt.",
        );
      }
      if (attempt < MAX_ATTEMPTS) { await sleep((BACKOFF_MS[attempt - 1] || 4000) + Math.random() * 500); continue; }
      throw new QuotaExhaustedError("Zu viele Anfragen pro Minute.", { untilReset: false });
    }
    if (!res.ok) {
      record.outcome = `http_${res.status}`;
      await store.put(STORE_CALLS, `${key}:${attempt}:${now()}`, record);
      lastError = new Error(`Gemini ${res.status}: ${text.slice(0, 200)}`);
      if (attempt < MAX_ATTEMPTS) { await sleep(BACKOFF_MS[attempt - 1] || 4000); continue; }
      throw lastError;
    }

    let payload;
    try {
      payload = JSON.parse(text);
    } catch {
      record.outcome = "unparsable";
      await store.put(STORE_CALLS, `${key}:${attempt}:${now()}`, record);
      lastError = new Error("Gemini sent something that is not JSON");
      if (attempt < MAX_ATTEMPTS) continue;
      throw lastError;
    }
    const usage = payload.usageMetadata || {};
    record.tokens = (usage.promptTokenCount || 0) + (usage.candidatesTokenCount || 0);
    const raw = payload.candidates?.[0]?.content?.parts?.[0]?.text || "";
    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch {
      record.outcome = "unparsable_content";
      await store.put(STORE_CALLS, `${key}:${attempt}:${now()}`, record);
      lastError = new Error("Gemini did not answer with the JSON it was asked for");
      if (attempt < MAX_ATTEMPTS) continue;
      throw lastError;
    }

    const cards = [];
    for (const row of parsed.cards || []) {
      const card = await toCard(unit, row, model);
      if (card) cards.push(card);
    }
    record.outcome = cards.length ? "ok" : "no_usable_card";
    await store.put(STORE_CALLS, `${key}:${attempt}:${now()}`, record);
    if (!cards.length) {
      lastError = new Error("Gemini wrote nothing usable for this word");
      if (attempt < MAX_ATTEMPTS) continue;
      throw lastError;
    }
    await store.put(STORE_CACHE, key, { cards, model, ts: new Date(now()).toISOString() });
    return { cards, fromCache: false };
  }
  throw lastError || new Error("Gemini could not be reached");
}

// What the settings screen shows, so the spend is visible rather than
// something the owner has to take on trust.
export async function callSummary(store, now = () => Date.now()) {
  const calls = await store.all(STORE_CALLS);
  const today = quotaDay(now());
  const free = calls.filter((c) => c.lane === "free");
  return {
    today: free.filter((c) => c.day === today).length,
    limit: MAX_CALLS_PER_DAY,
    total: free.length,
    cacheHits: calls.filter((c) => c.lane === "cache").length,
    tokens: free.reduce((sum, c) => sum + (c.tokens || 0), 0),
  };
}
