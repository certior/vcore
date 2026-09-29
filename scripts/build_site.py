#!/usr/bin/env python3
"""Embed generated data into the write-up and produce site/index.html.

Everything the write-up shows that the tool computes (the counterexample trace, the
shortened session, the representatives, the evaluation figures) is produced here by
running vcore, so the page cannot drift from the code. Run after `scripts/evaluate.py`:

    python3 scripts/build_site.py

site/article.html is the source: the page body with its data embedded. site/index.html
wraps it in a complete HTML document for static hosting.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "python"))

from vcore.lean import VCore, find_vcore  # noqa: E402
from vcore.policy import Pat, fresh_segment, parse_path  # noqa: E402

EXAMPLES = REPO / "examples" / "coding-agent"
ARTICLE = REPO / "site" / "article.html"
INDEX = REPO / "site" / "index.html"


def load(name: str):
    return json.loads((EXAMPLES / name).read_text())


def hero() -> dict:
    out = subprocess.run([str(find_vcore()), "check", "policy.json", "trust-issues.json", "--json"],
                         cwd=EXAMPLES, capture_output=True, text=True)
    return {"old": "policy.json", "new": "trust-issues.json", **json.loads(out.stdout)}


def shrink(v: VCore) -> dict:
    """A longer session with the same escalation, and the requests the argument keeps."""
    old, new = load("policy.json"), load("trust-issues.json")
    session = ["Read/src/app.py", "mcp/github/get_issue", "Edit/src/app.py",
               "Read/customers/export.csv", "Bash/pytest/tests", "mcp/github/create_pull_request"]
    short = ["mcp/github/get_issue", "Read/customers/export.csv", "mcp/github/create_pull_request"]
    steps = lambda auth, trace: [{"allow": a, "context": list(c)} for a, c in v.eval(auth, trace)]
    assert v.check(old, new) is not None
    return {"session": session, "kept": [session.index(r) for r in short],
            "old": steps(old, session), "new": steps(new, session),
            "short": short, "short_old": steps(old, short), "short_new": steps(new, short)}


def representatives() -> dict:
    patterns = ["Read/**", "Read/secrets/**", "**/.env"]
    pats = [Pat.parse(p) for p in patterns]
    fresh = fresh_segment({s for p in pats for s in p.path})
    prefixes = [p.path for p in pats if p.kind == "prefixed"]
    suffixes = [p.path for p in pats if p.kind == "suffixed"]
    rows = []
    for request in ["WebFetch/pypi.org/simple", "Read/src/app.py", "Read/src/.env",
                    "Read/secrets/prod/key", "Read/secrets/prod/.env"]:
        x = parse_path(request)
        q = max((p for p in [()] + prefixes if x[:len(p)] == p), key=len)
        u = max((s for s in [()] + suffixes if len(s) <= len(x) and x[len(x) - len(s):] == s), key=len)
        rep = q + (fresh,) + u
        covered = [str(p) for p in pats if p.covers(x)]
        assert covered == [str(p) for p in pats if p.covers(rep)]
        rows.append({"request": request, "rep": "/".join(rep), "covered": covered})
    return {"patterns": patterns, "rows": rows,
            "count": (1 + len(prefixes)) * (1 + len(suffixes))}


def main() -> None:
    results = json.loads((REPO / "site" / "results.json").read_text())
    with VCore() as v:
        data = {"results": results, "hero": hero(), "shrink": shrink(v),
                "reps": representatives()}
    html = ARTICLE.read_text()
    block = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    pattern = r'(<script type="application/json" id="data">).*?(</script>)'
    if not re.search(pattern, html, flags=re.S):
        raise SystemExit("site/article.html: data block not found")
    html = re.sub(pattern, lambda m: m.group(1) + block + m.group(2), html, count=1, flags=re.S)
    ARTICLE.write_text(html)
    head, body = html.split('<header class="masthead">', 1)
    INDEX.write_text('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                     '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
                     + head.strip() + '\n</head>\n<body>\n<header class="masthead">' + body.strip()
                     + '\n</body>\n</html>\n')
    print("updated site/article.html and site/index.html", file=sys.stderr)


if __name__ == "__main__":
    main()
