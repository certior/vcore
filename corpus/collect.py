"""Find public workflow files that use an AI agent action, with GitHub code search.

Code search returns at most 1000 results per query and allows 10 requests per minute. The
query for a target is therefore split into shards by file size (``size:LO..HI``); a shard
whose ``total_count`` exceeds 1000 is split in half until every shard fits. Each shard is
then read page by page (100 results per page, at most 10 pages).

Everything is resumable: the shard queue and the pages already read are checkpointed in
``data/<target>/collect_state.json``, and hits are appended to ``data/<target>/hits.jsonl``
(one line per result; duplicates across pages are removed later by ``fetch.py``).

Usage::

    python3 corpus/collect.py                       # every target, in order
    python3 corpus/collect.py claude-code-action    # one target

Credentials come from ``gh auth token``; nothing is written to disk.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
LOG = DATA / "collect.log"

TARGETS = {
    "claude-code-action": "anthropics/claude-code-action",
    "run-gemini-cli": "google-github-actions/run-gemini-cli",
    "codex-action": "openai/codex-action",
}

MAX_RESULTS = 1000   # code search never returns more than this for one query
PER_PAGE = 100
MIN_INTERVAL = 6.5   # seconds between code search requests (limit: 10 per minute)
MAX_SIZE = 400_000   # code search does not index files larger than 384 KB
HTML_URL = re.compile(r"^https://github\.com/(?P<repo>[^/]+/[^/]+)/blob/(?P<commit>[0-9a-f]{40})/(?P<path>.+)$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg: str) -> None:
    line = f"{now()} {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def query_for(action: str, lo: int, hi: int) -> str:
    return f'"{action}" path:.github/workflows language:YAML size:{lo}..{hi}'


class Search:
    """Rate-limited client for ``GET /search/code``."""

    def __init__(self) -> None:
        self.token = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True,
                                    check=True).stdout.strip()
        self.last = 0.0

    def get(self, q: str, page: int, per_page: int = PER_PAGE) -> dict:
        params = urllib.parse.urlencode({"q": q, "per_page": per_page, "page": page})
        url = f"https://api.github.com/search/code?{params}"
        attempt = 0
        while True:
            wait = self.last + MIN_INTERVAL - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.last = time.monotonic()
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "vcore-corpus",
            })
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    remaining = resp.headers.get("X-RateLimit-Remaining")
                    reset = resp.headers.get("X-RateLimit-Reset")
                    body = json.load(resp)
                    if remaining == "0" and reset:
                        pause = max(0.0, int(reset) - time.time()) + 1
                        log(f"  rate limit exhausted, sleeping {pause:.0f}s")
                        time.sleep(pause)
                    return body
            except urllib.error.HTTPError as e:
                attempt += 1
                if e.code in (403, 429) or e.code >= 500:
                    retry_after = e.headers.get("Retry-After")
                    reset = e.headers.get("X-RateLimit-Reset")
                    if retry_after:
                        pause = float(retry_after) + 1
                    elif reset and e.headers.get("X-RateLimit-Remaining") == "0":
                        pause = max(0.0, int(reset) - time.time()) + 1
                    else:
                        pause = min(600.0, 30.0 * 2 ** (attempt - 1))
                    log(f"  HTTP {e.code} on page {page}; retry {attempt} in {pause:.0f}s")
                    time.sleep(pause)
                    continue
                if e.code == 422:  # e.g. page beyond the 1000-result window
                    return {"total_count": 0, "incomplete_results": True, "items": []}
                raise
            except (urllib.error.URLError, TimeoutError) as e:
                attempt += 1
                pause = min(600.0, 15.0 * 2 ** (attempt - 1))
                log(f"  network error {e}; retry {attempt} in {pause:.0f}s")
                time.sleep(pause)


def hit_record(item: dict, shard: str, retrieved: str) -> dict | None:
    m = HTML_URL.match(item.get("html_url", ""))
    if not m:
        return None
    return {
        "repo": item["repository"]["full_name"],
        "repo_id": item["repository"]["id"],
        "path": item["path"],
        "blob_sha": item["sha"],
        "commit_sha": m.group("commit"),
        "shard": shard,
        "retrieved_at": retrieved,
    }


def collect(target: str) -> None:
    action = TARGETS[target]
    out = DATA / target
    out.mkdir(parents=True, exist_ok=True)
    state_path = out / "collect_state.json"
    hits_path = out / "hits.jsonl"

    if state_path.exists():
        state = json.loads(state_path.read_text())
    else:
        state = {"target": target, "action": action, "started_at": now(), "finished_at": None,
                 "pending": [[0, MAX_SIZE]], "leaves": {}, "unsplittable": []}

    def save() -> None:
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1))
        tmp.replace(state_path)

    search = Search()
    log(f"[{target}] resume: {len(state['pending'])} shards pending, {len(state['leaves'])} leaves known")

    # Phase 1: split shards until each has at most MAX_RESULTS results. The first page of a
    # leaf is kept, so a leaf costs no extra request beyond its pages.
    while state["pending"]:
        lo, hi = state["pending"][0]
        q = query_for(action, lo, hi)
        body = search.get(q, page=1)
        total = body.get("total_count", 0)
        if total > MAX_RESULTS and hi > lo:
            mid = (lo + hi) // 2
            state["pending"][0:1] = [[lo, mid], [mid + 1, hi]]
            log(f"[{target}] size:{lo}..{hi} has {total} results; split")
        else:
            if total > MAX_RESULTS:
                state["unsplittable"].append([lo, hi, total])
                log(f"[{target}] WARNING size:{lo}..{hi} has {total} > {MAX_RESULTS} results and cannot be split by size")
            key = f"{lo}..{hi}"
            retrieved = now()
            with hits_path.open("a") as f:
                for item in body.get("items", []):
                    rec = hit_record(item, key, retrieved)
                    if rec:
                        f.write(json.dumps(rec) + "\n")
            pages = min(-(-total // PER_PAGE), MAX_RESULTS // PER_PAGE)
            state["leaves"][key] = {"total_count": total, "pages": max(pages, 1), "done": [1],
                                    "incomplete": bool(body.get("incomplete_results"))}
            state["pending"].pop(0)
            log(f"[{target}] leaf size:{key}: {total} results, {pages} pages")
        save()

    # Phase 2: read the remaining pages of every leaf.
    for key, leaf in state["leaves"].items():
        lo, hi = (int(x) for x in key.split(".."))
        q = query_for(action, lo, hi)
        for page in range(1, leaf["pages"] + 1):
            if page in leaf["done"]:
                continue
            body = search.get(q, page=page)
            retrieved = now()
            with hits_path.open("a") as f:
                for item in body.get("items", []):
                    rec = hit_record(item, key, retrieved)
                    if rec:
                        f.write(json.dumps(rec) + "\n")
            leaf["done"].append(page)
            leaf["incomplete"] = leaf["incomplete"] or bool(body.get("incomplete_results"))
            save()
            log(f"[{target}] size:{key} page {page}/{leaf['pages']}: {len(body.get('items', []))} items")

    if not state["finished_at"]:
        state["finished_at"] = now()
        save()
    n = sum(1 for _ in hits_path.open()) if hits_path.exists() else 0
    reported = sum(leaf["total_count"] for leaf in state["leaves"].values())
    log(f"[{target}] done: {n} hit lines, {reported} results reported across {len(state['leaves'])} leaves")


def main(argv: list[str]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    targets = argv or list(TARGETS)
    for t in targets:
        if t not in TARGETS:
            sys.exit(f"unknown target {t!r}; choose from {', '.join(TARGETS)}")
    for t in targets:
        collect(t)


if __name__ == "__main__":
    main(sys.argv[1:])
