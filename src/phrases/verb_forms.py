"""The past forms of the deck's verbs, for the Perfekt/Präteritum drill.

Owner, 2026-11-04: a mode that gives a verb and asks for its past forms, no
sentence, scheduled by FSRS like everything else. The drill asks, for er/sie/
es, the Präteritum ("ging") and the Perfekt ("ist gegangen").

The forms are drafted by agy (``scripts/agy_jobs.py verb-forms``) into
``data/phrases/verb_forms.jsonl`` and checked here, deterministically, before
anything is exported:

* shape: one word, or for a separable verb the particle after the verb in
  the Präteritum ("stand auf") and fused in front in the participle
  ("aufgestanden"); the auxiliary is "hat" or "ist";
* every form has to be a real German word (``carrier_validation``'s
  dictionary), which "gehte" and "gegeht" are not.

A form that fails the second check is not rejected outright, because the
dictionary is a trimmed list and rare verbs fall outside it; it is reported
for a reader. See ``doubtful``. The weak-verb rule (``weak_forms``) only
says whether a verb is regular, which the drill tells the learner.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from src.atomic_write import write_text_atomic
from src.contracts import PhraseUnit, VerbCard, VerbFormsRecord
from src.lexicon.lemmatizer import SEPARABLE_PREFIXES, normalise
from src.phrases.units import slug

DEFAULT_VERB_FORMS_PATH = Path("data/phrases/verb_forms.jsonl")

#: The deck's verb kinds the drill takes. Reflexive verbs wait for a second
#: version: "setzte sich / hat sich gesetzt" needs the pronoun in the answer.
DRILL_KINDS: frozenset[str] = frozenset({"verb", "separable_verb"})

#: Verbs the drill must have that the deck does not carry as verb units:
#: "sein" is mined as an auxiliary, "tun" only as the noun "das Tun". Ranked
#: in front of everything, which is where the commonest verbs belong.
EXTRA_VERBS: tuple[str, ...] = ("sein", "tun")

#: Particles of separable verbs, longest first when splitting. The
#: lemmatiser's list plus the longer and rarer ones the deck's separable
#: verbs carry ("herausfinden", "teilnehmen", "kennenlernen").
PARTICLES: tuple[str, ...] = tuple(
    sorted(
        SEPARABLE_PREFIXES
        | {
            "dar", "durch", "empor", "entgegen", "fern", "fort", "frei", "gegenüber",
            "heim", "herab", "heran", "herauf", "heraus", "herbei", "herein", "herum",
            "herunter", "hervor", "herüber", "hinab", "hinauf", "hinaus", "hinein",
            "hinterher", "hinunter", "hinüber", "hoch", "kennen", "nieder", "statt", "teil",
            "um", "unter", "voran", "voraus", "vorbei", "vorüber", "wieder", "zurecht",
            "zuvor", "über", "bereit", "fertig", "kaputt", "sicher", "spazieren",
        },
        key=lambda p: (-len(p), p),
    )
)  # fmt: skip

#: A participle without "ge-": the base starts with one of these.
INSEPARABLE_PREFIXES: tuple[str, ...] = ("miss", "emp", "ent", "zer", "be", "er", "ge", "ver")


def drill_unit_id(infinitive: str) -> str:
    return f"pf:{slug(infinitive)}"


def split_particle(infinitive: str) -> tuple[str, str]:
    """(particle, base) of a separable verb: the longest particle that
    leaves a base of at least three letters ending in -n."""
    for particle in PARTICLES:
        rest = infinitive[len(particle) :]
        if infinitive.startswith(particle) and len(rest) >= 3 and rest.endswith("n"):
            return particle, rest
    return "", infinitive


def _stem(base: str) -> str:
    # "lächeln", "ändern": the -n alone; "machen": -en
    if base.endswith(("eln", "ern")) or not base.endswith("en"):
        return base[:-1]
    return base[:-2]


def _needs_e(stem: str) -> bool:
    """ "arbeit-ete", "atm-ete", "öffn-ete", but "lern-te", "wohn-te"."""
    if stem.endswith(("d", "t", "chn", "chm")):
        return True
    return bool(re.search(r"[^aeiouäöülrhmn][mn]$", stem))


def weak_forms(infinitive: str, separable: bool, particle: str | None = None) -> tuple[str, str]:
    """(Präteritum, Partizip II) as the weak-verb rule gives them. A known
    ``particle`` (from the draft) beats guessing one from the list."""
    if particle:
        base = infinitive[len(particle) :]
    else:
        particle, base = split_particle(infinitive) if separable else ("", infinitive)
    stem = _stem(base)
    e = "e" if _needs_e(stem) else ""
    praeteritum = f"{stem}{e}te"
    # "anerkennen": the particle goes in front of a base that takes no ge-
    no_ge = base.endswith("ieren") or (base.startswith(INSEPARABLE_PREFIXES) and len(base) > 5)
    partizip = f"{particle}{'' if no_ge else 'ge'}{stem}{e}t"
    if particle:
        praeteritum = f"{praeteritum} {particle}"
    return praeteritum, partizip


def draft_particle(record: VerbFormsRecord) -> str:
    """The particle the draft splits off ("lief davon" -> "davon",
    "stellte wieder her" -> "wiederher"), or "" for a verb it does not
    split. Read from the draft rather than a list: the deck's separable verbs
    carry particles no list has ("raus", "weh", "zugute"), and some reach the
    drill filed as plain verbs."""
    words = record.praeteritum[0].split() if record.praeteritum else []
    return "".join(words[1:])


def check_record(record: VerbFormsRecord, separable: bool) -> str | None:
    """Why the record cannot be exported, or None. Shape only; see
    ``doubtful`` for the dictionary. ``separable`` is the deck's word for
    it: such a verb's usual Präteritum must be split ("stand auf")."""
    if not (record.praeteritum and record.partizip and record.auxiliary):
        return "missing_form"
    particle = draft_particle(record)
    if separable and not particle:
        return "particle_not_after_the_verb"
    for form in record.praeteritum:
        words = form.split()
        # a one-word alternative is fine ("anerkannte" beside "erkannte an")
        rest = "".join(words[1:])
        if rest and not (record.infinitive.startswith(rest) and len(record.infinitive) > len(rest)):
            return "particle_not_after_the_verb"
    for form in record.partizip:
        if " " in form or not form.endswith(("t", "n")):
            return "partizip_shape"
        if particle and not form.startswith(particle):
            return "particle_not_in_front"
    return None


def answer_in_gloss(record: VerbFormsRecord) -> str | None:
    """A past form the English gives away, or None. The English is shown
    before the answer (rule 3), and a word like "stand" or "fand" is also
    English, so only a whole form of two or more words, or a one-word form
    that is no English word the gloss could mean, counts: the participle
    always, the Präteritum when it is not a plain English word."""
    gloss = f" {record.gloss_en or ''} ".lower()
    for form in [*record.partizip, *record.praeteritum]:
        if f" {form.lower()} " in gloss or f"({form.lower()})" in gloss:
            if form in record.praeteritum and form.lower() in _ENGLISH_HOMOGRAPHS:
                continue
            return form
    return None


#: Präteritum forms that are also English words a verb's English can hold.
_ENGLISH_HOMOGRAPHS: frozenset[str] = frozenset({"stand", "fand", "bat", "hing", "rang", "band"})


def _unknown(record: VerbFormsRecord, dictionary: frozenset[str]) -> list[str]:
    out = [f for f in record.praeteritum if normalise(f.split()[0]) not in dictionary]
    return out + [f for f in record.partizip if normalise(f) not in dictionary]


def _derived(prefix: str, base: VerbFormsRecord) -> tuple[str, str]:
    """A prefixed verb's forms from its base verb's: "ver" + "stand",
    "verstanden"; or, separable, "kam an", "angekommen"."""
    praet, part = base.praeteritum[0], base.partizip[0]
    if prefix in INSEPARABLE_PREFIXES:
        stripped = (
            part[2:] if part.startswith("ge") and not base.infinitive.startswith("ge") else part
        )
        return prefix + praet, prefix + stripped
    return f"{praet} {prefix}", prefix + part


def doubtful(
    record: VerbFormsRecord,
    dictionary: frozenset[str],
    records: Mapping[str, VerbFormsRecord] | None = None,
) -> list[str]:
    """Forms nothing vouches for. A form vouches for itself when the
    dictionary knows it. Failing that, with ``records``, a prefixed verb is
    vouched for by a base verb the dictionary knows whose forms derive its
    own ("ankommen" from "kommen"), and a verb with no such irregular base
    inside it by the weak-verb rule. The rule alone is not enough: "gehte"
    and "gegeht" are exactly what it gives for "gehen"."""
    unknown = _unknown(record, dictionary)
    if not unknown or records is None:
        return unknown
    first = (record.praeteritum[0], record.partizip[0])
    irregular_base = False
    for k in range(1, len(record.infinitive) - 2):
        base = records.get(record.infinitive[k:])
        if base is None or not base.accepted or not base.praeteritum or _unknown(base, dictionary):
            continue
        if _derived(record.infinitive[:k], base) == first:
            return []
        irregular_base = irregular_base or (
            (base.praeteritum[0], base.partizip[0]) != weak_forms(base.infinitive, False)
        )
    particle = draft_particle(record)
    if not irregular_base and first == weak_forms(record.infinitive, bool(particle), particle):
        return []
    return unknown


def drill_verbs(units: Iterable[PhraseUnit]) -> list[tuple[str, bool, PhraseUnit | None]]:
    """(infinitive, separable, source unit) for every verb the drill asks,
    in deck rank order, one per infinitive. The deck's display is the
    infinitive (it carries the overrides); anything with a space in it is a
    phrase, not a verb, and waits."""
    seen: set[str] = set()
    out: list[tuple[str, bool, PhraseUnit | None]] = []
    for verb in EXTRA_VERBS:
        seen.add(verb)
        out.append((verb, False, None))
    for unit in sorted(units, key=lambda u: u.rank):
        infinitive = unit.display_de
        if unit.kind not in DRILL_KINDS or not re.fullmatch(r"[a-zäöüß]+", infinitive):
            continue
        if infinitive in seen or infinitive == "möchten":
            continue
        seen.add(infinitive)
        out.append((infinitive, unit.kind == "separable_verb", unit))
    return out


def verb_cards(
    units: Iterable[PhraseUnit], records: Mapping[str, VerbFormsRecord]
) -> list[VerbCard]:
    """The drill's verbs: every deck verb with an accepted, well-formed
    record and an English to show."""
    verbs = drill_verbs(units)
    cards: list[VerbCard] = []
    for position, (infinitive, separable, unit) in enumerate(verbs, start=1):
        record = records.get(infinitive)
        if record is None or not record.accepted or check_record(record, separable):
            continue
        gloss = (unit.gloss_en if unit is not None else None) or record.gloss_en
        if not gloss:
            continue
        particle = draft_particle(record)
        weak = (record.praeteritum[0], record.partizip[0]) == weak_forms(
            infinitive, bool(particle), particle
        )
        cards.append(
            VerbCard(
                unit_id=drill_unit_id(infinitive),
                infinitive=infinitive,
                gloss_en=gloss,
                rank=unit.rank if unit is not None else position,
                source_unit_id=unit.unit_id if unit is not None else None,
                praeteritum=list(record.praeteritum),
                auxiliary=list(record.auxiliary),
                partizip=list(record.partizip),
                weak=weak,
            )
        )
    # "sein" and "tun" sit at positions 1 and 2, ahead of the deck's ranks.
    return sorted(cards, key=lambda c: (c.source_unit_id is not None, c.rank, c.infinitive))


def verbs_version(cards: Iterable[VerbCard]) -> str:
    digest = hashlib.sha1()
    for card in cards:
        digest.update(card.model_dump_json().encode("utf-8"))
    return digest.hexdigest()[:12]


def load_records(path: Path = DEFAULT_VERB_FORMS_PATH) -> dict[str, VerbFormsRecord]:
    if not path.exists():
        return {}
    out: dict[str, VerbFormsRecord] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = VerbFormsRecord.model_validate_json(line)
            out[record.infinitive] = record
    return out


def save_records(records: Iterable[VerbFormsRecord], path: Path = DEFAULT_VERB_FORMS_PATH) -> None:
    ordered = sorted(records, key=lambda r: r.infinitive)
    write_text_atomic(
        path,
        "".join(
            json.dumps(r.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n"
            for r in ordered
        ),
    )
