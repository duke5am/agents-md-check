#!/usr/bin/env python3
"""agent-config-lint: a static linter for AI coding-agent instruction files.

This wrapper exists so `python3 agent_config_lint.py <path>` keeps working from
a clone. The same CLI is installed as the `agents-md-check` console script; the
implementation lives in `agents_md_check/lint.py` (analysis) and
`agents_md_check/cli.py` (command line), so the installed package and the
checkout are the same code, not two versions of it.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agents_md_check.lint import *  # noqa: E402,F401,F403
from agents_md_check.lint import (  # noqa: E402
    _brace_expand,
    _clean_token,
    _opposition_pair,
)
from agents_md_check.cli import build_parser, format_rules, main  # noqa: E402,F401

__all__ = ["build_parser", "format_rules", "main"]

if __name__ == "__main__":
    sys.exit(main())
