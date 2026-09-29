"""Envelope analysis of the corpus: run the model and the verified checker on every workflow.

    python3 corpus/analyze.py [--target claude-code-action] [--jobs N] [--repairs] [--limit K]

Reads ``data/<target>/workflows.jsonl`` (from ``clean.py``) and the content store, and writes

* ``data/<target>/analysis.jsonl``: one record per distinct workflow file, with its
  configurations, verdicts, abstract counterexample shapes and verified repairs;
* ``data/<target>/analysis_summary.json``: the aggregates used in the write-up (docs/actions.md and the blog post).

Records keep the content hash for joins inside this directory only; ``release.py`` drops
every identifier before anything is published. Only steps whose ref resolves to the ``v1``
family are analyzed with the v1.0.225 model; the others are counted and skipped.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "python"))

from vcore.actions import catalog  # noqa: E402
from vcore.actions import claude_code_action as cca  # noqa: E402
from vcore.actions.properties import PROPERTIES, verdict  # noqa: E402
from vcore.actions.repairs import suggest  # noqa: E402
from vcore.actions.workflow import WorkflowError, load  # noqa: E402
from vcore.lean import VCore  # noqa: E402
from vcore.policy import Pat, parse_path  # noqa: E402

DATA = HERE / "data"
FILES = DATA / "files"


def request_class(req: str) -> str:
    """An abstract name for a counterexample step, for grouping traces."""
    x = parse_path(req)

    def covered(patterns) -> bool:
        return any(Pat.parse(p).covers(x) for p in patterns)
    if req.startswith("Input/"):
        return "outsider-text:" + "/".join(req.split("/")[1:3])
    if req in ("Read/.git/config",) or req.startswith("Grep/.git"):
        return "read:git-credential"
    if req.startswith("mcp/github_comment/"):
        return "sink:tracking-comment"
    if req.startswith("mcp/github_inline_comment/"):
        return "sink:inline-comment"
    if req.startswith(catalog.PUSH_WRAPPER) or req.startswith("Bash/git/push"):
        return "sink:git-push"
    if req.startswith("mcp/github_file_ops/"):
        return "sink:commit-api"
    if req.startswith("mcp/github/"):
        return "sink:github-mcp-write" if req.split("/")[2] in catalog.GITHUB_MCP_WRITE else "read:github-mcp"
    if req.startswith("Bash/gh/"):
        return "bash:gh"
    if req.startswith(("WebFetch", "WebSearch")):
        return "web"
    if req == "Bash/@shell" or covered(catalog.bash(catalog.INTERPRETERS)):
        return "bash:arbitrary-code"
    if covered(catalog.bash(catalog.ENV_PRINTERS)):
        return "bash:print-env"
    if covered(catalog.bash(catalog.FILE_PRINTERS)) or covered(catalog.bash(catalog.GIT_REVEAL)):
        return "bash:print-file"
    if covered(catalog.bash(catalog.EGRESS_COMMANDS)):
        return "bash:network"
    if req.startswith(("Read/", "Glob/", "Grep/", "LS/")):
        return "read:workspace"
    if req.startswith("Output/"):
        return "sink:step-summary"
    return req.split("/")[0]


def tool_categories(rules: list[str]) -> list[str]:
    cats = set()
    for r in rules:
        if r in ("Bash",) or r.startswith("Bash(*"):
            cats.add("bash-unrestricted")
        elif r.startswith("Bash(gh"):
            cats.add("bash-gh")
        elif r.startswith("Bash(git") or "git-push.sh" in r:
            cats.add("bash-git")
        elif r.startswith("Bash("):
            cats.add("bash-other")
        elif r.startswith(("WebFetch", "WebSearch")):
            cats.add("web")
        elif r.startswith("mcp__github__") or r == "mcp__github":
            cats.add("github-mcp")
        elif r.startswith("mcp__github_"):
            cats.add("action-mcp")
        elif r.startswith("mcp__"):
            cats.add("other-mcp")
        elif r in ("Edit", "Write", "MultiEdit", "NotebookEdit") or r.startswith(("Edit(", "Write(")):
            cats.add("edit")
    return sorted(cats)


_vc: VCore | None = None


def _worker_init() -> None:
    global _vc
    _vc = VCore()


def analyze_file(args: tuple[dict, bool, bool]) -> dict:
    rec, repairs, trust_unknown = args
    assert _vc is not None
    t0 = time.perf_counter()
    text = (FILES / rec["content_sha256"][:2] / rec["content_sha256"]).read_text(errors="replace")
    out = {"content_sha256": rec["content_sha256"], "copies": rec["copies"], "stars": rec["stars"],
           "steps": [], "error": None}
    try:
        w = load(text)
    except WorkflowError as e:
        out["error"] = f"workflow: {e}"
        return out
    families = {(s["job"], s["step"]): s for s in rec["agent_steps"]}
    cache: dict[str, dict] = {}
    for job, step in w.steps_using(cca.ACTION):
        meta = families.get((job.id, step.index), {})
        srec = {"job_index": [j.id for j in w.jobs].index(job.id), "ref_class": meta.get("ref_class"),
                "version": meta.get("version"), "family": meta.get("family"), "configurations": []}
        out["steps"].append(srec)
        if meta.get("family") != "v1":
            srec["skipped"] = "not the v1 family"
            continue
        try:
            confs = cca.configurations(w, job, step)
        except Exception as e:  # a model bug must not stop the corpus run; it is reported
            srec["skipped"] = f"model error: {type(e).__name__}: {e}"
            continue
        for rc in confs:
            c = {"event": rc.event, "activity": rc.activity, "mode": rc.mode, "runs": rc.runs,
                 "stop_reason": rc.stop_reason}
            if rc.runs is not False:
                env = cca.envelope(rc, w, job, step, trust_unknown=trust_unknown)
                policy = env.policy()
                key = json.dumps(policy, sort_keys=True)
                if key not in cache:
                    vs = {p.id: verdict(_vc, policy, p) for p in PROPERTIES}
                    cache[key] = {pid: {"satisfied": v.satisfied,
                                        "shape": None if v.satisfied else [request_class(s.request) for s in v.counterexample],
                                        "length": None if v.satisfied else len(v.counterexample)}
                                  for pid, v in vs.items()}
                outsider_sources = sorted({s.request.split("/", 2)[1] + "/" + s.request.split("/")[2]
                                           for s in rc.sources if rc.outsider_writes(s.role) is not False})
                c.update({
                    "actor_outsider": "actor" not in rc.trusted_roles,
                    "outsider_sources": outsider_sources,
                    "workspace_outsider": rc.outsider_writes(rc.workspace_role) is not False,
                    "token_in_git_config": rc.token_in_git_config, "env_scrub": rc.env_scrub,
                    "permission_mode": rc.permission_mode, "skip_permissions": rc.skip_permissions, "tool_categories": tool_categories(rc.allowed_tools),
                    "n_allowed_tools": len(rc.allowed_tools), "verdicts": cache[key],
                    "if_restricted": any(n.startswith("if: condition restricts") for n in rc.notes),
                })
                if repairs:
                    c["repairs"] = {}
                    for p in PROPERTIES:
                        if not cache[key][p.id]["satisfied"]:
                            c["repairs"][p.id] = [(o.repair, o.refines) for o in suggest(
                                _vc, text, job.id, step.index, rc.event, rc.activity, rc.mode, p)]
            srec["configurations"].append(c)
    out["seconds"] = round(time.perf_counter() - t0, 4)
    return out


def summarize(results: list[dict]) -> dict:
    files = len(results)
    s = Counter()
    shapes = {p.id: Counter() for p in PROPERTIES}
    repairs = {p.id: Counter() for p in PROPERTIES}
    stops = Counter()
    times = []
    for r in results:
        if r["error"]:
            s["files_error"] += 1
            continue
        times.append(r.get("seconds", 0))
        steps_v1 = [st for st in r["steps"] if not st.get("skipped")]
        if not steps_v1:
            s["files_no_v1_step"] += 1
            continue
        s["files_v1"] += 1
        confs = [c for st in steps_v1 for c in st["configurations"]]
        running = [c for c in confs if c["runs"] is not False]
        for c in confs:
            if c["runs"] is False:
                stops[c["stop_reason"]] += 1
        s["configurations"] += len(confs)
        s["configurations_running"] += len(running)
        if not running:
            s["files_never_run"] += 1
            continue
        s["files_running"] += 1
        flags = {
            "any_outsider_text": any(c["outsider_sources"] or c["workspace_outsider"] for c in running),
            "any_actor_outsider": any(c["actor_outsider"] for c in running),
            "any_token_in_git_config": any(c["token_in_git_config"] is True for c in running),
            "any_token_in_git_config_possibly": any(c["token_in_git_config"] is not False for c in running),
            "any_tag_mode": any(c["mode"] == "tag" for c in running),
            "any_agent_mode": any(c["mode"] == "agent" for c in running),
            "any_bash_unrestricted": any("bash-unrestricted" in c["tool_categories"] for c in running),
            "any_bypass": any(c["permission_mode"] == "bypassPermissions" or c.get("skip_permissions") for c in running),
        }
        for p in PROPERTIES:
            viol = [c for c in running if not c["verdicts"][p.id]["satisfied"]]
            flags[f"{p.id}_violated"] = bool(viol)
            flags[f"{p.id}_violated_outsider_actor"] = any(c["actor_outsider"] for c in viol)
            for c in viol:
                shapes[p.id][" > ".join(c["verdicts"][p.id]["shape"])] += 1
                for rid, refines in c.get("repairs", {}).get(p.id, []):
                    repairs[p.id][f"{rid}{'' if refines else ' (non-refining)'}"] += 1
                if "repairs" in c:
                    repairs[p.id]["_configs_searched"] += 1
                    repairs[p.id]["_configs_repaired_refining"] += any(ref for _, ref in c["repairs"].get(p.id, []))
            flags[f"{p.id}_satisfied_all"] = not viol
        for k, v in flags.items():
            s[k] += bool(v)
    times.sort()
    return {"files": files, "counts": dict(s), "stop_reasons": dict(stops.most_common()),
            "shapes": {k: dict(v.most_common(15)) for k, v in shapes.items()},
            "repairs": {k: dict(v.most_common()) for k, v in repairs.items()},
            "seconds_per_file": {"median": times[len(times) // 2] if times else None,
                                 "p95": times[int(len(times) * 0.95)] if times else None,
                                 "total": round(sum(times), 1)}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="claude-code-action")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--repairs", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--trust-unknown", action="store_true",
                    help="sensitivity run: text of unknown authorship is trusted (writes *_trust_unknown files)")
    args = ap.parse_args()
    d = DATA / args.target
    recs = [json.loads(l) for l in (d / "workflows.jsonl").read_text().splitlines()]
    if args.limit:
        recs = recs[:args.limit]
    t0 = time.time()
    with ProcessPoolExecutor(args.jobs, initializer=_worker_init) as ex:
        results = list(ex.map(analyze_file, [(r, args.repairs, args.trust_unknown) for r in recs], chunksize=4))
    suffix = "_trust_unknown" if args.trust_unknown else ""
    with (d / f"analysis{suffix}.jsonl").open("w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    summary = summarize(results)
    summary["wall_seconds"] = round(time.time() - t0, 1)
    summary["model"] = {"action": cca.ACTION, "version": cca.VERSION, "commit": cca.COMMIT}
    (d / f"analysis{suffix}_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("files", "counts", "seconds_per_file", "wall_seconds")}, indent=2))


if __name__ == "__main__":
    main()
