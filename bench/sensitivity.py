"""Knob sensitivity: which single configuration changes flip a verdict.

For every seed workflow and every mutant from ``generate.py`` (one knob changed), compare the
verdicts of each (event, activity, mode) configuration present in both. Reports, per knob
value, how many configurations go from satisfied to violated and back, per property, and
how many configurations appear or disappear (the agent starts or stops running).

    python3 bench/sensitivity.py > bench/results/sensitivity.json
"""

from __future__ import annotations

import copy
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "python"))
sys.path.insert(0, str(HERE))

from generate import KNOBS, SEEDS, agent_step, dump, load as yload  # noqa: E402
from vcore.actions import claude_code_action as cca  # noqa: E402
from vcore.actions.properties import PROPERTIES, verdict  # noqa: E402
from vcore.actions.workflow import load  # noqa: E402
from vcore.lean import VCore  # noqa: E402


def verdicts(vc: VCore, text: str, cache: dict) -> dict[tuple, dict[str, bool]]:
    out = {}
    w = load(text)
    for job, step, rc, env in cca.envelopes(w):
        if rc.runs is False:
            continue
        policy = env.policy()
        key = json.dumps(policy, sort_keys=True)
        if key not in cache:
            cache[key] = {p.id: verdict(vc, policy, p).satisfied for p in PROPERTIES}
        out[(job.id, step.index, rc.event, rc.activity, rc.mode)] = cache[key]
    return out


def main() -> None:
    table: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    cache: dict = {}
    with VCore() as vc:
        for seed in sorted(SEEDS.glob("*.yml")):
            text = seed.read_text()
            doc = yload(text)
            if not agent_step(doc):
                continue
            base = verdicts(vc, text, cache)
            for knob, options in KNOBS.items():
                for label, edit in options:
                    d = copy.deepcopy(doc)
                    job, _, step = agent_step(d)
                    edit(d, job, step)
                    mutant = dump(d)
                    if yload(mutant) == doc:
                        continue
                    after = verdicts(vc, mutant, cache)
                    row = table[f"{knob}={label}"]
                    row["seeds"] += 1
                    row["configs_added"] += len(after.keys() - base.keys())
                    row["configs_removed"] += len(base.keys() - after.keys())
                    for k in base.keys() & after.keys():
                        row["configs_compared"] += 1
                        for p in PROPERTIES:
                            b, a = base[k][p.id], after[k][p.id]
                            if b and not a:
                                row[f"{p.id}:sat->viol"] += 1
                            elif a and not b:
                                row[f"{p.id}:viol->sat"] += 1
    print(json.dumps({k: dict(v) for k, v in sorted(table.items())}, indent=2))


if __name__ == "__main__":
    main()
