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


def _builder(counts: LemmaCounts, curated: CuratedLists | None = None) -> UnitBuilder:
    return UnitBuilder(
        counts,
        curated or CuratedLists(),
        Thresholds(),
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
    builder = _builder(_counts(treffen=30, trinken=80), curated)
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
    assert units[0].per_million == 10_000.0  # 20 of 1,000, averaged with 0 of 9,000


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
    builder = _builder(counts)
    builder.vocabulary.vocab["datum"] = "A1"
    keys = [u.lemma_key for u in builder.build(web_only + everyday)]
    assert keys == ["stark kaffee"]
    assert builder.report["rejected"]["adj_noun:no_everyday_evidence"] == 1
