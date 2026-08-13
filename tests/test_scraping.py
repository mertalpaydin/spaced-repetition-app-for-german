"""Unit tests for the exercise scraping and baseline anchor extraction module."""

from pathlib import Path

import pytest
from src.corpus.scraping import HtmlExerciseParser


@pytest.fixture
def sample_html_path(data_fixtures_dir: Path) -> Path:
    return data_fixtures_dir / "scraping" / "mein_deutschbuch_sample.html"


def test_html_exercise_parser_extracts_items(sample_html_path: Path) -> None:
    """Verify HTML parser extracts structured exercises with clean gap placeholders and answers."""
    parser = HtmlExerciseParser(topic_id="dativ_nach_praeposition", cefr="A2")
    exercises = parser.parse_html_file(sample_html_path)

    assert len(exercises) == 8
    first = exercises[0]
    assert first.id == "scraped_001"
    assert first.topic_id == "dativ_nach_praeposition"
    assert first.cefr == "A2"
    assert "___" in first.prompt
    assert "dem" in first.accepted_answers
    assert first.source_name == "mein_deutschbuch"


def test_html_parser_missing_file_raises_error(tmp_path: Path) -> None:
    """Verify HTML parser raises FileNotFoundError when file does not exist."""
    parser = HtmlExerciseParser()
    with pytest.raises(FileNotFoundError):
        parser.parse_html_file(tmp_path / "missing.html")
