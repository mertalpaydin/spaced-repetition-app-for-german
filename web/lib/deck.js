// Loading the exported deck (web/data/deck): manifest, units, shards.

// The manifest is always fetched fresh; every other deck file carries the
// deck version in its URL, so the service worker's cache-first deck store can
// never hand out a shard of an older build under a new manifest.
export async function loadDeck(base = "./data/deck") {
  const manifest = await (await fetch(`${base}/manifest.json`, { cache: "no-cache" })).json();
  const v = encodeURIComponent(manifest.deck_version);
  const units = (await (await fetch(`${base}/${manifest.units_file}?v=${v}`)).json()).units;
  const cardsByUnit = {};
  const shards = await Promise.all(manifest.shards.map((s) => fetch(`${base}/${s.file}?v=${v}`).then((r) => r.json())));
  for (const shard of shards) {
    for (const card of shard.cards) (cardsByUnit[card.unit_id] ||= []).push(card);
  }
  units.sort((a, b) => a.rank - b.rank);
  const byId = Object.fromEntries(units.map((u) => [u.unit_id, u]));
  const cardById = {};
  for (const cards of Object.values(cardsByUnit)) for (const c of cards) cardById[c.card_id] = c;
  return { manifest, units, byId, cardsByUnit, cardById };
}

// The deck plus whatever the learner added from the page. A static artifact
// on Pages cannot be written to from a phone, so an added word arrives as a
// request entry in the log and is folded in here: everything downstream sees
// one deck and never learns where a unit came from. The deck wins on a
// collision, so once a word is mined for real its reviewed cards replace the
// written ones. Matches Deck.with_added in src/engine/session.py.
export function withAdded(deck, state) {
  const addedUnits = state.addedUnits || {};
  const addedCards = state.addedCards || {};
  if (!Object.keys(addedUnits).length && !Object.keys(addedCards).length) return deck;
  const units = [...deck.units];
  const byId = { ...deck.byId };
  for (const unitId of Object.keys(addedUnits).sort()) {
    if (byId[unitId]) continue;
    units.push(addedUnits[unitId]);
    byId[unitId] = addedUnits[unitId];
  }
  const cardsByUnit = { ...deck.cardsByUnit };
  const cardById = { ...deck.cardById };
  for (const [unitId, cards] of Object.entries(addedCards)) {
    if (cardsByUnit[unitId]?.length) continue;
    cardsByUnit[unitId] = cards;
    for (const card of cards) cardById[card.card_id] = card;
  }
  units.sort((a, b) => a.rank - b.rank);
  return { ...deck, units, byId, cardsByUnit, cardById };
}

// Which reserve shard could hold a word. Folded, because the learner may
// type the umlaut either way. Matches reserve_letter in src/phrases/export.py.
export function reserveLetter(word) {
  const first = (word || "").slice(0, 1).toLowerCase()
    .replace("ä", "a").replace("ö", "o").replace("ü", "u").replace("ß", "s");
  return first >= "a" && first <= "z" ? first : "_";
}

const reserveShards = new Map();

export async function loadReserveIndex(base = "./data/deck/reserve") {
  try {
    const res = await fetch(`${base}/index.json`, { cache: "no-cache" });
    return res.ok ? await res.json() : null;
  } catch {
    // Offline, or a deck built before the reserve existed. The button says
    // so rather than the page failing to load.
    return null;
  }
}

// Every reading of a word the reserve holds: "stur" is an adjective and, in
// the corpus, also a surname the tagger read as a noun, so the learner picks.
// One fetch per letter, memoised, because a lookup knows only the word.
export async function lookupReserve(word, { base = "./data/deck/reserve", index = null } = {}) {
  const lemma = (word || "").trim().toLowerCase();
  if (!lemma) return [];
  const letter = reserveLetter(lemma);
  if (index && !index.letters.includes(letter)) return [];
  if (!reserveShards.has(letter)) {
    reserveShards.set(letter, fetch(`${base}/${letter}.json`).then((r) => (r.ok ? r.json() : null)).catch(() => null));
  }
  const shard = await reserveShards.get(letter);
  if (!shard) return [];
  const units = shard.units.filter((u) => u.lemma_key === lemma);
  return units.map((unit) => ({
    unit,
    cards: shard.cards.filter((c) => c.unit_id === unit.unit_id),
  }));
}
