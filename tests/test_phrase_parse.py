"""The parse layer: one spaCy load, the lexical-verb chain, lemma repair."""

import pytest
from src.phrases import parse

requires_model = pytest.mark.skipif(
    not parse.parser_available(), reason="de_core_news_sm is not installed"
)


@requires_model
def test_parse_one_carries_dependency_labels_and_char_offsets() -> None:
    sentence = parse.parse_one("Ich warte auf den Bus.")
    assert sentence is not None
    prep = sentence.tokens[2]
    assert prep.text == "auf" and prep.pos == "ADP" and prep.dep in {"mo", "op"}
    assert sentence.text[prep.idx : prep.end] == "auf"


@requires_model
def test_lexical_verb_descends_through_an_auxiliary_to_the_participle() -> None:
    sentence = parse.parse_one("Ich habe lange auf dich gewartet.")
    assert sentence is not None
    prep = next(t for t in sentence.tokens if t.text == "auf")
    verb = parse.lexical_verb(sentence, prep.i)
    assert verb is not None and verb.text == "gewartet"


@requires_model
def test_lexical_verb_descends_through_a_modal_tagged_as_verb() -> None:
    sentence = parse.parse_one("Ich muss mir das überlegen.")
    assert sentence is not None
    pron = next(t for t in sentence.tokens if t.text == "mir")
    verb = parse.lexical_verb(sentence, pron.i)
    assert verb is not None and verb.text == "überlegen"


@requires_model
def test_verb_lemma_key_reattaches_a_separated_particle() -> None:
    sentence = parse.parse_one("Er steht jeden Tag früh auf.")
    assert sentence is not None
    verb = next(t for t in sentence.tokens if t.text == "steht")
    assert parse.verb_lemma_key(sentence, verb) == "aufstehen"


@requires_model
def test_is_sentence_initial_ignores_a_leading_quote() -> None:
    sentence = parse.parse_one('"Trotzdem kam sie", sagte er.')
    assert sentence is not None
    trotzdem = next(t for t in sentence.tokens if t.text == "Trotzdem")
    assert parse.is_sentence_initial(sentence, trotzdem.i)
    kam = next(t for t in sentence.tokens if t.text == "kam")
    assert not parse.is_sentence_initial(sentence, kam.i)


def test_repair_verb_lemma_fixes_the_taggers_eren_slip() -> None:
    assert parse.repair_verb_lemma("erinneren") == "erinnern"


def test_repair_verb_lemma_rejects_a_surface_form_left_as_lemma() -> None:
    assert parse.repair_verb_lemma("muss") == ""


def test_form_key_distinguishes_finite_and_nonfinite_forms() -> None:
    finite = parse.ParsedToken(
        i=0,
        text="wartet",
        lemma="warten",
        pos="VERB",
        tag="VVFIN",
        dep="ROOT",
        head=0,
        idx=0,
        morph={"VerbForm": "Fin", "Tense": "Pres", "Person": "3", "Number": "Sing"},
    )
    participle = parse.ParsedToken(
        i=0,
        text="gewartet",
        lemma="warten",
        pos="VERB",
        tag="VVPP",
        dep="oc",
        head=0,
        idx=0,
        morph={"VerbForm": "Part"},
    )
    assert parse.form_key(finite) == "Fin|Pres|3|Sing"
    assert parse.form_key(participle, suffix="fused") == "Part|fused"


def test_repair_verb_lemma_rejects_a_garbled_infinitive() -> None:
    """The tagger produces "benimmsen" for "benimmst"; the dictionary says no."""
    assert parse.repair_verb_lemma("benimmsen") == ""
    assert parse.repair_verb_lemma("erinnerstn") == ""
    assert parse.repair_verb_lemma("warten") == "warten"
