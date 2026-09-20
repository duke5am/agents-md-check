#!/usr/bin/env python3
"""The command-line entry point for agent-config-lint.

Lints the instruction files AI coding agents read: AGENTS.md, CLAUDE.md,
.cursor/rules/*.mdc, .cursorrules, .github/copilot-instructions.md.

Exit codes:
    0  no findings at or above the selected severity threshold
    1  at least one finding at or above the threshold
    2  usage error (bad arguments, missing path, no instruction file found,
       unreadable file)

The implementation lives here, inside the importable package, so that the
installed console script (``agents-md-check``) and the repo-root wrapper
``agent_config_lint.py`` run exactly the same code.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from agents_md_check.lint import (
    DEFAULT_MAX_LINES,
    DEFAULT_MAX_WORDS,
    OPPOSITION_PAIRS,
    RULES,
    SEVERITIES,
    __version__,
    analyse,
    discover,
    format_human,
    format_json,
    severity_at_least,
)

PROG = "agent-config-lint"


def format_rules() -> str:
    lines = [
        f"agent-config-lint {__version__} -- rules",
        "",
        "basis values:",
        "  documented = a requirement of the file format itself",
        "  convention = a convention this tool enforces, not a documented requirement",
        "  heuristic  = pattern matching over text, not language understanding",
        "",
    ]
    for spec in RULES:
        lines.append(f"{spec.code}  {spec.severity:6} [{spec.basis:10}]  {spec.title}")
    lines.append("")
    lines.append("--severity only filters reporting and the exit code; it never "
                 "changes the analysis.")
    lines.append("")
    lines.append("Opposition pairs used by AGL002 (matched sentence by sentence):")
    for pid, left, right, label in OPPOSITION_PAIRS:
        lines.append(f"  {pid:20} left={left}")
        lines.append(f"  {'':20} right={right}")
        lines.append(f"  {'':20} -> {label}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Lint the instruction files AI coding agents read: AGENTS.md, "
            "CLAUDE.md, .cursor/rules/*.mdc, .cursorrules, "
            ".github/copilot-instructions.md."
        ),
        epilog=(
            "exit codes: 0 = no findings at or above the threshold; "
            "1 = findings at or above the threshold; 2 = usage or parse error."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", nargs="?",
                        help="repository root or a single instruction file")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable JSON instead of text")
    parser.add_argument("--severity", choices=SEVERITIES, default="low",
                        help="reporting floor (default: low)")
    parser.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES,
                        metavar="N",
                        help=f"line budget (default: {DEFAULT_MAX_LINES})")
    parser.add_argument("--max-words", type=int, default=DEFAULT_MAX_WORDS,
                        metavar="N",
                        help=f"word budget (default: {DEFAULT_MAX_WORDS})")
    parser.add_argument("--quiet", action="store_true",
                        help="omit the header and the file inventory")
    parser.add_argument("--list-rules", action="store_true",
                        help="print the rule catalogue and exit 0")
    parser.add_argument("--version", action="version",
                        version=f"agent-config-lint {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_rules:
        print(format_rules())
        return 0

    if not args.path:
        parser.error("a path is required (a repository root or a single file)")

    target = Path(args.path)
    if not target.exists():
        print(f"{PROG}: error: path does not exist: {args.path}", file=sys.stderr)
        return 2
    if not (target.is_dir() or target.is_file()):
        print(f"{PROG}: error: unsupported path: {args.path}", file=sys.stderr)
        return 2
    if args.max_lines < 1 or args.max_words < 1:
        print(f"{PROG}: error: --max-lines and --max-words must be >= 1",
              file=sys.stderr)
        return 2

    if target.is_dir() and not discover(target.resolve()):
        print(
            f"{PROG}: error: no agent instruction file found under {args.path} "
            "(looked for AGENTS.md, CLAUDE.md, .cursorrules, GEMINI.md, "
            "CONVENTIONS.md, .cursor/rules/*.mdc, .github/copilot-instructions.md)",
            file=sys.stderr,
        )
        return 2

    try:
        analysis = analyse(target, max_lines=args.max_lines, max_words=args.max_words)
    except OSError as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(format_json(analysis, args.severity))
    else:
        print(format_human(analysis, args.severity, args.quiet))

    at_threshold = [f for f in analysis.findings
                    if severity_at_least(f.severity, args.severity)]
    return 1 if at_threshold else 0


if __name__ == "__main__":
    sys.exit(main())
