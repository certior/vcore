#!/usr/bin/env python3
"""Seed bugs into the Lean definitions and check that the build fails for each one.

Works on a scratch copy of the repository, one mutant at a time, and reports the first
declarations that no longer check. Exit status 1 if any mutant builds.

    python3 scripts/mutate_lean.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# (name, what the bug is, file, original text, mutated text)
MUTANTS = [
    ("forbid-ignored", "`forbid` no longer overrides `permit`",
     "VCore/Policy.lean",
     "s.permit && !s.forbid && !s.blocked ctx", "s.permit && !s.blocked ctx"),
    ("block-on-any-tag", "a block fires when any tag of its combination is present",
     "VCore/Policy.lean",
     "b.all ctx.contains", "b.any ctx.contains"),
    ("empty-authority-allows", "an authority with no policies allows everything",
     "VCore/Policy.lean",
     "!ss.isEmpty && ss.all (·.allows ctx)", "ss.all (·.allows ctx)"),
    ("noncanonical-allowed", "requests with `.`, `..` or empty segments are evaluated",
     "VCore/Policy.lean",
     "Path.canonical x && Summary.allowsAll (a.summaries x) ctx",
     "Summary.allowsAll (a.summaries x) ctx"),
    ("context-reset", "running a request replaces the context with its tags",
     "VCore/Policy.lean",
     "ctx.union (a.taint x)", "([] : Ctx).union (a.taint x)"),
    ("insert-drops-tag", "adding a tag to a context can lose it",
     "VCore/Policy.lean",
     "else s :: insert t c", "else s :: c"),
    ("merge-overwrites", "the compiler overwrites a table entry instead of merging",
     "VCore/Compile.lean",
     "then (k', s'.merge s) :: t", "then (k', s) :: t"),
    ("keys-off-by-one", "lookup skips the prefix equal to the whole request",
     "VCore/Compile.lean",
     "(List.range (x.length + 1)).flatMap", "(List.range x.length).flatMap"),
    ("fresh-segment-constant", "the junction segment is always `_`",
     "VCore/Representatives.lean",
     "def fresh (ps : List Pat) : String := freshFor (ps.flatMap lit)",
     "def fresh (_ : List Pat) : String := \"_\""),
    ("no-junction-reps", "representatives omit the fresh junction segment",
     "VCore/Representatives.lean",
     "fun u => q ++ fresh ps :: u", "fun u => q ++ u"),
    ("search-depth-one", "the checker explores one step, the bound for single-tag rules",
     "VCore/Check.lean",
     "(min (n - 1) old.width)", "(min (n - 1) 1)"),
]

ERROR = re.compile(r"error: (VCore/[\w/]+\.lean):(\d+):\d+")
DECL = re.compile(r"^(?:private |protected )?(?:theorem|def|instance|abbrev)\s+([^\s:({]+)")


def enclosing_decl(path: Path, line: int) -> str:
    lines = path.read_text().splitlines()
    for i in range(min(line, len(lines)) - 1, -1, -1):
        if m := DECL.match(lines[i]):
            return m.group(1)
    return "?"


def run_all() -> list[dict]:
    """Build each mutant in a scratch copy of the repository."""
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "repo"
        shutil.copytree(REPO, work, ignore=shutil.ignore_patterns(
            "__pycache__", ".git", "python", "site", "docs", "examples"))
        for name, bug, rel, old, new in MUTANTS:
            path = work / rel
            original = path.read_text()
            assert original.count(old) == 1, f"{name}: pattern must occur exactly once in {rel}"
            path.write_text(original.replace(old, new))
            build = subprocess.run(["lake", "build", "VCore"], cwd=work,
                                   capture_output=True, text=True)
            path.write_text(original)
            broken: list[str] = []
            for file, line in ERROR.findall(build.stdout + build.stderr):
                decl = enclosing_decl(work / file, int(line))
                if decl not in broken:
                    broken.append(decl)
            rows.append({"name": name, "bug": bug, "rejected": build.returncode != 0,
                         "broken": broken})
            print(f"{name:24} {'build fails' if build.returncode else 'SURVIVED':12} "
                  f"{', '.join(broken)}", file=sys.stderr)
    return rows


def main() -> int:
    rows = run_all()
    return 0 if all(r["rejected"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
