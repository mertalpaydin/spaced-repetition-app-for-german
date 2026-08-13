"""Main executable entrypoint for the German Grammar Trainer CLI."""

import sys

from src.cli.app import run_cli

if __name__ == "__main__":
    sys.exit(run_cli())
