"""De-identified release of the corpus analysis.

    python3 corpus/release.py [--target claude-code-action]      # writes corpus/release/

Keeps only derived features and verdicts. Dropped: repository names, paths, commit and blob
ids, content hashes, dates, licences, languages and workflow text. Each workflow gets an
opaque id, an HMAC of its content hash under a key that stays in ``corpus/data/`` and is
never published, so the same file keeps its id across re-releases without being linkable
to its repository through the id. Stars are bucketed.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import secrets
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

KEEP = ("event", "activity", "mode", "runs", "stop_reason", "actor_outsider", "outsider_sources",
        "workspace_outsider", "token_in_git_config", "env_scrub", "permission_mode", "skip_permissions",
        "tool_categories", "n_allowed_tools", "if_restricted", "verdicts", "repairs")


def bucket(stars: int | None) -> str:
    s = stars or 0
    for bound, name in ((0, "0"), (9, "1-9"), (99, "10-99"), (999, "100-999"), (9999, "1000-9999")):
        if s <= bound:
            return name
    return "10000+"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="claude-code-action")
    args = ap.parse_args()
    d = DATA / args.target
    key_file = DATA / ".release-key"
    if not key_file.exists():
        key_file.write_text(secrets.token_hex(32))
    key = bytes.fromhex(key_file.read_text().strip())
    out = HERE / "release"
    out.mkdir(exist_ok=True)
    n = 0
    with (out / f"{args.target}.jsonl").open("w") as f:
        for line in (d / "analysis.jsonl").read_text().splitlines():
            r = json.loads(line)
            rec = {
                "id": hmac.new(key, r["content_sha256"].encode(), hashlib.sha256).hexdigest()[:16],
                "copies": r["copies"],
                "stars_bucket": bucket(r["stars"]),
                "parse_error": r["error"] is not None,
                "steps": [{
                    "ref_class": s.get("ref_class"), "version": s.get("version"), "family": s.get("family"),
                    "skipped": ("model-error" if (s.get("skipped") or "").startswith("model error")
                                else s.get("skipped")),
                    "configurations": [{k: c[k] for k in KEEP if k in c} for c in s["configurations"]],
                } for s in r["steps"]],
            }
            f.write(json.dumps(rec) + "\n")
            n += 1
    manifest = json.loads((d / "manifest.json").read_text())
    summary = json.loads((d / "analysis_summary.json").read_text())
    optimistic = d / "analysis_trust_unknown_summary.json"
    sensitivity = json.loads(optimistic.read_text())["counts"] if optimistic.exists() else None
    shutil.copy(HERE / "DATASET_CARD.md", out / "README.md")
    (out / f"{args.target}.manifest.json").write_text(json.dumps({
        "sensitivity_trust_unknown": sensitivity,
        "workflows": n,
        "collection": {k: manifest[k] for k in ("action", "search_query", "collection_started_at", "cleaned_at")},
        "funnel": manifest["funnel"],
        "model": summary["model"],
        "summary": {k: summary[k] for k in ("counts", "stop_reasons", "shapes", "repairs", "seconds_per_file")},
    }, indent=2) + "\n")
    print(f"{n} workflows written to {out}")


if __name__ == "__main__":
    main()
