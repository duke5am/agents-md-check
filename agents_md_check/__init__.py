"""agents-md-check: a static linter for AI coding-agent instruction files.

The analysis lives in :mod:`agents_md_check.lint`; the command line lives in
:mod:`agents_md_check.cli`. ``agent_config_lint.py`` at the project root is a
thin wrapper around both, so `python3 agent_config_lint.py` keeps working from
a clone.
"""

from agents_md_check.lint import (  # noqa: F401
    Analysis,
    Finding,
    InstructionFile,
    RULES,
    SEVERITIES,
    analyse,
    format_human,
    format_json,
    severity_at_least,
)

__version__ = "1.0.0"
__all__ = [
    "Analysis",
    "Finding",
    "InstructionFile",
    "RULES",
    "SEVERITIES",
    "analyse",
    "format_human",
    "format_json",
    "severity_at_least",
]
