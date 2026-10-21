// One log across devices through a private GitHub Gist. The page holds a
// fine-grained token with gist scope only; the gist holds review_log.jsonl.
// Sync = pull, merge (append-only, so two devices never conflict), write the
// union back locally and remotely when either side had something new.
//
// GitHub allows 5000 API calls an hour per user. Three things keep this well
// under that, added 2026-10-21 after a sync loop spent the whole allowance in
// about two hours and left the owner with a 403 and an unusable app:
//
//   * a conditional GET. GitHub documents that a 304 Not Modified does NOT
//     count against the rate limit, so a sync that finds the gist unchanged
//     is free. On one device that is almost every sync, and it also skips
//     re-downloading the whole log.
//   * the rate-limit headers. Every response carries how many calls are left
//     and when the window resets; below RATE_LIMIT_FLOOR the client stops of
//     its own accord rather than spending the last of it.
//   * a 403 or 429 that IS a rate limit parks sync until the reset instead of
//     being retried, since a retry cannot succeed and the error text GitHub
//     returns is three lines of terms of service.

import { mergeEntries, parseJsonl, toJsonl } from "./log.js";

const FILE = "review_log.jsonl";
const API = "https://api.github.com";
// GitHub refuses a gist file whose content is empty or whitespace (422), so an
// empty log is stored as one comment line; parseJsonl skips it.
const PLACEHOLDER = "# Phrasen review log\n";

//: Stop syncing while fewer than this many calls remain in the window, so a
//: burst never takes the last of the allowance.
export const RATE_LIMIT_FLOOR = 100;

//: What the last response said about the limit. `reset` is epoch seconds.
export const rateLimit = { remaining: null, reset: 0, blockedUntil: 0 };

//: ETag of the gist as last seen, so the next GET can be conditional. Kept
//: here rather than in settings: it is a cache detail, not a preference, and
//: losing it on reload costs exactly one unconditional GET.
let gistEtag = "";
let gistCache = null;

export class RateLimitedError extends Error {
  constructor(resetSeconds) {
    const at = new Date(resetSeconds * 1000);
    super(`GitHub-Limit erreicht, weiter ab ${at.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}`);
    this.name = "RateLimitedError";
    this.reset = resetSeconds;
  }
}

function body(entries) { return entries.length ? toJsonl(entries) : PLACEHOLDER; }

async function reason(res) {
  try { const j = await res.json(); return j.message || ""; } catch (e) { return ""; }
}

function headers(token, extra = {}) {
  return { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json", "Content-Type": "application/json", ...extra };
}

// Read what the response says about the allowance, and notice a rate-limit
// refusal. GitHub answers one with 403 or 429 and x-ratelimit-remaining: 0;
// a 403 for any other reason (a bad token, a deleted gist) is NOT this.
function noteLimits(res) {
  const remaining = Number(res.headers?.get?.("x-ratelimit-remaining"));
  const reset = Number(res.headers?.get?.("x-ratelimit-reset"));
  if (Number.isFinite(remaining)) rateLimit.remaining = remaining;
  if (Number.isFinite(reset) && reset > 0) rateLimit.reset = reset;
  const refused = (res.status === 403 || res.status === 429) && rateLimit.remaining === 0;
  if (refused) {
    rateLimit.blockedUntil = (rateLimit.reset || Math.floor(Date.now() / 1000) + 3600) * 1000;
    throw new RateLimitedError(Math.floor(rateLimit.blockedUntil / 1000));
  }
  return res;
}

// Is the client holding off? Either GitHub refused and the window has not
// reset, or the allowance is nearly gone.
export function rateLimited(now = Date.now()) {
  if (rateLimit.blockedUntil > now) return new RateLimitedError(Math.floor(rateLimit.blockedUntil / 1000));
  if (rateLimit.remaining !== null && rateLimit.remaining < RATE_LIMIT_FLOOR && rateLimit.reset * 1000 > now) {
    return new RateLimitedError(rateLimit.reset);
  }
  return null;
}

export function resetRateLimitState() {
  rateLimit.remaining = null;
  rateLimit.reset = 0;
  rateLimit.blockedUntil = 0;
  gistEtag = "";
  gistCache = null;
}

export async function createGist(token, entries = []) {
  const res = await fetch(`${API}/gists`, {
    method: "POST", headers: headers(token),
    body: JSON.stringify({ description: "Phrasen review log", public: false, files: { [FILE]: { content: body(entries) } } }),
  });
  noteLimits(res);
  if (!res.ok) throw new Error(`gist create failed: ${res.status} ${await reason(res)}`);
  return (await res.json()).id;
}

export async function pullGist(token, gistId) {
  const extra = gistEtag && gistCache !== null ? { "If-None-Match": gistEtag } : {};
  const res = await fetch(`${API}/gists/${gistId}`, { headers: headers(token, extra), cache: "no-store" });
  noteLimits(res);
  // Unchanged since last time, and free of charge.
  if (res.status === 304 && gistCache !== null) return gistCache;
  if (!res.ok) throw new Error(`gist read failed: ${res.status} ${await reason(res)}`);
  const etag = res.headers?.get?.("etag") || "";
  const gist = await res.json();
  const file = gist.files[FILE];
  if (!file) return [];
  let content = file.content;
  if (file.truncated) content = await (await fetch(file.raw_url, { cache: "no-store" })).text();
  const entries = parseJsonl(content || "");
  gistEtag = etag;
  gistCache = entries;
  return entries;
}

export async function pushGist(token, gistId, entries) {
  const res = await fetch(`${API}/gists/${gistId}`, {
    method: "PATCH", headers: headers(token),
    body: JSON.stringify({ files: { [FILE]: { content: body(entries) } } }),
  });
  noteLimits(res);
  if (!res.ok) throw new Error(`gist write failed: ${res.status} ${await reason(res)}`);
  // The gist has changed, so the ETag we hold is stale. Record the new log as
  // the cache so the next pull can still be conditional.
  gistEtag = res.headers?.get?.("etag") || "";
  gistCache = gistEtag ? entries : null;
}

// Returns {merged, pulled, pushed}. `local` is this device's log; the caller
// replaces its local log with `merged`.
export async function syncLog(settings, local) {
  const { token } = settings;
  let { gistId } = settings;
  if (!token) return { merged: local, pulled: 0, pushed: 0, skipped: true };
  const held = rateLimited();
  if (held) throw held;
  if (!gistId) {
    gistId = await createGist(token, local);
    settings.gistId = gistId;
    return { merged: local, pulled: 0, pushed: local.length, skipped: false };
  }
  const remote = await pullGist(token, gistId);
  const merged = mergeEntries(local, remote);
  const pulled = merged.length - local.length;
  const pushed = merged.length - remote.length;
  if (pushed > 0) await pushGist(token, gistId, merged);
  return { merged, pulled, pushed, skipped: false };
}
