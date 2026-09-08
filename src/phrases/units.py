"""Occurrences in, units out: thresholds, association, rank, trivial flag.

Detection is generous on purpose; every decision about what is a unit lives
here so it can be re-tuned from ``report.json`` without re-parsing the corpus.
The association measure abstains where counts are sparse (TODO item 4 of the
grammar trainer, kept): an unattested pair of two rare words is evidence of
nothing.
"""

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.contracts import UNIT_ID_PREFIX, Case, PhraseUnit
from src.lexicon.lemmatizer import normalise
from src.lexicon.vocabulary import VocabularyStore
from src.phrases import paradigms, verb_government
from src.phrases.curated import CuratedLists
from src.phrases.mining import LemmaCounts
from src.phrases.mining.common import CONTRACTED_PREPS
from src.phrases.occurrences import Occurrence

DEFAULT_VOCAB_PATH = Path("data/fixtures/corpus/vocab_levels.json")

_KIND_ORDER: dict[str, int] = {
    "verb_prep": 0,
    "reflexive_verb": 1,
    "separable_verb": 2,
    "noun_verb": 3,
    "adj_noun": 4,
    "connector": 5,
    "two_part_connector": 6,
    "idiom": 7,
}

_REFLEXIVE_HAND_LISTS: frozenset[str] = (
    paradigms.ACCUSATIVE_ONLY_REFLEXIVE_VERBS
    | paradigms.DATIVE_REFLEXIVE_VERBS_NO_OBJECT
    | paradigms.DATIVE_REFLEXIVE_VERBS_WITH_OBJECT
)


@dataclass(frozen=True)
class Thresholds:
    min_count: int = 5
    vp_min_ratio: float = 0.05
    vp_min_lift: float = 3.0
    rv_min_share: float = 0.3
    #: A ``sich V prep`` unit replaces ``sich V`` when it covers this share.
    reflexive_prep_share: float = 0.6
    colloc_min_count: int = 5
    colloc_min_g2: float = 15.13
    colloc_min_lift: float = 5.0
    colloc_min_lemma_count: int = 20
    colloc_cap_per_verb: int = 8
    colloc_cap_per_noun: int = 4
    adj_cap_per_noun: int = 4
    case_majority: float = 0.75
    trivial_rank_max: int = 300


@dataclass
class UnitStats:
    kind: str
    key: str
    parts: Counter[tuple[str, ...]] = field(default_factory=Counter)
    sentences: set[tuple[str, str]] = field(default_factory=set)
    count_by_source: Counter[str] = field(default_factory=Counter)
    case_tally: Counter[str] = field(default_factory=Counter)
    pron_case_tally: Counter[str] = field(default_factory=Counter)
    surface_tally: Counter[str] = field(default_factory=Counter)
    form_keys: Counter[str] = field(default_factory=Counter)
    discontinuous: int = 0
    fused: int = 0

    @property
    def count(self) -> int:
        return len(self.sentences)

    @property
    def best_parts(self) -> tuple[str, ...]:
        return self.parts.most_common(1)[0][0]


_TRANSLITERATE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def slug(key: str) -> str:
    """ASCII id fragment: umlauts transliterated, everything else collapsed
    to underscores. ``"sich interessieren für"`` -> ``"sich_interessieren_fuer"``."""
    text = key.lower().translate(_TRANSLITERATE)
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


def unit_id_for(kind: str, key: str) -> str:
    return f"{UNIT_ID_PREFIX[kind]}:{slug(key)}"


def log_likelihood(pair: int, a: int, b: int, n: int) -> float:
    """Dunning's G² over the 2x2 contingency of ``a`` and ``b`` co-occurring
    in ``pair`` of ``n`` sentences."""

    def term(observed: int, expected: float) -> float:
        if observed == 0 or expected <= 0:
            return 0.0
        return observed * math.log(observed / expected)

    o11, o12, o21 = pair, a - pair, b - pair
    o22 = n - a - b + pair
    if min(o11, o12, o21, o22) < 0 or n <= 0:
        return 0.0
    e11 = a * b / n
    e12 = a * (n - b) / n
    e21 = (n - a) * b / n
    e22 = (n - a) * (n - b) / n
    return 2 * (term(o11, e11) + term(o12, e12) + term(o21, e21) + term(o22, e22))


def lift(pair: int, a: int, b: int, n: int) -> float:
    if a == 0 or b == 0 or n == 0:
        return 0.0
    return (pair / n) / ((a / n) * (b / n))


def aggregate(occurrences: Iterable[Occurrence]) -> dict[tuple[str, str], UnitStats]:
    stats: dict[tuple[str, str], UnitStats] = {}
    for occ in occurrences:
        entry = stats.get((occ.kind, occ.unit_key))
        if entry is None:
            entry = UnitStats(kind=occ.kind, key=occ.unit_key)
            stats[(occ.kind, occ.unit_key)] = entry
        if occ.sentence_key in entry.sentences:
            continue
        entry.sentences.add(occ.sentence_key)
        entry.count_by_source[occ.corpus_source] += 1
        entry.parts[tuple(occ.parts)] += 1
        entry.surface_tally[" ".join(occ.surfaces)] += 1
        entry.form_keys[occ.form_key] += 1
        if occ.case is not None:
            entry.case_tally[occ.case] += 1
        pron_case = occ.evidence.get("pron_case")
        if pron_case:
            entry.pron_case_tally[pron_case] += 1
        if occ.form_key.endswith("discontinuous"):
            entry.discontinuous += 1
        elif occ.form_key.endswith("fused"):
            entry.fused += 1
    return stats


def _majority_case(tally: Counter[str], share: float) -> Case | None:
    total = sum(tally.values())
    if total == 0:
        return None
    case, count = tally.most_common(1)[0]
    if count / total >= share and case in {"Akk", "Dat", "Gen"}:
        return case  # type: ignore[return-value]
    return None


def _base_prep_counts(counts: LemmaCounts) -> Counter[str]:
    merged: Counter[str] = Counter()
    for prep, count in counts.prepositions.items():
        merged[CONTRACTED_PREPS.get(prep, prep)] += count
    return merged


@dataclass
class _Decision:
    accept: bool
    reason: str = ""
    case: Case | None = None
    cefr: str | None = None
    gloss_en: str | None = None
    display: str | None = None
    source: str = "mined"
    trivial: bool = False
    trivial_reason: str | None = None
    score: float = 0.0


class UnitBuilder:
    def __init__(
        self,
        counts: LemmaCounts,
        curated: CuratedLists,
        thresholds: Thresholds | None = None,
        vocabulary: VocabularyStore | None = None,
        frequency_ranks: dict[str, int] | None = None,
    ) -> None:
        self.counts = counts
        self.curated = curated
        self.t = thresholds or Thresholds()
        self.vocabulary = vocabulary or VocabularyStore.load(DEFAULT_VOCAB_PATH)
        self.frequency_ranks = (
            frequency_ranks if frequency_ranks is not None else self.vocabulary._frequency_ranks
        )
        self.prep_counts = _base_prep_counts(counts)
        self.vp_seeds = {s.key: s for s in curated.verb_prep_seeds}
        self.colloc_seeds = {s.key: s for s in curated.collocation_seeds}
        self.connectors = {c.key: c for c in curated.connectors}
        self.idioms = {i.key: i for i in curated.idioms}
        self.stoplist = set(curated.trivial_stoplist)
        self.excluded = set(curated.exclude)
        self.report: dict[str, Any] = {
            "rejected": Counter(),
            "seed_disagreements": [],
            "zero_hit_curated": [],
            "top_by_kind": {},
        }

    # -- per-kind decisions ---------------------------------------------------

    def _cefr_for(self, lemma: str) -> str | None:
        return self.vocabulary.get_level(lemma)

    def _decide_verb_prep(self, s: UnitStats, stats: dict[tuple[str, str], UnitStats]) -> _Decision:
        verb, prep = s.best_parts[0], s.best_parts[-1]
        seed = self.vp_seeds.get(s.key)
        n = self.counts.sentences
        verb_count = self.counts.verbs.get(verb, 0)
        prep_count = self.prep_counts.get(prep, 0)
        ratio = s.count / verb_count if verb_count else 0.0
        lift_value = lift(s.count, verb_count, prep_count, n)
        corpus_case = _majority_case(s.case_tally, self.t.case_majority)
        if seed is not None:
            if seed.case is not None and corpus_case is not None and seed.case != corpus_case:
                self.report["seed_disagreements"].append(
                    {"unit": s.key, "seed_case": seed.case, "corpus_case": corpus_case}
                )
            return _Decision(
                True,
                case=seed.case or corpus_case,
                cefr=seed.cefr or self._cefr_for(verb),
                gloss_en=seed.gloss_en,
                source="mined+curated" if s.count else "curated",
                score=lift_value,
            )
        # A verb whose preposition use is mostly reflexive is taught as the
        # reflexive unit instead.
        reflexive = stats.get(("reflexive_verb", f"sich {s.key}"))
        if (
            reflexive is not None
            and s.count
            and reflexive.count / s.count >= self.t.reflexive_prep_share
        ):
            return _Decision(False, "mostly_reflexive")
        if s.count < self.t.min_count:
            return _Decision(False, "count")
        if ratio < self.t.vp_min_ratio:
            return _Decision(False, "ratio")
        if lift_value < self.t.vp_min_lift:
            return _Decision(False, "lift")
        return _Decision(True, case=corpus_case, cefr=self._cefr_for(verb), score=lift_value)

    def _decide_reflexive(self, s: UnitStats, stats: dict[tuple[str, str], UnitStats]) -> _Decision:
        parts = s.best_parts
        verb = parts[1]
        has_prep = len(parts) == 3
        seed = self.vp_seeds.get(s.key)
        bare = stats.get(("reflexive_verb", f"sich {verb}"))
        bare_count = bare.count if bare is not None else 0
        # Pronoun case: the corpus tally first, the government lexicon second.
        case: Case | None = None
        tally_total = sum(s.pron_case_tally.values())
        if tally_total >= self.t.min_count:
            case = _majority_case(s.pron_case_tally, 0.9)
        if case is None:
            verdict = verb_government.reflexive_verdict(verb)
            case = {"Dat": "Dat", "Acc": "Akk"}.get(verdict or "")  # type: ignore[assignment]
        if has_prep:
            prep_case = _majority_case(s.case_tally, self.t.case_majority)
            if seed is not None:
                if seed.case is not None and prep_case is not None and seed.case != prep_case:
                    self.report["seed_disagreements"].append(
                        {"unit": s.key, "seed_case": seed.case, "corpus_case": prep_case}
                    )
                return _Decision(
                    True,
                    case=seed.case or prep_case,
                    cefr=seed.cefr or self._cefr_for(verb),
                    gloss_en=seed.gloss_en,
                    source="mined+curated" if s.count else "curated",
                    score=float(s.count),
                )
            if s.count < self.t.min_count:
                return _Decision(False, "count")
            if bare_count and s.count / bare_count < self.t.reflexive_prep_share:
                return _Decision(False, "prep_share")
            return _Decision(True, case=prep_case, cefr=self._cefr_for(verb), score=float(s.count))
        # Bare ``sich V``: dropped when a prepositional unit took it over.
        for (kind, key), other in stats.items():
            if kind == "reflexive_verb" and key.startswith(f"sich {verb} ") and other.count:
                if key in self.vp_seeds or (
                    other.count >= self.t.min_count
                    and other.count / s.count >= self.t.reflexive_prep_share
                ):
                    return _Decision(False, "taken_by_prep_unit")
        verb_count = self.counts.verbs.get(verb, 0)
        share = s.count / verb_count if verb_count else 0.0
        if verb in _REFLEXIVE_HAND_LISTS and s.count >= 1:
            return _Decision(True, case=case, cefr=self._cefr_for(verb), score=share)
        if s.count < self.t.min_count:
            return _Decision(False, "count")
        if share < self.t.rv_min_share:
            return _Decision(False, "share")
        return _Decision(True, case=case, cefr=self._cefr_for(verb), score=share)

    def _decide_separable(self, s: UnitStats) -> _Decision:
        if s.discontinuous == 0:
            return _Decision(False, "fused_only")
        if s.count < self.t.min_count:
            return _Decision(False, "count")
        return _Decision(True, cefr=self._cefr_for(s.key), score=float(s.count))

    def _decide_collocation(self, s: UnitStats) -> _Decision:
        parts = s.best_parts
        seed = self.colloc_seeds.get(s.key)
        if s.kind == "noun_verb":
            noun_lemma = parts[-2].lower()
            verb_lemma = parts[-1]
            a, b = self.counts.nouns.get(noun_lemma, 0), self.counts.verbs.get(verb_lemma, 0)
            head = verb_lemma
        else:
            a = self.counts.adjectives.get(parts[0].lower(), 0)
            b = self.counts.nouns.get(parts[1].lower(), 0)
            head = parts[1].lower()
        n = self.counts.sentences
        g2 = log_likelihood(s.count, a, b, n)
        lift_value = lift(s.count, a, b, n)
        if seed is not None:
            return _Decision(
                True,
                cefr=seed.cefr or self._cefr_for(head),
                gloss_en=seed.gloss_en,
                display=seed.display,
                source="mined+curated" if s.count else "curated",
                score=g2,
            )
        if s.count < self.t.colloc_min_count:
            return _Decision(False, "count")
        # A mined collocation is taught only when its noun is on the Goethe
        # A1-B1 lists or in the B2 frequency band. This is what keeps the news
        # corpus's "Täter festnehmen" out while "Frage stellen" stays in: the
        # word lists are the learner's vocabulary, the corpus is not.
        if min(a, b) < self.t.colloc_min_lemma_count:
            return _Decision(False, "sparse")
        noun = parts[-2].lower() if s.kind == "noun_verb" else parts[1].lower()
        if self._cefr_for(noun) is None:
            return _Decision(False, "noun_not_in_wordlist")
        if g2 < self.t.colloc_min_g2:
            return _Decision(False, "g2")
        if lift_value < self.t.colloc_min_lift:
            return _Decision(False, "lift")
        return _Decision(True, cefr=self._cefr_for(head), score=g2)

    def _decide_curated(self, s: UnitStats) -> _Decision:
        if s.kind in {"connector", "two_part_connector"}:
            spec = self.connectors.get(s.key)
            if spec is None:
                return _Decision(False, "unknown_connector")
            return _Decision(
                True,
                cefr=spec.cefr,
                gloss_en=spec.gloss_en,
                display=spec.display,
                source="curated",
                trivial=spec.trivial,
                trivial_reason="curated" if spec.trivial else None,
                score=float(s.count),
            )
        idiom = self.idioms.get(s.key)
        if idiom is None:
            return _Decision(False, "unknown_idiom")
        return _Decision(
            True,
            cefr=idiom.cefr,
            gloss_en=idiom.gloss_en,
            display=idiom.display,
            source="curated",
            score=float(s.count),
        )

    # -- assembly ---------------------------------------------------------------

    def _display(self, s: UnitStats, decision: _Decision) -> str:
        if decision.display:
            return decision.display
        parts = s.best_parts
        if s.kind in {"verb_prep", "reflexive_verb", "separable_verb"}:
            return " ".join(parts)
        if s.kind == "noun_verb":
            return " ".join(parts)
        if s.kind == "adj_noun":
            return s.surface_tally.most_common(1)[0][0] if s.surface_tally else " ".join(parts)
        return s.key

    def _trivial(self, s: UnitStats, decision: _Decision) -> tuple[bool, str | None]:
        if decision.trivial:
            return True, decision.trivial_reason
        if s.key in self.stoplist:
            return True, "stoplist"
        if " " not in s.key and s.kind in {"connector", "idiom"}:
            rank = self.frequency_ranks.get(normalise(s.key))
            if rank is not None and rank <= self.t.trivial_rank_max:
                return True, f"rank<={self.t.trivial_rank_max}"
        return False, None

    def _apply_caps(self, accepted: dict[tuple[str, str], tuple[UnitStats, _Decision]]) -> None:
        by_verb: dict[str, list[tuple[str, str]]] = defaultdict(list)
        by_noun: dict[str, list[tuple[str, str]]] = defaultdict(list)
        by_noun_adj: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for ident, (s, d) in accepted.items():
            if d.source != "mined":
                continue
            if s.kind == "noun_verb":
                by_verb[s.best_parts[-1]].append(ident)
                by_noun[s.best_parts[-2].lower()].append(ident)
            elif s.kind == "adj_noun":
                by_noun_adj[s.best_parts[1].lower()].append(ident)

        def trim(groups: dict[str, list[tuple[str, str]]], cap: int, reason: str) -> None:
            for group in groups.values():
                members = [ident for ident in group if ident in accepted]
                if len(members) <= cap:
                    continue
                members.sort(key=lambda ident: -accepted[ident][1].score)
                for ident in members[cap:]:
                    if ident in accepted:
                        del accepted[ident]
                        self.report["rejected"][reason] += 1

        trim(by_verb, self.t.colloc_cap_per_verb, "cap_per_verb")
        trim(by_noun, self.t.colloc_cap_per_noun, "cap_per_noun")
        trim(by_noun_adj, self.t.adj_cap_per_noun, "cap_adj_per_noun")

    def _ensure_curated_present(self, stats: dict[tuple[str, str], UnitStats]) -> None:
        """Curated entries with no corpus hit still become (card-less) units,
        ranked last, and are listed in the report."""
        for spec in self.curated.connectors:
            if (spec.kind, spec.key) not in stats:
                stats[(spec.kind, spec.key)] = UnitStats(kind=spec.kind, key=spec.key)
                stats[(spec.kind, spec.key)].parts[(spec.key,)] += 0
                self.report["zero_hit_curated"].append(spec.key)
        for idiom in self.curated.idioms:
            if ("idiom", idiom.key) not in stats:
                stats[("idiom", idiom.key)] = UnitStats(kind="idiom", key=idiom.key)
                stats[("idiom", idiom.key)].parts[(idiom.key,)] += 0
                self.report["zero_hit_curated"].append(idiom.key)
        for seed in self.curated.verb_prep_seeds:
            kind = "reflexive_verb" if seed.reflexive else "verb_prep"
            if (kind, seed.key) not in stats:
                stats[(kind, seed.key)] = UnitStats(kind=kind, key=seed.key)
                parts = (["sich"] if seed.reflexive else []) + [seed.verb, seed.prep]
                stats[(kind, seed.key)].parts[tuple(parts)] += 0
                self.report["zero_hit_curated"].append(seed.key)
        for colloc in self.curated.collocation_seeds:
            if (colloc.kind, colloc.key) not in stats:
                stats[(colloc.kind, colloc.key)] = UnitStats(kind=colloc.kind, key=colloc.key)
                stats[(colloc.kind, colloc.key)].parts[tuple(colloc.key.split())] += 0
                self.report["zero_hit_curated"].append(colloc.key)

    def build(self, occurrences: Iterable[Occurrence]) -> list[PhraseUnit]:
        stats = aggregate(occurrences)
        self._ensure_curated_present(stats)
        accepted: dict[tuple[str, str], tuple[UnitStats, _Decision]] = {}
        for ident, s in stats.items():
            if s.key in self.excluded:
                self.report["rejected"][f"{s.kind}:excluded"] += 1
                continue
            if s.kind == "verb_prep":
                decision = self._decide_verb_prep(s, stats)
            elif s.kind == "reflexive_verb":
                decision = self._decide_reflexive(s, stats)
            elif s.kind == "separable_verb":
                decision = self._decide_separable(s)
            elif s.kind in {"noun_verb", "adj_noun"}:
                decision = self._decide_collocation(s)
            else:
                decision = self._decide_curated(s)
            if not decision.accept:
                self.report["rejected"][f"{s.kind}:{decision.reason}"] += 1
                continue
            accepted[ident] = (s, decision)
        self._apply_caps(accepted)

        ordered = sorted(
            accepted.items(),
            key=lambda item: (-item[1][0].count, _KIND_ORDER[item[1][0].kind], item[1][0].key),
        )
        units: list[PhraseUnit] = []
        seen_ids: set[str] = set()
        for rank, (_, (s, d)) in enumerate(ordered, start=1):
            unit_id = unit_id_for(s.kind, s.key)
            if unit_id in seen_ids:
                self.report["rejected"]["duplicate_id"] += 1
                continue
            seen_ids.add(unit_id)
            trivial, reason = self._trivial(s, d)
            units.append(
                PhraseUnit(
                    unit_id=unit_id,
                    kind=s.kind,  # type: ignore[arg-type]
                    lemma_key=s.key,
                    parts=list(s.best_parts),
                    display_de=self._display(s, d),
                    case=d.case,
                    cefr=d.cefr,  # type: ignore[arg-type]
                    gloss_en=d.gloss_en,
                    sentence_count=s.count,
                    count_by_source=dict(s.count_by_source),
                    rank=rank,
                    trivial=trivial,
                    trivial_reason=reason,
                    source=d.source,  # type: ignore[arg-type]
                    card_count=0,
                )
            )
        self.report["rejected"] = dict(self.report["rejected"])
        by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for unit in units:
            if len(by_kind[unit.kind]) < 200:
                by_kind[unit.kind].append(
                    {"unit": unit.lemma_key, "count": unit.sentence_count, "case": unit.case}
                )
        self.report["top_by_kind"] = dict(by_kind)
        self.report["unit_count"] = len(units)
        self.report["kinds"] = dict(Counter(u.kind for u in units))
        self.report["wordlist_coverage"] = self._wordlist_coverage(units)
        return units

    def _head_lemma(self, unit: PhraseUnit) -> str:
        if unit.kind in {"verb_prep", "separable_verb"}:
            return unit.parts[0]
        if unit.kind == "reflexive_verb":
            return unit.parts[1]
        if unit.kind == "noun_verb":
            return unit.parts[-2].lower()
        if unit.kind == "adj_noun":
            return unit.parts[1].lower()
        return unit.lemma_key

    def _wordlist_coverage(self, units: list[PhraseUnit]) -> dict[str, Any]:
        """Every unit's head lemma against the Goethe A1-B1 lists and the B2
        band (``data/fixtures/corpus/vocab_levels.json``). Units whose head is
        on no list are listed per kind for review."""
        by_level: Counter[str] = Counter()
        missing: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for unit in units:
            level = unit.cefr or self._cefr_for(self._head_lemma(unit))
            by_level[level or "none"] += 1
            if level is None and len(missing[unit.kind]) < 200:
                missing[unit.kind].append(
                    {"unit": unit.lemma_key, "rank": unit.rank, "count": unit.sentence_count}
                )
        return {"by_level": dict(by_level), "not_in_wordlist": dict(missing)}
