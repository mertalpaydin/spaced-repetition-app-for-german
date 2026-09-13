// Service worker: the shell is precached under a versioned cache; the deck
// shards are cached on first use under a cache named after the deck version
// the page reports, so a new deck build replaces the old shards and an old
// one never lingers. Everything else (the GitHub API) goes to the network.

const APP_VERSION = "3b.4";
const SHELL_CACHE = `app-${APP_VERSION}`;
const SHELL = [
  "./",
  "./index.html",
  "./app.js",
  "./styles.css",
  "./manifest.json",
  "./icon-192.png",
  "./icon-512.png",
  "./lib/deck.js",
  "./lib/engine.js",
  "./lib/grader-tables.js",
  "./lib/grader.js",
  "./lib/log.js",
  "./lib/session.js",
  "./lib/store.js",
  "./lib/sync.js",
];

let deckCache = null;   // "deck-<version>", set by the page after it read the manifest

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((k) => k.startsWith("app-") && k !== SHELL_CACHE).map((k) => caches.delete(k)),
    )).then(() => self.clients.claim()),
  );
});

self.addEventListener("message", (event) => {
  const msg = event.data || {};
  if (msg.type !== "deck" || !msg.version) return;
  deckCache = `deck-${msg.version}`;
  event.waitUntil(caches.keys().then((keys) => Promise.all(
    keys.filter((k) => k.startsWith("deck-") && k !== deckCache).map((k) => caches.delete(k)),
  )));
});

function isDeck(url) { return url.pathname.includes("/data/deck/"); }
function isManifest(url) { return url.pathname.endsWith("/data/deck/manifest.json"); }

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;

  if (isManifest(url)) {
    // Network first: the manifest says which deck version is current.
    event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
    return;
  }
  if (isDeck(url)) {
    event.respondWith((async () => {
      const name = deckCache || (await currentDeckCache());
      const cache = await caches.open(name);
      const hit = await cache.match(event.request);
      if (hit) return hit;
      const res = await fetch(event.request);
      if (res.ok) cache.put(event.request, res.clone());
      return res;
    })());
    return;
  }
  event.respondWith(caches.match(event.request).then((hit) => hit || fetch(event.request)));
});

async function currentDeckCache() {
  const keys = await caches.keys();
  return keys.find((k) => k.startsWith("deck-")) || "deck-unknown";
}
