"""The build caches: the pass over the parse file, and carrier verdicts.

A cache may only ever save time. Each test here pins one way it could
instead change the deck: a stale key, a half-written rebuild, a line the
cards stage needed and the tags hid.
"""

import json
from pathlib import Path

import pytest
from src.phrases.build_cache import (
    ParseCache,
    VerdictCache,
    file_digest,
    parse_cache_key,
    source_fingerprint,
)
from src.phrases.occurrences import Occurrence, write_occurrences
from src.phrases.units import aggregate, candidate_unit_ids, match_to_unit, unit_id_for


def _occ(kind: str, key: str, line_id: str, parts: list[str] | None = None) -> Occurrence:
    parts = parts or key.split()
    text = " ".join(parts) + " ist hier."
    spans, at = [], 0
    for part in parts:
        spans.append((at, at + len(part)))
        at += len(part) + 1
    return Occurrence(
        kind=kind,  # type: ignore[arg-type]
        unit_key=key,
        parts=parts,
        token_indices=list(range(len(parts))),
        spans=spans,
        surfaces=parts,
        corpus_source="tatoeba",
        line_id=line_id,
        text=text,
        form_key="Fin|Pres|3|Sing",
    )


def _sample() -> list[Occurrence]:
    return [
        _occ("verb_prep", "warten auf", "1", ["warten", "auf"]),
        _occ("verb_prep", "warten auf", "2", ["warten", "auf"]),
        _occ("adjective", "oft", "3"),
        _occ("adj_noun", "in sicherer entfernung", "4", ["in", "sicherer", "Entfernung"]),
        _occ("verb_prep", "warten auf", "1", ["warten", "auf"]),  # same sentence twice
        _occ("noun_verb", "frage stellen", "5", ["Frage", "stellen"]),
    ]


def _fingerprint_of(tmp_path: Path, source: str, name: str) -> str:
    import importlib
    import sys

    (tmp_path / f"{name}.py").write_text(source, encoding="utf-8")
    # The path finder caches directory listings; a file written a moment
    # ago can be missing from them.
    importlib.invalidate_caches()
    sys.path.insert(0, str(tmp_path))
    try:
        return source_fingerprint(name, skip_classes=frozenset({"Builder"}))
    finally:
        sys.path.remove(str(tmp_path))


def test_source_fingerprint_ignores_comments_and_skipped_classes(tmp_path: Path) -> None:
    # The module name is part of the digest, so each pair shares one name.
    base = "X = 1\n\nclass Builder:\n    y = 2\n"
    commented = "# note\nX = 1  # one\n\nclass Builder:\n    y = 2\n"
    rebuilt = "X = 1\n\nclass Builder:\n    y = 3\n"
    changed = "X = 2\n\nclass Builder:\n    y = 2\n"
    assert _fingerprint_of(tmp_path, base, "fp_a") == _fingerprint_of(tmp_path, commented, "fp_a")
    assert _fingerprint_of(tmp_path, base, "fp_b") == _fingerprint_of(tmp_path, rebuilt, "fp_b")
    assert _fingerprint_of(tmp_path, base, "fp_c") != _fingerprint_of(tmp_path, changed, "fp_c")


def test_source_fingerprint_of_a_missing_module_is_an_error() -> None:
    with pytest.raises(ValueError, match="no Python source"):
        source_fingerprint("src.no_such_module")


def test_source_fingerprint_of_the_miner_follows_its_imports() -> None:
    """The canonicalisation the cache holds calls into the lemmatiser and
    the paradigms; a change there must invalidate it too."""
    assert source_fingerprint("src.phrases.units") != source_fingerprint(
        "src.phrases.units", skip_classes=frozenset({"UnitBuilder"})
    )


def test_aggregate_continued_tally_equals_one_pass() -> None:
    occs = _sample()
    whole = aggregate(occs)
    continued = aggregate(occs[3:], stats=aggregate(occs[:3]))
    assert list(whole) == list(continued)
    for ident in whole:
        assert whole[ident] == continued[ident]


def test_candidate_unit_ids_cover_every_match() -> None:
    """The cards stage skips a cached line whose tags miss every wanted id,
    so the tags must include whatever ``match_to_unit`` could return."""
    for occ in _sample():
        candidates = candidate_unit_ids(occ)
        for wanted in [*candidates, "nn:frage", "av:oft", "an:sicherer_entfernung"]:
            matched = match_to_unit(occ, {wanted})
            if matched is not None:
                assert matched[0] in candidates
    assert "av:oft" in candidate_unit_ids(_occ("adjective", "oft", "3"))
    bare = unit_id_for("adj_noun", "sicherer entfernung")
    tagged = candidate_unit_ids(
        _occ("adj_noun", "in sicherer entfernung", "4", ["in", "sicherer", "Entfernung"])
    )
    assert bare in tagged


def _parse_file(tmp_path: Path, occs: list[Occurrence]) -> Path:
    path = tmp_path / "occurrences.jsonl"
    write_occurrences(path, occs)
    return path


def test_parse_cache_rebuild_then_load_gives_the_same_tally(tmp_path: Path) -> None:
    parse = _parse_file(tmp_path, _sample())
    data = tmp_path / "vocab.json"
    data.write_text("{}", encoding="utf-8")
    key = parse_cache_key(parse, [data])
    cache = ParseCache(tmp_path / "cache")
    assert not cache.is_current(key)
    built = cache.rebuild(parse, key, None, None, candidate_unit_ids)
    assert cache.is_current(key)
    loaded = cache.load_stats()
    assert list(loaded) == list(built) == list(aggregate(_sample()))
    assert all(loaded[i] == built[i] for i in built)


def test_parse_cache_key_changes_with_the_file_and_the_data(tmp_path: Path) -> None:
    parse = _parse_file(tmp_path, _sample())
    data = tmp_path / "vocab.json"
    data.write_text("{}", encoding="utf-8")
    key = parse_cache_key(parse, [data])
    cache = ParseCache(tmp_path / "cache")
    cache.rebuild(parse, key, None, None, candidate_unit_ids)
    data.write_text('{"warten": "A1"}', encoding="utf-8")
    assert not cache.is_current(parse_cache_key(parse, [data]))
    _parse_file(tmp_path, _sample()[:2])
    assert not cache.is_current(parse_cache_key(parse, [data]))


def test_parse_cache_rebuild_drops_the_old_key_before_replacing_files(tmp_path: Path) -> None:
    """A rebuild killed halfway must not leave the previous key standing
    over files it already replaced: that key could match again later."""
    parse = _parse_file(tmp_path, _sample())
    key = parse_cache_key(parse, [])
    cache = ParseCache(tmp_path / "cache")
    cache.rebuild(parse, key, None, None, candidate_unit_ids)

    def killed(occ: Occurrence) -> list[str]:
        raise KeyboardInterrupt

    try:
        cache.rebuild(parse, key, None, None, killed)
    except KeyboardInterrupt:
        pass
    assert not cache.key_path.exists()
    assert not cache.is_current(key)


def test_canonical_for_reads_only_tagged_lines_in_file_order(tmp_path: Path) -> None:
    parse = _parse_file(tmp_path, _sample())
    key = parse_cache_key(parse, [])
    cache = ParseCache(tmp_path / "cache")
    cache.rebuild(parse, key, None, None, candidate_unit_ids)
    got = list(cache.canonical_for({"vp:warten_auf", "av:oft"}))
    assert [(o.unit_key, o.line_id) for o in got] == [
        ("warten auf", "1"),
        ("warten auf", "2"),
        ("oft", "3"),
        ("warten auf", "1"),
    ]
    assert list(cache.canonical_for({"nn:nichts"})) == []


def test_verdict_cache_reuses_verdicts_and_drops_them_on_a_new_key(tmp_path: Path) -> None:
    path = tmp_path / "verdicts.json"
    calls: list[str] = []

    def validate(text: str) -> bool:
        calls.append(text)
        return text.endswith(".")

    first = VerdictCache(path, "k1")
    wrapped = first.wrap(validate)
    assert wrapped("Gut.") is True
    assert wrapped("Schlecht") is False
    first.save()
    assert calls == ["Gut.", "Schlecht"]

    again = VerdictCache(path, "k1").wrap(validate)
    assert again("Gut.") is True and again("Schlecht") is False
    assert calls == ["Gut.", "Schlecht"], "known sentences are not validated twice"

    VerdictCache(path, "k2").wrap(validate)("Gut.")
    assert calls == ["Gut.", "Schlecht", "Gut."], "a new validator starts afresh"
    assert json.loads(path.read_text(encoding="utf-8"))["key"] == "k1"


def test_file_digest_of_a_missing_file_is_stable(tmp_path: Path) -> None:
    assert file_digest(tmp_path / "nope") == "missing"
