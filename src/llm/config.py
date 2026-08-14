"""Loader for config.yaml: the privacy and lane-behaviour flags that govern LLM calls."""

from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config.yaml")


def _load_yaml(config_path: Path | str) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return data


def load_restrict_user_content_to_paid_lane(
    config_path: Path | str = DEFAULT_CONFIG_PATH,
) -> bool:
    """Read ``privacy.restrict_user_content_to_paid_lane`` from config.yaml.

    Defaults to ``False`` (the documented default) when the file or key is absent,
    so a fresh checkout without a config.yaml still behaves per spec.
    """
    data = _load_yaml(config_path)
    privacy = data.get("privacy", {})
    if not isinstance(privacy, dict):
        return False
    return bool(privacy.get("restrict_user_content_to_paid_lane", False))
