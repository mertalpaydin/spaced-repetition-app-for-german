"""Exercise scraping and baseline anchor extraction module.

Parses human-authored exercises from educational German portals (e.g. Mein-Deutschbuch, Schubert)
to serve as golden external anchors for verification and distribution baselines.
"""

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from src.contracts import CEFR, ItemType


class ScrapedExercise(BaseModel):
    """Human-authored gold exercise with verified published answer keys."""

    model_config = ConfigDict(frozen=True)
    id: str
    topic_id: str
    cefr: CEFR
    item_type: ItemType = "cloze_free"
    prompt: str
    cue: str | None = None
    accepted_answers: list[str] = Field(default_factory=list)
    distractors: list[str] = Field(default_factory=list)
    source_name: str = "mein_deutschbuch"
    source_file: str | None = None


class HtmlExerciseParser:
    """Extracts structured grammar items from frozen HTML exercise sheets."""

    def __init__(self, topic_id: str = "dativ_nach_praeposition", cefr: CEFR = "A2") -> None:
        self.topic_id = topic_id
        self.cefr = cefr

    def parse_html_file(self, file_path: Path | str) -> list[ScrapedExercise]:
        """Parse a frozen HTML file containing fill-in-the-blank or transformation exercises."""
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"HTML baseline file not found: {p}")

        content = p.read_text(encoding="utf-8")
        return self.parse_html_string(content, source_file=p.name)

    def parse_html_string(
        self, html_content: str, source_file: str = "sample.html"
    ) -> list[ScrapedExercise]:
        """Parse HTML string for exercise items marked with class or data attributes."""
        items: list[ScrapedExercise] = []

        # Find exercise list items: <li class="exercise-item" ...>
        # Pattern handles data-answer="..." or <span class="solution">...</span>
        item_patterns = re.findall(
            r'<li[^>]*data-id="([^"]+)"[^>]*data-answer="([^"]+)"[^>]*>(.*?)</li>',
            html_content,
            re.DOTALL | re.IGNORECASE,
        )

        for item_id, answers_str, prompt_html in item_patterns:
            # Clean prompt HTML tags but preserve the gap ___
            clean_prompt = re.sub(r"<input[^>]*>", "___", prompt_html)
            clean_prompt = re.sub(r"<[^>]+>", "", clean_prompt)
            clean_prompt = re.sub(r"\s+", " ", clean_prompt).strip()

            accepted_answers = [a.strip() for a in answers_str.split("|") if a.strip()]

            # Look for optional cues in brackets like (gehen)
            cue_match = re.search(r"\(([^)]+)\)", clean_prompt)
            cue = cue_match.group(1) if cue_match else None

            items.append(
                ScrapedExercise(
                    id=item_id,
                    topic_id=self.topic_id,
                    cefr=self.cefr,
                    item_type="cloze_cued" if cue else "cloze_free",
                    prompt=clean_prompt,
                    cue=cue,
                    accepted_answers=accepted_answers,
                    source_name="mein_deutschbuch",
                    source_file=source_file,
                )
            )

        return items
