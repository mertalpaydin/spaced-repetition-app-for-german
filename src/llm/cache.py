"""Content-addressed local cache for LLM requests and responses."""

import hashlib
import json
from pathlib import Path
from typing import Any


class LlmCache:
    """Local disk cache keyed by SHA-256 hash of the complete LLM request payload."""

    def __init__(self, cache_dir: Path | str = ".cache/llm") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _compute_key(self, model: str, prompt: str, **kwargs: Any) -> str:
        payload = {
            "model": model,
            "prompt": prompt,
            "params": sorted(kwargs.items()),
        }
        raw = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, model: str, prompt: str, **kwargs: Any) -> str | None:
        """Retrieve cached response string if present."""
        key = self._compute_key(model, prompt, **kwargs)
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                return str(data.get("response"))
            except Exception:
                return None
        return None

    def set(self, model: str, prompt: str, response: str, **kwargs: Any) -> None:
        """Persist response string to disk cache."""
        key = self._compute_key(model, prompt, **kwargs)
        cache_file = self.cache_dir / f"{key}.json"
        data = {
            "model": model,
            "prompt": prompt,
            "response": response,
            "key": key,
        }
        cache_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
