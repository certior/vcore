"""Derived labels for the benchmark, and the release directory.

    python3 bench/label.py            # writes bench/release/

For every scenario, the release pairs what the action did (the oracle's observation) with
what the model derives for the same run: the authority envelope of the concrete
configuration and the verdicts of the verified checker for P1 and P2. A scanner can be
scored against the observations alone, the verdicts alone, or both.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "python"))

from vcore.actions import claude_code_action as cca  # noqa: E402
from vcore.actions.predict import predict_run  # noqa: E402
from vcore.actions.properties import PROPERTIES, verdict  # noqa: E402
from vcore.lean import VCore  # noqa: E402

OUT = HERE / "release"


def main() -> None:
    scenarios = [json.loads(l) for l in (HERE / "scenarios" / "generated.jsonl").read_text().splitlines()]
    observed = {o["id"]: o for o in map(json.loads, (HERE / "observations" / "generated.jsonl").read_text().splitlines())}
    OUT.mkdir(exist_ok=True)
    counts = {"scenarios": 0, "runs": 0, **{f"{p.id}_violated": 0 for p in PROPERTIES}}
    cache: dict[str, dict] = {}
    with VCore() as vc, (OUT / "scenarios.jsonl").open("w") as fs, (OUT / "observations.jsonl").open("w") as fo, \
            (OUT / "labels.jsonl").open("w") as fl:
        for sc in scenarios:
            seed, variant = sc["id"].split("|")[:2]
            knob, _, value = variant.partition("=")
            fs.write(json.dumps({**sc, "seed": seed, "knob": knob, "knob_value": value or None}) + "\n")
            if sc["id"] in observed:
                o = dict(observed[sc["id"]])
                o.pop("extra", None)  # diagnostics, host-specific paths
                fo.write(json.dumps(o) + "\n")
            obs, run = predict_run(sc)
            label = {"id": sc["id"], "model_runs": obs["runs"], "model_stop_reason": obs["stop_reason"],
                     "envelope": None, "verdicts": None, "assumptions": None}
            counts["scenarios"] += 1
            if run is not None:
                rc, w, job, step = run
                env = cca.envelope(rc, w, job, step)
                policy = env.policy()
                key = json.dumps(policy, sort_keys=True)
                if key not in cache:
                    cache[key] = {p.id: verdict(vc, policy, p).to_json() for p in PROPERTIES}
                label.update({"envelope": policy, "verdicts": cache[key], "assumptions": sorted(env.assumptions)})
                counts["runs"] += 1
                for p in PROPERTIES:
                    counts[f"{p.id}_violated"] += not cache[key][p.id]["satisfied"]
            fl.write(json.dumps(label) + "\n")
    for name in ("fidelity.json", "sensitivity.json", "fidelity-log.md"):
        src = HERE / "results" / name
        if src.exists():
            shutil.copy(src, OUT / name)
    shutil.copy(HERE / "DATASET_CARD.md", OUT / "README.md")
    shutil.copytree(HERE / "schema", OUT / "schema", dirs_exist_ok=True)
    shutil.copytree(HERE / "seeds", OUT / "seeds", dirs_exist_ok=True)
    (OUT / "counts.json").write_text(json.dumps({**counts, "envelopes_distinct": len(cache),
                                                 "action": cca.ACTION, "commit": cca.COMMIT}, indent=2) + "\n")
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
