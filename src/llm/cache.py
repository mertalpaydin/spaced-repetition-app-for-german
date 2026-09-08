"""Content-addressed local cache for LLM requests and responses."""

import hashlib
import json
from pathlib import Path
from typing import Any


def cache_key_kwargs(namespace: str | None) -> dict[str, str]:
    """The extra key parameters for a namespaced cache slot, or none.

    ``None`` maps to NO parameter rather than ``namespace=None``, so every
    key written before namespaces existed is still the key an un-namespaced
    call computes today. The cache is the idempotency mechanism (CLAUDE.md
    section 9); changing the default key would re-buy everything in it.
    """
    return {} if namespace is None else {"namespace": namespace}


class LlmCache:
    """Local disk cache keyed by SHA-256 hash of the complete LLM request payload."""

    def __init__(self, cache_dir: Path | str = ".cache/llm") -> None:
        self.cache_dir = Path(cache_dir)

    def _ensure_dir(self) -> None:
        """Create the cache directory lazily, only when we are about to write to it."""
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
        """Retrieve cached response string if present. A missing/null response is a miss."""
        key = self._compute_key(model, prompt, **kwargs)
        cache_file = self.cache_dir / f"{key}.json"
        if not cache_file.exists():
            return None
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        response = data.get("response")
        if response is None:
            return None
        return str(response)

    def set(self, model: str, prompt: str, response: str, **kwargs: Any) -> None:
        """Persist response string to disk cache."""
        self._ensure_dir()
        key = self._compute_key(model, prompt, **kwargs)
        cache_file = self.cache_dir / f"{key}.json"
        data = {
            "model": model,
            "prompt": prompt,
            "response": response,
            "key": key,
        }
        cache_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
