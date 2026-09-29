#!/usr/bin/env python3
"""Run every experiment and write docs/evaluation.md and site/results.json.

    python3 scripts/evaluate.py              # 1000 random cases per experiment (~5 min)
    python3 scripts/evaluate.py --cases 100  # quick run

Numbers in the documentation and on the website come from these two files only;
`scripts/build_site.py` embeds them in site/index.html (`make evaluate` runs both).
"""

from __future__ import annotations

import argparse
import copy
import json
import platform
import random
import re
import statistics
import subprocess
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "python"), str(REPO / "python" / "tests"), str(REPO / "scripts")]

import generate  # noqa: E402
import harness  # noqa: E402
import mutants  # noqa: E402
import mutate_lean  # noqa: E402
from vcore import smt  # noqa: E402
from vcore.lean import VCore  # noqa: E402
from vcore.policy import parse_authority, parse_path, parse_policy, width  # noqa: E402
from vcore.runtime import Session  # noqa: E402

EXAMPLES = REPO / "examples" / "coding-agent"


def table(header: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.1f}%" if b else "-"


# ── Proofs ─────────────────────────────────────────────────────────────────


def proofs() -> dict:
    files = sorted((REPO / "VCore").glob("*.lean"))
    text = "\n".join(f.read_text() for f in files)
    axioms_out = subprocess.run(["lake", "env", "lean", "scripts/Axioms.lean"], cwd=REPO,
                                capture_output=True, text=True, check=True).stdout
    used = sorted({a.strip() for group in re.findall(r"\[([^\]]*)\]", axioms_out)
                   for a in group.split(",") if a.strip()})
    print("  mutating Lean definitions...", file=sys.stderr)
    lean_mutants = mutate_lean.run_all()
    return {
        "lean_lines": sum(len(f.read_text().splitlines()) for f in files),
        "theorems": len(re.findall(r"^theorem ", text, re.M)),
        "sorry": len(re.findall(r"\bsorry\b|\badmit\b", text)),
        "axioms": used,
        "lean_mutants": lean_mutants,
    }


# ── Agreement and precision ───────────────────────────────────────────────


def agreement(v: VCore, cases: int) -> dict:
    return {
        "runtime": sum(1 for s in range(cases) if harness.runtime_vs_lean(v, s)),
        "checker": sum(1 for s in range(cases) if harness.smt_vs_lean(v, s)),
        "parser": sum(1 for s in range(2 * cases) if harness.parser_vs_lean(v, s)),
        "parser_cases": 2 * cases,
    }


def precision(v: VCore, cases: int) -> dict:
    by_width: dict[int, Counter] = {}
    missed_stateless = false_alarms = missed_depth2 = escalations = refinements = 0
    for seed in range(cases):
        old, new = harness.pair(seed)
        w_old = v.width(old)
        witness = v.check(old, new)
        stateless = smt.stateless_escalation(parse_authority(old), parse_authority(new))
        row = by_width.setdefault(str(w_old), Counter())
        row["pairs"] += 1
        if witness is None:
            refinements += 1
            row["refines"] += 1
            false_alarms += stateless is not None
        else:
            escalations += 1
            row[str(len(witness))] += 1
            missed_stateless += stateless is None
            missed_depth2 += v.check(old, new, depth=2) is None
    return {
        "pairs": cases, "refinements": refinements, "escalations": escalations,
        "missed_stateless": missed_stateless, "false_alarms": false_alarms,
        "missed_depth2": missed_depth2,
        "by_width": {w: dict(c) for w, c in sorted(by_width.items(), key=lambda kv: int(kv[0]))},
    }


def unverified_mutants(v: VCore, cases: int) -> list[dict]:
    rows = []
    for mutant, check in [(m, harness.runtime_vs_lean) for m in mutants.RUNTIME] + \
            [(m, harness.smt_vs_lean) for m in mutants.CHECKER + mutants.RARE]:
        with mutant():
            found = next((seed + 1 for seed in range(cases) if check(v, seed)), None)
        rows.append({"name": mutant.__name__, "bug": " ".join((mutant.__doc__ or "").split()),
                     "caught_after": found, "rare": mutant in mutants.RARE})
    return rows


# ── Case study ────────────────────────────────────────────────────────────


def case_study(v: VCore) -> list[dict]:
    load = lambda name: json.loads((EXAMPLES / name).read_text())
    policy, rule = load("policy.json"), load("rule-of-two.json")
    rows = []
    for label, old, new in [
        ("trust the issue tracker (`trust-issues.json`)", policy, load("trust-issues.json")),
        ("remove a dead rule (`cleanup.json`)", policy, load("cleanup.json")),
        ("attach a sub-agent policy alone (`docs-agent.json`)", policy, load("docs-agent.json")),
        ("attach it as a delegate (`docs-agent-delegated.json`)", policy, load("docs-agent-delegated.json")),
        ("`policy.json` satisfies the Rule of Two?", [rule, policy], policy),
        ("`hardened.json` satisfies the Rule of Two?", [rule, load("hardened.json")], load("hardened.json")),
    ]:
        witness = v.check(old, new)
        stateless = smt.stateless_escalation(parse_authority(old), parse_authority(new))
        rows.append({"edit": label, "escalation": len(witness) if witness else None,
                     "depth2": v.check(old, new, depth=2) is not None,
                     "stateless": stateless is not None})
    return rows


# ── Cost ──────────────────────────────────────────────────────────────────


def synthetic(rng: random.Random, n: int) -> dict:
    """A policy with about `n` statements over a vocabulary that grows with `n`."""
    tools = ["Read", "Edit", "Bash", "WebFetch", "mcp"]
    dirs = [f"d{i}" for i in range(max(2, n // 6))]
    files = [f"f{i}" for i in range(max(2, n // 10))]
    tags = ["private", "untrusted", "secret", "prod"]

    def pat() -> str:
        k = rng.random()
        if k < 0.5:
            return f"{rng.choice(tools)}/{rng.choice(dirs)}/**"
        if k < 0.7:
            return f"**/{rng.choice(files)}"
        return f"{rng.choice(tools)}/{rng.choice(dirs)}/{rng.choice(files)}"

    return {"permit": [pat() for _ in range(n // 2)], "forbid": [pat() for _ in range(n // 6)],
            "label": {pat(): rng.choice(tags) for _ in range(n // 6)},
            "block": [{"on": pat(), "after": rng.sample(tags, 2)} for _ in range(n // 6)]}


def scan_decide(policy, ctx: frozenset, x: tuple) -> bool:
    """The source semantics, scanning every statement: the baseline for the runtime."""
    permit = forbid = blocked = False
    for st in policy:
        if st.pat.covers(x):
            permit |= st.effect == "permit"
            forbid |= st.effect == "forbid"
            blocked |= st.effect == "block" and set(st.after) <= ctx
    return permit and not forbid and not blocked


def timed(fn) -> float:
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


def runtime_cost(v: VCore, sizes: list[int]) -> list[dict]:
    rows = []
    for n in sizes:
        rng = random.Random(n)
        policy = synthetic(rng, n)
        pats = policy["permit"] + policy["forbid"] + list(policy["label"])
        requests = ["/".join(s for s in rng.choice(pats).split("/") if s != "**") + "/x"
                    for _ in range(2000)]
        compile_s = timed(lambda: v.compile_json(policy))
        session = Session([v.compile(policy)])
        source, paths = parse_policy(policy), [parse_path(r) for r in requests]
        index_s = timed(lambda: [session.check(r) for r in requests]) / len(requests)
        scan_s = timed(lambda: [scan_decide(source, frozenset(), x) for x in paths]) / len(paths)
        rows.append({"statements": n, "compile_ms": compile_s * 1e3,
                     "index_us": index_s * 1e6, "scan_us": scan_s * 1e6})
    return rows


def checker_cost(v: VCore, sizes: list[int], z3_max: int, strings_max: int) -> list[dict]:
    rows = []
    for n in sizes:
        t: dict[str, list] = {"lean": [], "z3": [], "strings": []}
        timeouts = 0
        for k in range(3):
            rng = random.Random(1000 * n + k)
            old = synthetic(rng, n)
            new = copy.deepcopy(old)
            del new["label"][rng.choice(sorted(new["label"]))]
            t["lean"].append(timed(lambda: v.check(old, new)))
            o, w = parse_authority(old), parse_authority(new)
            if n <= z3_max:
                t["z3"].append(timed(lambda: smt.escalation(o, w)))
            if n <= strings_max:
                try:
                    t["strings"].append(timed(lambda: smt.escalation(
                        o, w, encoding=smt.Strings, timeout_ms=10_000)))
                except TimeoutError:
                    timeouts += 1
        med = lambda xs: statistics.median(xs) * 1e3 if xs else None
        rows.append({"statements": n, "lean_ms": med(t["lean"]), "z3_ms": med(t["z3"]),
                     "strings_ms": med(t["strings"]), "strings_timeouts": timeouts,
                     "strings_run": n <= strings_max})
    return rows


# ── Report ────────────────────────────────────────────────────────────────


def ms(x: float | None) -> str:
    if x is None:
        return "-"
    return f"{x / 1e3:.1f} s" if x >= 1000 else f"{x:.0f} ms" if x >= 10 else f"{x:.1f} ms"


def bound_sentence(pr: dict) -> str:
    over, reached = [], []
    for w, row in pr["by_width"].items():
        lengths = [int(k) for k in row if k.isdigit()]
        if any(k > int(w) + 1 for k in lengths):
            over.append(w)
        if int(w) + 1 in lengths:
            reached.append(w)
    if over:
        return f"**Counterexamples longer than width + 1 at widths {', '.join(over)}: this contradicts the theorem and is a bug.**"
    return ("No counterexample is longer than width + 1, as `short_counterexample` requires; the bound "
            f"is reached at width{'s' if len(reached) > 1 else ''} {', '.join(reached) or 'none'}.")


def missed_sentence(rows: list[dict]) -> str:
    missed = [m for m in rows if not m["caught_after"]]
    if not missed:
        return "Every seeded bug is caught."
    parts = []
    for m in missed:
        if m["name"] == "no_fresh_segment":
            parts.append("`no_fresh_segment` is a real completeness bug: it misses escalations that go "
                         "through a path segment neither policy names, and random policies almost always "
                         "name enough segments to hide it. `test_fresh_segment_needed` exposes it with a "
                         "three-line policy pair, and its Lean counterpart (`no-junction-reps`) breaks the "
                         "proof of `exists_rep`. Testing finds the common bugs quickly; the proof closes "
                         "the rare ones.")
        else:
            parts.append(f"**`{m['name']}` was not caught in this run.**")
    return "\n\n".join(parts)


def write_report(r: dict) -> str:
    p, a, pr = r["proofs"], r["agreement"], r["precision"]
    n = r["cases"]
    widths = sorted(pr["by_width"], key=int)
    max_len = max((int(k) for row in pr["by_width"].values() for k in row if k.isdigit()), default=1)
    longer = sum(c for row in pr["by_width"].values() for k, c in row.items() if k.isdigit() and int(k) > 2)
    width_rows = [[w, row.get("pairs", 0), row.get("refines", 0)] +
                  [row.get(str(k), 0) for k in range(1, max_len + 1)]
                  for w, row in ((w, pr["by_width"][w]) for w in widths)]
    caught = [m for m in r["unverified_mutants"] if m["caught_after"]]
    lean_rejected = sum(m["rejected"] for m in p["lean_mutants"])
    return f"""# Evaluation

_Generated by `scripts/evaluate.py` on {r["date"]}: {r["machine"]}; Python {r["python"]};
Lean {r["lean"]}; {n} random cases per experiment. Do not edit by hand._

Four questions:

1. **Proofs.** Do the theorems constrain the definitions, or could they be vacuous?
2. **Agreement.** Does the unverified code (Python runtime, Z3 checker, both parsers) agree
   with the verified Lean core?
3. **Precision.** Does checking whole sessions matter, compared with the obvious
   alternatives?
4. **Cost.** What do enforcement and checking cost as policies grow?

## 1. Proofs

The Lean development is {p["lean_lines"]} lines with {p["theorems"]} theorems and no
dependency beyond Lean's standard library. It contains {p["sorry"]} `sorry` or `admit`.
The headline theorems use only the axioms {", ".join(f"`{x}`" for x in p["axioms"])}
(`make axioms`).

To check that the proofs depend on the definitions, `scripts/mutate_lean.py` seeds one bug
at a time into the definitions and rebuilds. {lean_rejected} of {len(p["lean_mutants"])}
mutants break the build:

{table(["Mutant", "Bug", "Build fails", "First declarations that fail"],
       [[f"`{m['name']}`", m["bug"], "yes" if m["rejected"] else "**no**",
         ", ".join(f"`{d}`" for d in m["broken"][:3]) or "-"] for m in p["lean_mutants"]])}

This shows the proofs are not vacuous; it does not show the definitions say what you want.
The first declaration to fail is often a lemma that restates a definition (`allows_iff`),
and Lean then stops checking the theorems built on it. Whether the semantics is the
intended one is what the contract theorems in `Policy.lean` are for: they state the rules
of the language (`forbid_denies`, `block_denies`, `allows_canonical`, ...) in a few lines
each, for a reviewer to read.

## 2. Agreement

Random authorities come from two families (see `python/tests/generate.py`): arbitrary
patterns over an adversarial segment alphabet with combination rules of one to three tags,
and small tool/resource policies where most behaviour is in labels and combinations.
30% are two-policy authorities. Request sequences include non-canonical requests. The
parser experiment uses documents that are broken in one place 85% of the time.

{table(["Comparison", "Cases", "Disagreements"], [
    ["Python runtime (Lean-compiled index) vs Lean source semantics, sequences of 1 to 6 requests", n, a["runtime"]],
    ["Z3 checker (searching 2 requests deeper than the bound) vs Lean checker: verdict, both witnesses replayed through Lean, witness length", n, a["checker"]],
    ["Python parser vs Lean parser: accept or reject, and the statements read", a["parser_cases"], a["parser"]],
])}

**Seeded bugs in the unverified code.** Each bug is patched into the Python runtime or the
Z3 checker, and the differential harness runs until it disagrees with Lean.
{len(caught)} of {len(r["unverified_mutants"])} are caught by random testing:

{table(["Mutant", "Bug", f"Random cases until caught (of {n})"],
       [[f"`{m['name']}`", m["bug"], m["caught_after"] or "**not caught**"] for m in r["unverified_mutants"]])}

{missed_sentence(r["unverified_mutants"])}

## 3. Precision

On the same {n} policy pairs (the new policy is a small edit of the old one 60% of the
time, otherwise independent), {pr["refinements"]} are refinements and {pr["escalations"]}
are not. Counterexample lengths by the width of the old policy (its largest combination):

{table(["Width", "Pairs", "Refinements"] + [f"{k} request{'s' if k > 1 else ''}" for k in range(1, max_len + 1)], width_rows)}

{bound_sentence(pr)} {longer} of the {pr["escalations"]} counterexamples need more than two
requests.

Two obvious alternatives, on the same pairs:

{table(["Alternative", "Escalations missed", "Refinements reported as escalations"], [
    ["Per-request diff with the session's tags left free (what analyzers for stateless policy languages check)",
     f"{pr['missed_stateless']} of {pr['escalations']} ({pct(pr['missed_stateless'], pr['escalations'])})",
     f"{pr['false_alarms']} of {pr['refinements']} ({pct(pr['false_alarms'], pr['refinements'])})"],
    ["Bounded search over sessions of two requests",
     f"{pr['missed_depth2']} of {pr['escalations']} ({pct(pr['missed_depth2'], pr['escalations'])})", "0 (sound)"],
])}

The per-request diff misses changes that only act across requests, such as removing a
label, which changes no single decision. It raises false alarms for changes that only
matter in combinations of tags no session can reach. A bounded search is sound but needs to
know how deep to go; `short_counterexample` supplies that depth.

**Case study** (`examples/coding-agent`):

{table(["Change", "vcore", "Two-request search", "Per-request diff"],
       [[c["edit"], f"escalation, {c['escalation']} request{'s' if c['escalation'] > 1 else ''}" if c["escalation"] else "no escalation",
         "finds it" if c["depth2"] else ("—" if not c["escalation"] else "**misses it**"),
         ("flags it" if c["stateless"] else "**misses it**") if c["escalation"] else
         ("**false alarm**" if c["stateless"] else "—")] for c in r["case_study"]])}

## 4. Cost

**Enforcement.** Synthetic policies of `n` statements; 2000 requests each, decided by the
Python runtime.

{table(["Statements", "Compile (Lean, incl. JSON)", "Decision, compiled index", "Decision, scanning statements"],
       [[c["statements"], ms(c["compile_ms"]), f"{c['index_us']:.1f} µs", f"{c['scan_us']:.1f} µs"] for c in r["runtime_cost"]])}

A decision on the compiled index costs `2|x| + 3` lookups for a request of `|x|` segments,
whatever the size of the policy, which is what `compile_correct` licenses. Compilation is
quadratic in the number of distinct patterns (the Lean table is an association list).

**Refinement checking.** Median of 3 pairs per size; the new policy drops one label. Z3 has
a 10 s timeout per query with the string encoding.

{table(["Statements", "Lean checker", "Z3, segment encoding", "Z3, string encoding"],
       [[c["statements"], ms(c["lean_ms"]), ms(c["z3_ms"]),
         ("not run" if not c["strings_run"] else ms(c["strings_ms"]) +
          (f" ({c['strings_timeouts']} of 3 timed out)" if c["strings_timeouts"] else ""))]
        for c in r["checker_cost"]])}

The verified checker computes what every policy says about every representative request;
there are roughly (distinct prefix patterns) × (distinct suffix patterns) of them, so its
cost grows faster than Z3's. It is interactive up to a few hundred statements, which covers
agent policies written by hand. The Z3 encoding by segments, which builds in the same
finite-alphabet idea, scales better; the natural string encoding does not.

## Threats to validity

* Random policies are synthetic, and the precision figures depend on how they are drawn.
  The case study is small and hand-written; policies from deployed agents would be better
  evidence.
* The Lean and Z3 checkers were written by the same author. They share no code and use
  different encodings, but a shared misreading of the semantics would not show up as a
  disagreement. The contract theorems are the defence.
* Timings are single-machine medians over few samples.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, default=1000)
    parser.add_argument("--report-only", action="store_true",
                        help="rewrite docs/evaluation.md from site/results.json")
    args = parser.parse_args()
    if args.report_only:
        results = json.loads((REPO / "site" / "results.json").read_text())
        (REPO / "docs" / "evaluation.md").write_text(write_report(results))
        return
    n = args.cases
    cpu = subprocess.run(["sh", "-c", "grep -m1 'model name' /proc/cpuinfo | cut -d: -f2"],
                         capture_output=True, text=True).stdout.strip() or platform.processor()
    lean = subprocess.run(["lean", "--version"], capture_output=True, text=True).stdout.split()[2].rstrip(",")
    results = {"date": date.today().isoformat(), "machine": cpu, "python": platform.python_version(),
               "lean": lean, "cases": n}
    with VCore() as v:
        for name, fn in [("proofs", proofs), ("agreement", lambda: agreement(v, n)),
                         ("precision", lambda: precision(v, n)),
                         ("unverified_mutants", lambda: unverified_mutants(v, n)),
                         ("case_study", lambda: case_study(v)),
                         ("runtime_cost", lambda: runtime_cost(v, [10, 100, 1000, 10000])),
                         ("checker_cost", lambda: checker_cost(v, [16, 64, 256, 1024], 1024, 64))]:
            start = time.time()
            print(f"{name}...", file=sys.stderr)
            results[name] = fn()
            print(f"  {time.time() - start:.0f} s", file=sys.stderr)
    (REPO / "site").mkdir(exist_ok=True)
    (REPO / "site" / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    (REPO / "docs").mkdir(exist_ok=True)
    (REPO / "docs" / "evaluation.md").write_text(write_report(results))
    print("wrote docs/evaluation.md and site/results.json", file=sys.stderr)


if __name__ == "__main__":
    main()
