// One log across devices through a private GitHub Gist. The page holds a
// fine-grained token with gist scope only; the gist holds review_log.jsonl.
// Sync = pull, merge (append-only, so two devices never conflict), write the
// union back locally and remotely when either side had something new.

import { mergeEntries, parseJsonl, toJsonl } from "./log.js";

const FILE = "review_log.jsonl";
const API = "https://api.github.com";
// GitHub refuses a gist file whose content is empty or whitespace (422), so an
// empty log is stored as one comment line; parseJsonl skips it.
const PLACEHOLDER = "# Phrasen review log\n";

function body(entries) { return entries.length ? toJsonl(entries) : PLACEHOLDER; }

async function reason(res) {
  try { const j = await res.json(); return j.message || ""; } catch (e) { return ""; }
}

function headers(token) {
  return { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json", "Content-Type": "application/json" };
}

export async function createGist(token, entries = []) {
  const res = await fetch(`${API}/gists`, {
    method: "POST", headers: headers(token),
    body: JSON.stringify({ description: "Phrasen review log", public: false, files: { [FILE]: { content: body(entries) } } }),
  });
  if (!res.ok) throw new Error(`gist create failed: ${res.status} ${await reason(res)}`);
  return (await res.json()).id;
}

export async function pullGist(token, gistId) {
  const res = await fetch(`${API}/gists/${gistId}`, { headers: headers(token), cache: "no-store" });
  if (!res.ok) throw new Error(`gist read failed: ${res.status} ${await reason(res)}`);
  const gist = await res.json();
  const file = gist.files[FILE];
  if (!file) return [];
  let content = file.content;
  if (file.truncated) content = await (await fetch(file.raw_url, { cache: "no-store" })).text();
  return parseJsonl(content || "");
}

export async function pushGist(token, gistId, entries) {
  const res = await fetch(`${API}/gists/${gistId}`, {
    method: "PATCH", headers: headers(token),
    body: JSON.stringify({ files: { [FILE]: { content: body(entries) } } }),
  });
  if (!res.ok) throw new Error(`gist write failed: ${res.status} ${await reason(res)}`);
}

// Returns {merged, pulled, pushed}. `local` is this device's log; the caller
// replaces its local log with `merged`.
export async function syncLog(settings, local) {
  const { token } = settings;
  let { gistId } = settings;
  if (!token) return { merged: local, pulled: 0, pushed: 0, skipped: true };
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
