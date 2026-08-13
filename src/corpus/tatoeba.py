"""Tatoeba carrier sentence corpus loader, filtering, and lemma tagger."""

import csv
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import CEFR
from src.lexicon.vocabulary import VocabularyStore


class CarrierSentence(BaseModel):
    """Clean carrier sentence suitable for item generation seeding."""

    model_config = ConfigDict(frozen=True)
    id: str
    german_text: str
    english_text: str | None = None
    token_count: int
    estimated_cefr: CEFR = "A2"
    carrier_lemmas: list[str] = Field(default_factory=list)


class TatoebaCorpus:
    """Manages ingestion and filtering of German sentences for exercise seed generation."""

    def __init__(self, sentences: list[CarrierSentence] | None = None) -> None:
        self.sentences: list[CarrierSentence] = sentences or []

    def filter_by_cefr(self, max_cefr: CEFR) -> list[CarrierSentence]:
        """Filter sentences that fit within the specified CEFR ceiling."""
        levels = ["A1", "A2", "B1", "B2"]
        max_idx = levels.index(max_cefr)
        return [s for s in self.sentences if levels.index(s.estimated_cefr) <= max_idx]

    def filter_by_length(self, min_tokens: int = 4, max_tokens: int = 20) -> list[CarrierSentence]:
        """Filter sentences by token length."""
        return [s for s in self.sentences if min_tokens <= s.token_count <= max_tokens]

    @classmethod
    def load_from_tsv(
        cls,
        tsv_path: Path | str,
        vocab_store: VocabularyStore | None = None,
    ) -> "TatoebaCorpus":
        """Load sentences from a tab-separated file (format: id, de_text, optional en_text)."""
        p = Path(tsv_path)
        if not p.exists():
            raise FileNotFoundError(f"TSV file not found: {p}")

        sentences: list[CarrierSentence] = []
        with p.open("r", encoding="utf-8") as f:
            reader = csv.reader(f, delimiter="\t")
            for row in reader:
                if not row or len(row) < 2:
                    continue
                s_id = row[0].strip()
                de_text = row[1].strip()
                en_text = row[2].strip() if len(row) > 2 else None

                # Extract tokens
                tokens = re.findall(r"\b[A-ZÄÖÜa-zäöüß]+\b", de_text)
                token_count = len(tokens)
                if token_count < 3 or token_count > 30:
                    continue

                # Extract carrier lemmas (content words with length >= 3)
                carrier_lemmas = [
                    t
                    for t in tokens
                    if len(t) >= 3 and t.lower() not in VocabularyStore.FUNCTION_WORDS
                ]

                # Estimate CEFR level based on hardest word
                estimated_cefr: CEFR = "A1"
                if vocab_store is not None:
                    max_rank = 1
                    for w in carrier_lemmas:
                        lvl = vocab_store.get_level(w) or "B1"
                        rank = VocabularyStore.LEVEL_RANKS.get(lvl, 3)
                        if rank > max_rank:
                            max_rank = rank
                            estimated_cefr = lvl

                sentences.append(
                    CarrierSentence(
                        id=s_id,
                        german_text=de_text,
                        english_text=en_text,
                        token_count=token_count,
                        estimated_cefr=estimated_cefr,
                        carrier_lemmas=carrier_lemmas,
                    )
                )

        return cls(sentences=sentences)
