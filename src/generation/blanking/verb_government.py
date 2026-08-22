"""TODO 8.1 (replaces TODO 8.5's own closed-list implementation): runtime
lookups into the corpus-built verb case-government lexicon.

``data/fixtures/verb_government/lexicon.v1.jsonl`` (built by
``scripts/build_verb_government.py`` -- see that script's own module
docstring for the full method) is the merged product of two sources: real
Dative/Accusative pronoun evidence harvested from roughly 450,000 Tatoeba
and Leipzig sentences, and the five hand-maintained closed lists in
``paradigms.py`` (Dreyer/Schmitt, Duden), which win on conflict. Every
record's own ``final`` field already carries that merge; this module's job
is only to load the fixture once and expose two small lookups, one per
relation:

* ``reflexive_verdict(verb_lemma)`` -- "Dat"/"Acc"/``None`` for a reflexive
  pronoun's own governing verb. Consulted by
  ``selectors._reflexive_case`` for every verb NOT on
  ``paradigms.DATIVE_REFLEXIVE_VERBS_WITH_OBJECT`` -- that list's own
  members are genuinely polysemous ("sich vorstellen" alone = Accusative,
  "sich (Dat) etwas vorstellen" = Dative), which a single scalar verdict
  cannot represent, so ``_reflexive_case`` keeps deciding those directly
  from whether an object is actually present, unchanged by this module.
* ``object_verdict(verb_lemma)`` -- "Dat"/``None`` for a bare Dative
  determiner slot's own governing verb. Consulted by
  ``selectors._select_kasus_dativ_formen``.

Both fall back to the LITERAL hand lists (``paradigms.py``, not the
fixture) whenever the fixture is missing, unreadable, or simply does not
mention a verb -- the fixture's own ``build_records`` already folds every
hand-listed verb in even at zero corpus evidence, so this fallback should
rarely matter in practice; it exists as the same "never crash, degrade"
posture every loader in this package already follows
(``sentence_tagger._load_model``, ``carrier_validation._load_model``): a
missing or corrupt fixture must not take the whole selector down, it must
quietly behave exactly as it did before this lexicon existed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from src.generation.blanking import paradigms

DEFAULT_LEXICON_PATH = Path("data/fixtures/verb_government/lexicon.v1.jsonl")

_VERDICT_TO_CASE: dict[str, str] = {"dat": "Dat", "acc": "Acc"}


@dataclass(frozen=True)
class _LexiconEntry:
    reflexive: str | None
    object: str | None


def _hand_reflexive_verdict(verb: str) -> str | None:
    """The literal hand-list verdict alone, no fixture involved -- the
    fallback path this module's own docstring describes."""
    if verb in paradigms.DATIVE_REFLEXIVE_VERBS_NO_OBJECT:
        return "Dat"
    if verb in paradigms.ACCUSATIVE_ONLY_REFLEXIVE_VERBS:
        return "Acc"
    return None


def _hand_object_verdict(verb: str) -> str | None:
    """The literal hand-list verdict alone. ``DITRANSITIVE_DATIVE_VERBS``
    is deliberately not read here -- membership on that list alone was
    never sufficient evidence (the selector also requires a co-occurring
    Accusative object, unchanged and outside this module), unlike
    ``DATIVE_ONLY_VERBS``, whose membership alone always was."""
    if verb in paradigms.DATIVE_ONLY_VERBS:
        return "Dat"
    return None


def _parse_entry(row: dict[str, object]) -> tuple[str, _LexiconEntry] | None:
    verb = row.get("verb")
    final = row.get("final")
    if not isinstance(verb, str) or not isinstance(final, dict):
        return None
    reflexive_raw = final.get("reflexive")
    object_raw = final.get("object")
    reflexive = _VERDICT_TO_CASE.get(reflexive_raw) if isinstance(reflexive_raw, str) else None
    obj = _VERDICT_TO_CASE.get(object_raw) if isinstance(object_raw, str) else None
    return verb, _LexiconEntry(reflexive=reflexive, object=obj)


@lru_cache(maxsize=4)
def _load(path: Path) -> dict[str, _LexiconEntry]:
    """Parse the fixture into ``{verb: _LexiconEntry}``, or ``{}`` on any
    failure to read or parse it -- never raises (module docstring). Cached
    per path: this is a versioned, static build artefact for the lifetime
    of a process, not something that changes under a running pipeline."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    entries: dict[str, _LexiconEntry] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("_meta"):
            continue
        parsed = _parse_entry(row)
        if parsed is not None:
            entries[parsed[0]] = parsed[1]
    return entries


def reflexive_verdict(verb_lemma: str, *, path: Path = DEFAULT_LEXICON_PATH) -> str | None:
    """ "Dat"/"Acc"/``None`` for ``verb_lemma``'s reflexive-pronoun case
    government, per this module's own docstring. ``None`` means neither
    the fixture nor the hand lists have an opinion (insufficient or mixed
    corpus evidence, and no hand-list membership) -- the caller's own
    structural fallback (object-presence scanning) decides from there,
    exactly as it did before this lexicon existed."""
    entry = _load(path).get(verb_lemma)
    if entry is not None and entry.reflexive is not None:
        return entry.reflexive
    return _hand_reflexive_verdict(verb_lemma)


def object_verdict(verb_lemma: str, *, path: Path = DEFAULT_LEXICON_PATH) -> str | None:
    """ "Dat"/``None`` for whether ``verb_lemma`` forces a Dative reading on
    its own (non-reflexive) object, per this module's own docstring."""
    entry = _load(path).get(verb_lemma)
    if entry is not None and entry.object is not None:
        return entry.object
    return _hand_object_verdict(verb_lemma)
