// Gist sync against a fake GitHub API: pull, merge, push only when the union
// has something the remote lacks, create the gist on first use.
import test from "node:test";
import assert from "node:assert/strict";

import { syncLog } from "../../web/lib/sync.js";
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
