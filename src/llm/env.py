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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.llm.client import GeminiLlmClient

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


class FreeLaneKeyMissingError(RuntimeError):
    """``client_from_env(free_lane_only=True)`` could not find an explicit
    ``GEMINI_FREE_API_KEY``.

    Its own error type rather than a bare ``ValueError`` so a caller can
    catch exactly this and print it as a setup instruction instead of a
    traceback, and so nothing can confuse it with a transport failure.
    """


#: What a free-lane-only run must find in the environment. Named here, once,
#: because both the refusal message and the tests quote it.
FREE_LANE_KEY_ENV_VAR = "GEMINI_FREE_API_KEY"

_FREE_LANE_ONLY_REFUSAL = (
    f"--free-lane-only was requested but {FREE_LANE_KEY_ENV_VAR} is not set. "
    "Refusing to start: this run would otherwise fall back to GEMINI_API_KEY, "
    "which on this project's machine is the BILLED key, and every call would "
    "be paid spend under a flag that promises none.\n"
    "Put the unbilled project's key in .env as:\n"
    f"    {FREE_LANE_KEY_ENV_VAR}=<the key from the UNBILLED Google Cloud project>\n"
    "It must come from the free (unbilled) project, not the billed one: "
    "Gemini quota is enforced per Cloud project, so a billed project's key "
    "has no free tier to run on (CLAUDE.md 9, 'Two lanes, two projects')."
)


def client_from_env(
    *, free_lane_only: bool = False, detached_batch: bool = False
) -> "GeminiLlmClient | None":
    """Build a real ``GeminiLlmClient`` only when a lane key is actually
    configured in the environment; ``None`` otherwise.

    Moved verbatim from the deleted ``src/generation/blanking/sentence_source.py``.

    **Default (``free_lane_only=False``).** Built with ``forbid_batch=True``
    and ``forbid_paid_lane=False``: no real Batch API job is ever queued, but
    once the free lane's daily quota is spent the run continues on the paid
    lane, synchronously (the owner's instruction: "no batch api ... go to paid
    on demand api, if free lane is already expired").

    **``free_lane_only=True``** is the deliberate zero-spend choice:
    ``forbid_paid_lane=True``, so a spent free-lane daily quota raises
    ``PaidLaneForbiddenError`` instead of continuing on the billed project.
    The key is resolved HERE and passed explicitly: ``GeminiLlmClient``
    resolves its free key as ``GEMINI_FREE_API_KEY or GEMINI_API_KEY``, and on
    the owner's machine ``GEMINI_API_KEY`` is the billed key, so a missing
    ``GEMINI_FREE_API_KEY`` raises ``FreeLaneKeyMissingError`` before any
    client exists. The check is on the variable being SET, never on comparing
    key material.

    **``detached_batch=True``** allows overflow to queue as a real Batch API
    job and exit instead of blocking on the poll loop. Mutually exclusive
    with ``free_lane_only``.
    """
    from src.llm.client import GeminiLlmClient

    if free_lane_only and detached_batch:
        raise ValueError(
            "free_lane_only and detached_batch are mutually exclusive: the "
            "free lane has no batch mode to detach, so asking for both is a "
            "contradiction rather than a preference."
        )
    if free_lane_only:
        free_key = os.getenv(FREE_LANE_KEY_ENV_VAR)
        if not free_key:
            raise FreeLaneKeyMissingError(_FREE_LANE_ONLY_REFUSAL)
        return GeminiLlmClient(free_api_key=free_key, forbid_paid_lane=True, forbid_batch=True)
    if (
        os.getenv("GEMINI_FREE_API_KEY")
        or os.getenv("GEMINI_PAID_API_KEY")
        or os.getenv("GEMINI_API_KEY")
    ):
        return GeminiLlmClient(
            forbid_paid_lane=False,
            forbid_batch=not detached_batch,
            detach_batch=detached_batch,
        )
    return None
