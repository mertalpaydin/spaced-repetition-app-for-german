"""Write the deck the clients read: a manifest, every unit, and rank-band
shards holding the cards. Byte-deterministic for the same input."""

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from src.atomic_write import write_text_atomic
from src.contracts import (
    DeckManifest,
    DeckShard,
    PhraseCard,
    PhraseUnit,
    ReserveIndex,
    ReserveShard,
    ShardInfo,
    UnitsIndex,
)
from src.phrases.unit_glosses import german_leak

DEFAULT_DECK_DIR = Path("web/data/deck")
DEFAULT_SCHEMA_PATH = Path("data/fixtures/schemas/phrase_deck.schema.json")
DEFAULT_RESERVE_DIR = DEFAULT_DECK_DIR / "reserve"


def _dump(model: BaseModel) -> str:
    payload = model.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def deck_version_for(units: Iterable[PhraseUnit], cards: Iterable[PhraseCard]) -> str:
    """A content hash, so a rebuild with identical content keeps its version
    and any change gets a new one."""
    digest = hashlib.sha1()
    for unit in sorted(units, key=lambda u: u.unit_id):
        digest.update(unit.model_dump_json().encode("utf-8"))
    for card in sorted(cards, key=lambda c: c.card_id):
        digest.update(card.model_dump_json().encode("utf-8"))
    return digest.hexdigest()[:12]


def export_deck(
    units: list[PhraseUnit],
    cards: list[PhraseCard],
    out_dir: Path = DEFAULT_DECK_DIR,
    *,
    shard_size: int = 200,
    corpus: dict[str, int] | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> DeckManifest:
    units = sorted(units, key=lambda u: u.rank)
    cards_by_unit: dict[str, list[PhraseCard]] = defaultdict(list)
    for card in sorted(cards, key=lambda c: c.card_id):
        cards_by_unit[card.unit_id].append(card)
    unit_ids = {u.unit_id for u in units}
    orphans = [c for c in cards if c.unit_id not in unit_ids]
    if orphans:
        raise ValueError(f"{len(orphans)} card(s) reference no unit, e.g. {orphans[0].card_id}")

    shards_dir = out_dir / "shards"
    shards: list[ShardInfo] = []
    for band, start in enumerate(range(0, len(units), shard_size)):
        band_units = units[start : start + shard_size]
        band_cards = [c for u in band_units for c in cards_by_unit.get(u.unit_id, [])]
        file = f"shards/band_{band:03d}.json"
        write_text_atomic(
            out_dir / file, _dump(DeckShard(band=band, units=band_units, cards=band_cards))
        )
        shards.append(
            ShardInfo(
                file=file,
                band=band,
                rank_from=band_units[0].rank,
                rank_to=band_units[-1].rank,
                unit_count=len(band_units),
                card_count=len(band_cards),
            )
        )
    # Remove stale shards from a previous, larger build.
    if shards_dir.exists():
        keep = {out_dir / s.file for s in shards}
        for stale in shards_dir.glob("band_*.json"):
            if stale not in keep:
                stale.unlink()

    write_text_atomic(out_dir / "units.json", _dump(UnitsIndex(units=units)))
    manifest = DeckManifest(
        deck_version=deck_version_for(units, cards),
        built_at=now(),
        corpus=corpus or {},
        unit_count=len(units),
        card_count=len(cards),
        glossed_card_count=sum(1 for c in cards if c.gloss_en is not None),
        trivial_count=sum(1 for u in units if u.trivial),
        contexts_generated=sum(1 for c in cards if c.context_de is not None),
        kinds=dict(Counter(u.kind for u in units)),
        units_file="units.json",
        shards=shards,
    )
    write_text_atomic(out_dir / "manifest.json", _dump(manifest))
    return manifest


#: A lemma that starts with anything but a-z (after folding the umlauts)
#: goes here, so every word has exactly one shard to look in.
RESERVE_OTHER_LETTER = "_"
_FOLD = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "s"})


def reserve_letter(lemma_key: str) -> str:
    """The shard a word is looked up in. Folded, so "Ärger" and "Arger" are
    one letter and the page can pick the file from what the learner typed."""
    first = lemma_key[:1].lower().translate(_FOLD)
    return first if "a" <= first <= "z" else RESERVE_OTHER_LETTER


def export_reserve(
    units: list[PhraseUnit],
    cards: list[PhraseCard],
    out_dir: Path,
    *,
    deck_word_min_count: int,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ReserveIndex:
    """Write ``reserve/<letter>.json`` plus its index.

    Sharded by first letter rather than by rank, because the reserve is only
    ever read by lookup: the learner names a word and the page fetches the
    one file that could hold it. Ordering inside a shard is by rank, so a
    shard is byte-identical for the same input like every other export.
    """
    cards_by_unit: dict[str, list[PhraseCard]] = defaultdict(list)
    for card in sorted(cards, key=lambda c: c.card_id):
        cards_by_unit[card.unit_id].append(card)
    unit_ids = {u.unit_id for u in units}
    orphans = [c for c in cards if c.unit_id not in unit_ids]
    if orphans:
        raise ValueError(f"{len(orphans)} reserve card(s) reference no unit: {orphans[0].card_id}")

    by_letter: dict[str, list[PhraseUnit]] = defaultdict(list)
    for unit in sorted(units, key=lambda u: u.rank):
        by_letter[reserve_letter(unit.lemma_key)].append(unit)

    written = 0
    for letter, letter_units in sorted(by_letter.items()):
        letter_cards = [c for u in letter_units for c in cards_by_unit.get(u.unit_id, [])]
        write_text_atomic(
            out_dir / f"{letter}.json",
            _dump(ReserveShard(letter=letter, units=letter_units, cards=letter_cards)),
        )
        written += len(letter_cards)
    # Remove stale shards from a previous, larger reserve.
    if out_dir.exists():
        keep = {out_dir / f"{letter}.json" for letter in by_letter}
        for stale in out_dir.glob("*.json"):
            if stale not in keep and stale.name != "index.json":
                stale.unlink()

    index = ReserveIndex(
        built_at=now(),
        letters=sorted(by_letter),
        unit_count=len(units),
        card_count=written,
        deck_word_min_count=deck_word_min_count,
    )
    write_text_atomic(out_dir / "index.json", _dump(index))
    return index


def load_deck(
    deck_dir: Path = DEFAULT_DECK_DIR,
) -> tuple[DeckManifest, list[PhraseUnit], list[PhraseCard]]:
    manifest = DeckManifest.model_validate_json((deck_dir / "manifest.json").read_text("utf-8"))
    units = UnitsIndex.model_validate_json(
        (deck_dir / manifest.units_file).read_text("utf-8")
    ).units
    cards: list[PhraseCard] = []
    for shard in manifest.shards:
        cards.extend(
            DeckShard.model_validate_json((deck_dir / shard.file).read_text("utf-8")).cards
        )
    return manifest, units, cards


def deck_schema() -> dict[str, Any]:
    """The JSON schema of every exported file, for the PWA and for the test
    that pins the committed copy."""
    return {
        "manifest": DeckManifest.model_json_schema(),
        "units": UnitsIndex.model_json_schema(),
        "shard": DeckShard.model_json_schema(),
        "reserve_index": ReserveIndex.model_json_schema(),
        "reserve_shard": ReserveShard.model_json_schema(),
    }


def write_schema(path: Path = DEFAULT_SCHEMA_PATH) -> None:
    write_text_atomic(path, json.dumps(deck_schema(), indent=2, sort_keys=True) + "\n")


def check_deck(deck_dir: Path = DEFAULT_DECK_DIR) -> list[str]:
    """Problems with a committed deck, for CI: every file parses, every card's
    unit exists, the manifest counts match, and no unit gloss quotes the
    German it translates (that gloss is shown before the answer)."""
    problems: list[str] = []
    try:
        manifest, units, cards = load_deck(deck_dir)
    except (OSError, ValueError) as exc:
        return [f"deck does not load: {exc}"]
    unit_ids = {u.unit_id for u in units}
    for card in cards:
        if card.unit_id not in unit_ids:
            problems.append(f"card {card.card_id} references unknown unit {card.unit_id}")
    if manifest.unit_count != len(units):
        problems.append(f"manifest says {manifest.unit_count} units, found {len(units)}")
    if manifest.card_count != len(cards):
        problems.append(f"manifest says {manifest.card_count} cards, found {len(cards)}")
    if manifest.deck_version != deck_version_for(units, cards):
        problems.append("manifest deck_version does not match the content")
    for unit in units:
        if unit.gloss_en is None:
            continue
        leak = german_leak(unit.gloss_en, unit)
        if leak is not None:
            problems.append(f"unit {unit.unit_id} gloss gives the German away: {leak!r}")
    return problems
