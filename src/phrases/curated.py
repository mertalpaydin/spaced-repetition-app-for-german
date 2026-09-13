"""The hand-written lists under ``data/phrases/``.

Connectors and idioms are matched, not mined: the corpus tells us how often
they occur, not what they are. Verb-preposition and collocation seeds are
units unconditionally, whatever the counts say, and a seed's case wins over
the corpus tally (the disagreement is reported, never silently resolved).
"""

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.contracts import CEFR, Case

DEFAULT_PHRASES_DIR = Path("data/phrases")


class ConnectorPart(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    forms: list[str] = Field(min_length=1)
    pos: list[str] | None = None
    #: Do not match when the previous token is one of these (``und zwar``).
    not_after: list[str] | None = None
    #: The next non-punctuation token must carry ``Degree=Cmp`` (``je mehr``).
    next_must_be_comparative: bool = False


class ConnectorSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    kind: Literal["connector", "two_part_connector"]
    forms: list[str] | None = None
    pos: list[str] | None = None
    first: ConnectorPart | None = None
    second: ConnectorPart | None = None
    cefr: CEFR | None = None
    gloss_en: str | None = None
    display: str | None = None
    needs_context_when_initial: bool = False
    trivial: bool = False
    #: Near-synonyms the grader accepts in this connector's gap.
    also_accepted: list[str] = Field(default_factory=list)

    def part(self) -> ConnectorPart:
        if self.forms is None:
            raise ValueError(f"connector {self.key!r} has no forms")
        return ConnectorPart(forms=self.forms, pos=self.pos)


class IdiomElement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Surface match, case-insensitive.
    surface: str | None = None
    #: Or lemma match, lowercased.
    lemma: str | None = None


class IdiomSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    pattern: list[IdiomElement] = Field(min_length=1)
    #: Tokens allowed between consecutive elements.
    max_gap: int = 0
    cefr: CEFR | None = None
    gloss_en: str | None = None
    display: str | None = None


class VerbPrepSeed(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    verb: str
    prep: str
    case: Case | None = None
    reflexive: bool = False
    cefr: CEFR | None = None
    gloss_en: str | None = None

    @property
    def key(self) -> str:
        head = f"sich {self.verb}" if self.reflexive else self.verb
        return f"{head} {self.prep}".lower()


class CollocationSeed(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    kind: Literal["noun_verb", "adj_noun"]
    display: str | None = None
    cefr: CEFR | None = None
    gloss_en: str | None = None


_CASE_SUFFIX_RE = re.compile(r"\s*\+(Akk|Dat|Gen)\s*$")


class UnitOverride(BaseModel):
    """A reviewer's correction to one unit: case, citation form, level.

    Reviewers write the citation form the way the deck shows it, case
    included ("abhängen von +Dat"); the deck appends the case itself, so a
    display kept verbatim showed "+Dat +Dat". The suffix is split off into
    ``case`` here, once, at load time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    case: Case | None = None
    display: str | None = None
    cefr: CEFR | None = None
    gloss_en: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _split_case_suffix(cls, data: object) -> object:
        if not isinstance(data, dict) or not isinstance(data.get("display"), str):
            return data
        match = _CASE_SUFFIX_RE.search(data["display"])
        if match is None:
            return data
        out = dict(data)
        out["display"] = data["display"][: match.start()].rstrip()
        out["case"] = data.get("case") or match.group(1)
        return out


class CuratedLists(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    connectors: list[ConnectorSpec] = Field(default_factory=list)
    idioms: list[IdiomSpec] = Field(default_factory=list)
    verb_prep_seeds: list[VerbPrepSeed] = Field(default_factory=list)
    collocation_seeds: list[CollocationSeed] = Field(default_factory=list)
    trivial_stoplist: list[str] = Field(default_factory=list)
    #: Lemma keys dropped outright, whatever the counts say.
    exclude: list[str] = Field(default_factory=list)
    #: Card ids a review found defective; never selected again.
    excluded_cards: list[str] = Field(default_factory=list)
    #: Reviewer corrections applied after the unit decision.
    overrides: list[UnitOverride] = Field(default_factory=list)


def _load_yaml_list(path: Path) -> list[object]:
    if not path.exists():
        return []
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if loaded is None:
        return []
    if not isinstance(loaded, list):
        raise ValueError(f"{path} must hold a YAML list")
    return list(loaded)


def _parse_idiom(raw: object) -> IdiomSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"idiom entry must be a mapping, got {raw!r}")
    pattern: list[IdiomElement] = []
    for element in raw.get("pattern", []):
        if isinstance(element, str):
            if element.startswith("lemma:"):
                pattern.append(IdiomElement(lemma=element[len("lemma:") :].lower()))
            else:
                pattern.append(IdiomElement(surface=element))
        else:
            pattern.append(IdiomElement.model_validate(element))
    return IdiomSpec.model_validate({**raw, "pattern": pattern})


def load_curated(phrases_dir: Path = DEFAULT_PHRASES_DIR) -> CuratedLists:
    connectors = [
        ConnectorSpec.model_validate(raw)
        for raw in _load_yaml_list(phrases_dir / "connectors.yaml")
    ]
    idioms = [_parse_idiom(raw) for raw in _load_yaml_list(phrases_dir / "idioms.yaml")]
    seeds = [
        VerbPrepSeed.model_validate(raw)
        for raw in _load_yaml_list(phrases_dir / "verb_prep_seed.yaml")
    ]
    colloc = [
        CollocationSeed.model_validate(raw)
        for raw in _load_yaml_list(phrases_dir / "collocation_seed.yaml")
    ]
    stoplist = [str(raw).lower() for raw in _load_yaml_list(phrases_dir / "trivial_stoplist.yaml")]
    exclude = [str(raw).lower() for raw in _load_yaml_list(phrases_dir / "exclude.yaml")]
    excluded_cards = [str(raw) for raw in _load_yaml_list(phrases_dir / "excluded_cards.yaml")]
    overrides = [
        UnitOverride.model_validate(raw)
        for raw in _load_yaml_list(phrases_dir / "unit_overrides.yaml")
    ]
    keys = [c.key for c in connectors]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate connector keys in connectors.yaml")
    return CuratedLists(
        connectors=connectors,
        idioms=idioms,
        verb_prep_seeds=seeds,
        collocation_seeds=colloc,
        trivial_stoplist=stoplist,
        exclude=exclude,
        excluded_cards=excluded_cards,
        overrides=overrides,
    )
