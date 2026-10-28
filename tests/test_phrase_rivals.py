"""Rivals and synonyms: words that are easy to mix up, and words that are
the same word (owner, 2026-10-24)."""

from src.contracts import GapSpan, PhraseCard, PhraseUnit
from src.engine.grading import grade_card, rival_typed, unit_forms
from src.phrases.rivals import apply_synonyms, assign_rivals, senses


def _unit(
    unit_id: str, kind: str, key: str, gloss: str | None, rank: int, **extra: object
) -> PhraseUnit:
    return PhraseUnit.model_validate(
        {
            "unit_id": unit_id,
            "kind": kind,
            "lemma_key": key,
            "parts": key.split(),
            "display_de": key,
            "gloss_en": gloss,
            "sentence_count": 10,
            "rank": rank,
            "source": "mined",
            "card_count": 1,
            **extra,
        }
    )


def _card(card_id: str, unit_id: str, text: str, answer: str) -> PhraseCard:
    start = text.index(answer)
    return PhraseCard(
        card_id=card_id,
        unit_id=unit_id,
        kind="verb",
        sentence_de=text,
        gloss_en="English",
        gloss_source="azure",
        gaps=[GapSpan(start=start, end=start + len(answer), answer=answer, token_index=0)],
        answers=[answer],
        form_key="Fin|Pres|3|Sing",
        corpus_source="tatoeba",
        corpus_line_id="1",
    )


def test_senses_drops_articles_notes_and_generic_words() -> None:
    assert senses("to stop / to detain (s.o.) / to get") == {"stop", "detain"}
    assert senses("a decision / the choice") == {"decision", "choice"}
    assert senses(None) == set()


def test_assign_rivals_pairs_shared_english_in_one_word_class_only() -> None:
    units = [
        _unit("vb:bekommen", "verb", "bekommen", "to get / to receive", 1),
        _unit("vb:kriegen", "verb", "kriegen", "to get / to receive", 5),
        # same English, different class: a noun cannot stand in a verb's gap
        _unit("nn:empfang", "noun", "empfang", "receive", 3),
        # same head word: "bitten um" typed for "bitten" is graded as the verb
        _unit("vb:bitten", "verb", "bitten", "to ask / to request", 2),
        _unit("vp:bitten_um", "verb_prep", "bitten um", "to ask for / to request", 4),
    ]
    by_id = {u.unit_id: u for u in assign_rivals(units)}
    assert by_id["vb:bekommen"].rivals == ["vb:kriegen"]
    assert by_id["vb:kriegen"].rivals == ["vb:bekommen"]
    assert by_id["nn:empfang"].rivals == []
    assert by_id["vb:bitten"].rivals == []


def test_assign_rivals_skips_a_word_the_unit_already_accepts() -> None:
    units = [
        _unit("aj:statt", "adverb", "statt", "instead", 1, also_accepted=["anstatt"]),
        _unit("aj:anstatt", "adverb", "anstatt", "instead", 2, also_accepted=["statt"]),
    ]
    assert all(u.rivals == [] for u in assign_rivals(units))


def test_apply_synonyms_makes_each_word_accept_the_others() -> None:
    units = [
        _unit("aj:statt", "adverb", "statt", "instead", 1),
        _unit("aj:anstatt", "adverb", "anstatt", "instead", 2),
        _unit("cn:deshalb", "connector", "deshalb", "therefore", 3, also_accepted=["daher"]),
    ]
    out = {
        u.lemma_key: u for u in apply_synonyms(units, [["statt", "anstatt"], ["deshalb", "daher"]])
    }
    assert out["statt"].also_accepted == ["anstatt"]
    assert out["anstatt"].also_accepted == ["statt"]
    assert out["deshalb"].also_accepted == ["daher"], "no duplicate of a curated alternative"


def test_rival_typed_kriegen_for_bekommen_is_a_rival() -> None:
    """The owner typed "kriegen" for "bekommen" four times; each cost a
    restart of the unit. It is the right idea and the wrong word."""
    bekommen = _unit("vb:bekommen", "verb", "bekommen", "to get", 1, rivals=["vb:kriegen"])
    kriegen = _unit("vb:kriegen", "verb", "kriegen", "to get", 5)
    cards = {
        "vb:bekommen": [_card("b" * 12, "vb:bekommen", "Er bekommt ein Buch.", "bekommt")],
        "vb:kriegen": [_card("k" * 12, "vb:kriegen", "Er kriegt ein Buch.", "kriegt")],
    }
    units = {"vb:bekommen": bekommen, "vb:kriegen": kriegen}

    def forms_of(unit_id: str) -> frozenset[str]:
        return unit_forms(units[unit_id], cards[unit_id])

    card = cards["vb:bekommen"][0]
    assert rival_typed(bekommen, grade_card(card, ["kriegt"]), forms_of) == "vb:kriegen"
    assert rival_typed(bekommen, grade_card(card, ["kriegen"]), forms_of) == "vb:kriegen"
    # a word that is no rival, the right answer, a revealed gap: none
    assert rival_typed(bekommen, grade_card(card, ["kauft"]), forms_of) is None
    assert rival_typed(bekommen, grade_card(card, ["bekommt"]), forms_of) is None
    assert rival_typed(bekommen, grade_card(card, [None]), forms_of) is None
    # the unit's own form in another shape is graded as itself, not a rival
    assert rival_typed(bekommen, grade_card(card, ["bekommen"]), forms_of) is None


def test_rival_typed_accepts_an_inflected_form_or_a_one_letter_slip() -> None:
    """The owner typed "bekannte" for "berühmte" and "Verhältnise" for
    "Beziehung" (2026-10-26/27). Both are rivals, and the retry did not
    come, because only a rival's exact card forms counted."""
    beruehmt = _unit("aj:beruehmt", "adjective", "berühmt", "famous", 2, rivals=["aj:bekannt"])
    bekannt = _unit("aj:bekannt", "adjective", "bekannt", "known / famous", 1)
    beziehung = _unit(
        "nn:beziehung", "noun", "beziehung", "relationship", 3, rivals=["nn:verhaeltnis"]
    )
    verhaeltnis = _unit("nn:verhaeltnis", "noun", "verhältnis", "relationship", 4)
    cards = {
        "aj:beruehmt": [_card("r" * 12, "aj:beruehmt", "Sie war eine berühmte Frau.", "berühmte")],
        "aj:bekannt": [_card("k" * 12, "aj:bekannt", "Das ist bekannt.", "bekannt")],
        "nn:beziehung": [_card("z" * 12, "nn:beziehung", "Die Beziehung hält.", "Beziehung")],
        "nn:verhaeltnis": [
            _card("v" * 12, "nn:verhaeltnis", "Die Verhältnisse sind gut.", "Verhältnisse")
        ],
    }
    units = {u.unit_id: u for u in (beruehmt, bekannt, beziehung, verhaeltnis)}

    def forms_of(unit_id: str) -> frozenset[str]:
        return unit_forms(units[unit_id], cards[unit_id])

    card = cards["aj:beruehmt"][0]
    assert rival_typed(beruehmt, grade_card(card, ["bekannte"]), forms_of) == "aj:bekannt"
    card = cards["nn:beziehung"][0]
    assert rival_typed(beziehung, grade_card(card, ["Verhältnise"]), forms_of) == "nn:verhaeltnis"
    # a slip of the asked word itself is the asked word, not a rival
    assert rival_typed(beziehung, grade_card(card, ["Beziehnug"]), forms_of) is None
