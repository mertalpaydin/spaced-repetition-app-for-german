// Gist sync against a fake GitHub API: pull, merge, push only when the union
// has something the remote lacks, create the gist on first use.
import test from "node:test";
import assert from "node:assert/strict";

import { RATE_LIMIT_FLOOR, resetRateLimitState, syncLog } from "../../web/lib/sync.js";
import { toJsonl } from "../../web/lib/log.js";

function fakeGithub(initialContent) {
  const calls = [];
  let content = initialContent;
  globalThis.fetch = async (url, init = {}) => {
    const method = init.method || "GET";
    calls.push(`${method} ${url}`);
    assert.equal(init.headers.Authorization, "Bearer tok");
    if (method === "POST" && url.endsWith("/gists")) {
      content = JSON.parse(init.body).files["review_log.jsonl"].content;
      if (!content.trim()) return { ok: false, status: 422, json: async () => ({ message: "Validation Failed" }) };
      return { ok: true, status: 201, json: async () => ({ id: "g1" }) };
    }
    if (method === "GET") {
      return { ok: true, status: 200, json: async () => ({ files: { "review_log.jsonl": { content, truncated: false } } }) };
    }
    if (method === "PATCH") {
      content = JSON.parse(init.body).files["review_log.jsonl"].content;
      return { ok: true, status: 200, json: async () => ({}) };
    }
    throw new Error(`unexpected ${method} ${url}`);
  };
  return { calls, get content() { return content; } };
}

const e = (seq, unit, ts) => ({ type: "mark", seq, ts, unit_id: unit, known: true, source: "triage" });

test("first sync creates the gist and pushes the local log", async () => {
  const gh = fakeGithub("\n");
  const settings = { token: "tok", gistId: "" };
  const r = await syncLog(settings, [e(1, "u1", "2026-09-01T08:00:00Z")]);
  assert.equal(settings.gistId, "g1");
  assert.equal(r.pushed, 1);
  assert.equal(r.pulled, 0);
  assert.ok(!gh.calls.some((c) => c.startsWith("PATCH")), "the create carries the log");
  assert.equal(gh.content, toJsonl([e(1, "u1", "2026-09-01T08:00:00Z")]));
});

test("an empty log is never sent as empty content, which GitHub rejects", async () => {
  const gh = fakeGithub("\n");
  await syncLog({ token: "tok", gistId: "" }, []);
  assert.ok(gh.content.trim().length > 0);
  const gh2 = fakeGithub(gh.content);
  const r = await syncLog({ token: "tok", gistId: "g1" }, []);
  assert.equal(r.merged.length, 0);
  assert.ok(!gh2.calls.some((c) => c.startsWith("PATCH")));
});

test("a sync pulls the other device's entries and pushes only when local had something new", async () => {
  const remote = [e(1, "u1", "2026-09-01T08:00:00Z"), e(2, "u2", "2026-09-01T08:01:00Z")];
  const gh = fakeGithub(toJsonl(remote));
  const r = await syncLog({ token: "tok", gistId: "g1" }, [remote[0]]);
  assert.equal(r.pulled, 1);
  assert.equal(r.pushed, 0);
  assert.equal(r.merged.length, 2);
  assert.ok(!gh.calls.some((c) => c.startsWith("PATCH")), "nothing to push");

  const local = [remote[0], e(2, "u3", "2026-09-02T08:00:00Z")];
  const r2 = await syncLog({ token: "tok", gistId: "g1" }, local);
  assert.equal(r2.pulled, 1);
  assert.equal(r2.pushed, 1);
  assert.equal(r2.merged.length, 3);
  assert.equal(gh.content, toJsonl(r2.merged));
});

test("without a token the log stays local", async () => {
  const r = await syncLog({ token: "" }, [e(1, "u1", "2026-09-01T08:00:00Z")]);
  assert.equal(r.skipped, true);
  assert.equal(r.merged.length, 1);
});

// -- rate limiting, added 2026-10-21 ------------------------------------------

function fakeGithubV2(initialContent, { etag = '"v1"', limit = 5000 } = {}) {
  const calls = [];
  let content = initialContent;
  let tag = etag;
  let remaining = limit;
  const reset = Math.floor(Date.now() / 1000) + 3600;
  const hdrs = (extra = {}) => ({
    get: (k) => ({ etag: tag, "x-ratelimit-remaining": String(remaining), "x-ratelimit-reset": String(reset), ...extra }[k.toLowerCase()] ?? null),
  });
  globalThis.fetch = async (url, init = {}) => {
    const method = init.method || "GET";
    calls.push(`${method} ${url}`);
    remaining -= 1;
    if (remaining < 0) {
      remaining = 0;
      return { ok: false, status: 403, headers: hdrs(), json: async () => ({ message: "API rate limit exceeded" }) };
    }
    if (method === "GET") {
      if (init.headers["If-None-Match"] === tag) {
        remaining += 1; // a 304 is free, which is the whole point
        return { ok: false, status: 304, headers: hdrs(), json: async () => ({}) };
      }
      return { ok: true, status: 200, headers: hdrs(), json: async () => ({ files: { "review_log.jsonl": { content, truncated: false } } }) };
    }
    if (method === "PATCH") {
      content = JSON.parse(init.body).files["review_log.jsonl"].content;
      tag = `"v${Number(tag.replace(/\D/g, "")) + 1}"`;
      return { ok: true, status: 200, headers: hdrs(), json: async () => ({}) };
    }
    throw new Error(`unexpected ${method} ${url}`);
  };
  return { calls, get remaining() { return remaining; }, get content() { return content; } };
}

test("an unchanged gist answers 304, which GitHub does not charge for", async () => {
  resetRateLimitState();
  const remote = [e(1, "u1", "2026-09-01T08:00:00Z")];
  const gh = fakeGithubV2(toJsonl(remote));
  const settings = { token: "tok", gistId: "g1" };

  const first = await syncLog(settings, remote);
  assert.equal(first.pulled, 0);
  const afterFirst = gh.remaining;

  // nothing changed on either side: the second sync is conditional and free
  const second = await syncLog(settings, remote);
  assert.equal(second.merged.length, 1);
  assert.equal(gh.remaining, afterFirst, "a 304 must not cost an API call");
  assert.ok(gh.calls.length > 1, "it still asked");
});

test("a push refreshes the etag so the next pull is still conditional", async () => {
  resetRateLimitState();
  const gh = fakeGithubV2(toJsonl([e(1, "u1", "2026-09-01T08:00:00Z")]));
  const settings = { token: "tok", gistId: "g1" };
  const local = [e(1, "u1", "2026-09-01T08:00:00Z"), e(2, "u2", "2026-09-02T08:00:00Z")];
  const r = await syncLog(settings, local);
  assert.equal(r.pushed, 1);
  const after = gh.remaining;
  await syncLog(settings, local);
  assert.equal(gh.remaining, after, "the pull after a push is conditional too");
});

test("a rate limit parks sync until the reset instead of hammering it", async () => {
  resetRateLimitState();
  const gh = fakeGithubV2(toJsonl([]), { limit: 0 });
  const settings = { token: "tok", gistId: "g1" };
  await assert.rejects(() => syncLog(settings, [e(1, "u1", "2026-09-01T08:00:00Z")]), /Limit erreicht/);
  const spent = gh.calls.length;
  // the next attempt does not reach the network at all
  await assert.rejects(() => syncLog(settings, [e(1, "u1", "2026-09-01T08:00:00Z")]), /Limit erreicht/);
  assert.equal(gh.calls.length, spent, "no further requests while parked");
});

test("sync stops on its own before the allowance runs out", async () => {
  resetRateLimitState();
  const gh = fakeGithubV2(toJsonl([]), { limit: RATE_LIMIT_FLOOR });
  const settings = { token: "tok", gistId: "g1" };
  await syncLog(settings, []);
  const spent = gh.calls.length;
  await assert.rejects(() => syncLog(settings, []), /Limit erreicht/);
  assert.equal(gh.calls.length, spent, "it refuses rather than spending the last of it");
});
