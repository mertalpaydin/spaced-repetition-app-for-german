"""Backward compatibility module redirecting to src.cli.sitting."""

from src.cli.sitting import AttemptResult, InteractiveSession, InteractiveSitting, RoundSummary

__all__ = [
    "AttemptResult",
    "InteractiveSession",
    "InteractiveSitting",
    "RoundSummary",
]
