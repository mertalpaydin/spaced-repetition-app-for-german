"""The verb drill's forms: the weak-verb rule, the shape checks, the cards
and their export (owner, 2026-11-04)."""

from datetime import UTC, datetime
from pathlib import Path

from src.contracts import PhraseUnit, VerbFormsRecord
from src.phrases.export import check_deck, export_deck
from src.phrases.verb_forms import (
    check_record,
    doubtful,
    drill_unit_id,
    drill_verbs,
    split_particle,
    verb_cards,
    weak_forms,
)

T = datetime(2026, 11, 4, tzinfo=UTC)


def _record(infinitive: str, prae: list[str], aux: list[str], part: list[str]) -> VerbFormsRecord:
    return VerbFormsRecord(
        infinitive=infinitive, gloss_en=f"to {infinitive}", praeteritum=prae,
        auxiliary=aux, partizip=part, accepted=True, model="test", generated_at=T,  # type: ignore[arg-type]
    )  # fmt: skip


def _unit(unit_id: str, kind: str, display: str, rank: int, gloss: str | None = None) -> PhraseUnit:
    return PhraseUnit.model_validate(
        {
            "unit_id": unit_id, "kind": kind, "lemma_key": display, "parts": [display],
            "display_de": display, "gloss_en": gloss, "sentence_count": 10, "rank": rank,
            "source": "mined", "card_count": 1,
        }
    )  # fmt: skip


def test_weak_forms_follow_the_rule_and_its_exceptions() -> None:
    assert weak_forms("machen", False) == ("machte", "gemacht")
    assert weak_forms("arbeiten", False) == ("arbeitete", "gearbeitet")
    assert weak_forms("öffnen", False) == ("öffnete", "geöffnet")
    assert weak_forms("rechnen", False) == ("rechnete", "gerechnet")
    assert weak_forms("lernen", False) == ("lernte", "gelernt")
    assert weak_forms("studieren", False) == ("studierte", "studiert")
    assert weak_forms("besuchen", False) == ("besuchte", "besucht")
    assert weak_forms("lächeln", False) == ("lächelte", "gelächelt")
    assert weak_forms("aufhören", True) == ("hörte auf", "aufgehört")
    assert weak_forms("vorbereiten", True) == ("bereitete vor", "vorbereitet")


def test_split_particle_takes_the_longest_that_leaves_a_verb() -> None:
    assert split_particle("herausfinden") == ("heraus", "finden")
    assert split_particle("anrufen") == ("an", "rufen")
    assert split_particle("teilnehmen") == ("teil", "nehmen")


def test_check_record_wants_the_particle_after_the_verb_and_in_front() -> None:
    assert (
        check_record(_record("aufstehen", ["stand auf"], ["ist"], ["aufgestanden"]), True) is None
    )
    assert check_record(_record("aufstehen", ["aufstand"], ["ist"], ["aufgestanden"]), True) == (
        "particle_not_after_the_verb"
    )
    assert check_record(_record("aufstehen", ["stand auf"], ["ist"], ["gestanden"]), True) == (
        "particle_not_in_front"
    )
    assert check_record(_record("gehen", ["ging"], [], ["gegangen"]), False) == "missing_form"
    assert check_record(_record("gehen", ["ging weg"], ["ist"], ["gegangen"]), False) == (
        "praeteritum_not_one_word"
    )


def test_doubtful_flags_a_form_no_dictionary_knows_even_a_regular_looking_one() -> None:
    """ "gehte"/"gegeht" is what the weak rule gives for "gehen", so agreeing
    with the rule must not let a strong verb through."""
    words = frozenset({"ging", "gegangen", "stand"})
    assert doubtful(_record("gehen", ["ging"], ["ist"], ["gegangen"]), words) == []
    assert doubtful(_record("gehen", ["gehte"], ["ist"], ["gegeht"]), words) == ["gehte", "gegeht"]
    assert doubtful(_record("aufstehen", ["stand auf"], ["ist"], ["aufgestanden"]), words) == [
        "aufgestanden"
    ]


def test_drill_verbs_puts_sein_and_tun_first_and_takes_each_infinitive_once() -> None:
    units = [
        _unit("vb:gehen", "verb", "gehen", 8),
        _unit("sv:aufstehen", "separable_verb", "aufstehen", 90),
        _unit("vb:aufstehen", "verb", "aufstehen", 95),
        _unit("vb:moechten", "verb", "möchten", 30),
        _unit("sv:bekannt_geben", "separable_verb", "bekannt geben", 40),
        _unit("nn:haus", "noun", "das Haus", 2),
    ]
    got = [(inf, sep) for inf, sep, _ in drill_verbs(units)]
    assert got == [("sein", False), ("tun", False), ("gehen", False), ("aufstehen", True)]


def test_verb_cards_and_export_round_trip(tmp_path: Path) -> None:
    units = [
        _unit("vb:gehen", "verb", "gehen", 8, gloss="to go"),
        _unit("vb:machen", "verb", "machen", 5),
        _unit("sv:aufstehen", "separable_verb", "aufstehen", 90, gloss="to get up"),
    ]
    records = {
        "sein": _record("sein", ["war"], ["ist"], ["gewesen"]),
        "gehen": _record("gehen", ["ging"], ["ist"], ["gegangen"]),
        "machen": _record("machen", ["machte"], ["hat"], ["gemacht"]),
        "aufstehen": _record("aufstehen", ["aufstand"], ["ist"], ["aufgestanden"]),  # malformed
    }
    cards = verb_cards(units, records)
    assert [c.unit_id for c in cards] == ["pf:sein", "pf:machen", "pf:gehen"]
    by_id = {c.unit_id: c for c in cards}
    assert by_id["pf:gehen"].gloss_en == "to go", "the deck's English wins"
    assert by_id["pf:machen"].gloss_en == "to machen", "the record's English when the deck has none"
    assert by_id["pf:machen"].weak and not by_id["pf:gehen"].weak
    assert drill_unit_id("aufstehen") == "pf:aufstehen"

    manifest = export_deck(units, [], tmp_path, verbs=cards)
    assert manifest.verbs_file == "verbs.json" and manifest.verb_count == 3
    assert check_deck(tmp_path) == []
    # a stale verbs_version is caught
    (tmp_path / "verbs.json").write_text(
        (tmp_path / "verbs.json").read_text(encoding="utf-8").replace("gegangen", "gegeht"),
        encoding="utf-8",
    )
    assert "manifest verbs_version does not match the verbs" in check_deck(tmp_path)
