#!/usr/bin/env python3
"""LLM evaluation entry point. See scripts/llm/cli.py for commands."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.llm.cli import cli

if __name__ == "__main__":
    cli()
