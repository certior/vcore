"""Download the workflow files found by ``collect.py`` and the metadata of their repositories.

Files are read from ``raw.githubusercontent.com`` at the commit the search result points to,
so a file is the version that was indexed, not whatever the branch holds today. Each
download is checked against the git blob id the search reported (``sha1("blob <n>\\0" +
content)``) and stored content-addressed under ``data/files/<sha256[:2]>/<sha256>``.

Repository metadata comes from the GraphQL API in batches.

Outputs (both resumable; an existing ``ok`` line is never fetched again):

* ``data/<target>/index.jsonl``: one line per distinct (repo, path), with ``sha256``,
  ``size``, ``blob_verified`` and ``status``.
* ``data/repos.jsonl``: one line per repository, shared by every target.

Usage::

    python3 corpus/fetch.py                       # every target with hits
    python3 corpus/fetch.py claude-code-action
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from collect import DATA, TARGETS, log

FILES = DATA / "files"
REPOS = DATA / "repos.jsonl"
WORKERS = 8
GRAPHQL_BATCH = 50


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git_blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def token() -> str:
    return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True,
                          check=True).stdout.strip()


def unique_hits(target: str) -> list[dict]:
    """One hit per (repo, path): the most recently retrieved."""
    latest: dict[tuple[str, str], dict] = {}
    for h in read_jsonl(DATA / target / "hits.jsonl"):
        key = (h["repo"], h["path"])
        if key not in latest or h["retrieved_at"] >= latest[key]["retrieved_at"]:
            latest[key] = h
    return sorted(latest.values(), key=lambda h: (h["repo"], h["path"]))


def download(hit: dict, auth: str) -> dict:
    path = urllib.parse.quote(hit["path"])
    url = f"https://raw.githubusercontent.com/{hit['repo']}/{hit['commit_sha']}/{path}"
    rec = {k: hit[k] for k in ("repo", "repo_id", "path", "commit_sha", "blob_sha")}
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {auth}",
                                                       "User-Agent": "vcore-corpus"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                content = resp.read()
            digest = hashlib.sha256(content).hexdigest()
            dest = FILES / digest[:2] / digest
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_suffix(f".tmp{threading.get_ident()}")
                tmp.write_bytes(content)
                tmp.replace(dest)
            rec.update(sha256=digest, size=len(content),
                       blob_verified=git_blob_sha(content) == hit["blob_sha"],
                       status="ok", fetched_at=now())
            return rec
        except urllib.error.HTTPError as e:
            if e.code == 404:
                rec.update(status="not_found", fetched_at=now())
                return rec
            if e.code in (403, 429) or e.code >= 500:
                pause = float(e.headers.get("Retry-After") or min(300, 10 * 2 ** attempt))
                time.sleep(pause)
                continue
            rec.update(status=f"http_{e.code}", fetched_at=now())
            return rec
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(min(300, 5 * 2 ** attempt))
    rec.update(status="failed", fetched_at=now())
    return rec


def fetch_files(target: str, auth: str) -> None:
    index_path = DATA / target / "index.jsonl"
    done = {(r["repo"], r["path"]) for r in read_jsonl(index_path) if r["status"] in ("ok", "not_found")}
    todo = [h for h in unique_hits(target) if (h["repo"], h["path"]) not in done]
    log(f"[{target}] fetch: {len(done)} already fetched, {len(todo)} to go")
    lock = threading.Lock()
    n = 0
    with ThreadPoolExecutor(WORKERS) as pool, index_path.open("a") as out:
        for fut in as_completed(pool.submit(download, h, auth) for h in todo):
            rec = fut.result()
            with lock:
                out.write(json.dumps(rec) + "\n")
                out.flush()
                n += 1
                if n % 500 == 0:
                    log(f"[{target}] fetched {n}/{len(todo)}")
    log(f"[{target}] fetch done ({n} new)")


REPO_FIELDS = ("stargazerCount isFork isArchived isPrivate pushedAt createdAt "
               "licenseInfo { spdxId } primaryLanguage { name } owner { __typename }")


def graphql(query: str, auth: str) -> dict:
    body = json.dumps({"query": query}).encode()
    for attempt in range(8):
        req = urllib.request.Request("https://api.github.com/graphql", data=body, headers={
            "Authorization": f"Bearer {auth}", "User-Agent": "vcore-corpus",
            "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) or e.code >= 500:
                reset = e.headers.get("X-RateLimit-Reset")
                if e.headers.get("X-RateLimit-Remaining") == "0" and reset:
                    pause = max(0.0, int(reset) - time.time()) + 1
                else:
                    pause = float(e.headers.get("Retry-After") or min(600, 15 * 2 ** attempt))
                log(f"  graphql HTTP {e.code}; retry in {pause:.0f}s")
                time.sleep(pause)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            log(f"  graphql network error {e}")
            time.sleep(min(600, 15 * 2 ** attempt))
    raise RuntimeError("graphql: giving up")


def fetch_repos(targets: list[str], auth: str) -> None:
    known = {r["repo"] for r in read_jsonl(REPOS)}
    wanted = sorted({h["repo"] for t in targets for h in unique_hits(t)} - known)
    log(f"repos: {len(known)} known, {len(wanted)} to query")
    with REPOS.open("a") as out:
        for i in range(0, len(wanted), GRAPHQL_BATCH):
            batch = wanted[i:i + GRAPHQL_BATCH]
            parts = []
            for j, full in enumerate(batch):
                owner, name = full.split("/", 1)
                parts.append(f"r{j}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ {REPO_FIELDS} }}")
            resp = graphql("query { " + " ".join(parts) + " }", auth)
            data = resp.get("data")
            if not data:
                # A failed query (not a missing repository, which yields a null alias): do not
                # record anything, so a later run retries this batch.
                log(f"repos: batch at {i} failed: {json.dumps(resp.get('errors'))[:300]}")
                continue
            for j, full in enumerate(batch):
                node = data.get(f"r{j}")
                if node is None:
                    rec = {"repo": full, "status": "unavailable"}
                else:
                    rec = {"repo": full, "status": "ok",
                           "stars": node["stargazerCount"], "is_fork": node["isFork"],
                           "is_archived": node["isArchived"], "is_private": node["isPrivate"],
                           "pushed_at": node["pushedAt"], "created_at": node["createdAt"],
                           "license": (node.get("licenseInfo") or {}).get("spdxId"),
                           "language": (node.get("primaryLanguage") or {}).get("name"),
                           "owner_type": (node.get("owner") or {}).get("__typename")}
                rec["queried_at"] = now()
                out.write(json.dumps(rec) + "\n")
            out.flush()
            if (i // GRAPHQL_BATCH) % 20 == 0:
                log(f"repos: {i + len(batch)}/{len(wanted)}")
    log("repos: done")


def main(argv: list[str]) -> None:
    targets = argv or [t for t in TARGETS if (DATA / t / "hits.jsonl").exists()]
    auth = token()
    for t in targets:
        fetch_files(t, auth)
    fetch_repos(targets, auth)


if __name__ == "__main__":
    main(sys.argv[1:])
