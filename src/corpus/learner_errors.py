"""Learner error mining and mapping module (Falko-MERLIN / ERRANT German).

Maps authentic L2 learner errors and grammatical edit operations to taxonomy topic IDs
and constructs empirical confusion matrices.
"""

import json
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from src.contracts import CEFR


class LearnerError(BaseModel):
    """An annotated learner error extracted from corpora like Falko-MERLIN."""

    model_config = ConfigDict(frozen=True)
    id: str
    original_text: str
    corrected_text: str
    original_token: str
    corrected_token: str
    errant_tag: str
    mapped_topic_id: str | None = None
    cefr: CEFR = "A2"
    source: str = "falko_merlin"


class LearnerErrorMapper:
    """Maps ERRANT edit tags and grammatical context to taxonomy topic IDs."""

    # Default heuristic mappings from ERRANT edit tags to likely topic families
    TAG_TO_TOPIC_HEURISTICS: dict[str, str] = {
        "R:DET:CASE:DAT": "kasus_dativ_formen",
        "R:DET:CASE:ACC": "kasus_akkusativ_formen",
        "R:DET:CASE:GEN": "kasus_genitiv_formen",
        "R:PREP:WECHSEL": "dativ_nach_praeposition",
        "R:ADJ:FORM:DEF": "adjektivdeklination_bestimmt",
        "R:ADJ:FORM:INDEF": "adjektivdeklination_unbestimmt",
        "R:VERB:TENSE:PERF_AUX": "perfekt_sein",
        "R:VERB:TENSE:PAST": "praeteritum_vollverben",
        "R:VERB:MOOD:SUBJ2": "konjunktiv_ii_irreal_gegenwart",
        "R:VERB:MOOD:SUBJ1": "konjunktiv_i_indirekte_rede",
        "R:CONJ:SUBORD": "nebensatz_weil_da",
        "R:PRON:REL": "relativsatz_nom_akk",
        "R:PASS:VOICE": "passiv_praesens",
    }

    def __init__(self, custom_mapping: dict[str, str] | None = None) -> None:
        self.mapping = dict(self.TAG_TO_TOPIC_HEURISTICS)
        if custom_mapping:
            self.mapping.update(custom_mapping)

    def map_error(self, errant_tag: str, context: str = "") -> str | None:
        """Map an ERRANT edit tag and context to a valid taxonomy topic ID."""
        # 1. Exact match
        if errant_tag in self.mapping:
            return self.mapping[errant_tag]

        # 2. Context-based disambiguation
        tag_upper = errant_tag.upper()
        context_lower = context.lower()

        if "PREP" in tag_upper:
            if any(p in context_lower for p in ["wegen", "trotz", "während", "statt"]):
                return "praepositionen_genitiv"
            if any(
                p in context_lower
                for p in ["in", "auf", "an", "unter", "über", "vor", "hinter", "neben"]
            ):
                return "dativ_nach_praeposition"
            if any(p in context_lower for p in ["mit", "nach", "von", "zu", "aus", "bei", "seit"]):
                return "praepositionen_dativ"
            if any(p in context_lower for p in ["für", "durch", "ohne", "gegen", "um"]):
                return "praepositionen_akkusativ"

        if "VERB" in tag_upper and "AUX" in tag_upper:
            return (
                "perfekt_sein"
                if "ist" in context_lower or "sein" in context_lower
                else "perfekt_haben"
            )

        if "ADJ" in tag_upper:
            return "adjektivdeklination_bestimmt"

        if "DET" in tag_upper or "NOUN" in tag_upper:
            if "DAT" in tag_upper:
                return "kasus_dativ_formen"
            if "ACC" in tag_upper:
                return "kasus_akkusativ_formen"
            if "GEN" in tag_upper:
                return "kasus_genitiv_formen"

        return None

    @classmethod
    def load_mapped_sample(cls, jsonl_path: Path | str) -> list[LearnerError]:
        """Load mapped learner error sample from a JSONL file."""
        p = Path(jsonl_path)
        if not p.exists():
            raise FileNotFoundError(f"Mapped sample file not found: {p}")

        errors: list[LearnerError] = []
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    errors.append(LearnerError.model_validate(data))
        return errors


class EmpiricalConfusionMatrix:
    """Builds and queries empirical confusion rates between grammar topics."""

    def __init__(self) -> None:
        # matrix[intended_topic][implied_topic] = count
        self.matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.topic_attempts: dict[str, int] = defaultdict(int)

    def record_error(self, intended_topic: str, implied_topic: str) -> None:
        """Record an error instance where intended topic produced an implied topic error."""
        self.matrix[intended_topic][implied_topic] += 1
        self.topic_attempts[intended_topic] += 1

    def get_confusion_rate(self, topic_a: str, topic_b: str) -> float:
        """Compute the empirical confusion rate: proportion of errors on A implying B."""
        total = self.topic_attempts.get(topic_a, 0)
        if total == 0:
            return 0.0
        confused_as_b = self.matrix[topic_a].get(topic_b, 0)
        return confused_as_b / total

    def get_top_confusions_for_topic(self, topic_id: str, top_n: int = 3) -> list[tuple[str, int]]:
        """Return the top N most frequent confusion topics for a given topic ID."""
        counts = self.matrix.get(topic_id, {})
        sorted_pairs = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        return sorted_pairs[:top_n]
