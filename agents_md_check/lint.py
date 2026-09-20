#!/usr/bin/env python3
"""agent-config-lint -- a static linter for AI coding-agent instruction files.

It reads the files an AI coding agent is told to obey -- ``AGENTS.md``,
``CLAUDE.md``, ``.cursor/rules/*.mdc``, ``.cursorrules``,
``.github/copilot-instructions.md`` and relatives -- and reports *structural*
problems: rules that point at files that do not exist, rules that contradict
each other, duplicated rules, oversized files, malformed Cursor frontmatter,
rules that cannot be verified, and so on.

The premise: an instruction file that grows without discipline stops being
read. This tool does not judge wording and has no idea whether a rule is
*good*. It matches patterns in text (see README, "What this does not do").

Standard library only: no dependencies, no network. The analysis lives here;
the command line lives in :mod:`agents_md_check.cli`, which the installed
``agents-md-check`` console script calls.

Exit codes
----------
0   No findings at or above the selected severity threshold.
1   At least one finding at or above the threshold.
2   Usage error (bad arguments, missing path, no instruction file found,
    unreadable file).

Severity threshold
------------------
``--severity high|medium|low`` (default ``low``) sets the reporting floor:
``--severity high`` prints only high findings, and exits 0 when there are
none. Every finding has a fixed severity; the threshold only filters display
and the exit code, never the analysis.

Caveats
-------
* Every check is pattern matching over text. Contradiction detection only
  knows the opposition pairs declared in ``OPPOSITION_PAIRS``, and only
  compares sentences inside the same file *and* the same heading, or two rule
  files whose globs target the same language.
* Checks that encode a convention rather than a documented requirement say so
  in their message (for example the Cursor frontmatter checks beyond the three
  documented keys).
* The only Cursor frontmatter keys validated are the three Cursor documents:
  ``description``, ``globs``, ``alwaysApply``. Anything else is reported at
  ``low`` severity and explicitly labelled as a convention.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

__version__ = "1.0.0"

#: How deep to search for instruction files below the given root.
MAX_DISCOVERY_DEPTH = 6

# --------------------------------------------------------------------------
# Severity model
# --------------------------------------------------------------------------

SEVERITIES = ("high", "medium", "low")
_SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}


def severity_at_least(severity: str, threshold: str) -> bool:
    """True when *severity* is at or above *threshold*."""
    return _SEVERITY_RANK[severity] >= _SEVERITY_RANK[threshold]


# --------------------------------------------------------------------------
# Rule catalogue (also the source for --list-rules)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleSpec:
    code: str
    severity: str
    title: str
    basis: str  # "documented" | "convention" | "heuristic"


RULES: tuple[RuleSpec, ...] = (
    RuleSpec("AGL001", "high", "Rule references a path that does not exist",
             "documented"),
    RuleSpec("AGL002", "high", "Two rules contradict each other on the same subject",
             "heuristic"),
    RuleSpec("AGL003", "medium", "Duplicate rule",
             "heuristic"),
    RuleSpec("AGL004", "medium", "Near-duplicate rule",
             "heuristic"),
    RuleSpec("AGL005", "medium", "Instruction file exceeds the size budget",
             "heuristic"),
    RuleSpec("AGL006", "low", "Neither the line nor the word budget is met",
             "heuristic"),
    RuleSpec("AGL007", "high", "Cursor .mdc file has no frontmatter block",
             "documented"),
    RuleSpec("AGL008", "high", "Cursor .mdc frontmatter is malformed",
             "documented"),
    RuleSpec("AGL009", "medium", "Cursor frontmatter is missing description or scope",
             "convention"),
    RuleSpec("AGL010", "low", "Cursor frontmatter contains an unknown key",
             "convention"),
    RuleSpec("AGL011", "medium", "Every rule file in a rules directory is alwaysApply: true",
             "heuristic"),
    RuleSpec("AGL012", "medium", "Conflicting scope: overlapping globs, divergent rules",
             "heuristic"),
    RuleSpec("AGL013", "low", "Rule appears before any heading",
             "convention"),
    RuleSpec("AGL014", "low", "Heading level jumps by more than one",
             "convention"),
    RuleSpec("AGL015", "low", "Empty section: heading with no content",
             "convention"),
    RuleSpec("AGL016", "low", "Rule is a question, not an instruction",
             "heuristic"),
    RuleSpec("AGL017", "low", "Rule is addressed to a human, not the agent",
             "heuristic"),
    RuleSpec("AGL018", "low", "Rule is not actionable / not verifiable",
             "heuristic"),
    RuleSpec("AGL019", "high", "CLAUDE.md and AGENTS.md are divergent",
             "heuristic"),
    RuleSpec("AGL020", "medium", "Only one of CLAUDE.md / AGENTS.md exists in a directory",
             "heuristic"),
)
RULE_BY_CODE = {spec.code: spec for spec in RULES}

# --------------------------------------------------------------------------
# Instruction files we look for
# --------------------------------------------------------------------------

#: Exact repo-root-relative names, mapped to a stable "kind".
KNOWN_ROOT_FILES: tuple[str, ...] = (
    "AGENTS.md",
    "CLAUDE.md",
    ".cursorrules",
    "GEMINI.md",
    "CONVENTIONS.md",
    ".github/copilot-instructions.md",
)

#: Filenames that are recognised at *any* directory depth.
KNOWN_BASENAMES: frozenset[str] = frozenset(
    {"AGENTS.md", "CLAUDE.md", ".cursorrules", "GEMINI.md", "CONVENTIONS.md"}
)

KIND_LABEL = {
    "agents": "AGENTS.md",
    "claude": "CLAUDE.md",
    "cursorrules": ".cursorrules",
    "mdc": "Cursor .mdc rule",
    "copilot": "copilot-instructions.md",
    "gemini": "GEMINI.md",
    "conventions": "CONVENTIONS.md",
    "other": "instruction file",
}

# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------

DEFAULT_MAX_LINES = 500
DEFAULT_MAX_WORDS = 6000

#: Near-duplicate similarity floor (token Jaccard over the two statements).
NEAR_DUPLICATE_SIMILARITY = 0.85
NEAR_DUPLICATE_MIN_TOKENS = 4

#: Cross-file rules are called "divergent" below this token similarity.
DIVERGENT_PAIR_SIMILARITY = 0.55

#: Similarity floor for "these two statements are about the same subject".
SAME_CLAIM_SIMILARITY = 0.45

#: Extensions we accept as "this token is a path".
PATH_EXTENSIONS = frozenset(
    """
    ts tsx js jsx mjs cjs mts cts py pyi rb go rs java kt kts swift c h cc cpp hpp
    cs php scala sh bash zsh fish ps1 sql graphql gql proto tf hcl lua r
    json jsonc yaml yml toml ini cfg conf env properties lock md mdx rst txt csv
    tsv html htm css scss sass less vue svelte astro xml svg png jpg jpeg gif webp
    ico pdf avif
    """.split()
)

#: Single-word tokens that look like paths but almost never are.
PATH_TOKEN_DENYLIST = frozenset(
    """
    node_modules dist build out target vendor coverage htmlcov site-packages
    __pycache__ venv env tmp temp var bin lib sbin etc usr opt
    """.split()
)

#: Words ending in ".word" that are prose, not filenames.
DOT_WORD_DENYLIST = frozenset({"e.g", "i.e", "etc", "vs", "st", "no"})

MDC_DOCUMENTED_KEYS = ("description", "globs", "alwaysApply")

_MDC_BOOLS = {"true": True, "false": False, "yes": True, "no": False}

# --------------------------------------------------------------------------
# Pattern tables
# --------------------------------------------------------------------------

#: Ways of writing a rule that cannot be checked by reading the diff.
UNACTIONABLE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bwrite clean code\b", "'clean code' has no observable definition"),
    (r"\bclean code\b", "'clean code' has no observable definition"),
    (r"\bbe careful\b", "'be careful' cannot be verified by reading the diff"),
    (r"\bcareful when\b", "'be careful' cannot be verified by reading the diff"),
    (r"\buse best practices?\b", "'best practices' names no specific practice"),
    (r"\bfollow best practices?\b", "'best practices' names no specific practice"),
    (r"\bbest practices?\b", "'best practices' names no specific practice"),
    (r"\bindustry[- ]standard\b", "'industry standard' names no concrete rule"),
    (r"\bhigh[- ]quality code\b", "'high quality' has no observable definition"),
    (r"\bgood code\b", "'good code' has no observable definition"),
    (r"\buse good judgment\b", "a judgement call is not a checkable instruction"),
    (r"\buse common sense\b", "common sense is not a checkable instruction"),
    (r"\bthink (?:carefully|step by step)\b", "no output can be checked against it"),
    (r"\btake your time\b", "no output can be checked against it"),
    (r"\bdo your best\b", "no output can be checked against it"),
    (r"\bmake sure (?:the )?code is (?:good|nice|clean)\b",
     "no observable definition of the target state"),
    (r"\bwhere (?:possible|appropriate|necessary)\b",
     "'where possible/appropriate' leaves the decision open"),
    (r"\bas (?:needed|appropriate|necessary)\b",
     "'as needed' leaves the decision open"),
    (r"\bif (?:needed|appropriate|necessary)\b",
     "'if needed' leaves the decision open"),
    (r"\bkeep it simple\b", "'simple' has no observable definition"),
    (r"\bwrite (?:clear|readable|maintainable|robust|elegant|idiomatic) code\b",
     "the adjective names no specific practice"),
    (r"\b(?:ensure|make sure) (?:the )?code (?:is )?(?:clear|readable|maintainable|robust)\b",
     "the adjective names no specific practice"),
    (r"\bmind the edge cases\b", "no specific edge case is named"),
    (r"\bhandle errors? (?:properly|gracefully|correctly)\b",
     "'properly/gracefully' names no specific behaviour"),
    (r"\bbe professional\b", "'professional' has no observable definition"),
)

_UNACTIONABLE_RES = tuple(
    (re.compile(pattern, re.IGNORECASE), why) for pattern, why in UNACTIONABLE_PATTERNS
)

#: Sentences addressed to a human rather than the agent.
HUMAN_DIRECTED_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bask (?:your|the|a) (?:teammate|team|colleague|reviewer|human|maintainer|owner)\b",
     "the agent cannot ask a person"),
    (r"\bcheck with (?:your|the|a) \w+", "the agent cannot check with a person"),
    (r"\bsee the wiki\b", "the agent has no access to the wiki"),
    (r"\bconsult the wiki\b", "the agent has no access to the wiki"),
    (r"\brefer to (?:the|our) (?:wiki|handbook|onboarding docs?)\b",
     "the agent has no access to that document"),
    (r"\bpost (?:it|this|that)? ?in slack\b", "the agent cannot use Slack"),
    (r"\bping (?:the|your|a) \w+", "the agent cannot ping a person"),
    (r"\bcontact (?:the|your|a) \w+", "the agent cannot contact a person"),
    (r"\bopen a ticket\b", "that is a human workflow, not an agent instruction"),
    (r"\bfile a ticket\b", "that is a human workflow, not an agent instruction"),
    (r"\byour (?:manager|lead|mentor)\b", "the agent has no manager"),
)

_HUMAN_RES = tuple(
    (re.compile(pattern, re.IGNORECASE), why) for pattern, why in HUMAN_DIRECTED_PATTERNS
)

#: Opposition pairs. Each entry is
#: (id, left_regex, right_regex, description of what they disagree about).
#: Both regexes are matched against a single lower-cased sentence.
OPPOSITION_PAIRS: tuple[tuple[str, str, str, str], ...] = (
    ("always-never", r"\balways\b", r"\bnever\b", "always vs never"),
    ("must-mustnot", r"\bmust\b", r"\bmust not\b|\bmustn'?t\b", "must vs must not"),
    ("always-avoid", r"\balways\b", r"\bavoid\b", "always vs avoid"),
    ("always-dont", r"\balways\b", r"\bdon'?t\b|\bdo not\b", "always vs do not"),
    ("require-forbid",
     r"\b(?:require[sd]?|mandatory|required)\b",
     r"\b(?:forbid(?:den)?|prohibit(?:ed)?|disallow(?:ed)?|banned)\b",
     "required vs forbidden"),
    ("prefer-never", r"\bprefer\b", r"\bnever\b", "prefer vs never"),
    ("tabs-indentation",
     r"\btabs?\b|tab indentation",
     r"spaces\b|space indentation",
     "tab indentation vs space indentation"),
    ("semicolon-on-off",
     r"\bsemicolons? (?:are )?(?:required|mandatory|needed)\b",
     r"\bno semicolons?\b|\bsemicolons? (?:are )?(?:forbidden|banned|optional)\b",
     "semicolons required vs semicolons not wanted"),
    ("any-allowed-banned",
     r"\bany\b.{0,24}\b(?:allowed|permitted|ok|okay|fine|use)\b",
     r"\b(?:avoid|no|never use|ban(?:ned)?|forbid(?:den)?)\b.{0,24}\bany\b",
     "the `any` type allowed vs banned"),
    ("strict-mode", r"\bstrict mode\b", r"\b(?:no|disable|without) strict mode\b",
     "strict mode on vs off"),
    ("trailing-comma", r"\btrailing commas?\b", r"\bno trailing commas?\b",
     "trailing commas vs no trailing commas"),
    ("commit-main",
     r"\bcommit (?:directly )?(?:to|on) (?:the )?main\b",
     r"\bnever commit (?:directly )?(?:to|on) (?:the )?main\b",
     "committing to main vs never committing to main"),
    ("mock-service",
     r"\bmocks?\b|\bmock(?:ing)?\b",
     r"\bdon'?t mock\b|\bno mocks?\b|\bavoid mocks?\b",
     "mocking vs not mocking"),
    ("comments-required",
     r"\bcomments? (?:are )?(?:required|mandatory|needed)\b|\badd comments?\b|"
     r"\bcomment (?:every|all)\b",
     r"\bno comments?\b|\bdon'?t (?:add|write) comments?\b|\bavoid comments?\b",
     "comments required vs comments not wanted"),
)

_OPPOSITION_RES = tuple(
    (pid, re.compile(left, re.IGNORECASE), re.compile(right, re.IGNORECASE), label)
    for pid, left, right, label in OPPOSITION_PAIRS
)

# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    path: str          # repo-root-relative; "" when no single file owns it
    line: int          # 1-based; 0 when the finding is file-level
    message: str
    fix: str
    rule_files: tuple[str, ...] = ()   # other files the finding spans

    def sort_key(self) -> tuple:
        return (self.path or "~", self.line, -_SEVERITY_RANK[self.severity], self.code)

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "path": self.path,
            "line": self.line,
            "message": self.message,
            "fix": self.fix,
            "rule_files": list(self.rule_files),
            "basis": RULE_BY_CODE[self.code].basis,
        }


def finding(code: str, path: str, line: int, message: str, fix: str,
            rule_files: Iterable[str] = ()) -> Finding:
    return Finding(code, RULE_BY_CODE[code].severity, path, line, message, fix,
                   tuple(rule_files))


# --------------------------------------------------------------------------
# Instruction file model
# --------------------------------------------------------------------------


@dataclass
class Statement:
    """One rule-ish unit of text, with the context it lives in."""

    index: int
    line: int
    text: str
    norm: str
    heading_path: tuple[str, ...]
    tokens: frozenset[str] = field(default_factory=frozenset)


@dataclass
class InstructionFile:
    abs_path: Path
    rel_path: str
    kind: str
    text: str
    lines: list[str]
    frontmatter: dict = field(default_factory=dict)
    frontmatter_present: bool = False
    frontmatter_ok: bool = True
    frontmatter_error: str = ""
    globs: tuple[str, ...] = ()
    always_apply: bool | None = None
    description: str = ""
    statements: list[Statement] = field(default_factory=list)

    @property
    def line_count(self) -> int:
        return self.text.count("\n") + (0 if self.text.endswith("\n") else 1)

    @property
    def word_count(self) -> int:
        return len(re.findall(r"\S+", self.text))


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_.+-]*")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'`(\[])")
_BULLET_RE = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.*)$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_SETEXT_RE = re.compile(r"^\s*(=+|-{2,})\s*$")
_CODE_SPAN_RE = re.compile(r"`([^`\n]{1,200})`")
_QUESTION_RE = re.compile(r"\?\s*$")


def tokenize(text: str) -> frozenset[str]:
    return frozenset(_WORD_RE.findall(text.lower()))


def token_similarity(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def normalise(text: str) -> str:
    """Normalise a statement for duplicate comparison."""
    lowered = text.lower()
    lowered = re.sub(r"`([^`]*)`", r"\1", lowered)
    lowered = re.sub(r"[\"'\u2018\u2019\u201c\u201d]", "", lowered)
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def split_sentences(text: str) -> list[str]:
    """Conservative sentence split: only on . ! ? followed by a capital."""
    parts: list[str] = []
    for chunk in _SENTENCE_SPLIT_RE.split(text):
        if parts and re.search(r"\b(?:e\.g|i\.e|etc|vs)\.$", parts[-1].strip()):
            parts[-1] = parts[-1] + " " + chunk
        else:
            parts.append(chunk)
    return [p.strip() for p in parts if p.strip()]


def shorten(text: str, limit: int = 70) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


# --------------------------------------------------------------------------
# Frontmatter
# --------------------------------------------------------------------------

_FM_SCALAR_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$")
_FM_LIST_ITEM_RE = re.compile(r"^\s+-\s+(.*)$")


def _strip_inline_comment(value: str) -> str:
    """Remove a YAML inline comment that is not inside quotes."""
    out = []
    quote = ""
    for i, ch in enumerate(value):
        if quote:
            out.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            continue
        if ch == "#" and (i == 0 or value[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _split_inline_list(inner: str) -> list[str] | None:
    items: list[str] = []
    depth = 0
    current: list[str] = []
    quote = ""
    for ch in inner:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            current.append(ch)
            continue
        if ch in "[{(":
            depth += 1
        elif ch in "]})":
            depth -= 1
            if depth < 0:
                return None
        if ch == "," and depth == 0:
            items.append("".join(current))
            current = []
            continue
        current.append(ch)
    if quote or depth != 0:
        return None
    items.append("".join(current))
    return [i.strip() for i in items]


def _unquote(value: str) -> tuple[str, bool]:
    """Return (cleaned value, was_quoted)."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1], True
    return value, False


def parse_frontmatter(text: str) -> dict:
    """Parse the small YAML subset Cursor rule frontmatter uses.

    Returns a dict with keys ``present``, ``ok``, ``error``, ``fields``
    (ordered {key: str | bool | list[str] | None}), ``line_of`` ({key: line}),
    ``unknown`` (keys outside the documented set) and ``end_line``.
    """
    result = {
        "present": False, "ok": True, "error": "", "fields": {},
        "line_of": {}, "unknown": [], "end_line": 0,
    }
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return result
    result["present"] = True
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        result["ok"] = False
        result["error"] = "the opening '---' is never closed by a second '---' line"
        return result
    result["end_line"] = end + 1

    index = 1
    while index < end:
        raw = lines[index]
        if not raw.strip() or raw.strip().startswith("#"):
            index += 1
            continue
        if raw[:1] in (" ", "\t"):
            # A continuation line of a nested structure we did not start.
            index += 1
            continue
        m = _FM_SCALAR_RE.match(raw)
        if not m:
            result["ok"] = False
            result["error"] = (
                f"line {index + 1} is not a 'key: value' pair: {raw.strip()!r}"
            )
            return result
        key, raw_value = m.group(1), _strip_inline_comment(m.group(2))
        result["line_of"][key] = index + 1

        if raw_value == "":
            # Maybe a block list follows; collect '- item' lines if it does.
            items: list[str] = []
            probe = index + 1
            while probe < end:
                item = _FM_LIST_ITEM_RE.match(lines[probe])
                if item is None:
                    break
                items.append(_unquote(_strip_inline_comment(item.group(1)))[0])
                probe += 1
            if items:
                result["fields"][key] = items
                index = probe
                continue
            result["fields"][key] = None
            index += 1
            continue

        cleaned, quoted = _unquote(raw_value)
        stray = ('"' if (not quoted or raw_value.strip()[:1] == '"') else "") + \
                ("'" if (not quoted or raw_value.strip()[:1] == "'") else "")
        if any(cleaned.count(ch) for ch in stray):
            result["ok"] = False
            result["error"] = (
                f"line {index + 1} has an unbalanced quote in the value for {key!r}"
            )
            return result

        if cleaned.startswith("[") and cleaned.endswith("]"):
            items = _split_inline_list(cleaned[1:-1])
            if items is None:
                result["ok"] = False
                result["error"] = (
                    f"line {index + 1} has an unbalanced list for {key!r}: {cleaned!r}"
                )
                return result
            result["fields"][key] = [_unquote(x)[0] for x in items if x != ""]
            index += 1
            continue
        if not quoted and cleaned in _MDC_BOOLS:
            result["fields"][key] = _MDC_BOOLS[cleaned]
            index += 1
            continue
        if cleaned.lower() in ("null", "~"):
            result["fields"][key] = None
            index += 1
            continue
        if "{" in cleaned or "}" in cleaned:
            depth = 0
            unbalanced = False
            for ch in cleaned:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth < 0:
                        unbalanced = True
                        break
            if unbalanced or depth != 0:
                result["ok"] = False
                result["error"] = (
                    f"line {index + 1} has unbalanced braces in {key!r}: {cleaned!r}"
                )
                return result
        if cleaned.count("[") != cleaned.count("]"):
            result["ok"] = False
            result["error"] = (
                f"line {index + 1} has an unclosed list in {key!r}: {cleaned!r}"
            )
            return result
        result["fields"][key] = cleaned
        index += 1

    result["unknown"] = [k for k in result["fields"] if k not in MDC_DOCUMENTED_KEYS]
    return result


def _coerce_globs(value) -> tuple[str, ...]:
    if value is None or isinstance(value, bool):
        return ()
    if isinstance(value, list):
        return tuple(str(v).strip() for v in value if str(v).strip())
    text = str(value).strip()
    if not text:
        return ()
    if text.startswith("[") and text.endswith("]"):
        inner = _split_inline_list(text[1:-1]) or []
        return tuple(_unquote(x)[0] for x in inner if x.strip())
    return (text,)


def _coerce_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    return _MDC_BOOLS.get(str(value).strip().strip("\"'").lower())


def kind_for(path: Path) -> str:
    name = path.name
    if name == "AGENTS.md":
        return "agents"
    if name == "CLAUDE.md":
        return "claude"
    if name == ".cursorrules":
        return "cursorrules"
    if name == "GEMINI.md":
        return "gemini"
    if name == "CONVENTIONS.md":
        return "conventions"
    if name == "copilot-instructions.md":
        return "copilot"
    if path.suffix == ".mdc":
        return "mdc"
    return "other"


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def discover(root: Path, max_depth: int = MAX_DISCOVERY_DEPTH) -> list[Path]:
    """Find instruction files under *root*, newest-first order by path."""
    found: list[Path] = []
    seen: set[str] = set()

    def add(p: Path) -> None:
        key = os.path.realpath(p)
        if key not in seen:
            seen.add(key)
            found.append(p)

    for name in KNOWN_ROOT_FILES:
        candidate = root / name
        if candidate.is_file():
            add(candidate)

    root_depth = len(root.resolve().parts)
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv",
                 ".mypy_cache", ".pytest_cache", "dist", "build", ".tox"}
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        if len(here.resolve().parts) - root_depth >= max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = sorted(d for d in dirnames if d not in skip_dirs)
        for filename in sorted(filenames):
            if filename in KNOWN_BASENAMES:
                add(here / filename)
        if here.name == "rules" and here.parent.name == ".cursor":
            for filename in sorted(filenames):
                if filename.endswith(".mdc"):
                    add(here / filename)
    found.sort(key=lambda p: str(p))
    return found


def find_repo_root(start: Path, stop: Path | None = None) -> Path:
    """Walk up from *start* looking for the directory a file's rules resolve in.

    Stops at *stop* (inclusive) when given, so that a nested project inside the
    repository being linted is found without walking out of the linted tree.
    """
    current = start.resolve()
    limit = stop.resolve() if stop is not None else None
    for _ in range(12):
        if (current / ".git").exists() or (current / ".cursor").is_dir() \
                or (current / "AGENTS.md").is_file() or (current / "CLAUDE.md").is_file():
            return current
        if current.parent == current:
            break
        if limit is not None and current == limit:
            break
        current = current.parent
    return start.resolve()


def load_instruction_file(path: Path, root: Path) -> InstructionFile:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        rel = os.path.relpath(path.resolve(), root.resolve())
    except ValueError:  # pragma: no cover - different drives (Windows only)
        rel = path.name
    inst = InstructionFile(
        abs_path=path,
        rel_path=rel,
        kind=kind_for(path),
        text=text,
        lines=text.splitlines(),
    )
    fm = parse_frontmatter(text)
    inst.frontmatter = fm
    inst.frontmatter_present = fm["present"]
    inst.frontmatter_ok = fm["ok"]
    inst.frontmatter_error = fm["error"]
    inst.globs = _coerce_globs(fm["fields"].get("globs"))
    inst.always_apply = _coerce_bool(fm["fields"].get("alwaysApply"))
    description = fm["fields"].get("description")
    inst.description = "" if description is None else str(description)
    inst.statements = extract_statements(inst)
    return inst


# --------------------------------------------------------------------------
# Statement extraction
# --------------------------------------------------------------------------


def _is_rule_like(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.endswith(":") and len(stripped.split()) <= 8:
        return False
    return len(re.findall(r"[A-Za-z]{2,}", stripped)) >= 2


_SETEXT_HEADING_RE = re.compile(r"^\s*(=+|-{2,})\s*$")


def heading_at(lines: Sequence[str], index: int) -> tuple[int, str, int] | None:
    """Return (level, title, line_number) when line *index* is a heading."""
    line = lines[index]
    match = _HEADING_RE.match(line)
    if match:
        return len(match.group(1)), match.group(2).strip(), index + 1
    nxt = lines[index + 1] if index + 1 < len(lines) else None
    stripped = line.strip()
    if (
        nxt is not None
        and stripped
        and len(stripped) < 80
        and not _BULLET_RE.match(line)
        and _SETEXT_HEADING_RE.match(nxt)
    ):
        level = 1 if nxt.strip().startswith("=") else 2
        return level, stripped, index + 1
    return None


def extract_statements(inst: InstructionFile) -> list[Statement]:
    """Split a file into rule-ish units, tracking the heading each lives under."""
    statements: list[Statement] = []
    heading_path: list[str] = []
    in_fence = False
    lines = inst.lines
    n = len(lines)
    fm_end = inst.frontmatter.get("end_line", 0) if inst.frontmatter_present else 0
    i = 0

    while i < n:
        line = lines[i]

        if i + 1 <= fm_end:
            i += 1
            continue  # YAML frontmatter is configuration, not a rule
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            i += 1
            continue
        if in_fence:
            i += 1
            continue

        heading = heading_at(lines, i)
        if heading:
            level, title, _lineno = heading
            heading_path = heading_path[: level - 1]
            while len(heading_path) < level - 1:
                heading_path.append("")
            heading_path.append(title)
            i += 2 if _HEADING_RE.match(line) is None else 1
            continue

        stripped = line.strip()
        if not stripped or stripped.startswith("<!--"):
            i += 1
            continue

        bullet = _BULLET_RE.match(line)
        if bullet:
            text = bullet.group(2).strip()
            j = i + 1
            while j < n:
                nxt = lines[j]
                if not nxt.strip() or _BULLET_RE.match(nxt) \
                        or _FENCE_RE.match(nxt) or heading_at(lines, j):
                    break
                if nxt[:1] in (" ", "\t"):
                    text += " " + nxt.strip()
                    j += 1
                    continue
                break
            if _is_rule_like(text):
                statements.append(_make_statement(len(statements), i + 1, text,
                                                  tuple(heading_path)))
            i = j
            continue

        if line[:1] in (" ", "\t") and len(line) - len(line.lstrip()) >= 4:
            if _is_rule_like(stripped):
                for sentence in split_sentences(stripped):
                    if _is_rule_like(sentence):
                        statements.append(_make_statement(
                            len(statements), i + 1, sentence, tuple(heading_path)))
            i += 1
            continue

        para = [stripped]
        j = i + 1
        while j < n:
            nxt = lines[j]
            if not nxt.strip() or _BULLET_RE.match(nxt) or _FENCE_RE.match(nxt) \
                    or heading_at(lines, j) or nxt[:1] in (" ", "\t"):
                break
            para.append(nxt.strip())
            j += 1
        for sentence in split_sentences(" ".join(para)):
            if _is_rule_like(sentence):
                statements.append(_make_statement(len(statements), i + 1, sentence,
                                                  tuple(heading_path)))
        i = j

    return statements


def _make_statement(index: int, line: int, text: str,
                    heading_path: tuple[str, ...]) -> Statement:
    return Statement(
        index=index,
        line=line,
        text=text,
        norm=normalise(text),
        heading_path=heading_path,
        tokens=tokenize(text),
    )


# --------------------------------------------------------------------------
# Path references
# --------------------------------------------------------------------------

_GLOB_CHARS = set("*?[")
_BAD_PATH_CHARS = set("(){}<>|=\"'`;!$&%#@\\^~,*")
#: Punctuation to drop from the end of an extracted token, and from its start.
#: These must be directional (str.strip() removes from BOTH ends, which would
#: eat the leading dot of `.cursor/rules/x.mdc`).
_TRAILING_STRIP = " \t.,;:)]}'\"`"
_LEADING_STRIP = " \t(;:'\"`"


def _clean_token(raw: str) -> str:
    """Drop surrounding punctuation from an extracted token, keeping dots."""
    return raw.lstrip(_LEADING_STRIP).rstrip(_TRAILING_STRIP)


def looks_like_path(token: str) -> bool:
    if not token or len(token) > 160:
        return False
    if token.startswith(("http://", "https://", "ftp://", "mailto:", "/", "~", "-")):
        return False
    if token.startswith(("@", "$", "%", "&", "!", "|", ">", "*", "?", "[")):
        return False
    if token != token.strip():
        return False
    for ch in token:
        if ch in _BAD_PATH_CHARS and ch not in _GLOB_CHARS:
            return False
    if re.search(r"={1,}>?", token):
        return False
    if re.search(r"^[a-z][a-z0-9+.-]*://", token):
        return False
    name = token.rstrip("/")
    if not name or name in PATH_TOKEN_DENYLIST:
        return False
    if re.fullmatch(r"\.[A-Za-z0-9]{1,6}", name):
        # An extension on its own, split off a glob such as `**/*.tsx`.
        return False
    if "/" in name:
        return True
    if name.startswith("."):
        return bool(re.match(r"^\.[A-Za-z][A-Za-z0-9_.-]*$", name))
    stem, dot, ext = name.rpartition(".")
    if dot and ext.lower() in PATH_EXTENSIONS and stem:
        return stem.lower() not in DOT_WORD_DENYLIST
    return False


def extract_path_refs(inst: InstructionFile) -> list[tuple[str, int, str]]:
    """Return (reference, line, where it was found) triples.

    Frontmatter is skipped: a ``globs:`` value is a scope pattern, not a claim
    that a particular file exists.
    """
    refs: list[tuple[str, int, str]] = []
    in_fence = False
    fm_end = inst.frontmatter.get("end_line", 0) if inst.frontmatter_present else 0
    for idx, line in enumerate(inst.lines):
        lineno = idx + 1
        if lineno <= fm_end:
            continue
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        stripped = line.strip()
        if stripped.startswith("<!--"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent >= 4 and not _BULLET_RE.match(line):
            continue  # an indented sample block, not a rule

        for match in _CODE_SPAN_RE.finditer(line):
            inner = match.group(1).strip()
            for piece in re.split(r"\s*(?:->|=>|\|\||&&|;)\s*", inner):
                for word in piece.split():
                    candidate = _clean_token(word)
                    if looks_like_path(candidate):
                        refs.append((candidate, lineno, "code span"))

        for match in re.finditer(r"\]\(([^)\s]+)\)", line):
            target = match.group(1).strip()
            if looks_like_path(target):
                refs.append((target, lineno, "markdown link"))

        scrubbed = _CODE_SPAN_RE.sub(" ", line)
        for match in re.finditer(r"\S+", scrubbed):
            token = _clean_token(match.group(0))
            if looks_like_path(token):
                refs.append((token, lineno, "bare reference"))
    return refs


def _brace_expand(token: str) -> list[str]:
    """Expand one brace group: '**/*.{ts,tsx}' -> two patterns."""
    m = re.search(r"\{([^{}]*)\}", token)
    if not m:
        return [token]
    out: list[str] = []
    for option in m.group(1).split(","):
        out.extend(_brace_expand(token[:m.start()] + option.strip() + token[m.end():]))
    return out


def resolve_reference(bases: Sequence[Path], token: str) -> Path | None:
    """Find *token* under one of *bases*; return the base that contains it."""
    token = token.strip().strip("`")
    if not token:
        return bases[0] if bases else None
    if token.startswith("./"):
        token = token[2:]
    if token.startswith("/"):
        return bases[0] if bases else None  # absolute paths are not ours to check
    for base in bases:
        for candidate in _brace_expand(token):
            has_glob = any(ch in candidate for ch in _GLOB_CHARS)
            if not has_glob:
                if (base / candidate).exists():
                    return base
                continue
            pattern = candidate
            if pattern.endswith("/"):
                pattern = pattern + "**"
            try:
                for _ in base.glob(pattern):
                    return base
            except (ValueError, NotImplementedError, OSError):
                continue
    return None


def check_path_references(inst: InstructionFile, bases: Sequence[Path]) -> list[Finding]:
    """Report references that resolve under none of *bases*.

    *bases* is ordered: the file's own directory first (a rule file may point at
    a sibling), then the repository root (where most paths in an AGENTS.md are
    written from). A reference that exists under any base is fine.
    """
    out: list[Finding] = []
    seen: set[tuple[str, int]] = set()
    for token, lineno, site in extract_path_refs(inst):
        if (token, lineno) in seen:
            continue
        seen.add((token, lineno))
        if resolve_reference(bases, token) is not None:
            continue
        out.append(finding(
            "AGL001", inst.rel_path, lineno,
            f"rule references `{token}` (found as a {site}), but that path exists "
            f"neither relative to {bases[0]} nor relative to the repository root "
            f"{bases[-1]}; a rule pointing at a deleted file is worse than no rule, "
            "because the agent has to guess what you meant",
            f"delete the rule, or correct the path to something that exists under "
            f"{bases[-1]}",
        ))
    return out


# --------------------------------------------------------------------------
# Duplicates
# --------------------------------------------------------------------------


def check_duplicates(files: Sequence[InstructionFile]) -> list[Finding]:
    out: list[Finding] = []
    by_signature: dict[tuple[str, str], list[tuple[InstructionFile, Statement]]] = \
        defaultdict(list)
    for inst in files:
        for st in inst.statements:
            if len(st.norm) < 12:
                continue
            by_signature[(inst.rel_path, st.norm)].append((inst, st))

    for hits in by_signature.values():
        if len(hits) < 2:
            continue
        first_inst, first = hits[0]
        for inst, st in hits[1:]:
            same_file = inst.rel_path == first_inst.rel_path
            where = (f"line {first.line}" if same_file
                     else f"{first_inst.rel_path}:{first.line}")
            out.append(finding(
                "AGL003", inst.rel_path, st.line,
                f"this rule repeats {where} verbatim (after normalising case, "
                f"punctuation and inline code): \"{shorten(st.text)}\". Duplicated "
                "rules push the file's real content out of the model's attention",
                f"keep one copy at {where} and delete this one",
                (first_inst.rel_path,),
            ))
    return out


def check_near_duplicates(files: Sequence[InstructionFile]) -> list[Finding]:
    out: list[Finding] = []
    pool: list[tuple[InstructionFile, Statement]] = []
    for inst in files:
        for st in inst.statements:
            if len(st.tokens) >= NEAR_DUPLICATE_MIN_TOKENS and len(st.norm) >= 12:
                pool.append((inst, st))

    reported: set[tuple[str, str]] = set()
    for i in range(len(pool)):
        inst_a, a = pool[i]
        for j in range(i + 1, len(pool)):
            inst_b, b = pool[j]
            pair_key = tuple(sorted((f"{inst_a.rel_path}:{a.line}",
                                     f"{inst_b.rel_path}:{b.line}")))
            if pair_key in reported or a.norm == b.norm:
                continue
            sim = token_similarity(a.tokens, b.tokens)
            if sim < NEAR_DUPLICATE_SIMILARITY:
                continue
            reported.add(pair_key)
            out.append(finding(
                "AGL004", inst_b.rel_path, b.line,
                f"this rule is {sim:.0%} similar to {inst_a.rel_path}:{a.line} "
                f"(\"{shorten(a.text)}\") -- near-duplicates of one rule dilute the "
                "file without adding instruction",
                "merge the two rules and keep the sharper wording",
                (inst_a.rel_path,),
            ))
    return out


# --------------------------------------------------------------------------
# Contradictions
# --------------------------------------------------------------------------


def check_contradictions(files: Sequence[InstructionFile]) -> list[Finding]:
    """Opposition-pair contradictions, only within one shared scope."""
    out: list[Finding] = []
    entries: list[tuple[InstructionFile, Statement]] = []
    for inst in files:
        for st in inst.statements:
            entries.append((inst, st))

    for i in range(len(entries)):
        inst_a, a = entries[i]
        for j in range(i + 1, len(entries)):
            inst_b, b = entries[j]
            if (inst_a.rel_path, a.heading_path) != (inst_b.rel_path, b.heading_path):
                continue
            if token_similarity(a.tokens, b.tokens) < SAME_CLAIM_SIMILARITY:
                continue
            pair = _opposition_pair(a, b)
            if pair is None:
                continue
            _pid, _la, _lb, label = pair
            out.append(finding(
                "AGL002", inst_b.rel_path, b.line,
                f"possible contradiction with {inst_a.rel_path}:{a.line} ({label}): "
                f"\"{shorten(a.text)}\" vs \"{shorten(b.text)}\". The tool matched an "
                "explicit opposition pair in two sentences about the same subject; "
                "it is not understanding the language",
                "decide which rule wins and delete or qualify the other",
                (inst_a.rel_path,),
            ))
    return out


def _opposition_pair(a: Statement, b: Statement) -> tuple | None:
    """Return (pair_id, line, line, label) when a and b take opposite sides."""
    for pid, left_re, right_re, label in _OPPOSITION_RES:
        a_left, a_right = bool(left_re.search(a.text)), bool(right_re.search(a.text))
        b_left, b_right = bool(left_re.search(b.text)), bool(right_re.search(b.text))
        if a_left and b_right and not (a_right and b_left):
            return pid, a.line, b.line, label
        if a_right and b_left and not (a_left and b_right):
            return pid, b.line, a.line, label
    return None


# --------------------------------------------------------------------------
# Size budget
# --------------------------------------------------------------------------


def check_size_budget(inst: InstructionFile, max_lines: int,
                      max_words: int) -> list[Finding]:
    out: list[Finding] = []
    lines = inst.line_count
    words = inst.word_count
    if lines > max_lines:
        out.append(finding(
            "AGL005", inst.rel_path, 0,
            f"file is {lines} lines, past the {max_lines}-line budget. Long "
            "instruction files are read less reliably: the rules at the bottom "
            "compete with everything above them for attention, and rules that "
            "stopped being relevant are rarely deleted. This is a budget, not a "
            "hard limit -- raise it with --max-lines if the file earns its length",
            f"move scoped detail into .cursor/rules/*.mdc files, so the "
            f"always-loaded part stays under {max_lines} lines",
        ))
    if words > max_words:
        out.append(finding(
            "AGL005", inst.rel_path, 0,
            f"file is {words} words, past the {max_words}-word budget. Word count "
            "matters as much as line count when a file is mostly prose: the whole "
            "file competes for attention, so every extra sentence dilutes the rules "
            "you actually care about. This is a budget, not a hard limit",
            f"trim restated context and move background out of the file; target "
            f"under {max_words} words",
        ))
    if lines > max_lines and words > max_words:
        out.append(finding(
            "AGL006", inst.rel_path, 0,
            f"neither budget is met ({lines} lines / {words} words). The two "
            "overruns reinforce each other: this reads as an accumulated log rather "
            "than a rule set",
            "start from the rules you would enforce in review, delete the rest, and "
            "put scoped detail in a .mdc file with a glob",
        ))
    return out


# --------------------------------------------------------------------------
# Cursor frontmatter
# --------------------------------------------------------------------------


def check_frontmatter(inst: InstructionFile) -> list[Finding]:
    out: list[Finding] = []
    if inst.kind != "mdc":
        return out
    fm = inst.frontmatter
    if not fm["present"]:
        out.append(finding(
            "AGL007", inst.rel_path, 1,
            "Cursor .mdc rule has no frontmatter block. Without one there is nothing "
            "telling Cursor when the rule applies, so the file is either applied to "
            "everything or never selected",
            "start the file with a '---' block containing 'description', 'globs' and "
            "'alwaysApply'",
        ))
        return out
    if not fm["ok"]:
        out.append(finding(
            "AGL008", inst.rel_path, _first_suspect_line(inst, fm),
            "Cursor .mdc frontmatter is malformed: " + fm["error"] +
            ". Cursor reads this block as YAML, so a block that does not parse can "
            "cost you every key in it",
            "make every line a 'key: value' pair, with balanced quotes and closed "
            "list brackets",
        ))
        return out

    fields = fm["fields"]
    globs = _coerce_globs(fields.get("globs"))
    always = _coerce_bool(fields.get("alwaysApply"))
    described = bool(str(fields.get("description") or "").strip())
    anchor = min(fm["line_of"].values()) if fm["line_of"] else 1

    if not globs and always is not True:
        out.append(finding(
            "AGL009", inst.rel_path, anchor,
            "Cursor frontmatter scopes this rule to nothing: there is no 'globs' and "
            "'alwaysApply' is not true, so the rule never reaches the model. "
            "(Convention, not a documented Cursor requirement: Cursor documents the "
            "three keys but not what an empty scope means.)",
            "either add a 'globs' pattern such as '**/*.ts', or set "
            "'alwaysApply: true' if the rule really does apply everywhere",
        ))
    if not described:
        out.append(finding(
            "AGL009", inst.rel_path, anchor,
            "Cursor frontmatter has no 'description'. Cursor surfaces a rule's "
            "description when an agent decides whether to pull the rule in, so a "
            "rule without one is harder to select. (Convention: 'description' is a "
            "documented key, but Cursor does not state that it is required.)",
            "add a one-line 'description' saying what the file governs",
        ))
    for key in fm["unknown"]:
        out.append(finding(
            "AGL010", inst.rel_path, fm["line_of"].get(key, 1),
            f"Cursor frontmatter key {key!r} is not one of the three keys this tool "
            "validates ('description', 'globs', 'alwaysApply'). It may well be valid "
            "-- the linter only knows those three and says so rather than guessing "
            "(convention, not documentation)",
            f"keep it if your Cursor version supports it; otherwise remove {key!r}",
        ))
    return out


def _first_suspect_line(inst: InstructionFile, fm: dict) -> int:
    m = re.search(r"line (\d+)", fm.get("error", ""))
    if m:
        return int(m.group(1))
    return fm.get("end_line") or 1


def check_always_apply(rule_files: Sequence[InstructionFile], root: Path) -> list[Finding]:
    """Every .mdc file in one rules directory is alwaysApply: true."""
    out: list[Finding] = []
    groups: dict[Path, list[InstructionFile]] = defaultdict(list)
    for inst in rule_files:
        if inst.kind == "mdc":
            groups[inst.abs_path.parent].append(inst)
    for directory, members in sorted(groups.items(), key=lambda kv: str(kv[0])):
        if len(members) < 2:
            continue
        # A file whose frontmatter does not parse has no usable alwaysApply, so
        # it is reported by the frontmatter rules instead. It does not, by
        # itself, make the directory scoped.
        parsed = [m for m in members if m.frontmatter_ok and m.frontmatter_present]
        if not parsed or not all(m.always_apply is True for m in parsed):
            continue
        rel_dir = os.path.relpath(directory, root).replace(os.sep, "/")
        out.append(finding(
            "AGL011", rel_dir + "/", 0,
            f"all {len(members)} rule files in {rel_dir}/ have "
            "'alwaysApply: true', so nothing in this directory is scoped and the "
            "whole set is in context for every request. Scoping is the reason to "
            "split rules into separate files",
            "give each file a 'globs' pattern and set 'alwaysApply: false' for all "
            "but the rules that genuinely apply everywhere",
            tuple(m.rel_path for m in members),
        ))
    return out


# --------------------------------------------------------------------------
# Scope conflicts
# --------------------------------------------------------------------------


_EXT_LANGUAGE = {
    "ts": "typescript", "tsx": "typescript", "mts": "typescript", "cts": "typescript",
    "js": "javascript", "jsx": "javascript", "mjs": "javascript", "cjs": "javascript",
    "py": "python", "pyi": "python",
    "go": "go", "rs": "rust", "rb": "ruby", "java": "java",
    "cs": "csharp", "php": "php", "kt": "kotlin", "swift": "swift",
    "c": "c", "h": "c", "cpp": "cpp", "hpp": "cpp", "cc": "cpp",
    "sql": "sql", "sh": "shell", "bash": "shell", "zsh": "shell",
    "css": "css", "scss": "css", "html": "html", "vue": "vue", "svelte": "svelte",
    "tf": "terraform", "proto": "protobuf", "graphql": "graphql", "gql": "graphql",
    "md": "markdown", "mdx": "markdown", "json": "json", "yaml": "yaml", "yml": "yaml",
}


def glob_languages(pattern: str) -> frozenset[str]:
    """Languages a glob targets. An empty set means 'everything'."""
    pat = pattern.strip().strip("\"'")
    if pat in ("", "*", "**", "**/*", "*.*", "**/*.*"):
        return frozenset()
    languages: set[str] = set()
    # Brace groups first: `**/*.{ts,tsx}` must yield typescript, not nothing.
    for expanded in _brace_expand(pat):
        for match in re.finditer(r"\*\.([A-Za-z0-9]+)", expanded):
            languages.add(_EXT_LANGUAGE.get(match.group(1).lower(),
                                            match.group(1).lower()))
        for match in re.finditer(r"\.([A-Za-z0-9]{1,6})\b", expanded):
            languages.add(_EXT_LANGUAGE.get(match.group(1).lower(),
                                            match.group(1).lower()))
    return frozenset(languages)


SCOPABLE_KINDS = frozenset({"mdc", "cursorrules", "copilot", "agents", "claude",
                            "gemini", "conventions"})


def check_scope_conflicts(files: Sequence[InstructionFile]) -> list[Finding]:
    out: list[Finding] = []
    scoped = [f for f in files if f.globs and f.kind in SCOPABLE_KINDS]
    for i in range(len(scoped)):
        a = scoped[i]
        for j in range(i + 1, len(scoped)):
            b = scoped[j]
            shared = _shared_language(a, b)
            if not shared:
                continue
            for sa, sb in _divergent_pairs(a, b):
                out.append(finding(
                    "AGL012", b.rel_path, sb.line,
                    f"conflicting scope: {b.rel_path} and {a.rel_path} both target "
                    f"{', '.join(sorted(shared))}, but they assert different rules "
                    f"about nearly the same subject -- "
                    f"\"{shorten(sa.text)}\" ({a.rel_path}:{sa.line}) vs "
                    f"\"{shorten(sb.text)}\" ({b.rel_path}:{sb.line}). When two rule "
                    "files can both apply, whichever the agent reads last wins",
                    "make one glob exclude the other, or move the shared rule into a "
                    "single file",
                    (a.rel_path,),
                ))
    return out


def _shared_language(a: InstructionFile, b: InstructionFile) -> frozenset[str]:
    langs_a: set[str] = set()
    for g in a.globs:
        langs_a |= glob_languages(g)
    langs_b: set[str] = set()
    for g in b.globs:
        langs_b |= glob_languages(g)
    wants_all_a = any(not glob_languages(g) for g in a.globs)
    wants_all_b = any(not glob_languages(g) for g in b.globs)
    if wants_all_a and wants_all_b:
        return frozenset({"all files"})
    if wants_all_a:
        return frozenset(langs_b)
    if wants_all_b:
        return frozenset(langs_a)
    return frozenset(langs_a & langs_b)


def _divergent_pairs(a: InstructionFile,
                     b: InstructionFile) -> list[tuple[Statement, Statement]]:
    pairs: list[tuple[Statement, Statement]] = []
    for sa in a.statements:
        for sb in b.statements:
            if token_similarity(sa.tokens, sb.tokens) < SAME_CLAIM_SIMILARITY:
                continue
            if _opposition_pair(sa, sb) is not None:
                pairs.append((sa, sb))
    return pairs


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------


def check_structure(inst: InstructionFile) -> list[Finding]:
    out: list[Finding] = []
    lines = inst.lines
    in_fence = False
    headings: list[tuple[int, str, int]] = []
    fm_end = inst.frontmatter.get("end_line", 0) if inst.frontmatter_present else 0

    idx = 0
    while idx < len(lines):
        line = lines[idx]
        lineno = idx + 1
        if lineno <= fm_end:
            idx += 1
            continue
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            idx += 1
            continue
        if in_fence:
            idx += 1
            continue
        heading = heading_at(lines, idx)
        if heading:
            headings.append(heading)
            idx += 2 if _HEADING_RE.match(line) is None else 1
            continue
        idx += 1

    if headings:
        for idx, line in enumerate(lines):
            lineno = idx + 1
            if lineno >= headings[0][2] or lineno <= fm_end:
                continue
            stripped = line.strip()
            if not stripped or stripped.startswith("<!--"):
                continue
            if _BULLET_RE.match(line) or (len(line) - len(line.lstrip()) >= 2
                                          and _is_rule_like(stripped)):
                out.append(finding(
                    "AGL013", inst.rel_path, lineno,
                    f"rule appears before the first heading "
                    f"(\"{shorten(stripped)}\"). Unstructured preamble is easy to "
                    "skip and hard to reference (convention: headings are how a "
                    "reader -- human or model -- finds the rule that applies now)",
                    "move it under a heading that names the topic",
                ))
                break

    previous = 0
    for level, title, lineno in headings:
        if previous and level > previous + 1:
            out.append(finding(
                "AGL014", inst.rel_path, lineno,
                f"heading level jumps from h{previous} to h{level} "
                f"(\"{shorten(title)}\"). The jump hides the fact that this section "
                "belongs under the previous heading (convention, not a markdown "
                "requirement)",
                f"re-level this heading to h{previous + 1}, or add the missing "
                f"h{previous + 1} heading",
            ))
        previous = level

    # AGL015 -- an empty section: from this heading up to the next heading at the
    # same level or shallower there is no text at all, only blank lines and
    # comments. A parent heading whose content lives in its subsections has text
    # underneath it (the subsection headings), so it is not empty.
    # NOTE: the heading level is passed as an argument rather than closed over,
    # because `level` is also a loop variable above and a closure cell would be
    # shared between the two loops.
    for _position, (heading_level, title, lineno) in enumerate(headings):
        if not section_has_text(lines, lineno, heading_level):
            out.append(finding(
                "AGL015", inst.rel_path, lineno,
                f"empty section: \"{shorten(title)}\" has no content. An empty "
                "heading promises rules that are not there, and a model may read the "
                "heading itself as the rule",
                "either fill the section or delete the heading",
            ))
    return out


def section_has_text(lines: Sequence[str], start: int, heading_level: int) -> bool:
    """True when anything follows the heading before its section ends.

    *start* is the 1-based line number of the heading; the section ends at the
    next heading of the same level or shallower, or at the end of the file.
    Blank lines and comments do not count. Anything else does -- including a
    deeper heading, because a heading whose content lives in its subsections
    has something to point at and is not the "empty section" this rule is
    about.

    This is a module-level function on purpose: written as a closure over the
    loop variables it was wrong, because the heading level was shared with an
    earlier loop in the same function.
    """
    index = start  # lines is 0-based, so this is the line after the heading
    while index < len(lines):
        found = heading_at(lines, index)
        if found and found[0] <= heading_level:
            return False  # the section ended without any text
        text = lines[index].strip()
        if text and not text.startswith("<!--"):
            # Anything here is content: prose, a bullet, an image -- or a deeper
            # heading, whose own section then carries the content.
            return True
        index += 1
    return False


# --------------------------------------------------------------------------
# Rule quality
# --------------------------------------------------------------------------


def check_rule_quality(inst: InstructionFile) -> list[Finding]:
    out: list[Finding] = []
    for st in inst.statements:
        text = st.text
        if _QUESTION_RE.search(text) and re.search(
                r"\b(?:do you|did you|should (?:you|we|i)\b|can you|are you|have you|"
                r"what|which|why|how|where|when)\b", text, re.IGNORECASE):
            out.append(finding(
                "AGL016", inst.rel_path, st.line,
                f"rule is written as a question rather than an instruction: "
                f"\"{shorten(text)}\". A question names a topic; the agent still has "
                "to guess the answer",
                "rewrite it as an instruction (\"Do X when Y\")",
            ))
        for pattern, why in _HUMAN_RES:
            if pattern.search(text):
                out.append(finding(
                    "AGL017", inst.rel_path, st.line,
                    f"rule is addressed to a human, not the agent: "
                    f"\"{shorten(text)}\" -- {why}",
                    "delete it, or restate it as something the agent itself can do",
                ))
                break
        for pattern, why in _UNACTIONABLE_RES:
            if pattern.search(text):
                out.append(finding(
                    "AGL018", inst.rel_path, st.line,
                    f"rule is not verifiable: \"{shorten(text)}\" -- {why}. It cannot "
                    "be checked against a diff, so it will not change what the agent "
                    "writes",
                    "replace it with an observable instruction: a named command, a "
                    "file, a threshold, or a concrete example",
                ))
                break
    return out


# --------------------------------------------------------------------------
# CLAUDE.md / AGENTS.md pair
# --------------------------------------------------------------------------


def check_agent_doc_pair(files: Sequence[InstructionFile]) -> list[Finding]:
    out: list[Finding] = []
    by_dir: dict[str, dict[str, InstructionFile]] = defaultdict(dict)
    for inst in files:
        if inst.kind in ("claude", "agents"):
            by_dir[os.path.dirname(inst.rel_path).replace(os.sep, "/")][inst.kind] = inst

    for directory, present in sorted(by_dir.items()):
        where = f"{directory}/" if directory else "the repository root"
        if "claude" in present and "agents" not in present:
            inst = present["claude"]
            out.append(finding(
                "AGL020", inst.rel_path, 0,
                f"CLAUDE.md exists in {where} but there is no AGENTS.md next to it. "
                "Agents that read AGENTS.md -- and the tooling built on it -- will "
                "find no instructions in this directory",
                "add an AGENTS.md with the same rules, or make one file a pointer to "
                "the other if your tooling supports includes",
            ))
        elif "agents" in present and "claude" not in present:
            inst = present["agents"]
            out.append(finding(
                "AGL020", inst.rel_path, 0,
                f"AGENTS.md exists in {where} but there is no CLAUDE.md next to it. "
                "Claude Code reads CLAUDE.md, so it starts with no project "
                "instructions here",
                "add a CLAUDE.md with the same rules, or make one file a pointer to "
                "the other if your tooling supports includes",
            ))
        else:
            claude, agents = present["claude"], present["agents"]
            if claude.text.strip() == agents.text.strip():
                continue
            sim = token_similarity(tokenize(claude.text), tokenize(agents.text))
            if sim >= DIVERGENT_PAIR_SIMILARITY:
                continue
            out.append(finding(
                "AGL019", claude.rel_path, 0,
                f"CLAUDE.md and AGENTS.md in {where} have diverged (token overlap "
                f"{sim:.0%}). Two agents reading this repository will follow "
                "different rules, and reviewers will not know which file is "
                "authoritative",
                "make one file the source of truth and reduce the other to a "
                "pointer, or sync them",
                (agents.rel_path,),
            ))
    return out


# --------------------------------------------------------------------------
# Analysis entry point
# --------------------------------------------------------------------------


@dataclass
class Analysis:
    root: Path
    files: list[InstructionFile]
    findings: list[Finding]
    notes: list[str]


def analyse(target: Path, max_lines: int = DEFAULT_MAX_LINES,
            max_words: int = DEFAULT_MAX_WORDS) -> Analysis:
    target = target.resolve()
    if target.is_dir():
        root = target
        paths = discover(root)
    else:
        root = find_repo_root(target.parent)
        paths = [target]

    files: list[InstructionFile] = []
    notes: list[str] = []
    for path in paths:
        try:
            files.append(load_instruction_file(path, root))
        except OSError as exc:
            notes.append(f"could not read {path}: {exc}")
    files.sort(key=lambda f: f.rel_path)

    findings: list[Finding] = []
    for inst in files:
        # A rule file may write paths relative to its own directory, and a
        # repository may nest another project (examples/good/ inside a checkout):
        # that nested directory is the base its rules were written for.
        bases = [inst.abs_path.parent]
        project_root = find_repo_root(inst.abs_path.parent, stop=root)
        if project_root != bases[-1]:
            bases.append(project_root)
        if bases[-1].resolve() != root.resolve():
            bases.append(root)
        findings.extend(check_path_references(inst, bases))
        findings.extend(check_size_budget(inst, max_lines, max_words))
        findings.extend(check_frontmatter(inst))
        findings.extend(check_structure(inst))
        findings.extend(check_rule_quality(inst))

    findings.extend(check_duplicates(files))
    findings.extend(check_near_duplicates(files))
    findings.extend(check_contradictions(files))
    findings.extend(check_always_apply([f for f in files if f.kind == "mdc"], root))
    findings.extend(check_scope_conflicts(files))
    findings.extend(check_agent_doc_pair(files))

    findings = _dedupe(findings)
    findings.sort(key=Finding.sort_key)
    return Analysis(root=root, files=files, findings=findings, notes=notes)


def _dedupe(findings: Iterable[Finding]) -> list[Finding]:
    seen: set[tuple] = set()
    out: list[Finding] = []
    for f in findings:
        key = (f.code, f.path, f.line, f.message)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def summary_counts(findings: Sequence[Finding]) -> dict[str, int]:
    counts = {sev: 0 for sev in SEVERITIES}
    for f in findings:
        counts[f.severity] += 1
    return counts


def format_human(analysis: Analysis, threshold: str, quiet: bool) -> str:
    shown = [f for f in analysis.findings if severity_at_least(f.severity, threshold)]
    counts = summary_counts(shown)
    lines: list[str] = []
    if not quiet:
        lines.append(f"agent-config-lint {__version__}")
        lines.append(f"root: {analysis.root}")
        for inst in analysis.files:
            label = KIND_LABEL.get(inst.kind, inst.kind)
            scope = ""
            if inst.globs:
                scope = f", globs: {', '.join(inst.globs)}"
            elif inst.always_apply:
                scope = ", alwaysApply: true"
            lines.append(
                f"  {inst.rel_path}  [{label}]  {inst.line_count} lines, "
                f"{inst.word_count} words, {len(inst.statements)} rules{scope}"
            )
        if not analysis.files:
            lines.append("  (no instruction files found)")
        lines.append("")

    if not shown:
        lines.append(f"No findings at or above severity '{threshold}'.")
        if not quiet and analysis.findings:
            lines.append(f"({len(analysis.findings) - len(shown)} finding(s) below "
                         "the threshold are hidden.)")
        return "\n".join(lines)

    for f in shown:
        location = f.path or "(repository)"
        if f.line:
            location = f"{location}:{f.line}"
        lines.append(f"{f.severity.upper():6} {f.code}  {location}")
        lines.append(f"       {f.message}")
        lines.append(f"       fix: {f.fix}")
        lines.append("")
    lines.append(
        f"{len(shown)} finding(s) at or above '{threshold}': "
        + ", ".join(f"{counts[sev]} {sev}" for sev in SEVERITIES if counts[sev])
    )
    return "\n".join(lines)


def format_json(analysis: Analysis, threshold: str) -> str:
    shown = [f for f in analysis.findings if severity_at_least(f.severity, threshold)]
    payload = {
        "tool": "agent-config-lint",
        "version": __version__,
        "root": str(analysis.root),
        "threshold": threshold,
        "files": [
            {
                "path": inst.rel_path,
                "kind": inst.kind,
                "lines": inst.line_count,
                "words": inst.word_count,
                "rules": len(inst.statements),
                "globs": list(inst.globs),
                "alwaysApply": inst.always_apply,
            }
            for inst in analysis.files
        ],
        "counts": summary_counts(shown),
        "findings": [f.as_dict() for f in shown],
        "notes": list(analysis.notes),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)

