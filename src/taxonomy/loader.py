"""Taxonomy loader module for loading YAML definitions into typed Topic models."""

from pathlib import Path

import yaml
from pydantic import TypeAdapter

from src.contracts import Topic


def load_taxonomy(path: Path | str | None = None) -> list[Topic]:
    """Load taxonomy topics from a YAML file.

    Args:
        path: Path to the taxonomy YAML file. If None, defaults to data/taxonomy.yaml.

    Returns:
        A list of validated Topic instances.
    """
    if path is None:
        path = Path(__file__).parent.parent.parent / "data" / "taxonomy.yaml"
    else:
        path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Taxonomy file not found at: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw_data = yaml.safe_load(f)

    if not isinstance(raw_data, list):
        raise ValueError("Taxonomy YAML must contain a top-level list of topic objects.")

    adapter = TypeAdapter(list[Topic])
    return adapter.validate_python(raw_data)
