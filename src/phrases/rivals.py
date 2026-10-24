"""Units that are easy to mix up, and words that are simply the same word.

Two things the deck exports so the clients can be fair about a near
synonym (owner, 2026-10-24: "check units that are interchangeable or easy to
mix ... look for the upcoming ones too"):

* ``also_accepted`` from ``data/phrases/synonyms.yaml``: words that mean the
  same and fit the same slot, "statt" and "anstatt". Either is right.
* ``rivals``: units whose English overlaps, in the same word class, found
  mechanically for the whole deck. Typing a rival is the right idea and the
  wrong word, so the learner is told another word is wanted and tries again.
  There were 806 such pairs among the units seen and the next 500, too many
  to list by hand, and every rebuild adds more.

Deterministic: rivals are ordered by rank, then id.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence

from src.contracts import PhraseUnit

#: A sense on its own says too little to call two words rivals: "to get",
#: "then", "also" are shared by words that do not fit each other's slots.
GENERIC_SENSES: frozenset[str] = frozenset(
    {
        "be", "have", "do", "make", "get", "go", "come", "give", "take", "put", "set", "let",
        "keep", "bring", "hold", "turn", "run", "see", "say", "tell", "show", "work", "play",
        "also", "so", "then", "now", "there", "here", "very", "well", "just", "still", "even",
        "only", "again", "yet", "too", "really", "quite", "already", "thing", "matter", "way",
        "time", "part", "place", "point", "case",
    }
)  # fmt: skip

#: A sense shared by more units of one word class than this is generic in
#: this deck. Counted per class: "return" is shared by verbs and nouns
#: alike, and counting both dropped "zurückkehren"/"zurückkommen".
MAX_SENSE_SPREAD = 12
#: The rivals kept per unit, most frequent first.
MAX_RIVALS = 10

#: Word classes inside which a word can stand in for another in a cloze.
_CLASS: dict[str, str] = {
    "verb": "V",
    "separable_verb": "V",
    "reflexive_verb": "V",
    "verb_prep": "V",
    "noun_verb": "V",
    "adj_verb": "V",
    "noun": "N",
    "adj_noun": "N",
    "adjective": "A",
    "adverb": "A",
    "connector": "A",
    "two_part_connector": "A",
    "expression": "A",
    "idiom": "A",
}

#: Words a lemma key carries that are not what the unit is about.
_FUNCTION_WORDS: frozenset[str] = frozenset(
    {
        "sich", "an", "auf", "aus", "bei", "durch", "für", "gegen", "in", "mit", "nach", "über",
        "um", "unter", "von", "vor", "zu", "zwischen", "ab", "seit", "es", "der", "die", "das",
        "den", "dem", "ein", "eine", "einen", "nicht",
    }
)  # fmt: skip


def senses(gloss: str | None) -> set[str]:
    """The English senses of a gloss: "to stop / to detain (s.o.)" ->
    {"stop", "detain"}. Parenthesised notes and articles go."""
    out: set[str] = set()
    for part in (gloss or "").split("/"):
        s = re.sub(r"\(.*?\)", "", part).strip().lower()
        s = re.sub(r"^(to|a|an|the)\s+", "", s)
        s = re.sub(r"\s+", " ", s).strip(" .,;")
        if s and s not in GENERIC_SENSES:
            out.add(s)
    return out


def _head(unit: PhraseUnit) -> str:
    words = [w for w in unit.lemma_key.split() if w not in _FUNCTION_WORDS]
    return words[0] if words else unit.lemma_key


def assign_rivals(units: Sequence[PhraseUnit]) -> list[PhraseUnit]:
    """Every unit with its rivals filled in.

    Two units are rivals when they share a non-generic English sense, sit in
    the same word class, and are about different words: "bitten" and "bitten
    um" share "to ask" and the verb, and typing one in the other's gap is
    already graded as the verb it is. A word the unit already accepts is not
    a rival.
    """
    teachable = [u for u in units if not u.trivial and u.kind in _CLASS]
    by_sense: dict[tuple[str, str], list[PhraseUnit]] = defaultdict(list)
    for unit in teachable:
        for sense in senses(unit.gloss_en):
            by_sense[(_CLASS[unit.kind], sense)].append(unit)
    rivals: dict[str, set[str]] = defaultdict(set)
    for sharing in by_sense.values():
        if len(sharing) < 2 or len(sharing) > MAX_SENSE_SPREAD:
            continue
        for a in sharing:
            for b in sharing:
                if a.unit_id == b.unit_id:
                    continue
                if _head(a) == _head(b) or b.lemma_key in a.also_accepted:
                    continue
                rivals[a.unit_id].add(b.unit_id)
    rank = {u.unit_id: (u.rank, u.unit_id) for u in units}
    out: list[PhraseUnit] = []
    for unit in units:
        found = sorted(rivals.get(unit.unit_id, ()), key=rank.__getitem__)[:MAX_RIVALS]
        out.append(unit.model_copy(update={"rivals": found}) if found != unit.rivals else unit)
    return out


def apply_synonyms(
    units: Iterable[PhraseUnit], groups: Sequence[Sequence[str]]
) -> list[PhraseUnit]:
    """Each unit in a synonym group accepts the group's other words.

    Keyed by lemma key, so "statt" accepts "anstatt" whatever kind either
    was mined as. Kept after anything a curated list already accepts.
    """
    others: dict[str, list[str]] = defaultdict(list)
    for group in groups:
        for key in group:
            others[key].extend(k for k in group if k != key)
    out: list[PhraseUnit] = []
    for unit in units:
        extra = [k for k in others.get(unit.lemma_key, []) if k not in unit.also_accepted]
        if extra:
            unit = unit.model_copy(update={"also_accepted": [*unit.also_accepted, *extra]})
        out.append(unit)
    return out
