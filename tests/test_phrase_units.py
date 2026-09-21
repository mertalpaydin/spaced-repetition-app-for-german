"""Thresholds, association, seeds, rank and the trivial flag, on synthetic
occurrences (no spaCy)."""

from collections import Counter

import pytest
from src.lexicon.vocabulary import VocabularyStore
from src.phrases.curated import (
    CollocationSeed,
    ConnectorSpec,
    CuratedLists,
    IdiomElement,
    IdiomSpec,
    VerbPrepSeed,
)
from src.phrases.mining import LemmaCounts
from src.phrases.occurrences import Occurrence
from src.phrases.units import Thresholds, UnitBuilder, log_likelihood, slug, unit_id_for


def _occ(kind: str, key: str, parts: list[str], n: int, **extra: object) -> list[Occurrence]:
    out = []
    for i in range(n):
        text = f"Satz {i} {key}."
        out.append(
            Occurrence(
                kind=kind,  # type: ignore[arg-type]
                unit_key=key,
                parts=parts,
                token_indices=[1, 2],
                spans=[(0, 4), (5, 6)],
                surfaces=[text[0:4], text[5:6]],
                corpus_source="tatoeba",
                line_id=str(i),
                text=text,
                **extra,  # type: ignore[arg-type]
            )
        )
    return out


def _counts(**verbs: int) -> LemmaCounts:
    return LemmaCounts(
        sentences=10_000,
        verbs=Counter(verbs),
        nouns=Counter({"entscheidung": 40, "kaffee": 60}),
        adjectives=Counter({"stark": 50}),
        prepositions=Counter({"auf": 400, "an": 400, "mit": 900}),
    )


#: The adjective-noun bar was raised hard on 2026-09-20 (count 8 -> 40, lift
#: 8 -> 25) because the adjective and the noun are now units in their own
#: right. These tests are about the mechanism, not about the tuned constant,
#: so they keep the bar they were written against.
ADJ_NOUN_TEST_THRESHOLDS = Thresholds(adj_min_count=8, adj_min_lift=8.0, adj_cap_per_noun=4)


def _builder(
    counts: LemmaCounts,
    curated: CuratedLists | None = None,
    thresholds: Thresholds | None = None,
) -> UnitBuilder:
    return UnitBuilder(
        counts,
        curated or CuratedLists(),
        thresholds or Thresholds(),
        vocabulary=VocabularyStore({"warten": "A1", "kaffee": "A1"}, frequency_ranks={"und": 1}),
        frequency_ranks={"und": 1, "trotzdem": 900},
    )


def test_slug_and_unit_id_are_stable_ascii() -> None:
    assert slug("sich interessieren für") == "sich_interessieren_fuer"
    assert unit_id_for("two_part_connector", "zwar … aber") == "c2:zwar_aber"


def test_log_likelihood_is_zero_for_independence_and_large_for_association() -> None:
    assert log_likelihood(10, 100, 1000, 10_000) == pytest.approx(0.0, abs=1e-9)
    assert log_likelihood(50, 100, 100, 10_000) > 100


def test_verb_prep_needs_count_ratio_and_lift() -> None:
    builder = _builder(_counts(warten=100, gehen=5000))
    units = builder.build(
        _occ("verb_prep", "warten auf", ["warten", "auf"], 20, case="Akk")
        + _occ("verb_prep", "gehen auf", ["gehen", "auf"], 6)
    )
    keys = [u.lemma_key for u in units]
    assert "warten auf" in keys
    assert "gehen auf" not in keys  # 6/5000 is below the ratio
    assert builder.report["rejected"]["verb_prep:ratio"] == 1
    unit = units[0]
    assert unit.case == "Akk" and unit.cefr == "A1" and unit.rank == 1


def test_seed_wins_over_the_corpus_and_the_disagreement_is_reported() -> None:
    curated = CuratedLists(verb_prep_seeds=[VerbPrepSeed(verb="warten", prep="auf", case="Dat")])
    builder = _builder(_counts(warten=100), curated)
    units = builder.build(_occ("verb_prep", "warten auf", ["warten", "auf"], 20, case="Akk"))
    assert units[0].case == "Dat"
    assert units[0].source == "mined+curated"
    assert builder.report["seed_disagreements"] == [
        {"unit": "warten auf", "seed_case": "Dat", "corpus_case": "Akk"}
    ]


def test_zero_hit_seed_is_still_a_unit_ranked_last_and_reported() -> None:
    curated = CuratedLists(verb_prep_seeds=[VerbPrepSeed(verb="hoffen", prep="auf", case="Akk")])
    builder = _builder(_counts(warten=100), curated)
    units = builder.build(_occ("verb_prep", "warten auf", ["warten", "auf"], 20, case="Akk"))
    assert [u.lemma_key for u in units] == ["warten auf", "hoffen auf"]
    assert units[-1].sentence_count == 0 and units[-1].source == "curated"
    assert builder.report["zero_hit_curated"] == ["hoffen auf"]


def test_reflexive_prep_unit_replaces_the_bare_reflexive_and_the_plain_verb_prep() -> None:
    builder = _builder(_counts(interessieren=40))
    occurrences = (
        _occ("verb_prep", "interessieren für", ["interessieren", "für"], 20, case="Akk")
        + _occ("reflexive_verb", "sich interessieren", ["sich", "interessieren"], 22)
        + _occ(
            "reflexive_verb",
            "sich interessieren für",
            ["sich", "interessieren", "für"],
            20,
            case="Akk",
        )
    )
    keys = [u.lemma_key for u in builder.build(occurrences)]
    assert keys == ["sich interessieren für"]


def test_fused_only_separable_verb_is_dropped() -> None:
    builder = _builder(_counts())
    fused = _occ("separable_verb", "übersetzen", ["übersetzen"], 30, form_key="Inf|fused")
    real = _occ(
        "separable_verb", "aufstehen", ["aufstehen"], 30, form_key="Fin|Pres|3|Sing|discontinuous"
    )
    assert [u.lemma_key for u in builder.build(fused + real)] == ["aufstehen"]
    assert builder.report["rejected"]["separable_verb:fused_only"] == 1


def test_collocation_abstains_when_a_lemma_is_sparse() -> None:
    counts = _counts(treffen=30)
    counts.nouns["rarität"] = 6
    builder = _builder(counts)
    units = builder.build(_occ("noun_verb", "rarität treffen", ["Rarität", "treffen"], 6))
    assert units == []
    assert builder.report["rejected"]["noun_verb:sparse"] == 1


def test_collocation_passes_on_g2_and_lift_and_seed_display_wins() -> None:
    curated = CuratedLists(
        collocation_seeds=[
            CollocationSeed(
                key="entscheidung treffen", kind="noun_verb", display="eine Entscheidung treffen"
            )
        ]
    )
    builder = _builder(_counts(treffen=30, trinken=80), curated, ADJ_NOUN_TEST_THRESHOLDS)
    units = builder.build(
        _occ("noun_verb", "entscheidung treffen", ["Entscheidung", "treffen"], 12)
        + _occ("adj_noun", "stark kaffee", ["stark", "Kaffee"], 9)
    )
    by_key = {u.lemma_key: u for u in units}
    assert by_key["entscheidung treffen"].display_de == "eine Entscheidung treffen"
    assert "stark kaffee" in by_key


def test_trivial_flag_from_curated_rank_and_stoplist() -> None:
    curated = CuratedLists(
        connectors=[
            ConnectorSpec(key="und", kind="connector", forms=["und"], trivial=True),
            ConnectorSpec(key="trotzdem", kind="connector", forms=["trotzdem"]),
            ConnectorSpec(key="also", kind="connector", forms=["also"]),
        ],
        idioms=[IdiomSpec(key="es gibt", pattern=[IdiomElement(surface="es")])],
        trivial_stoplist=["also"],
    )
    builder = _builder(_counts(), curated)
    units = builder.build(
        _occ("connector", "und", ["und"], 50)
        + _occ("connector", "trotzdem", ["trotzdem"], 10)
        + _occ("connector", "also", ["also"], 9)
        + _occ("idiom", "es gibt", ["es gibt"], 8)
    )
    flags = {u.lemma_key: (u.trivial, u.trivial_reason) for u in units}
    assert flags["und"] == (True, "curated")
    assert flags["also"] == (True, "stoplist")
    assert flags["trotzdem"] == (False, None)
    assert flags["es gibt"] == (False, None)


def test_ranks_are_unique_and_follow_sentence_count() -> None:
    builder = _builder(_counts(warten=100, denken=100))
    units = builder.build(
        _occ("verb_prep", "warten auf", ["warten", "auf"], 20, case="Akk")
        + _occ("verb_prep", "denken an", ["denken", "an"], 30, case="Akk")
    )
    assert [(u.rank, u.lemma_key) for u in units] == [(1, "denken an"), (2, "warten auf")]
    assert units[0].unit_id == "vp:denken_an"


def test_excluded_keys_never_become_units() -> None:
    builder = _builder(_counts(kommen=100), CuratedLists(exclude=["kommen zu"]))
    assert builder.build(_occ("verb_prep", "kommen zu", ["kommen", "zu"], 40, case="Dat")) == []
    assert builder.report["rejected"]["verb_prep:excluded"] == 1


def test_rank_uses_mean_per_source_relative_frequency() -> None:
    """A phrase common in the small everyday corpus outranks one that is only
    common in the large news corpus, whatever the raw counts say."""
    counts = _counts(warten=100, denken=100)
    counts.sentences_by_source = Counter({"tatoeba": 1_000, "leipzig_news_2025": 9_000})
    everyday = _occ("verb_prep", "warten auf", ["warten", "auf"], 20, case="Akk")
    news = [
        o.model_copy(update={"corpus_source": "leipzig_news_2025"})
        for o in _occ("verb_prep", "denken an", ["denken", "an"], 30, case="Akk")
    ]
    units = _builder(counts).build(everyday + news)
    assert [(u.rank, u.lemma_key) for u in units] == [(1, "warten auf"), (2, "denken an")]
    # 20 of 1,000 weighted ten, averaged with 0 of 9,000 weighted one
    assert units[0].per_million == pytest.approx(20_000 * 10 / 11, abs=0.01)


def test_a_collocation_seen_only_in_news_and_web_is_not_a_unit() -> None:
    counts = _counts(treffen=30, trinken=80)
    counts.sentences_by_source = Counter({"tatoeba": 1_000, "leipzig_web_2021": 9_000})
    counts.nouns["datum"] = 60
    counts.adjectives["personenbezogen"] = 40
    web_only = [
        o.model_copy(update={"corpus_source": "leipzig_web_2021"})
        for o in _occ("adj_noun", "personenbezogen datum", ["personenbezogen", "Datum"], 12)
    ]
    everyday = _occ("adj_noun", "stark kaffee", ["stark", "Kaffee"], 9)
    builder = _builder(counts, thresholds=ADJ_NOUN_TEST_THRESHOLDS)
    builder.vocabulary.vocab["datum"] = "A1"
    keys = [u.lemma_key for u in builder.build(web_only + everyday)]
    assert keys == ["stark kaffee"]
    assert builder.report["rejected"]["adj_noun:no_everyday_evidence"] == 1


def _dict() -> frozenset[str]:
    return frozenset({"leiden", "regeln", "ersetzen", "einbringen", "angabe", "machen"})


def test_participle_left_as_lemma_is_cited_as_its_infinitive() -> None:
    counts = _counts(leiden=100, gelitten=100)
    counts.prepositions["unter"] = 50
    builder = _builder(counts)
    builder.dictionary = _dict()
    occs = [
        o.model_copy(update={"surfaces": ["gelitten", "unter"], "form_key": "Part"})
        for o in _occ("verb_prep", "gelitten unter", ["gelitten", "unter"], 30)
    ]
    keys = [u.lemma_key for u in builder.build(occs)]
    assert keys == ["leiden unter"]


def test_participle_without_a_known_infinitive_is_dropped() -> None:
    builder = _builder(_counts(gewurmt=100))
    builder.dictionary = None
    occs = [
        o.model_copy(update={"surfaces": ["gewurmt", "an"], "form_key": "Part"})
        for o in _occ("verb_prep", "gewurmt an", ["gewurmt", "an"], 30)
    ]
    assert builder.build(occs) == []


def test_passive_agent_durch_is_not_a_complement_but_finite_durch_is() -> None:
    counts = _counts(regeln=100, ersetzen=100)
    counts.prepositions["durch"] = 50
    builder = _builder(counts)
    builder.dictionary = _dict()
    passive = [
        o.model_copy(update={"surfaces": ["durch", "geregelt"], "form_key": "Part"})
        for o in _occ("verb_prep", "regeln durch", ["regeln", "durch"], 30)
    ]
    finite = [
        o.model_copy(update={"form_key": "Fin|Pres|3|Sing"})
        for o in _occ("verb_prep", "ersetzen durch", ["ersetzen", "durch"], 30)
    ]
    assert [u.lemma_key for u in builder.build(passive + finite)] == ["ersetzen durch"]


def test_zu_infinitive_left_as_lemma_loses_its_zu() -> None:
    builder = _builder(_counts(einbringen=100, einzubringen=100))
    builder.dictionary = _dict()
    occs = [
        o.model_copy(update={"surfaces": ["sich", "einzubringen"], "form_key": "Inf"})
        for o in _occ("reflexive_verb", "sich einzubringen", ["sich", "einzubringen"], 30)
    ]
    assert [u.lemma_key for u in builder.build(occs)] == ["sich einbringen"]


def test_noun_verb_display_uses_the_commonest_noun_surface() -> None:
    counts = _counts(machen=50)
    counts.nouns["angabe"] = 40
    builder = UnitBuilder(
        counts,
        CuratedLists(),
        ADJ_NOUN_TEST_THRESHOLDS,
        vocabulary=VocabularyStore({"angabe": "B1", "machen": "A1"}, frequency_ranks={}),
        frequency_ranks={},
        dictionary=_dict(),
    )
    occs = [
        o.model_copy(update={"surfaces": ["Angaben", "macht"]})
        for o in _occ("noun_verb", "angabe machen", ["Angabe", "machen"], 30)
    ]
    units = builder.build(occs)
    assert [u.display_de for u in units] == ["Angaben machen"]


def _sep(text: str, verb: str, particle: str, key: str) -> Occurrence:
    v, p = text.index(verb), text.index(particle)
    return Occurrence(
        kind="separable_verb",
        unit_key=key,
        parts=[key],
        token_indices=[1, 3],
        spans=[(v, v + len(verb)), (p, p + len(particle))],
        surfaces=[verb, particle],
        corpus_source="tatoeba",
        line_id=text,
        text=text,
        form_key="Fin|Pres|3|Sing|discontinuous",
    )


@pytest.mark.parametrize(
    ("text", "verb", "particle", "key", "kept"),
    [
        ("Das Training hilft dabei.", "hilft", "dabei", "dabeihelfen", False),
        ("Dann gehe ich halt wieder.", "gehe", "wieder", "wiedergehen", False),
        ("Wir machen morgen weiter.", "machen", "weiter", "weitermachen", True),
        (
            "Das Factsheet finden Sie unter: https://act.de.",
            "finden",
            "unter",
            "unterfinden",
            False,
        ),
        ('Mehr finden Sie unter "Meine Bücher".', "finden", "unter", "unterfinden", False),
        ("Er steht jeden Tag früh auf.", "steht", "auf", "aufstehen", True),
        ("Er ruft mich an, wenn er da ist.", "ruft", "an", "anrufen", True),
    ],
)
def test_parser_invented_particles_are_dropped_post_hoc(
    text: str, verb: str, particle: str, key: str, kept: bool
) -> None:
    from src.phrases.units import canonical_occurrence

    words = frozenset({"weitermachen", "aufstehen", "anrufen", "helfen", "gehen", "finden"})
    assert (canonical_occurrence(_sep(text, verb, particle, key), words) is not None) is kept


def test_plain_reflexive_verb_shows_no_accusative_case() -> None:
    builder = _builder(_counts(einmischen=40))
    occs = _occ(
        "reflexive_verb",
        "sich einmischen",
        ["sich", "einmischen"],
        20,
        evidence={"pron_case": "Akk"},
    )
    units = builder.build(occs)
    assert [u.case for u in units] == [None]


def test_dative_reflexive_pronoun_is_kept() -> None:
    builder = _builder(_counts(vorstellen=40))
    occs = _occ(
        "reflexive_verb",
        "sich vorstellen",
        ["sich", "vorstellen"],
        20,
        evidence={"pron_case": "Dat"},
    )
    assert [u.case for u in builder.build(occs)] == ["Dat"]


def test_adj_noun_display_prefers_the_nominative_with_its_article() -> None:
    counts = _counts(dienen=50)
    counts.adjectives["gut"] = 60
    counts.nouns["zweck"] = 40
    builder = UnitBuilder(
        counts,
        CuratedLists(),
        ADJ_NOUN_TEST_THRESHOLDS,
        vocabulary=VocabularyStore({"gut": "A1", "zweck": "B1"}, frequency_ranks={}),
        frequency_ranks={},
        dictionary=frozenset(),
    )
    oblique = [
        o.model_copy(
            update={
                "surfaces": ["guten", "Zweck"],
                "form_key": "Acc|Sing",
                "text": "Es dient einem guten Zweck.",
                "spans": [(15, 20), (21, 26)],
            }
        )
        for o in _occ("adj_noun", "gut zweck", ["gut", "Zweck"], 12)
    ]
    nominative = [
        o.model_copy(
            update={
                "surfaces": ["guter", "Zweck"],
                "form_key": "Nom|Sing",
                "text": "Das ist ein guter Zweck.",
                "spans": [(12, 17), (18, 23)],
                "line_id": f"n{o.line_id}",
            }
        )
        for o in _occ("adj_noun", "gut zweck", ["gut", "Zweck"], 3)
    ]
    units = builder.build(oblique + nominative)
    assert [u.display_de for u in units] == ["ein guter Zweck"]
    assert units[0].cefr == "B1"


def test_mined_unit_cefr_is_the_hardest_part_and_unknown_words_are_b2() -> None:
    counts = _counts(nehmen=200)
    counts.nouns["kenntnis"] = 40
    builder = UnitBuilder(
        counts,
        CuratedLists(),
        ADJ_NOUN_TEST_THRESHOLDS,
        vocabulary=VocabularyStore({"nehmen": "A1", "kenntnis": "B2"}, frequency_ranks={}),
        frequency_ranks={},
        dictionary=frozenset(),
    )
    occs = _occ("noun_verb", "kenntnis nehmen", ["Kenntnis", "nehmen"], 30)
    assert [u.cefr for u in builder.build(occs)] == ["B2"]
    counts2 = _counts(weisen=200)
    b2 = UnitBuilder(
        counts2,
        CuratedLists(),
        Thresholds(),
        vocabulary=VocabularyStore({"weisen": "A1"}, frequency_ranks={}),
        frequency_ranks={},
        dictionary=frozenset({"nachweisen"}),
    )
    sep = _occ(
        "separable_verb", "nachweisen", ["nachweisen"], 30, form_key="Fin|Pres|3|Sing|discontinuous"
    )
    assert [u.cefr for u in b2.build(sep)] == ["B2"]


@pytest.mark.parametrize(
    ("verb", "surface", "form_key", "expected"),
    [
        ("ankamen", "ankamen", "Part|fused", "ankommen"),
        ("aufwuchsen", "wuchsen auf", "Fin|Past|3|Plur|discontinuous", "aufwachsen"),
        ("füllten", "füllten", "Fin|Pres|3|Plur", "füllen"),
        ("eingestochen", "eingestochen", "Inf", "einstechen"),
        ("umgesehen", "umgesehen", "Inf", "umsehen"),
        ("kommen", "kommen", "Fin|Pres|3|Plur", "kommen"),
        ("erhalten", "erhalten", "Inf", "erhalten"),
        ("warten", "warten", "Fin|Pres|3|Plur", "warten"),
        ("geschrien", "geschrien um", "Part", "schreien"),
        ("hafen", "haften im", "Fin|Pres|3|Plur", "haften"),
        ("zusammenkommen", "kommen zusammen", "Fin|Pres|3|Plur", "zusammenkommen"),
        ("zulegen", "legen sich zu", "Fin|Pres|3|Plur", "zulegen"),
        ("bleiben", "bleiben offen", "Fin|Pres|3|Plur", "bleiben"),
    ],
)
def test_inflected_forms_left_as_lemma_are_cited_as_infinitives(
    verb: str, surface: str, form_key: str, expected: str
) -> None:
    from src.phrases.units import canonical_verb

    words = frozenset(
        {"füllen", "warten", "waren", "kommen", "erhalten", "haften", "hafen", "zusammen", "offen"}
    )
    infinitives = {"warten": "A1", "kommen": "A1", "erhalten": "B1", "geschrien": "B1"}
    assert canonical_verb(verb, surface.split(), form_key, words, infinitives) == expected


def test_particle_outside_the_known_set_is_a_parser_artefact() -> None:
    from src.phrases.units import canonical_occurrence

    words = frozenset({"offenstehen"})
    assert canonical_occurrence(_sep("Er weiß, wie.", "weiß", "wie", "wiewissen"), words) is None
    assert canonical_occurrence(
        _sep("Die Tür steht offen.", "steht", "offen", "offenstehen"), words
    )
    assert canonical_occurrence(
        _sep("Es kommt uns zugute.", "kommt", "zugute", "zugutekommen"), words
    )


def test_adj_noun_display_uses_the_governing_preposition_when_never_nominative() -> None:
    counts = _counts(bleiben=50)
    counts.adjectives["sicher"] = 60
    counts.nouns["entfernung"] = 40
    builder = UnitBuilder(
        counts,
        CuratedLists(),
        ADJ_NOUN_TEST_THRESHOLDS,
        vocabulary=VocabularyStore({"sicher": "A2", "entfernung": "B1"}, frequency_ranks={}),
        frequency_ranks={},
        dictionary=frozenset(),
    )
    occs = [
        o.model_copy(
            update={
                "surfaces": ["sicherer", "Entfernung"],
                "form_key": "Dat|Sing",
                "text": "Sie bleiben in sicherer Entfernung.",
                "spans": [(15, 23), (24, 34)],
            }
        )
        for o in _occ("adj_noun", "sicher entfernung", ["sicher", "Entfernung"], 12)
    ]
    assert [u.display_de for u in builder.build(occs)] == ["in sicherer Entfernung"]


def test_adj_noun_display_drops_sentence_capital_and_negation() -> None:
    counts = _counts(fallen=50)
    counts.adjectives["heftig"] = 60
    counts.nouns["regen"] = 40
    builder = UnitBuilder(
        counts,
        CuratedLists(),
        ADJ_NOUN_TEST_THRESHOLDS,
        vocabulary=VocabularyStore({"heftig": "B1", "regen": "A1"}, frequency_ranks={}),
        frequency_ranks={},
        dictionary=frozenset(),
    )
    occs = [
        o.model_copy(
            update={
                "surfaces": ["Heftiger", "Regen"],
                "form_key": "Nom|Sing",
                "text": "Heftiger Regen fiel.",
                "spans": [(0, 8), (9, 14)],
            }
        )
        for o in _occ("adj_noun", "heftig regen", ["heftig", "Regen"], 7)
    ] + [
        o.model_copy(
            update={
                "surfaces": ["heftiger", "Regen"],
                "form_key": "Nom|Sing",
                "text": "Es fiel kein heftiger Regen.",
                "spans": [(13, 21), (22, 27)],
                "line_id": f"k{o.line_id}",
            }
        )
        for o in _occ("adj_noun", "heftig regen", ["heftig", "Regen"], 5)
    ]
    assert [u.display_de for u in builder.build(occs)] == ["heftiger Regen"]


def test_excluded_collocations_still_occupy_their_cap_slot() -> None:
    curated = CuratedLists(exclude=["buch lesen"])
    counts = _counts(lesen=200)
    for noun in ("buch", "zeitung", "brief", "text", "roman", "artikel", "schild", "karte", "mail"):
        counts.nouns[noun] = 40
    builder = UnitBuilder(
        counts,
        curated,
        Thresholds(colloc_cap_per_verb=2),
        vocabulary=VocabularyStore(
            dict.fromkeys(("lesen", "buch", "zeitung", "brief", "text"), "A1"), frequency_ranks={}
        ),
        frequency_ranks={},
        dictionary=frozenset(),
    )
    occs = (
        _occ("noun_verb", "buch lesen", ["Buch", "lesen"], 30)
        + _occ("noun_verb", "zeitung lesen", ["Zeitung", "lesen"], 25)
        + _occ("noun_verb", "brief lesen", ["Brief", "lesen"], 20)
        + _occ("noun_verb", "text lesen", ["Text", "lesen"], 15)
    )
    keys = [u.lemma_key for u in builder.build(occs)]
    assert "buch lesen" not in keys
    assert len(keys) == 1  # the excluded unit keeps one of the two slots


def test_capitalised_verb_surface_mid_sentence_is_a_noun() -> None:
    from src.phrases.units import canonical_occurrence

    occ = Occurrence(
        kind="verb_prep",
        unit_key="hafen in",
        parts=["hafen", "in"],
        token_indices=[2, 3],
        spans=[(11, 16), (17, 19)],
        surfaces=["Hafen", "in"],
        corpus_source="tatoeba",
        line_id="1",
        text="Das Schiff Hafen in Kiel.",
    )
    assert canonical_occurrence(occ, None) is None


def _adj(
    text: str, adj: str, noun: str, form_key: str = "Dat|Sing", line: str = "", lemma: str = ""
) -> Occurrence:
    a, n = text.index(adj), text.index(noun)
    lemma = lemma or adj
    return Occurrence(
        kind="adj_noun",
        unit_key=f"{lemma.lower()} {noun.lower()}",
        parts=[lemma, noun],
        token_indices=[3, 4],
        spans=[(a, a + len(adj)), (n, n + len(noun))],
        surfaces=[adj, noun],
        corpus_source="tatoeba",
        line_id=line or text,
        text=text,
        form_key=form_key,
    )


def test_governing_preposition_becomes_a_third_token() -> None:
    from src.phrases.units import with_governing_preposition, without_governing_preposition

    occ = with_governing_preposition(
        _adj("Sie bleiben in sicherer Entfernung.", "sicherer", "Entfernung", lemma="sicher")
    )
    assert occ.parts == ["in", "sicher", "Entfernung"]
    assert occ.surfaces == ["in", "sicherer", "Entfernung"]
    assert occ.text[occ.spans[0][0] : occ.spans[0][1]] == "in"
    assert occ.token_indices == [2, 3, 4]
    assert without_governing_preposition(occ).parts == ["sicher", "Entfernung"]
    contracted = with_governing_preposition(
        _adj("Wir sitzen im hohen Gras.", "hohen", "Gras", lemma="hoch")
    )
    assert contracted.unit_key == "in hoch gras" and contracted.surfaces[0] == "im"
    bare = with_governing_preposition(_adj("Das ist sicherer Boden.", "sicherer", "Boden"))
    assert len(bare.parts) == 2


def _adj_builder(adj: str, noun: str) -> UnitBuilder:
    counts = _counts(bleiben=50)
    counts.adjectives[adj] = 60
    counts.nouns[noun] = 40
    return UnitBuilder(
        counts,
        CuratedLists(),
        ADJ_NOUN_TEST_THRESHOLDS,
        vocabulary=VocabularyStore({adj: "A2", noun: "B1"}, frequency_ranks={}),
        frequency_ranks={},
        dictionary=frozenset(),
    )


def test_prepositional_unit_wins_at_eighty_percent_and_is_gapped_with_its_preposition() -> None:
    builder = _adj_builder("sicher", "entfernung")
    occs = [
        _adj(
            "Sie bleiben in sicherer Entfernung.",
            "sicherer",
            "Entfernung",
            line=f"a{i}",
            lemma="sicher",
        )
        for i in range(9)
    ] + [_adj("Das ist sicherer Entfernung.", "sicherer", "Entfernung", line="b", lemma="sicher")]
    units = builder.build(occs)
    assert [u.lemma_key for u in units] == ["in sicher entfernung"]
    assert units[0].display_de == "in sicherer Entfernung"
    assert units[0].sentence_count == 10


def test_bare_pair_wins_below_the_share_and_keeps_the_prepositional_sentences() -> None:
    builder = _adj_builder("gut", "idee")
    occs = [
        _adj(
            "Er hatte eine gute Idee.",
            "gute",
            "Idee",
            form_key="Acc|Sing",
            line=f"a{i}",
            lemma="gut",
        )
        for i in range(6)
    ] + [
        _adj(
            "Wir kamen auf gute Idee.",
            "gute",
            "Idee",
            form_key="Acc|Sing",
            line=f"b{i}",
            lemma="gut",
        )
        for i in range(4)
    ]
    units = builder.build(occs)
    assert [u.lemma_key for u in units] == ["gut idee"]
    assert units[0].sentence_count == 10


@pytest.mark.parametrize(
    ("verb", "surfaces", "expected"),
    [
        ("anliegen", ["legt", "an"], "anlegen"),
        ("festliegen", ["legte", "fest"], "festlegen"),
        ("vorführen", ["fuhr", "vor"], "vorfahren"),
        ("durchfahren", ["führt", "durch"], "durchführen"),
        ("anliegen", ["liegt", "an"], "anliegen"),
        ("anlegen", ["legen", "an"], "anlegen"),
        ("anlegen", ["anlegen"], "anlegen"),
        ("legen", ["lag"], "liegen"),
        ("liegen", ["liegen"], "liegen"),
    ],
)
def test_homograph_stems_are_relemmatised(verb: str, surfaces: list[str], expected: str) -> None:
    from src.phrases.units import canonical_verb

    assert canonical_verb(verb, surfaces, "Fin|Pres|3|Sing", None, {}) == expected


def test_existential_gibt_and_tag_question_are_not_particles() -> None:
    from src.phrases.units import canonical_occurrence

    assert (
        canonical_occurrence(_sep("Es gibt das Buch raus.", "gibt", "raus", "rausgeben"), None)
        is None
    )
    assert (
        canonical_occurrence(_sep("Du kommst, nicht wahr?", "kommst", "wahr", "wahrkommen"), None)
        is None
    )
    kept = _sep("Sie gibt das Buch raus.", "gibt", "raus", "rausgeben")
    assert canonical_occurrence(kept, None) is not None


def test_unit_override_display_with_case_suffix_is_split_into_case() -> None:
    from src.phrases.curated import UnitOverride

    o = UnitOverride(key="abhingen von", display="abhängen von +Dat")
    assert (o.display, o.case) == ("abhängen von", "Dat")
    kept = UnitOverride(key="x", display="warten auf +Akk", case="Dat")
    assert (kept.display, kept.case) == ("warten auf", "Dat")
    plain = UnitOverride(key="y", display="nach Hause")
    assert (plain.display, plain.case) == ("nach Hause", None)


# -- single words, adjective + verb, and the interleaved ranking ----------------


def _word_occ(kind: str, lemma: str, n: int) -> list[Occurrence]:
    out = []
    for i in range(n):
        text = f"Ein Satz mit {lemma} Nummer {i}."
        start = text.index(lemma)
        out.append(
            Occurrence(
                kind=kind,  # type: ignore[arg-type]
                unit_key=lemma,
                parts=[lemma],
                token_indices=[3],
                spans=[(start, start + len(lemma))],
                surfaces=[lemma],
                corpus_source="tatoeba",
                line_id=f"{kind}{i}",
                text=text,
                form_key="Nom|Sing",
                evidence={"gender": "Fem"},
            )
        )
    return out


def _word_counts(**kw: object) -> LemmaCounts:
    counts = _counts(warten=100, stellen=300)
    counts.nouns.update({"frage": 500})
    counts.adjectives.update({"wichtig": 400, "schnell": 20})
    counts.adverbs = Counter({"schnell": 90, "endlich": 300})
    counts.word_by_source = {
        "noun:frage": Counter({"tatoeba": 500}),
        "adjective:wichtig": Counter({"tatoeba": 400}),
        "adjective:schnell": Counter({"tatoeba": 110}),
        "adjective:endlich": Counter({"tatoeba": 300}),
    }
    for key, value in kw.items():
        setattr(counts, key, value)
    return counts


def test_interleave_by_share_mixes_two_rankings_in_proportion() -> None:
    from src.phrases.units import interleave_by_share

    words = [f"w{i}" for i in range(6)]
    phrases = [f"p{i}" for i in range(4)]
    mixed = interleave_by_share(words, phrases, 0.6)
    assert len(mixed) == 10
    assert [m for m in mixed if m.startswith("w")] == words  # each group keeps its order
    assert [m for m in mixed if m.startswith("p")] == phrases
    assert sum(1 for m in mixed[:5] if m.startswith("w")) == 3
    # the degenerate shares fall back to one group
    assert interleave_by_share(words, phrases, 1.0) == words
    assert interleave_by_share(words, phrases, 0.0) == phrases
    assert interleave_by_share([], phrases, 0.6) == phrases


def test_a_word_needs_the_frequency_threshold_and_carries_its_corpus_count() -> None:
    counts = _word_counts()
    builder = _builder(counts)
    builder.t = Thresholds(word_min_count=200)
    units = {u.unit_id: u for u in builder.build(_word_occ("noun", "frage", 3))}
    # three occurrences, but the corpus count from the counts file is what rules
    assert units["nn:frage"].sentence_count == 500
    assert units["nn:frage"].display_de == "die Frage"  # gender from the corpus
    builder2 = _builder(_word_counts())
    builder2.t = Thresholds(word_min_count=600)
    assert builder2.build(_word_occ("noun", "frage", 3)) == []


def test_a_word_the_tagger_never_calls_an_adjective_is_filed_as_an_adverb() -> None:
    """The tagger calls a predicative adjective an adverb, so "schnell" is
    ADV in most of its sentences and is still an adjective. A real adverb is
    never ADJ at all (measured: "endlich" 0.7%, "sofort" 0%)."""
    counts = _word_counts()
    counts.adjectives.update({"endlich": 0})
    counts.adverbs.update({"endlich": 300})
    counts.word_by_source["adjective:endlich"] = Counter({"tatoeba": 300})
    builder = _builder(counts)
    builder.t = Thresholds(word_min_count=50)
    units = {u.lemma_key: u for u in builder.build(_word_occ("adjective", "endlich", 2))}
    assert units["endlich"].kind == "adverb"

    # schnell: 20 adjective uses against 90 adverb ones, still an adjective
    builder2 = _builder(_word_counts())
    builder2.t = Thresholds(word_min_count=50)
    kept = {u.lemma_key: u for u in builder2.build(_word_occ("adjective", "schnell", 2))}
    assert kept["schnell"].kind == "adjective"


def test_a_collocation_forces_its_components_to_be_units() -> None:
    """Kaffee, trinken and Kaffee trinken are three units (owner, 2026-09-20).
    The components are kept even under the word threshold."""
    counts = _word_counts()
    counts.verbs.update({"trinken": 300})
    counts.word_by_source["noun:kaffee"] = Counter({"tatoeba": 60})
    builder = _builder(counts)
    builder.t = Thresholds(
        word_min_count=10_000,  # far above anything here
        colloc_min_count=2,
        colloc_min_g2=0.0,
        colloc_min_lift=0.0,
        colloc_min_lemma_count=1,
        colloc_min_everyday=0,
    )
    occurrences = _occ("noun_verb", "kaffee trinken", ["Kaffee", "trinken"], 5) + _word_occ(
        "noun", "kaffee", 2
    )
    kinds = {u.lemma_key: u.kind for u in builder.build(occurrences)}
    assert kinds["kaffee trinken"] == "noun_verb"
    assert kinds["kaffee"] == "noun"  # kept although it is under the threshold


def _noun_occ(lemma: str, n: int, *, determiner: str | None) -> list[Occurrence]:
    """Carriers for a noun, with or without an article in front of it."""
    out = []
    for i in range(n):
        head = f"Ich sehe {determiner} " if determiner else f"Nummer {i}, "
        text = f"{head}{lemma} heute."
        start = text.index(lemma)
        out.append(
            Occurrence(
                kind="noun",
                unit_key=lemma,
                parts=[lemma],
                token_indices=[3],
                spans=[(start, start + len(lemma))],
                surfaces=[lemma],
                corpus_source="tatoeba",
                line_id=f"n{i}",
                text=text,
                form_key="Nom|Sing",
                evidence={"gender": "Fem"},
            )
        )
    return out


def test_a_noun_the_corpus_never_puts_behind_an_article_is_not_a_noun() -> None:
    """ "die Hoer" and "das Gib" are capitalised imperatives. Measured over
    the occurrence file, they follow a determiner in 0 of 16 and 0 of 21
    sentences, while the thinnest real noun manages 0.31 (review,
    2026-09-21)."""
    counts = _word_counts()
    counts.nouns.update({"hoer": 500})
    builder = _builder(counts)
    builder.t = Thresholds(word_min_count=50)
    assert builder.build(_noun_occ("hoer", 10, determiner=None)) == []

    kept = _builder(_word_counts())
    kept.t = Thresholds(word_min_count=50)
    units = kept.build(_noun_occ("frage", 10, determiner="die"))
    assert [u.lemma_key for u in units] == ["frage"]


def test_a_verb_the_corpus_uses_reflexively_is_not_taught_as_a_plain_verb() -> None:
    """ "sich kuemmern um" taught as "kuemmern" leaves the pronoun outside
    the gap, and the learner never meets the unit that matters. The review
    of 2026-09-21 found this on eight lemmas."""
    counts = _word_counts()
    counts.verbs.update({"kuemmern": 100})
    builder = _builder(counts)
    builder.t = Thresholds(word_min_count=50)
    occurrences = [
        *_word_occ("verb", "kuemmern", 3),
        *_occ("reflexive_verb", "sich kuemmern", ["sich", "kuemmern"], 80),
    ]
    kinds = {u.lemma_key: u.kind for u in builder.build(occurrences)}
    assert "kuemmern" not in kinds

    # a verb that is only sometimes reflexive stays a plain verb too
    counts2 = _word_counts()
    counts2.verbs.update({"waschen": 100})
    builder2 = _builder(counts2)
    builder2.t = Thresholds(word_min_count=50)
    occurrences2 = [
        *_word_occ("verb", "waschen", 3),
        *_occ("reflexive_verb", "sich waschen", ["sich", "waschen"], 25),
    ]
    assert "waschen" in {u.lemma_key for u in builder2.build(occurrences2)}


def test_a_conjugated_form_the_tagger_called_an_adverb_is_dropped() -> None:
    """ "glaubst", "warst" and "lass" reached the adverb kind because the
    tagger files what it cannot place under ADV. "wert" is a predicative
    adjective and must survive, so the kind is settled before the test."""
    counts = _word_counts()
    counts.adverbs.update({"glaubst": 300})
    counts.verbs.update({"glauben": 4000})
    counts.word_by_source["adjective:glaubst"] = Counter({"tatoeba": 300})
    builder = _builder(counts)
    builder.t = Thresholds(word_min_count=50)
    assert builder.build(_word_occ("adjective", "glaubst", 2)) == []

    counts2 = _word_counts()
    counts2.adjectives.update({"wert": 50})
    counts2.adverbs.update({"wert": 1880})
    counts2.word_by_source["adjective:wert"] = Counter({"tatoeba": 1930})
    builder2 = _builder(counts2)
    builder2.t = Thresholds(word_min_count=50)
    kept = {u.lemma_key: u.kind for u in builder2.build(_word_occ("adjective", "wert", 2))}
    assert kept["wert"] == "adjective"


def test_an_infinitive_the_tagger_mislabelled_is_not_an_adverb() -> None:
    """ "wissen" reached the adverbs on 152 uses against 37,413 as a verb.
    A genuine modifier that shares a form with a verb keeps its place:
    "trocken" is 34 verb uses against 1,477 (review, 2026-09-21)."""
    counts = _word_counts()
    counts.verbs.update({"wissen": 37_413})
    counts.adverbs.update({"wissen": 152})
    counts.word_by_source["adjective:wissen"] = Counter({"tatoeba": 152})
    builder = _builder(counts)
    builder.t = Thresholds(word_min_count=50)
    assert builder.build(_word_occ("adjective", "wissen", 2)) == []

    counts2 = _word_counts()
    counts2.verbs.update({"trocken": 34})
    counts2.adjectives.update({"trocken": 657})
    counts2.adverbs.update({"trocken": 820})
    counts2.word_by_source["adjective:trocken"] = Counter({"tatoeba": 1477})
    builder2 = _builder(counts2)
    builder2.t = Thresholds(word_min_count=50)
    assert [u.lemma_key for u in builder2.build(_word_occ("adjective", "trocken", 2))] == [
        "trocken"
    ]


def test_a_finite_verb_form_is_not_an_adverb_but_a_common_word_survives() -> None:
    """The test that catches "glaubst" must not catch "bitte". The old one
    did worse: it dropped 454 ordinary adverbs, "mindestens" among them,
    because a single stray tagging of "mindesten" outscored a word with no
    verb count at all (review, 2026-09-21)."""
    counts = _word_counts()
    counts.verbs.update({"glauben": 17_084})
    counts.adverbs.update({"glaubst": 2_006, "mindestens": 6_019, "bitte": 12_612})
    counts.verbs.update({"mindesten": 1, "bitten": 6_774})
    builder = _builder(counts)
    builder.t = Thresholds(word_min_count=50)
    assert builder._finite_verb_form("glaubst") is True
    assert builder._finite_verb_form("mindestens") is False
    assert builder._finite_verb_form("bitte") is False

    counts.word_by_source["adjective:mindestens"] = Counter({"tatoeba": 6_019})
    kept = {u.lemma_key: u.kind for u in builder.build(_word_occ("adjective", "mindestens", 2))}
    assert kept["mindestens"] == "adverb"


def test_an_ordinary_adverb_is_not_banned_by_the_collocation_stop_list() -> None:
    """STOP_ADVERBS says what may not anchor a collocation. Until
    2026-09-21 it also decided what may not be a unit, which cost the deck
    "vielleicht", "nun" and "vorbei"."""
    from src.phrases.mining.common import NON_UNIT_ADVERBS, STOP_ADVERBS

    for word in ("vielleicht", "nun", "vorbei", "heute", "gern", "oft"):
        assert word in STOP_ADVERBS
        assert word not in NON_UNIT_ADVERBS
    for word in ("dann", "auch", "nicht", "darauf", "warum"):
        assert word in NON_UNIT_ADVERBS


def test_a_separable_verb_does_not_also_stand_as_a_plain_verb() -> None:
    """ "herausfinden", "durchfuehren", "beibringen" and "stattfinden" each
    stood twice. The detector rejects a verb token carrying a particle, but
    the fused infinitive has none to reject (review, 2026-09-21)."""
    counts = _word_counts()
    counts.verbs.update({"herausfinden": 900})
    counts.word_by_source["verb:herausfinden"] = Counter({"tatoeba": 900})
    builder = _builder(counts)
    builder.t = Thresholds(word_min_count=50)
    occurrences = [
        *_word_occ("verb", "herausfinden", 3),
        *_occ("separable_verb", "herausfinden", ["heraus", "finden"], 40),
    ]
    kinds = {(u.lemma_key, u.kind) for u in builder.build(occurrences)}
    assert ("herausfinden", "verb") not in kinds

    # without the separable candidate the plain verb stands as before
    bare = _word_counts()
    bare.verbs.update({"herausfinden": 900})
    bare.word_by_source["verb:herausfinden"] = Counter({"tatoeba": 900})
    plain = _builder(bare)
    plain.t = Thresholds(word_min_count=50)
    assert ("herausfinden", "verb") in {
        (u.lemma_key, u.kind) for u in plain.build(_word_occ("verb", "herausfinden", 3))
    }


def test_an_intensity_adverb_never_anchors_a_collocation() -> None:
    """ "wirklich wissen" and "bitte erklaeren" are compositional; "hart
    arbeiten" and "schwer fallen" are not, so manner adverbs stay out of
    the list (review, 2026-09-21: 535 of 1,307 adj_verb units)."""
    from src.phrases.mining.common import STOP_ADVERBS

    for word in ("wirklich", "bitte", "gerade", "einfach", "ziemlich", "eigentlich"):
        assert word in STOP_ADVERBS
    for word in ("hart", "schwer", "genau", "gut", "schnell", "richtig"):
        assert word not in STOP_ADVERBS


def test_the_noun_article_comes_from_the_corpus_not_the_tagger() -> None:
    """The morph tally cited "die Fan", "das Kuchen" and "der Schloss"; the
    singular articles say der, der and das. Only the singular counts, since
    the plural takes "die" whatever the gender, which is how "die Fan"
    happened (review, 2026-09-21)."""
    counts = _word_counts()
    counts.nouns.update({"fan": 500})
    counts.word_by_source["noun:fan"] = Counter({"tatoeba": 500})
    builder = _builder(counts)
    builder.t = Thresholds(word_min_count=50)

    def noun_occ(text: str, number: str) -> Occurrence:
        start = text.index("fan")
        return Occurrence(
            kind="noun",
            unit_key="fan",
            parts=["fan"],
            token_indices=[2],
            spans=[(start, start + 3)],
            surfaces=["fan"],
            corpus_source="tatoeba",
            line_id=text + number,
            text=text,
            form_key=f"Nom|{number}",
            evidence={"gender": "Fem"},  # what the tagger said, and it is wrong
        )

    units = builder.build(
        [
            noun_occ("Ich sah der fan dort.", "Sing"),
            noun_occ("Dort stand der fan allein.", "Sing"),
            noun_occ("Alle die fan waren laut.", "Plur"),
        ]
    )
    assert units[0].display_de == "der Fan"


def test_the_article_beats_the_plural_only_rule() -> None:
    """ "Medien" is commoner than "Medium", so the plural-only rule would
    cite "die Medium". The nominative singular article says das, and it is
    consulted first (review, 2026-09-21)."""
    counts = _word_counts()
    counts.nouns.update({"medium": 500})
    counts.word_by_source["noun:medium"] = Counter({"tatoeba": 500})
    builder = _builder(counts)
    builder.t = Thresholds(word_min_count=50)

    def occ(text: str, number: str, line: str) -> Occurrence:
        start = text.index("medium")
        return Occurrence(
            kind="noun",
            unit_key="medium",
            parts=["medium"],
            token_indices=[1],
            spans=[(start, start + 6)],
            surfaces=["medium"],
            corpus_source="tatoeba",
            line_id=line,
            text=text,
            form_key=f"Nom|{number}",
            evidence={"gender": "Fem"},
        )

    units = builder.build(
        [
            occ("Das medium berichtet darüber.", "Sing", "a"),
            *[occ(f"Die medium berichten {i}.", "Plur", f"p{i}") for i in range(12)],
        ]
    )
    assert units[0].display_de == "das Medium"
