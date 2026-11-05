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
    form_requests,
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
    # "weg" is not the start of "gehen", so it is no particle of it
    assert check_record(_record("gehen", ["ging weg"], ["ist"], ["gegangen"]), False) == (
        "particle_not_after_the_verb"
    )


def test_check_record_reads_the_particle_from_the_draft() -> None:
    """Separable verbs the deck files as plain verbs, particles no list has
    ("davon", "raus"), a particle in two words, a one-word alternative."""
    assert (
        check_record(_record("davonlaufen", ["lief davon"], ["ist"], ["davongelaufen"]), False)
        is None
    )
    assert (
        check_record(_record("rausgehen", ["ging raus"], ["ist"], ["rausgegangen"]), True) is None
    )
    wieder = _record("wiederherstellen", ["stellte wieder her"], ["hat"], ["wiederhergestellt"])
    assert check_record(wieder, True) is None
    both = _record("anerkennen", ["erkannte an", "anerkannte"], ["hat"], ["anerkannt"])
    assert check_record(both, True) is None
    assert check_record(_record("rausgehen", ["ging raus"], ["ist"], ["gegangen"]), True) == (
        "particle_not_in_front"
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


def test_doubtful_with_the_store_derives_from_a_known_base_or_the_weak_rule() -> None:
    words = frozenset({"kam", "gekommen", "ging", "gegangen"})
    records = {
        "kommen": _record("kommen", ["kam"], ["ist"], ["gekommen"]),
        "gehen": _record("gehen", ["ging"], ["ist"], ["gegangen"]),
    }
    ankommen = _record("ankommen", ["kam an"], ["ist"], ["angekommen"])
    bekommen = _record("bekommen", ["bekam"], ["hat"], ["bekommen"])
    assert doubtful(ankommen, words, records) == []
    assert doubtful(bekommen, words, records) == []
    # a regular verb with no irregular verb inside it: the rule vouches
    assert (
        doubtful(
            _record("rausschaffen", ["schaffte raus"], ["hat"], ["rausgeschafft"]), words, records
        )
        == []
    )
    # a compound of an irregular verb drafted weak: nothing vouches
    weak = _record("weggehen", ["gehte weg"], ["ist"], ["weggegeht"])
    assert doubtful(weak, words, records) == ["gehte weg", "weggegeht"]


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


def test_reflexive_verbs_derive_their_forms_from_the_base_verb() -> None:
    """ "sich setzen": setzte sich, hat sich gesetzt; "sich vorstellen": stellte
    sich vor, hat sich vorgestellt. A reflexive verb takes "hat", whatever
    the base takes ("sich verlaufen" beside "ist verlaufen")."""
    reflexive = PhraseUnit.model_validate(
        {
            "unit_id": "rv:sich_setzen", "kind": "reflexive_verb", "lemma_key": "sich setzen",
            "parts": ["sich", "setzen"], "display_de": "sich setzen", "gloss_en": "to sit down",
            "sentence_count": 10, "rank": 300, "source": "mined", "card_count": 1,
        }
    )  # fmt: skip
    vorstellen = reflexive.model_copy(
        update={"unit_id": "rv:sich_vorstellen", "lemma_key": "sich vorstellen",
                "parts": ["sich", "vorstellen"], "display_de": "sich vorstellen",
                "gloss_en": "to imagine", "rank": 310}
    )  # fmt: skip
    verlaufen = reflexive.model_copy(
        update={"unit_id": "rv:sich_verlaufen", "lemma_key": "sich verlaufen",
                "parts": ["sich", "verlaufen"], "display_de": "sich verlaufen",
                "gloss_en": "to get lost", "rank": 320}
    )  # fmt: skip
    records = {
        "setzen": _record("setzen", ["setzte"], ["hat"], ["gesetzt"]),
        "vorstellen": _record("vorstellen", ["stellte vor"], ["hat"], ["vorgestellt"]),
        "verlaufen": _record("verlaufen", ["verlief"], ["ist"], ["verlaufen"]),
    }
    stellen = reflexive.model_copy(
        update={
            "unit_id": "vb:stellen",
            "kind": "verb",
            "lemma_key": "stellen",
            "parts": ["stellen"],
            "display_de": "stellen",
            "rank": 50,
        }
    )
    assert form_requests([reflexive, vorstellen, verlaufen, stellen])[-3:] == [
        ("setzen", False, None), ("vorstellen", True, None), ("verlaufen", False, None),
    ]  # fmt: skip
    cards = {c.unit_id: c for c in verb_cards([reflexive, vorstellen, verlaufen], records)}
    assert cards["pf:sich_setzen"].praeteritum == ["setzte sich"]
    assert cards["pf:sich_setzen"].partizip == ["sich gesetzt"]
    assert cards["pf:sich_setzen"].gloss_en == "to sit down", "the reflexive's own English"
    assert cards["pf:sich_vorstellen"].praeteritum == ["stellte sich vor"]
    assert cards["pf:sich_verlaufen"].auxiliary == ["hat"]
    assert cards["pf:sich_setzen"].infinitive == "sich setzen"


def test_a_reflexive_verb_s_base_comes_from_its_citation_form() -> None:
    """The key can be misspelt where an override fixed the display
    ("sich_umschaun" shown as "sich umschauen"), and the key hides what the
    display shows: "sich einschüchtern lassen" was drilled as "schüchterte
    sich ein" (2026-11-05). A reflexive verb is in the drill when its
    citation form is "sich" and one verb, whatever case it governs."""
    from src.phrases.verb_forms import drill_reflexives

    def rv(key: str, display: str, rank: int) -> PhraseUnit:
        return PhraseUnit.model_validate(
            {
                "unit_id": f"rv:sich_{key}", "kind": "reflexive_verb", "lemma_key": f"sich {key}",
                "parts": ["sich", key], "display_de": display, "gloss_en": "x",
                "sentence_count": 10, "rank": rank, "source": "mined", "card_count": 1,
            }
        )  # fmt: skip

    units = [
        rv("umschaun", "sich umschauen", 1),
        rv("schnappen", "sich schnappen +Dat", 2),
        rv("einschuechtern", "sich einschüchtern lassen", 3),
        rv("zuhalten", "sich die Ohren zuhalten", 4),
        rv("annaeheren", "sich annähern an", 5),
    ]
    assert [base for base, _ in drill_reflexives(units)] == ["umschauen", "schnappen"]


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
