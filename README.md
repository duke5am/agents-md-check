# agents-md-check

[![PyPI](https://img.shields.io/pypi/v/agents-md-check)](https://pypi.org/project/agents-md-check/)

A static linter for the instruction files AI coding agents read: `AGENTS.md`,
`CLAUDE.md`, `.cursor/rules/*.mdc`, `.cursorrules`,
`.github/copilot-instructions.md` and relatives.

It reports **structural** problems — the ones that make an agent ignore or
misread the file:

* rules that point at files that do not exist any more,
* rules that contradict each other,
* duplicated and near-duplicated rules,
* a file that has outgrown the attention it gets,
* Cursor `.mdc` frontmatter that is missing or malformed,
* rule files whose scopes overlap and disagree,
* a rules directory where nothing is scoped at all,
* headings that hide rules, empty sections, rules that are questions,
* rules addressed to a human instead of the agent,
* a `CLAUDE.md` / `AGENTS.md` pair where one is missing or the two have drifted
  apart.

It deliberately does not have an opinion about wording. It never says a rule is
*good* or *bad*; it says where a rule is broken, duplicated, unreachable or
unverifiable.

Written in the Python standard library. No dependencies, no network access.

---

## Install

```bash
pip install agents-md-check          # from PyPI, Python 3.9+
agents-md-check <path>
```

Or run it straight from a clone, no install. Python 3.10+ (developed and tested
on 3.13.5).

```console
$ python3 agent_config_lint.py <path> [options]
```

`<path>` is either a repository root — the known filenames are discovered, up to
six directories deep — or a single instruction file.

| Option | Meaning |
| --- | --- |
| `--json` | machine-readable output (stable keys: `code`, `severity`, `path`, `line`, `message`, `fix`, `basis`) |
| `--severity high\|medium\|low` | reporting floor. Default `low`. Filters display **and** the exit code; it never changes the analysis |
| `--max-lines N` | line budget. Default `500` |
| `--max-words N` | word budget. Default `6000` |
| `--quiet` | omit the header and the file inventory |
| `--list-rules` | print the rule catalogue and the opposition pairs the tool knows, then exit `0` |
| `--version` | print the version and exit `0` |

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | no findings at or above the selected severity threshold — clean |
| `1` | at least one finding at or above the threshold |
| `2` | usage error: bad arguments, path does not exist, no instruction file found under the given directory, unreadable file, or a budget below 1 |

A typical CI line, which fails the build only on high findings:

```console
$ python3 agent_config_lint.py . --severity high
```

---

## The rules

`--list-rules` prints this table. `basis` says how much to trust a rule:

* **documented** — a requirement of the file format itself.
* **convention** — a convention this tool enforces; the file's own
  documentation does not require it, and the message says so.
* **heuristic** — pattern matching over text, not language understanding.

| Code | Severity | Basis | What it reports |
| --- | --- | --- | --- |
| AGL001 | high | documented | A rule references a path that does not exist (glob-aware; checked against the file's own directory and the repository root) |
| AGL002 | high | heuristic | Two rules contradict each other on the same subject, from the declared opposition pairs |
| AGL003 | medium | heuristic | Duplicate rule (case-, punctuation- and whitespace-insensitive comparison) |
| AGL004 | medium | heuristic | Near-duplicate rule (≥ 0.85 token similarity) |
| AGL005 | medium | heuristic | File exceeds the line or word budget |
| AGL006 | low | heuristic | Neither budget is met — the file reads as an accumulated log |
| AGL007 | high | documented | Cursor `.mdc` file has no frontmatter block |
| AGL008 | high | documented | Cursor `.mdc` frontmatter is malformed (unclosed block, bad line, unbalanced quotes, unclosed list, unbalanced braces) |
| AGL009 | medium | convention | Frontmatter has no `description`, or scopes the rule to nothing |
| AGL010 | low | convention | Frontmatter key outside `description` / `globs` / `alwaysApply` |
| AGL011 | medium | heuristic | Every `.mdc` in one rules directory is `alwaysApply: true` |
| AGL012 | medium | heuristic | Two rule files with overlapping globs assert opposing rules for the same language |
| AGL013 | low | convention | Rule appears before the first heading |
| AGL014 | low | convention | Heading level jumps by more than one |
| AGL015 | low | convention | Empty section: a heading with no text under it |
| AGL016 | low | heuristic | Rule is written as a question instead of an instruction |
| AGL017 | low | heuristic | Rule is addressed to a human ("ask your teammate", "see the wiki") |
| AGL018 | low | heuristic | Rule is not actionable / not verifiable ("write clean code") |
| AGL019 | high | heuristic | `CLAUDE.md` and `AGENTS.md` in the same directory have diverged |
| AGL020 | medium | heuristic | Only one of `CLAUDE.md` / `AGENTS.md` exists in a directory |

### Cursor frontmatter: exactly three keys are validated

The three keys this tool validates are the three Cursor documents:
`description`, `globs`, `alwaysApply`. Any other key is reported at `low`
severity as **not one of the three keys this tool validates** — the message
says explicitly that the key may well be valid, because the tool only knows
those three and will not invent a requirement. An empty `globs` with
`alwaysApply` not true, and a missing `description`, are reported as
**conventions**, not as documented requirements.

### The opposition pairs

AGL002 can only fire on pairs this tool declares. They are printed verbatim by
`--list-rules`, and they are:

`always`/`never`, `must`/`must not`, `always`/`avoid`, `always`/`do not`,
`required`/`forbidden`, `prefer`/`never`, tab indentation/space indentation,
semicolons required/semicolons unwanted, `any` allowed/`any` banned, strict
mode on/off, trailing commas/no trailing commas, commit to main/never commit to
main, mocking/not mocking, comments required/comments unwanted.

Both sides are matched against **one sentence each**, and the two sentences must
be about the same subject (≥ 0.45 token similarity) and live in the **same file
and the same heading**, or in two rule files whose globs cover the same
language (that is AGL012). Two opposite rules in different sections of one file
are not reported: they may well be deliberate scoping.

### Size budgets

The defaults are ~500 lines and ~6000 words per file, and both are budgets, not
limits — the message says so, and `--max-lines` / `--max-words` raise them.

The reasoning, which is in the finding text rather than asserted as a hard fact:
a long instruction file is read less reliably. Rules at the bottom compete with
everything above them for attention, and rules that stopped being relevant are
rarely deleted. Splitting scoped detail into `.cursor/rules/*.mdc` files with a
`globs:` pattern keeps the always-loaded part small — which is the same advice
AGL011 gives from the other direction.

---

## Example output

Both examples shipped in `examples/` are real, runnable repositories: rule files
**plus the source files some of the rules reference**, so the path checks have
something to resolve against.

### `examples/good/` — the negative control

A well-structured, scoped, path-accurate rules set. Five instruction files, no
findings at the default threshold:

```console
$ python3 agent_config_lint.py examples/good
agent-config-lint 1.0.0
root: /root/money_making/free/agent-config-lint/examples/good
  .cursor/rules/python-service.mdc  [Cursor .mdc rule]  17 lines, 62 words, 4 rules, globs: **/*.py
  .cursor/rules/typescript.mdc  [Cursor .mdc rule]  22 lines, 84 words, 5 rules, globs: **/*.{ts,tsx}
  .github/copilot-instructions.md  [copilot-instructions.md]  11 lines, 48 words, 3 rules, globs: **/*.{ts,tsx}
  AGENTS.md  [AGENTS.md]  40 lines, 220 words, 17 rules
  CLAUDE.md  [CLAUDE.md]  40 lines, 220 words, 17 rules

No findings at or above severity 'low'.
$ echo $?
0
```

That is the whole point of the tool: a rules set that is scoped, referenced
correctly, deduplicated and structured produces **zero** findings — not "few".
`AGENTS.md` and `CLAUDE.md` are byte-identical there, both `.mdc` files are
scoped with `globs` and `alwaysApply: false`, and every path they mention
exists.

### `examples/bad/` — a realistic accumulated mess

An `AGENTS.md` that grew by accretion (723 lines, 6343 words — its long tail is
generated by `build_examples.py`, which asserts that no two generated bullets
are near-duplicates), a `CLAUDE.md` that has drifted away from it, and four
`.cursor/rules/*.mdc` files with frontmatter and scope defects.

Here is the complete run — all 23 findings, unabridged, because the middle
of a long list is exactly where this tool earns its keep:

```console
$ python3 agent_config_lint.py examples/bad
agent-config-lint 1.0.0
root: /root/money_making/free/agent-config-lint/examples/bad
  .cursor/rules/all-rules.mdc  [Cursor .mdc rule]  13 lines, 50 words, 5 rules, globs: **/*
  .cursor/rules/broken-frontmatter.mdc  [Cursor .mdc rule]  10 lines, 35 words, 1 rules
  .cursor/rules/python.mdc  [Cursor .mdc rule]  16 lines, 47 words, 3 rules, globs: **/*.py
  .cursor/rules/typescript.mdc  [Cursor .mdc rule]  27 lines, 83 words, 8 rules, globs: **/*.{ts,tsx}
  AGENTS.md  [AGENTS.md]  723 lines, 6343 words, 621 rules
  CLAUDE.md  [CLAUDE.md]  22 lines, 118 words, 11 rules

MEDIUM AGL011  .cursor/rules/
       all 4 rule files in .cursor/rules/ have 'alwaysApply: true', so nothing in this directory is scoped and the whole set is in context for every request. Scoping is the reason to split rules into separate files
       fix: give each file a 'globs' pattern and set 'alwaysApply: false' for all but the rules that genuinely apply everywhere

HIGH   AGL008  .cursor/rules/broken-frontmatter.mdc:3
       Cursor .mdc frontmatter is malformed: line 3 has an unclosed list in 'globs': '[**/*.ts, **/*.tsx'. Cursor reads this block as YAML, so a block that does not parse can cost you every key in it
       fix: make every line a 'key: value' pair, with balanced quotes and closed list brackets

MEDIUM AGL009  .cursor/rules/typescript.mdc:2
       Cursor frontmatter has no 'description'. Cursor surfaces a rule's description when an agent decides whether to pull the rule in, so a rule without one is harder to select. (Convention: 'description' is a documented key, but Cursor does not state that it is required.)
       fix: add a one-line 'description' saying what the file governs

LOW    AGL010  .cursor/rules/typescript.mdc:4
       Cursor frontmatter key 'colours' is not one of the three keys this tool validates ('description', 'globs', 'alwaysApply'). It may well be valid -- the linter only knows those three and says so rather than guessing (convention, not documentation)
       fix: keep it if your Cursor version supports it; otherwise remove 'colours'

MEDIUM AGL012  .cursor/rules/typescript.mdc:17
       conflicting scope: .cursor/rules/typescript.mdc and .cursor/rules/all-rules.mdc both target typescript, but they assert different rules about nearly the same subject -- "Never use semicolons." (.cursor/rules/all-rules.mdc:11) vs "Always use semicolons." (.cursor/rules/typescript.mdc:17). When two rule files can both apply, whichever the agent reads last wins
       fix: make one glob exclude the other, or move the shared rule into a single file

MEDIUM AGL005  AGENTS.md
       file is 723 lines, past the 500-line budget. Long instruction files are read less reliably: the rules at the bottom compete with everything above them for attention, and rules that stopped being relevant are rarely deleted. This is a budget, not a hard limit -- raise it with --max-lines if the file earns its length
       fix: move scoped detail into .cursor/rules/*.mdc files, so the always-loaded part stays under 500 lines

MEDIUM AGL005  AGENTS.md
       file is 6343 words, past the 6000-word budget. Word count matters as much as line count when a file is mostly prose: the whole file competes for attention, so every extra sentence dilutes the rules you actually care about. This is a budget, not a hard limit
       fix: trim restated context and move background out of the file; target under 6000 words

LOW    AGL006  AGENTS.md
       neither budget is met (723 lines / 6343 words). The two overruns reinforce each other: this reads as an accumulated log rather than a rule set
       fix: start from the rules you would enforce in review, delete the rest, and put scoped detail in a .mdc file with a glob

MEDIUM AGL004  AGENTS.md:12
       this rule is 89% similar to AGENTS.md:3 ("Always run `npm test` before opening a pull request.") -- near-duplicates of one rule dilute the file without adding instruction
       fix: merge the two rules and keep the sharper wording

LOW    AGL018  AGENTS.md:16
       rule is not verifiable: "Write clean code." -- 'clean code' has no observable definition. It cannot be checked against a diff, so it will not change what the agent writes
       fix: replace it with an observable instruction: a named command, a file, a threshold, or a concrete example

LOW    AGL015  AGENTS.md:24
       empty section: "Release notes" has no content. An empty heading promises rules that are not there, and a model may read the heading itself as the rule
       fix: either fill the section or delete the heading

HIGH   AGL002  AGENTS.md:29
       possible contradiction with AGENTS.md:28 (committing to main vs never committing to main): "Commit directly to main in this repository." vs "Never commit directly to main in this repository.". The tool matched an explicit opposition pair in two sentences about the same subject; it is not understanding the language
       fix: decide which rule wins and delete or qualify the other

MEDIUM AGL004  AGENTS.md:29
       this rule is 88% similar to AGENTS.md:28 ("Commit directly to main in this repository.") -- near-duplicates of one rule dilute the file without adding instruction
       fix: merge the two rules and keep the sharper wording

HIGH   AGL001  AGENTS.md:30
       rule references `docs/deploy-runbook.md` (found as a code span), but that path exists neither relative to /root/money_making/free/agent-config-lint/examples/bad nor relative to the repository root /root/money_making/free/agent-config-lint/examples/bad; a rule pointing at a deleted file is worse than no rule, because the agent has to guess what you meant
       fix: delete the rule, or correct the path to something that exists under /root/money_making/free/agent-config-lint/examples/bad

LOW    AGL017  AGENTS.md:31
       rule is addressed to a human, not the agent: "Ask your teammate before changing the CI pipeline." -- the agent cannot ask a person
       fix: delete it, or restate it as something the agent itself can do

LOW    AGL014  AGENTS.md:33
       heading level jumps from h2 to h4 ("Post-deploy checks"). The jump hides the fact that this section belongs under the previous heading (convention, not a markdown requirement)
       fix: re-level this heading to h3, or add the missing h3 heading

LOW    AGL016  AGENTS.md:35
       rule is written as a question rather than an instruction: "Should you check the error budget after every deploy?". A question names a topic; the agent still has to guess the answer
       fix: rewrite it as an instruction ("Do X when Y")

HIGH   AGL001  AGENTS.md:36
       rule references `ops/grafana/errors.json` (found as a code span), but that path exists neither relative to /root/money_making/free/agent-config-lint/examples/bad nor relative to the repository root /root/money_making/free/agent-config-lint/examples/bad; a rule pointing at a deleted file is worse than no rule, because the agent has to guess what you meant
       fix: delete the rule, or correct the path to something that exists under /root/money_making/free/agent-config-lint/examples/bad

MEDIUM AGL003  AGENTS.md:42
       this rule repeats line 12 verbatim (after normalising case, punctuation and inline code): "Run `npm test` before opening a pull request.". Duplicated rules push the file's real content out of the model's attention
       fix: keep one copy at line 12 and delete this one

MEDIUM AGL004  AGENTS.md:42
       this rule is 89% similar to AGENTS.md:3 ("Always run `npm test` before opening a pull request.") -- near-duplicates of one rule dilute the file without adding instruction
       fix: merge the two rules and keep the sharper wording

HIGH   AGL019  CLAUDE.md
       CLAUDE.md and AGENTS.md in the repository root have diverged (token overlap 7%). Two agents reading this repository will follow different rules, and reviewers will not know which file is authoritative
       fix: make one file the source of truth and reduce the other to a pointer, or sync them

LOW    AGL017  CLAUDE.md:15
       rule is addressed to a human, not the agent: "Ask your teammate which Node version to use; nobody wrote it down." -- the agent cannot ask a person
       fix: delete it, or restate it as something the agent itself can do

HIGH   AGL001  CLAUDE.md:21
       rule references `scripts/deploy.sh` (found as a code span), but that path exists neither relative to /root/money_making/free/agent-config-lint/examples/bad nor relative to the repository root /root/money_making/free/agent-config-lint/examples/bad; a rule pointing at a deleted file is worse than no rule, because the agent has to guess what you meant
       fix: delete the rule, or correct the path to something that exists under /root/money_making/free/agent-config-lint/examples/bad

23 finding(s) at or above 'low': 6 high, 9 medium, 8 low
```

The same run with `--severity high --quiet` prints only the six high findings
(AGL008, AGL002, AGL001 for the three dead paths, AGL019) and still exits `1`:

```console
$ python3 agent_config_lint.py examples/bad --severity high --quiet
HIGH   AGL008  .cursor/rules/broken-frontmatter.mdc:3
       Cursor .mdc frontmatter is malformed: line 3 has an unclosed list in 'globs': '[**/*.ts, **/*.tsx'. Cursor reads this block as YAML, so a block that does not parse can cost you every key in it
       fix: make every line a 'key: value' pair, with balanced quotes and closed list brackets

HIGH   AGL002  AGENTS.md:29
       possible contradiction with AGENTS.md:28 (committing to main vs never committing to main): "Commit directly to main in this repository." vs "Never commit directly to main in this repository.". The tool matched an explicit opposition pair in two sentences about the same subject; it is not understanding the language
       fix: decide which rule wins and delete or qualify the other

HIGH   AGL001  AGENTS.md:30
       rule references `docs/deploy-runbook.md` (found as a code span), but that path exists neither relative to /root/money_making/free/agent-config-lint/examples/bad nor relative to the repository root /root/money_making/free/agent-config-lint/examples/bad; a rule pointing at a deleted file is worse than no rule, because the agent has to guess what you meant
       fix: delete the rule, or correct the path to something that exists under /root/money_making/free/agent-config-lint/examples/bad

HIGH   AGL001  AGENTS.md:36
       rule references `ops/grafana/errors.json` (found as a code span), but that path exists neither relative to /root/money_making/free/agent-config-lint/examples/bad nor relative to the repository root /root/money_making/free/agent-config-lint/examples/bad; a rule pointing at a deleted file is worse than no rule, because the agent has to guess what you meant
       fix: delete the rule, or correct the path to something that exists under /root/money_making/free/agent-config-lint/examples/bad

HIGH   AGL019  CLAUDE.md
       CLAUDE.md and AGENTS.md in the repository root have diverged (token overlap 7%). Two agents reading this repository will follow different rules, and reviewers will not know which file is authoritative
       fix: make one file the source of truth and reduce the other to a pointer, or sync them

HIGH   AGL001  CLAUDE.md:21
       rule references `scripts/deploy.sh` (found as a code span), but that path exists neither relative to /root/money_making/free/agent-config-lint/examples/bad nor relative to the repository root /root/money_making/free/agent-config-lint/examples/bad; a rule pointing at a deleted file is worse than no rule, because the agent has to guess what you meant
       fix: delete the rule, or correct the path to something that exists under /root/money_making/free/agent-config-lint/examples/bad

6 finding(s) at or above 'high': 6 high
$ echo $?
1
$ python3 agent_config_lint.py examples/good --severity high --quiet
No findings at or above severity 'high'.
$ echo $?
0
```

### `--list-rules`

The complete catalogue, including the `basis` column and the named
`CLAUDE.md` / `AGENTS.md` sides of every opposition pair the tool will ever call a
contradiction:

```console
$ python3 agent_config_lint.py --list-rules
agent-config-lint 1.0.0 -- rules

basis values:
  documented = a requirement of the file format itself
  convention = a convention this tool enforces, not a documented requirement
  heuristic  = pattern matching over text, not language understanding

AGL001  high   [documented]  Rule references a path that does not exist
AGL002  high   [heuristic ]  Two rules contradict each other on the same subject
AGL003  medium [heuristic ]  Duplicate rule
AGL004  medium [heuristic ]  Near-duplicate rule
AGL005  medium [heuristic ]  Instruction file exceeds the size budget
AGL006  low    [heuristic ]  Neither the line nor the word budget is met
AGL007  high   [documented]  Cursor .mdc file has no frontmatter block
AGL008  high   [documented]  Cursor .mdc frontmatter is malformed
AGL009  medium [convention]  Cursor frontmatter is missing description or scope
AGL010  low    [convention]  Cursor frontmatter contains an unknown key
AGL011  medium [heuristic ]  Every rule file in a rules directory is alwaysApply: true
AGL012  medium [heuristic ]  Conflicting scope: overlapping globs, divergent rules
AGL013  low    [convention]  Rule appears before any heading
AGL014  low    [convention]  Heading level jumps by more than one
AGL015  low    [convention]  Empty section: heading with no content
AGL016  low    [heuristic ]  Rule is a question, not an instruction
AGL017  low    [heuristic ]  Rule is addressed to a human, not the agent
AGL018  low    [heuristic ]  Rule is not actionable / not verifiable
AGL019  high   [heuristic ]  CLAUDE.md and AGENTS.md are divergent
AGL020  medium [heuristic ]  Only one of CLAUDE.md / AGENTS.md exists in a directory

--severity only filters reporting and the exit code; it never changes the analysis.

Opposition pairs used by AGL002 (matched sentence by sentence):
  always-never         left=\balways\b
                       right=\bnever\b
                       -> always vs never
  must-mustnot         left=\bmust\b
                       right=\bmust not\b|\bmustn'?t\b
                       -> must vs must not
  always-avoid         left=\balways\b
                       right=\bavoid\b
                       -> always vs avoid
  always-dont          left=\balways\b
                       right=\bdon'?t\b|\bdo not\b
                       -> always vs do not
  require-forbid       left=\b(?:require[sd]?|mandatory|required)\b
                       right=\b(?:forbid(?:den)?|prohibit(?:ed)?|disallow(?:ed)?|banned)\b
                       -> required vs forbidden
  prefer-never         left=\bprefer\b
                       right=\bnever\b
                       -> prefer vs never
  tabs-indentation     left=\btabs?\b|tab indentation
                       right=spaces\b|space indentation
                       -> tab indentation vs space indentation
  semicolon-on-off     left=\bsemicolons? (?:are )?(?:required|mandatory|needed)\b
                       right=\bno semicolons?\b|\bsemicolons? (?:are )?(?:forbidden|banned|optional)\b
                       -> semicolons required vs semicolons not wanted
  any-allowed-banned   left=\bany\b.{0,24}\b(?:allowed|permitted|ok|okay|fine|use)\b
                       right=\b(?:avoid|no|never use|ban(?:ned)?|forbid(?:den)?)\b.{0,24}\bany\b
                       -> the `any` type allowed vs banned
  strict-mode          left=\bstrict mode\b
                       right=\b(?:no|disable|without) strict mode\b
                       -> strict mode on vs off
  trailing-comma       left=\btrailing commas?\b
                       right=\bno trailing commas?\b
                       -> trailing commas vs no trailing commas
  commit-main          left=\bcommit (?:directly )?(?:to|on) (?:the )?main\b
                       right=\bnever commit (?:directly )?(?:to|on) (?:the )?main\b
                       -> committing to main vs never committing to main
  mock-service         left=\bmocks?\b|\bmock(?:ing)?\b
                       right=\bdon'?t mock\b|\bno mocks?\b|\bavoid mocks?\b
                       -> mocking vs not mocking
  comments-required    left=\bcomments? (?:are )?(?:required|mandatory|needed)\b|\badd comments?\b|\bcomment (?:every|all)\b
                       right=\bno comments?\b|\bdon'?t (?:add|write) comments?\b|\bavoid comments?\b
                       -> comments required vs comments not wanted
```

### JSON

```console
$ python3 agent_config_lint.py examples/good --json
{
  "tool": "agent-config-lint",
  "version": "1.0.0",
  "root": "/root/money_making/free/agent-config-lint/examples/good",
  "threshold": "low",
  "files": [
    {
      "path": ".cursor/rules/python-service.mdc",
      "kind": "mdc",
      "lines": 17,
      "words": 62,
      "rules": 4,
      "globs": [
        "**/*.py"
      ],
      "alwaysApply": false
    }
  ],
  "counts": {
    "high": 0,
    "medium": 0,
    "low": 0
  },
  "findings": [],
  "notes": []
}
```

(Trimmed to one entry in `files` for length; the real payload lists all five.)

### The `good` example, for reference

```
examples/good/
├── AGENTS.md
├── CLAUDE.md                        # byte-identical to AGENTS.md
├── package.json
├── .cursor/rules/
│   ├── typescript.mdc               # globs: **/*.{ts,tsx}, alwaysApply: false
│   └── python-service.mdc           # globs: **/*.py,       alwaysApply: false
├── .github/
│   ├── copilot-instructions.md
│   └── workflows/ci.yml
├── src/
│   ├── errors.ts
│   ├── api/{client.ts,handlers.ts}
│   └── services/orders.py
└── tests/{test_orders.py,integration/test_retry.py}
```

---

## Tests

```console
$ python3 -m unittest discover -s tests -v
...
Ran 108 tests in 59.090s

OK
```

`python3 run_tests.py` runs the same suite and exits `0`/`1` the same way, if
you would rather not remember the discover flags.

What they cover:

* every one of the 20 rules, each with a positive case and a case that must
  **not** fire (a path that exists, a glob that matches, two opposite rules in
  different headings, a scoped rules directory, a near-copy `CLAUDE.md` that has
  not drifted);
* the **negative control**: `examples/good` must produce exactly zero findings
  at the default threshold, and exit `0`;
* a missing file, an empty file, a whitespace-only file, malformed frontmatter
  in five different ways, and a broken path reference;
* exit codes `0`, `1` and `2`, the `--severity` threshold, `--json` shape,
  `--quiet`, `--list-rules`, and `--max-lines`;
* the examples contain no secrets — the fixtures use obviously fake values and
  a test asserts that no credential-shaped string appears in them.

The scratch directories tests build live under `.scratch/` inside the project
and are removed in `tearDown`; nothing is written to the system temp directory.

---

## What this does not do

Read this before trusting a clean run.

* **It matches patterns in text. It does not understand intent.** Every check is
  a regular expression, a token comparison or a filesystem lookup. Nothing here
  parses the meaning of a sentence.
* **It has no idea whether a rule is *good*.** A precise, well-scoped,
  path-accurate rule that is simply wrong about your project passes silently. The
  tool reports duplicates, dead references, unreachable scopes and unverifiable
  phrasing — not correctness of judgement.
* **Contradiction detection (AGL002) only knows the table above.** More than a
  dozen declared opposition pairs, matched sentence by sentence, and only for
  statements about the same subject in the same file and heading. A genuine
  contradiction it has no pair for ("prefer 100-character lines" against "never
  exceed 80") is **not reported**. It will also not tell you which of the two
  rules is the one you meant.
* **The near-duplicate threshold is a heuristic.** AGL004 fires at ≥ 0.85 token
  similarity (Jaccard, after lowercasing and stripping punctuation and inline
  code). Two rules that differ by a single important word can look like
  duplicates, and two rules worded completely differently but saying the same
  thing will not be caught. AGL002 and AGL004 can both fire on one pair of
  lines — the pair really is that close.
* **Path checking is a best-effort filesystem lookup, not a parser.** It skips
  fenced code blocks, indented code, URLs, absolute paths and YAML frontmatter;
  a reference resolves if it exists relative to the rule file's own directory or
  to the repository root, with `*`, `?`, `[]` and `{}` glob support. A path
  written for a different working directory, or described in prose rather than
  backticks, can be missed; an invented example path can be flagged.
* **The heading and section checks are conventions, not requirements.** AGL013,
  AGL014 and AGL015 encode how this tool thinks a rule file stays readable —
  Markdown itself requires none of it, and the findings say so. A heading whose
  content lives in its subsections is *not* reported as empty.
* **The rules that reflect conventions are labelled as conventions.** Every
  finding carries a `basis` of `documented`, `convention` or `heuristic`, and
  each message repeats it where it matters. Only AGL001, AGL007 and AGL008 rest
  on something the file format itself requires — and even for `.mdc` files the
  tool validates exactly the three keys Cursor documents (`description`,
  `globs`, `alwaysApply`) and explicitly refuses to guess about any other key.
* **Word and line budgets are budgets, not limits.** They are configurable, and
  the finding text explains the reasoning instead of asserting a hard rule.
* **It reads files, it does not run anything.** No command in a rule file is
  executed, and no claim in a rule file is verified against your build.

---

## Repository layout

```
agent-config-lint/
├── agent_config_lint.py            # the linter (standard library only)
├── run_tests.py                    # convenience entry point for the test suite
├── build_examples.py               # regenerates the long body of examples/bad/AGENTS.md
├── tests/test_agent_config_lint.py # 108 tests, including the negative control
├── examples/good/                  # a clean, scoped, path-accurate rules set
├── examples/bad/                   # an accumulated mess with known defects
├── README.md
└── LICENSE
```

`build_examples.py` composes the several-hundred-line tail of
`examples/bad/AGENTS.md` from disjoint word pools, then asserts that no two
generated bullets are near-duplicates under the same 0.85 threshold the linter
uses — and that the file really does exceed both budgets. Run it from the
project root to regenerate that fixture; it is the only file in the repository
that writes anything.

---

## Licence

MIT. See [`LICENSE`](LICENSE).

Copyright (c) 2026 duke5am.

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

If you want the rules themselves rather than a linter for them — the scoped
`.cursor/rules/*.mdc` set and the `CLAUDE.md` / `AGENTS.md` pair this tool is
built to keep honest — they are sold as two packs:

<!-- RELATED:START -->

## Related tools

- **[production-mcp-server-template](https://github.com/duke5am/production-mcp-server-template)** — A minimal working MCP server in TypeScript and Python with the production details right: stderr logging, structured error results, transports.
  *(if you were searching for "mcp server example")*

All 28 tools in this set, grouped by what they check: **[dev-tools-index](https://duke5am.github.io/dev-tools-index/)**

If you arrived here searching for one of these, this is the tool: **agents.md best practices** · **claude.md rules** · **cursor rules globs** · **ai coding agent instructions**

<!-- RELATED:END -->

→ **[Production Rules Pack for Cursor](https://duke5am.gumroad.com/l/01-cursor-rules-pack)** — $19 on Gumroad, **[Claude Code Config Pack](https://duke5am.gumroad.com/l/02-claude-code-config-pack)** — $19 on Gumroad <!-- GUMROAD-LINK -->
