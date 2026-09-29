"""Analysis results for workflow files, as data and as text.

:func:`scan` runs the model and the verified checker over one workflow and returns plain
data (also the ``--json`` output). :func:`render` formats it for a terminal or a job
summary. Configurations whose envelopes and verdicts coincide are grouped, so an ``issues``
trigger with eighteen activity types prints once per distinct authority.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..lean import VCore
from . import claude_code_action as cca
from .properties import PROPERTIES, Property, verdict, widening
from .repairs import suggest
from .workflow import Workflow, WorkflowError, load

ROLE_TEXT = {"actor": "the triggering account", "issue-author": "the issue author",
             "pr-author": "the pull request author", "commenters": "other commenters",
             "named": "named accounts", "workflow": "the workflow", "unknown": "unknown"}


@dataclass
class Group:
    job: str
    step: int
    uses: str
    event: str
    mode: str | None
    activities: list[str | None]
    config: cca.RunConfig
    policy: dict
    verdicts: list = field(default_factory=list)

    def key(self) -> tuple:
        return (self.job, self.step, self.event, self.mode, self.config.stop_reason,
                json.dumps(self.policy, sort_keys=True))


def ref_supported(ref: str) -> bool:
    """Whether the model's version plausibly describes a ``uses:`` ref (the v1 line)."""
    return ref in ("v1", cca.VERSION) or ref.startswith("v1.") or len(ref) == 40


def analyze(workflow: Workflow, vc: VCore, properties: tuple[Property, ...] = PROPERTIES,
            conservative: tuple[str, ...] = (), text: str | None = None) -> list[Group]:
    """Group configurations and decide each property; with ``text`` (the workflow source),
    also search for single-edit repairs of every violated property."""
    groups: dict[tuple, Group] = {}
    for job, step, rc, env in cca.envelopes(workflow, conservative):
        policy = env.policy() if rc.runs is not False else {}
        g = Group(job.id, step.index, step.uses.raw if step.uses else "", rc.event, rc.mode,
                  [rc.activity], rc, policy)
        if g.key() in groups:
            groups[g.key()].activities.append(rc.activity)
            continue
        if rc.runs is not False:
            g.verdicts = [verdict(vc, policy, p) for p in properties]
            g.assumptions = sorted(env.assumptions)
            g.repairs = {}
            if text is not None:
                for p, v in zip(properties, g.verdicts):
                    if not v.satisfied:
                        g.repairs[p.id] = [o.__dict__ for o in suggest(
                            vc, text, job.id, step.index, rc.event, rc.activity, rc.mode, p)]
            g.notes = list(dict.fromkeys(env.notes))
        groups[g.key()] = g
    return list(groups.values())


def to_json(path: str, groups: list[Group]) -> dict:
    out = []
    for g in groups:
        rc = g.config
        item = {"job": g.job, "step": g.step, "uses": g.uses, "event": g.event,
                "activities": g.activities, "mode": g.mode,
                "runs": rc.runs, "stop_reason": rc.stop_reason}
        if rc.runs is not False:
            item.update({
                "trusted_roles": sorted(rc.trusted_roles),
                "sources": [{"request": s.request, "role": s.role,
                             "outsider": rc.outsider_writes(s.role)} for s in rc.sources],
                "workspace_role": rc.workspace_role,
                "allowed_tools": rc.allowed_tools, "disallowed_tools": rc.disallowed_tools,
                "permission_mode": rc.permission_mode, "mcp_servers": rc.mcp_servers,
                "token_in_git_config": rc.token_in_git_config, "env_scrub": rc.env_scrub,
                "envelope": g.policy, "assumptions": getattr(g, "assumptions", []),
                "notes": getattr(g, "notes", []),
                "verdicts": [v.to_json() for v in g.verdicts],
                "repairs": getattr(g, "repairs", {}),
            })
        out.append(item)
    return {"workflow": path, "model": {"action": cca.ACTION, "version": cca.VERSION, "commit": cca.COMMIT},
            "configurations": out}


def scan(path: str | Path, vc: VCore, conservative: tuple[str, ...] = (), repairs: bool = False) -> dict:
    text = Path(path).read_text()
    try:
        workflow = load(text)
    except WorkflowError as e:
        return {"workflow": str(path), "error": str(e)}
    return to_json(str(path), analyze(workflow, vc, conservative=conservative, text=text if repairs else None))


def _tri(x) -> str:
    return {True: "yes", False: "no", None: "depends on the run"}[x]


def render(result: dict) -> str:
    if "error" in result:
        return f"{result['workflow']}: {result['error']}\n"
    lines = []
    confs = result["configurations"]
    if not confs:
        return f"{result['workflow']}: no {cca.ACTION} step\n"
    by_step: dict[tuple, list] = {}
    for c in confs:
        by_step.setdefault((c["job"], c["step"], c["uses"]), []).append(c)
    for (job, step, uses), cs in by_step.items():
        ref = uses.rsplit("@", 1)[-1] if "@" in uses else ""
        lines.append(f"{result['workflow']}  job {job}, step {step + 1}: {uses}")
        if not ref_supported(ref):
            lines.append(f"  note: the model describes {cca.VERSION}; ref {ref!r} may behave differently")
        for c in cs:
            acts = ", ".join(a for a in c["activities"] if a) or "any"
            head = f"  {c['event']} ({acts})"
            if c["runs"] is False:
                lines.append(f"{head}: does not run Claude ({c['stop_reason']})")
                continue
            lines.append(f"{head}, {c['mode']} mode")
            outsider = [s for s in c["sources"] if s["outsider"] is not False]
            if outsider:
                lines.append("    outsider text in the session: " + "; ".join(
                    f"{s['request'].split('/', 1)[1]} ({ROLE_TEXT.get(s['role'], s['role'])})" for s in outsider))
            else:
                lines.append("    outsider text in the session: none from the trigger")
            lines.append(f"    token in .git/config: {_tri(c['token_in_git_config'])};"
                         f" subprocess env scrub: {_tri(c['env_scrub'])}")
            if c["allowed_tools"]:
                lines.append("    allowed tools: " + ", ".join(c["allowed_tools"]))
            for v in c["verdicts"]:
                if v["satisfied"]:
                    lines.append(f"    {v['property']:<16} satisfied")
                else:
                    ce = v["counterexample"]
                    lines.append(f"    {v['property']:<16} counterexample ({len(ce)} requests)")
                    for i, s in enumerate(ce, 1):
                        tags = "{" + ", ".join(s["tags"]) + "}"
                        lines.append(f"      {i}  {s['request']:<52} {tags}")
                    for r in c.get("repairs", {}).get(v["property"], []):
                        kind = "only removes behaviors" if r["refines"] else "changes behaviors; review"
                        lines.append(f"      repair: {r['text']} (verified; {kind})")
            for n in c.get("notes", []):
                lines.append(f"    note: {n}")
        lines.append("")
    return "\n".join(lines) + "\n"


def diff(old_text: str, new_text: str, vc: VCore) -> list[dict]:
    """Per event and mode, a request sequence the new workflow's agent can run and the old
    one's cannot. Empty when the new workflow refines the old for every configuration."""
    old, new = load(old_text), load(new_text)

    def table(w: Workflow) -> dict[tuple, dict]:
        out = {}
        for job, step, rc, env in cca.envelopes(w):
            if rc.runs is False:
                continue
            out.setdefault((job.id, rc.event, rc.activity, rc.mode), env.policy())
        return out
    before, after = table(old), table(new)
    found = []
    for key, policy in after.items():
        base = before.get(key, {"permit": []})
        w = widening(vc, base, policy)
        if w is not None:
            found.append({"job": key[0], "event": key[1], "activity": key[2], "mode": key[3],
                          "new_configuration": key not in before, "sequence": w})
    return found
