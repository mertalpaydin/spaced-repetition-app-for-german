// Local persistence: the review log in IndexedDB (one row per entry, keyed by
// type|unit|time so a merge never duplicates), settings in localStorage.

import { entryKey, sortEntries } from "./log.js";

const DB_NAME = "phrasen";
const DB_VERSION = 1;

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains("log")) db.createObjectStore("log");
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function tx(db, mode, fn) {
  return new Promise((resolve, reject) => {
    const t = db.transaction("log", mode);
    const store = t.objectStore("log");
    const out = fn(store);
    t.oncomplete = () => resolve(out);
    t.onerror = () => reject(t.error);
    t.onabort = () => reject(t.error);
  });
}

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
