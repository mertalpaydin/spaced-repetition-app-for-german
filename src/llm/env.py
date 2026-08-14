"""Minimal .env loader.

The stage 0 spec (docs/01-foundation.md) puts the two lane keys in a gitignored
`.env`, but nothing in the process reads that file: `os.getenv` only sees what
the shell exported. Running an entry point straight from a terminal would
therefore fail with a missing-key error even though `.env` is correctly filled
in, which is a confusing first-run experience.

This is deliberately stdlib-only rather than a `python-dotenv` dependency:
CLAUDE.md section 6 requires a justification for every new dependency, and the
subset of the format this project needs is roughly twenty lines.

Values already present in the environment always win, so an explicit
`GEMINI_FREE_API_KEY=... python -m ...` overrides the file, and CI secrets are
never shadowed by a stray local `.env`.
"""

import os
from pathlib import Path

DEFAULT_ENV_PATH = Path(".env")


def load_env_file(path: Path | str = DEFAULT_ENV_PATH, *, override: bool = False) -> dict[str, str]:
    """Read simple ``KEY=value`` lines into ``os.environ``.

    Returns the mapping that was applied. A missing file is not an error: the
    variables may legitimately come from the shell, from CI secrets, or from a
    Worker binding.

    Supports comments, blank lines, `export ` prefixes and single or double
    quoted values. It deliberately does not support multi-line values or
    interpolation; anything needing those belongs in real configuration.
    """
    env_path = Path(path)
    applied: dict[str, str] = {}
    if not env_path.is_file():
        return applied

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if override or key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied
