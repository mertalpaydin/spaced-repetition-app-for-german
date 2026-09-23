// Local persistence: the review log in IndexedDB (one row per entry, keyed by
// type|unit|time so a merge never duplicates), settings in localStorage.

import { entryKey, sortEntries } from "./log.js";

const DB_NAME = "phrasen";
// Version 2 adds the two stores web/lib/gemini.js needs: the cache it checks
// before calling the model, and one row per attempt so the day's free-lane
// calls can be counted. Purely additive; the log store is untouched and a
// database written by version 1 upgrades in place.
const DB_VERSION = 2;
const STORES = ["log", "geminiCache", "geminiCalls"];

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      for (const name of STORES) {
        if (!db.objectStoreNames.contains(name)) db.createObjectStore(name);
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function tx(db, mode, fn, name = "log") {
  return new Promise((resolve, reject) => {
    const t = db.transaction(name, mode);
    const store = t.objectStore(name);
    const out = fn(store);
    t.oncomplete = () => resolve(out);
    t.onerror = () => reject(t.error);
    t.onabort = () => reject(t.error);
  });
}

// A small keyed store, the shape web/lib/gemini.js expects. Kept here so
// there is one database and one upgrade path rather than two.
export const kv = {
  async get(name, key) {
    const db = await openDb();
    const value = await new Promise((resolve, reject) => {
      const req = db.transaction(name, "readonly").objectStore(name).get(key);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    db.close();
    return value;
  },
  async put(name, key, value) {
    const db = await openDb();
    await tx(db, "readwrite", (store) => store.put(value, key), name);
    db.close();
  },
  async all(name) {
    const db = await openDb();
    const rows = await new Promise((resolve, reject) => {
      const req = db.transaction(name, "readonly").objectStore(name).getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => reject(req.error);
    });
    db.close();
    return rows;
  },
};

export async function readLog() {
  const db = await openDb();
  const rows = await new Promise((resolve, reject) => {
    const t = db.transaction("log", "readonly");
    const req = t.objectStore("log").getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  db.close();
  return sortEntries(rows);
}

export async function appendEntries(entries) {
  if (!entries.length) return;
  const db = await openDb();
  await tx(db, "readwrite", (store) => { for (const e of entries) store.put(e, entryKey(e)); });
  db.close();
}

export async function replaceLog(entries) {
  const db = await openDb();
  await tx(db, "readwrite", (store) => { store.clear(); for (const e of entries) store.put(e, entryKey(e)); });
  db.close();
}

const SETTINGS_KEY = "phrasen.settings";

export function loadSettings() {
  try { return { ...JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") }; } catch (e) { return {}; }
}

export function saveSettings(settings) {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch (e) { /* private mode */ }
}
