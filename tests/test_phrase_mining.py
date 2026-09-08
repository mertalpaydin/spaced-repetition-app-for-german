"""One hand sentence per detector, checked on key, gaps, case and form."""

from pathlib import Path

import pytest
from src.phrases import carrier_validation, parse
from src.phrases.curated import load_curated
from src.phrases.mining import LemmaCounts, detect_all
from src.phrases.occurrences import Occurrence

requires_model = pytest.mark.skipif(
    not parse.parser_available(), reason="de_core_news_sm is not installed"
)

CURATED = load_curated(Path("data/phrases"))
DICTIONARY = carrier_validation._load_dictionary()  # noqa: SLF001


def _detect(text: str) -> list[Occurrence]:
    sentence = parse.parse_one(text)
    assert sentence is not None
    return detect_all(sentence, CURATED, source="tatoeba", line_id="1", dictionary=DICTIONARY)


def _one(text: str, kind: str, key: str) -> Occurrence:
    matches = [o for o in _detect(text) if o.kind == kind and o.unit_key == key]
    assert len(matches) == 1, [(o.kind, o.unit_key) for o in _detect(text)]
    return matches[0]


@requires_model
def test_verb_prep_finds_warten_auf_with_accusative_and_both_gaps() -> None:
    occ = _one("Ich warte auf den Bus.", "verb_prep", "warten auf")
    assert occ.surfaces == ["warte", "auf"]
    assert occ.token_indices == [1, 2]
    assert occ.case == "Akk"
    assert occ.form_key == "Fin|Pres|1|Sing"
    assert [occ.text[s:e] for s, e in occ.spans] == occ.surfaces


@requires_model
def test_verb_prep_skips_a_temporal_frame() -> None:
    assert not [o for o in _detect("Das Konzert findet am Samstag statt.") if o.kind == "verb_prep"]


@requires_model
def test_reflexive_with_preposition_has_three_gaps_in_sentence_order() -> None:
    occ = _one("Sie interessiert sich für Musik.", "reflexive_verb", "sich interessieren für")
    assert occ.surfaces == ["interessiert", "sich", "für"]
    assert occ.case == "Akk"


@requires_model
def test_reflexive_accepts_first_person_pronoun_bound_to_the_verb() -> None:
    occ = _one("Ich erinnere mich an dich.", "reflexive_verb", "sich erinnern an")
    assert occ.surfaces == ["erinnere", "mich", "an"]


@requires_model
def test_separable_verb_is_discontinuous_with_verb_and_particle_gaps() -> None:
    occ = _one("Er steht jeden Tag früh auf.", "separable_verb", "aufstehen")
    assert occ.surfaces == ["steht", "auf"]
    assert occ.token_indices == [1, 5]
    assert occ.form_key.endswith("discontinuous")


@requires_model
def test_separable_verb_fused_infinitive_is_marked_fused() -> None:
    occ = _one("Er will morgen früh aufstehen.", "separable_verb", "aufstehen")
    assert occ.surfaces == ["aufstehen"]
    assert occ.form_key == "Inf|fused"


@requires_model
def test_two_part_connector_zwar_aber() -> None:
    occ = _one("Das Zimmer ist zwar klein, aber gemütlich.", "two_part_connector", "zwar … aber")
    assert occ.surfaces == ["zwar", "aber"]
    assert occ.token_indices == [3, 6]


@requires_model
def test_je_desto_requires_a_comparative_after_je() -> None:
    assert [
        o
        for o in _detect("Je mehr man übt, desto besser wird man.")
        if o.kind == "two_part_connector"
    ]
    assert not [
        o for o in _detect("Je Person ein Stück, desto mehr.") if o.kind == "two_part_connector"
    ]


@requires_model
def test_sentence_initial_connector_needs_context() -> None:
    occ = _one("Trotzdem kam sie.", "connector", "trotzdem")
    assert occ.sentence_initial and occ.needs_context
    medial = _one("Sie kam trotzdem.", "connector", "trotzdem")
    assert not medial.needs_context


@requires_model
def test_idiom_matches_with_a_gap() -> None:
    occ = _one("Es geht hier um Geld.", "idiom", "es geht um")
    assert occ.surfaces == ["Es", "geht", "um"]


@requires_model
def test_noun_verb_finds_the_object_and_its_participle() -> None:
    occ = _one("Wir haben eine Entscheidung getroffen.", "noun_verb", "entscheidung treffen")
    assert occ.surfaces == ["Entscheidung", "getroffen"]


@requires_model
def test_noun_verb_finds_a_funktionsverbgefuege_through_cvc() -> None:
    occ = _one("Das steht mir zur Verfügung.", "noun_verb", "zur verfügung stehen")
    assert occ.surfaces == ["steht", "zur", "Verfügung"]


@requires_model
def test_noun_verb_uses_haben_as_a_collocation_head() -> None:
    occ = _one("Ich habe Hunger.", "noun_verb", "hunger haben")
    assert occ.surfaces == ["habe", "Hunger"]


@requires_model
def test_noun_verb_ignores_a_subject_noun() -> None:
    assert not [o for o in _detect("Die Polizei ermittelt.") if o.kind == "noun_verb"]


@requires_model
def test_adj_noun_finds_starken_kaffee() -> None:
    occ = _one("Er trinkt starken Kaffee.", "adj_noun", "stark kaffee")
    assert occ.surfaces == ["starken", "Kaffee"]


@requires_model
def test_adj_noun_skips_ordinals_left_as_their_own_lemma() -> None:
    assert not [o for o in _detect("Es war das erste Mal.") if o.kind == "adj_noun"]


@requires_model
def test_lemma_counts_count_each_lemma_once_per_sentence() -> None:
    counts = LemmaCounts()
    sentence = parse.parse_one("Ich warte und warte auf den Bus.")
    assert sentence is not None
    counts.add(sentence)
    assert counts.sentences == 1
    assert counts.verbs["warten"] == 1
    assert counts.prepositions["auf"] == 1
    assert LemmaCounts.from_dict(counts.to_dict()).verbs == counts.verbs
