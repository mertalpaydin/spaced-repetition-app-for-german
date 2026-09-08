"""One phrase match in one corpus sentence, and the JSONL it is written to.

The parse stage writes every match every detector finds, generously, to
``data/phrases/build/occurrences.jsonl``. Thresholds live in ``units.py`` so
re-tuning them never costs another parse of the corpus.
"""

from collections.abc import Iterable, Iterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import Case, PhraseKind


class Occurrence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: PhraseKind
    #: Aggregation key, lowercased lemmas, e.g. ``"warten auf"``.
    unit_key: str
    #: The unit's parts in citation order.
    parts: list[str]
    #: Token indices of the unit in this sentence, in sentence order.
    token_indices: list[int]
    #: Character spans of those tokens, same order.
    spans: list[tuple[int, int]]
    #: The tokens' surface forms, same order.
    surfaces: list[str]
    corpus_source: str
    line_id: str
    text: str
    form_key: str = ""
    case: Case | None = None
    sentence_initial: bool = False
    needs_context: bool = False
    #: Detector-specific evidence for the report (dependency labels, etc.).
    evidence: dict[str, str] = Field(default_factory=dict)

    @property
    def sentence_key(self) -> tuple[str, str]:
        return (self.corpus_source, self.line_id)


def write_occurrences(path: Path, occurrences: Iterable[Occurrence]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for occurrence in occurrences:
            handle.write(occurrence.model_dump_json())
            handle.write("\n")
            count += 1
    return count


def read_occurrences(path: Path) -> Iterator[Occurrence]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield Occurrence.model_validate_json(line)
