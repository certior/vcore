"""Fidelity: compare the model's predictions with the oracle's observations.

    python3 bench/compare.py bench/scenarios/generated.jsonl bench/observations/generated.jsonl \
        [--mismatches bench/results/mismatches.jsonl] [--json bench/results/fidelity.json]

Scenarios the oracle could not evaluate (expressions over outputs of steps it does not run,
harness errors) are counted and excluded. A run the action aborts because the token lacks a
scope is kept, as a disagreement: the model does not model scopes (assumption A9). For every remaining scenario each
compared field is reported as agreeing or not; ``claude_env`` is compared on the credentials
the envelope relies on (``GITHUB_TOKEN``, ``GH_TOKEN``) only.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "python"))

from vcore.actions.predict import predict  # noqa: E402

FIELDS = ("runs", "mode", "allowed_tools", "disallowed_tools", "permission_mode", "mcp_servers",
          "token_in_git_config", "credential_helper", "env_github_token", "env_scrub", "prompt_canaries")


def view(o: dict) -> dict:
    runs = o["runs"]
    return {
        "runs": runs,
        "stop": o["stop_reason"],
        "mode": o["mode"],
        "allowed_tools": o["allowed_tools"] if runs else None,
        "disallowed_tools": o["disallowed_tools"] if runs else None,
        "permission_mode": o["permission_mode"] if runs else None,
        "mcp_servers": o["mcp_servers"] if runs else None,
        "token_in_git_config": o["credentials"]["token_in_git_config"] if runs else None,
        "credential_helper": o["credentials"]["credential_helper"] if runs else None,
        "env_github_token": ({"GITHUB_TOKEN", "GH_TOKEN"} <= set(o["credentials"]["claude_env"])) if runs else None,
        "env_scrub": o["env_scrub"] if runs else None,
        "prompt_canaries": o["prompt_canaries"] if runs else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios")
    ap.add_argument("observations")
    ap.add_argument("--mismatches")
    ap.add_argument("--json")
    args = ap.parse_args()
    scenarios = {s["id"]: s for s in map(json.loads, Path(args.scenarios).read_text().splitlines())}
    observed = {o["id"]: o for o in map(json.loads, Path(args.observations).read_text().splitlines())}
    agree, total = Counter(), Counter()
    excluded = Counter()
    stops = Counter()
    by_knob = defaultdict(Counter)
    mismatches = []
    for sid, obs in observed.items():
        sc = scenarios.get(sid)
        if sc is None:
            continue
        stop = obs["stop_reason"] or ""
        if stop.startswith("error:track_progress"):
            obs = {**obs, "stop_reason": "track-progress-invalid-event"}
        elif stop == "error:Resource not accessible by integration":
            # The GitHub token lacked a scope the action needed before Claude started: the
            # behavior assumption A9 over-approximates. Counted as a disagreement.
            obs = {**obs, "stop_reason": "token-scope"}
        elif stop.startswith("error:") or stop == "unsupported-expression":
            # Outside the harness: expressions over earlier steps' outputs, harness errors.
            excluded[stop.split(":")[0]] += 1
            continue
        pred = predict(sc)
        o, p = view(obs), view(pred)
        stops[(o["stop"], p["stop"])] += 1
        diffs = []
        for f in FIELDS:
            if f != "runs" and not (o["runs"] and p["runs"]):
                if f == "mode":
                    total[f] += 1
                    agree[f] += o[f] == p[f]
                    if o[f] != p[f]:
                        diffs.append(f)
                continue
            total[f] += 1
            if o[f] == p[f]:
                agree[f] += 1
            else:
                diffs.append(f)
        knob = sid.split("|")[1].split("=")[0] if sid.count("|") else "smoke"
        by_knob[knob]["scenarios"] += 1
        by_knob[knob]["exact"] += not diffs
        if diffs:
            mismatches.append({"id": sid, "fields": diffs, "oracle": {f: o[f] for f in diffs + ["stop"]},
                               "model": {f: p[f] for f in diffs + ["stop"]}})
    n = sum(1 for _ in observed) - sum(excluded.values())
    exact = n - len(mismatches)
    report = {"scenarios": len(observed), "excluded": dict(excluded), "compared": n, "exact": exact,
              "fields": {f: {"agree": agree[f], "total": total[f]} for f in FIELDS},
              "by_knob": {k: dict(v) for k, v in sorted(by_knob.items())},
              "stop_pairs": {f"{a} | {b}": c for (a, b), c in stops.most_common()}}
    print(f"compared {n} scenarios ({dict(excluded)} excluded): {exact} agree on every field")
    for f in FIELDS:
        print(f"  {f:<22} {agree[f]:>5}/{total[f]:<5}")
    if args.mismatches:
        Path(args.mismatches).write_text("".join(json.dumps(m) + "\n" for m in mismatches))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
