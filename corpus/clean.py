"""Filter and deduplicate the fetched files into the analysis corpus.

Each stage removes files for one stated reason, and the counts after every stage are
written to ``data/<target>/manifest.json`` (the funnel reported in the write-up):

 1. ``search_reported``   results code search reported, summed over shards
 2. ``hits_unique``       distinct (repository, path) pairs returned
 3. ``fetched``           downloaded at the indexed commit
 4. ``blob_verified``     content matches the git blob id code search reported
 5. ``root_workflow``     path is ``.github/workflows/<file>.yml|yaml`` (GitHub only runs these)
 6. ``repo_public``       repository metadata available and the repository is public
 7. ``not_fork``          repository is not a fork
 8. ``not_archived``      repository is not archived
 9. ``yaml_valid``        the file parses to a mapping with ``jobs``
10. ``uses_action``       some job step has ``uses: <action>@<ref>`` (the action itself,
                          not a sub-path action from the same repository)
11. ``distinct_content``  one representative per exact content (sha256), with a copy count

The representative of a group of identical files is the copy in the repository with the
most stars (ties: repository name), so repository metadata stays meaningful.

Output: ``data/<target>/workflows.jsonl`` (one line per distinct file; internal, contains
repository names) and ``data/<target>/manifest.json``.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

from collect import DATA, TARGETS, log
from common import ROOT_WORKFLOW, SHA_REF as SHA, agent_steps, load_workflow, ref_class, split_uses, uses_in_repo
from fetch import FILES, REPOS, read_jsonl


VERSION = re.compile(r"^v(\d+)(?:\.(\d+))?(?:\.(\d+))?$")


def action_tags(target: str) -> dict[str, list[str]]:
    """Commit id -> tag names of the action's repository (``git ls-remote``, no API quota).

    Annotated tags are peeled to the commit they point to. The listing is cached in
    ``data/<target>/tags.json`` with its retrieval time, so a rerun resolves pins the same way.
    """
    cache = DATA / target / "tags.json"
    if cache.exists():
        return json.loads(cache.read_text())["commits"]
    url = f"https://github.com/{TARGETS[target]}"
    out = subprocess.run(["git", "ls-remote", "--tags", url], capture_output=True, text=True,
                         check=True, timeout=120).stdout
    direct: dict[str, str] = {}
    peeled: dict[str, str] = {}
    for line in out.splitlines():
        sha, ref = line.split("\t")
        name = ref.removeprefix("refs/tags/")
        if name.endswith("^{}"):
            peeled[name[:-3]] = sha
        else:
            direct[name] = sha
    commits: dict[str, list[str]] = defaultdict(list)
    for name, sha in direct.items():
        commits[peeled.get(name, sha)].append(name)
    cache.write_text(json.dumps({"url": url, "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                 "commits": commits}, indent=1) + "\n")
    return commits


def version_family(name: str) -> str:
    m = VERSION.match(name)
    if m:
        return f"v{m.group(1)}"
    return name if name in ("beta", "main", "master") else "other"


def resolve_ref(ref: str | None, commits: dict[str, list[str]]) -> tuple[str | None, str]:
    """(version, family) of a ``uses:`` reference. A commit pin resolves to the most specific
    tag at that commit; a pin to an untagged commit stays unresolved."""
    if ref is None:
        return None, "none"
    if SHA.match(ref):
        names = commits.get(ref.lower(), [])
        if not names:
            return None, "untagged commit"
        best = max(names, key=lambda n: (n.count("."), n))
        return best, version_family(best)
    return ref, version_family(ref)


def normalized_hash(doc: object) -> str:
    """Hash of the parsed document: equal for files that differ only in comments, quoting,
    key order or whitespace."""
    return hashlib.sha256(json.dumps(doc, sort_keys=True, default=str).encode()).hexdigest()


def clean(target: str) -> dict:
    action = TARGETS[target]
    state_path = DATA / target / "collect_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    funnel: dict[str, int] = {}
    dropped: dict[str, Counter] = defaultdict(Counter)

    funnel["search_reported"] = sum(l["total_count"] for l in state.get("leaves", {}).values())
    hits = {(h["repo"], h["path"]) for h in read_jsonl(DATA / target / "hits.jsonl")}
    funnel["hits_unique"] = len(hits)

    index = {}
    for rec in read_jsonl(DATA / target / "index.jsonl"):
        index[(rec["repo"], rec["path"])] = rec  # later lines win
    rows = [r for r in index.values() if (r["repo"], r["path"]) in hits]
    missing = len(hits) - len(rows)
    if missing:
        dropped["fetched"]["not attempted"] += missing
    for r in rows:
        if r["status"] != "ok":
            dropped["fetched"][r["status"]] += 1
    rows = [r for r in rows if r["status"] == "ok"]
    funnel["fetched"] = len(rows)

    dropped["blob_verified"]["blob id mismatch"] = sum(1 for r in rows if not r["blob_verified"])
    rows = [r for r in rows if r["blob_verified"]]
    funnel["blob_verified"] = len(rows)

    for r in rows:
        if not ROOT_WORKFLOW.match(r["path"]):
            dropped["root_workflow"]["nested .github/workflows (template or vendored)" if ".github/workflows/" in r["path"]
                                     else "not a .yml/.yaml file"] += 1
    rows = [r for r in rows if ROOT_WORKFLOW.match(r["path"])]
    funnel["root_workflow"] = len(rows)

    repos = {}
    for rec in read_jsonl(REPOS):
        repos[rec["repo"]] = rec
    keep = []
    for r in rows:
        meta = repos.get(r["repo"])
        if meta is None:
            dropped["repo_public"]["metadata not fetched"] += 1
        elif meta["status"] != "ok":
            dropped["repo_public"]["repository unavailable"] += 1
        elif meta["is_private"]:
            dropped["repo_public"]["private"] += 1
        else:
            r["meta"] = meta
            keep.append(r)
    rows = keep
    funnel["repo_public"] = len(rows)

    dropped["not_fork"]["fork"] = sum(1 for r in rows if r["meta"]["is_fork"])
    rows = [r for r in rows if not r["meta"]["is_fork"]]
    funnel["not_fork"] = len(rows)

    dropped["not_archived"]["archived"] = sum(1 for r in rows if r["meta"]["is_archived"])
    rows = [r for r in rows if not r["meta"]["is_archived"]]
    funnel["not_archived"] = len(rows)

    keep = []
    for r in rows:
        text = (FILES / r["sha256"][:2] / r["sha256"]).read_bytes().decode("utf-8", errors="replace")
        try:
            doc = load_workflow(text)
        except Exception as e:  # noqa: BLE001 - any YAML error drops the file
            dropped["yaml_valid"][f"YAML error: {type(e).__name__}"] += 1
            continue
        if not isinstance(doc, dict) or not isinstance(doc.get("jobs"), dict):
            dropped["yaml_valid"]["no jobs mapping"] += 1
            continue
        r["doc"] = doc
        keep.append(r)
    rows = keep
    funnel["yaml_valid"] = len(rows)

    commits = action_tags(target)
    keep = []
    for r in rows:
        found = list(agent_steps(r["doc"], action))
        if not found:
            other = uses_in_repo(r["doc"], action)
            if other:
                dropped["uses_action"]["only sub-path actions or reusable workflows from the repo"] += 1
            else:
                dropped["uses_action"]["action named outside a step `uses:` (comment, string, docs)"] += 1
            continue
        r["steps"] = []
        for job_id, _, i, step in found:
            ref = split_uses(step["uses"])[2]
            version, family = resolve_ref(ref, commits)
            r["steps"].append({"job": job_id, "step": i, "uses": step["uses"], "ref": ref,
                               "ref_class": ref_class(ref), "version": version, "family": family})
        keep.append(r)
    rows = keep
    funnel["uses_action"] = len(rows)

    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r["sha256"]].append(r)
    norm_sizes = Counter(normalized_hash(g[0]["doc"]) for g in groups.values() for _ in g)
    reps = []
    for sha, group in groups.items():
        group.sort(key=lambda r: (-(r["meta"]["stars"] or 0), r["repo"], r["path"]))
        rep = group[0]
        norm = normalized_hash(rep["doc"])
        reps.append({
            "content_sha256": sha,
            "repo": rep["repo"],
            "path": rep["path"],
            "commit_sha": rep["commit_sha"],
            "blob_sha": rep["blob_sha"],
            "size": rep["size"],
            "copies": len(group),
            "copy_repos": len({r["repo"] for r in group}),
            "normalized_sha256": norm,
            "normalized_copies": norm_sizes[norm],
            "stars": rep["meta"]["stars"],
            "stars_max_copy": max(r["meta"]["stars"] or 0 for r in group),
            "created_at": rep["meta"]["created_at"],
            "pushed_at": rep["meta"]["pushed_at"],
            "license": rep["meta"]["license"],
            "language": rep["meta"]["language"],
            "owner_type": rep["meta"]["owner_type"],
            "agent_steps": rep["steps"],
        })
    reps.sort(key=lambda r: r["content_sha256"])
    funnel["distinct_content"] = len(reps)
    funnel["distinct_normalized"] = len({r["normalized_sha256"] for r in reps})
    funnel["distinct_repos"] = len({r["repo"] for r in rows})

    out = DATA / target / "workflows.jsonl"
    with out.open("w") as f:
        for rec in reps:
            f.write(json.dumps(rec) + "\n")

    manifest = {
        "target": target,
        "action": action,
        "search_query": f'"{action}" path:.github/workflows language:YAML size:LO..HI',
        "collection_started_at": state.get("started_at"),
        "collection_finished_at": state.get("finished_at"),
        "search_shards": len(state.get("leaves", {})),
        "search_incomplete_shards": sum(1 for l in state.get("leaves", {}).values() if l.get("incomplete")),
        "unsplittable_shards": state.get("unsplittable", []),
        "cleaned_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "funnel": funnel,
        "dropped": {stage: dict(c) for stage, c in dropped.items() if sum(c.values())},
    }
    (DATA / target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    log(f"[{target}] clean: " + ", ".join(f"{k}={v}" for k, v in funnel.items()))
    return manifest


def main(argv: list[str]) -> None:
    targets = argv or [t for t in TARGETS if (DATA / t / "index.jsonl").exists()]
    for t in targets:
        clean(t)


if __name__ == "__main__":
    main(sys.argv[1:])
