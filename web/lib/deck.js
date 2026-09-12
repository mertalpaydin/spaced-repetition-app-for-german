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
