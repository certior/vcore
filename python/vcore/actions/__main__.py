"""``python -m vcore.actions``: authority envelopes for AI agents in GitHub workflows.

    scan PATH...            analyze workflow files or directories
    diff OLD NEW            does NEW let the agent do anything OLD does not?
    envelope FILE EVENT     print one envelope as a vcore policy

Exit status: 0 no counterexample (scan) or no widening (diff), 1 otherwise, 2 bad input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..lean import VCore
from . import claude_code_action as cca
from .report import diff, render, scan
from .workflow import WorkflowError, load


def _files(paths: list[str]) -> list[Path]:
    out = []
    for p in map(Path, paths):
        if p.is_dir():
            out += sorted(q for q in p.rglob("*") if q.suffix in (".yml", ".yaml"))
        else:
            out.append(p)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m vcore.actions", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("paths", nargs="+")
    s.add_argument("--json", action="store_true")
    s.add_argument("--conservative", action="append", default=[], choices=["A4"],
                   help="also apply a conservative assumption")
    s.add_argument("--repairs", action="store_true", help="search single-edit repairs for violations")
    d = sub.add_parser("diff")
    d.add_argument("old")
    d.add_argument("new")
    d.add_argument("--json", action="store_true")
    e = sub.add_parser("envelope")
    e.add_argument("file")
    e.add_argument("event")
    e.add_argument("--activity")
    e.add_argument("--mode", choices=["tag", "agent"])
    args = ap.parse_args(argv)

    try:
        with VCore() as vc:
            if args.cmd == "scan":
                results = [scan(f, vc, tuple(args.conservative), args.repairs) for f in _files(args.paths)]
                if args.json:
                    print(json.dumps(results, indent=2))
                else:
                    sys.stdout.write("".join(render(r) for r in results))
                if any("error" in r for r in results):
                    return 2
                return int(any(not v["satisfied"] for r in results for c in r["configurations"]
                               for v in c.get("verdicts", [])))
            if args.cmd == "diff":
                found = diff(Path(args.old).read_text(), Path(args.new).read_text(), vc)
                if args.json:
                    print(json.dumps(found, indent=2))
                elif not found:
                    print(f"{args.new} refines {args.old}: the agent can do nothing new.")
                else:
                    for f in found:
                        what = "new configuration" if f["new_configuration"] else "widened"
                        print(f"{f['event']} ({f['activity'] or 'any'}), {f['mode']} mode, job {f['job']}: {what}")
                        for i, r in enumerate(f["sequence"], 1):
                            print(f"  {i}  {r}")
                return int(bool(found))
    except WorkflowError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    workflow = load(Path(args.file).read_text())
    for job, step, rc, env in cca.envelopes(workflow):
        if rc.event == args.event and rc.runs is not False and \
                (args.activity is None or rc.activity == args.activity) and \
                (args.mode is None or rc.mode == args.mode):
            print(json.dumps(env.policy(), indent=2))
            return 0
    print(f"no configuration for event {args.event}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
