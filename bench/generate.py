"""Generate benchmark scenarios for anthropics/claude-code-action.

A scenario is one run: a workflow file, the event that fires it, the account that fires
it and who authored the content involved (``schema/scenario.schema.json``).

Workflows come from two places:

* ``seeds/claude-code-action/``: the action's own examples, unmodified;
* **mutants**: each seed with exactly one configuration knob changed (:data:`KNOBS`), so
  every knob's effect is isolated against a realistic baseline.

Each workflow is paired with the event scenarios its triggers allow (:func:`events_for`):
every declared activity type, crossed with an actor who has write access or not, and
content authored by a maintainer or an outsider. Generation is deterministic.

    python3 bench/generate.py > bench/scenarios/generated.jsonl
    python3 bench/generate.py --stats
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
SEEDS = HERE / "seeds" / "claude-code-action"
sys.path.insert(0, str(HERE.parent / "python"))

from vcore.actions.workflow import _Loader  # noqa: E402  (YAML 1.2 booleans, 'on' as a key)

ACTION = "anthropics/claude-code-action"

# Knob -> list of (label, edit). An edit takes (workflow doc, job id, step dict) and mutates.


def _with(key, value):
    def edit(doc, job, step):
        if value is None:
            step.setdefault("with", {}).pop(key, None)
        else:
            step.setdefault("with", {})[key] = value
    return edit


def _args(text):
    def edit(doc, job, step):
        w = step.setdefault("with", {})
        w["claude_args"] = (w.get("claude_args", "").rstrip() + "\n" + text).strip()
    return edit


def _env(key, value):
    def edit(doc, job, step):
        doc["jobs"][job].setdefault("env", {})[key] = value
    return edit


def _permissions(value):
    def edit(doc, job, step):
        if value is None:
            doc["jobs"][job].pop("permissions", None)
            doc.pop("permissions", None)
        else:
            doc["jobs"][job]["permissions"] = value
    return edit


def _drop_if(doc, job, step):
    doc["jobs"][job].pop("if", None)


def _checkout_head(doc, job, step):
    for s in doc["jobs"][job]["steps"]:
        if str(s.get("uses", "")).startswith("actions/checkout"):
            s.setdefault("with", {})["ref"] = "${{ github.event.pull_request.head.sha }}"


KNOBS = {
    "prompt": [("absent", _with("prompt", None)), ("literal", _with("prompt", "Review this change.")),
               ("issue-body", _with("prompt", "Triage: ${{ github.event.issue.body }}")),
               ("comment-body", _with("prompt", "Do: ${{ github.event.comment.body }}")),
               ("pr-body", _with("prompt", "Review: ${{ github.event.pull_request.title }} ${{ github.event.pull_request.body }}"))],
    "allowed_tools": [("bash", _args('--allowedTools "Bash"')),
                      ("gh-view", _args('--allowedTools "Bash(gh issue view:*),Bash(gh pr view:*)"')),
                      ("gh-comment", _args('--allowedTools "Bash(gh pr comment:*)"')),
                      ("webfetch", _args('--allowedTools "WebFetch"')),
                      ("git-all", _args('--allowedTools "Bash(git:*)"')),
                      ("github-mcp", _args('--allowedTools "mcp__github__get_issue,mcp__github__add_issue_comment"')),
                      ("inline-comment", _args('--allowedTools "mcp__github_inline_comment__create_inline_comment"')),
                      ("comment-tool", _args('--allowedTools "mcp__github_comment__update_claude_comment"')),
                      ("ci-tools", _args('--allowedTools "mcp__github_ci__get_ci_status"'))],
    "disallowed_tools": [("no-push", _args('--disallowedTools "Bash(git push:*)"')),
                         ("no-webfetch", _args('--disallowedTools "WebFetch"'))],
    "permission_mode": [("accept-edits", _args("--permission-mode acceptEdits")),
                        ("bypass", _args("--permission-mode bypassPermissions")),
                        ("skip-permissions", _args("--dangerously-skip-permissions"))],
    "allowed_non_write_users": [("named", _with("allowed_non_write_users", "alice")),
                                ("star", _with("allowed_non_write_users", "*"))],
    "github_token": [("auto", _with("github_token", "${{ secrets.GITHUB_TOKEN }}"))],
    "allowed_bots": [("star", _with("allowed_bots", "*"))],
    "use_commit_signing": [("true", _with("use_commit_signing", True))],
    "ssh_signing_key": [("set", _with("ssh_signing_key", "${{ secrets.SSH_SIGNING_KEY }}"))],
    "track_progress": [("true", _with("track_progress", True))],
    "include_comments_by_actor": [("maintainer", _with("include_comments_by_actor", "maintainer"))],
    "exclude_comments_by_actor": [("outsider", _with("exclude_comments_by_actor", "outsider"))],
    "triggers": [("assignee", _with("assignee_trigger", "claude-bot")), ("label-off", _with("label_trigger", "")),
                 ("phrase", _with("trigger_phrase", "/ai"))],
    "env_scrub": [("off", _env("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", "0")),
                  ("on", _env("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", "1"))],
    "permissions": [("absent", _permissions(None)), ("read-all", _permissions("read-all")),
                    ("actions-read", _permissions({"contents": "write", "issues": "write",
                                                   "pull-requests": "write", "actions": "read"}))],
    "settings": [("allow-bash", _with("settings", json.dumps({"permissions": {"allow": ["Bash"]}})))],
    "job_if": [("drop", _drop_if)],
    "checkout": [("pr-head", _checkout_head)],
    "combo_star_bash": [("star+token+bash", lambda d, j, s: [
        _with("allowed_non_write_users", "*")(d, j, s), _with("github_token", "${{ secrets.GITHUB_TOKEN }}")(d, j, s),
        _args('--allowedTools "Bash"')(d, j, s)])],
    "combo_star_env_off": [("star+token+scrub-off+bash", lambda d, j, s: [
        _with("allowed_non_write_users", "*")(d, j, s), _with("github_token", "${{ secrets.GITHUB_TOKEN }}")(d, j, s),
        _env("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", "0")(d, j, s), _args('--allowedTools "Bash"')(d, j, s)])],
}


def load(text: str) -> dict:
    return yaml.load(text, Loader=_Loader)


def dump(doc: dict) -> str:
    return yaml.safe_dump(doc, sort_keys=False, width=1000, allow_unicode=True)


def agent_step(doc: dict) -> tuple[str, int, dict] | None:
    for job_id, job in (doc.get("jobs") or {}).items():
        for i, step in enumerate(job.get("steps") or []):
            if str(step.get("uses", "")).split("@")[0].lower() == ACTION:
                return job_id, i, step
    return None


def triggers(doc: dict) -> list[tuple[str, list[str | None]]]:
    on = doc["on"]
    defaults = {"issues": ["opened", "labeled", "assigned"], "issue_comment": ["created"],
                "pull_request": ["opened", "synchronize"], "pull_request_target": ["opened", "synchronize"],
                "pull_request_review": ["submitted"], "pull_request_review_comment": ["created"],
                "workflow_run": ["completed"]}
    if isinstance(on, str):
        on = {on: None}
    elif isinstance(on, list):
        on = {e: None for e in on}
    out = []
    for event, cfg in on.items():
        types = (cfg or {}).get("types") if isinstance(cfg, dict) else None
        if isinstance(types, str):
            types = [types]
        out.append((event, list(types) if types else defaults.get(event, [None])))
    return out


def events_for(event: str, action: str | None) -> list[dict]:
    """Event scenarios: who acts, who wrote the content, whether the trigger is present."""
    base = {"event": {"name": event, "action": action}}
    actors = [{"login": "maintainer", "permission": "write"}, {"login": "outsider", "permission": "read"}]
    out = []
    if event in ("issue_comment",):
        for actor, target, author in itertools.product(actors, ["issue", "pr"], ["maintainer", "outsider"]):
            out.append({**base, "actor": actor, "content": {
                "target": target, "issue_author": author, "pr_author": author, "pr_head": "fork" if author == "outsider" else "same-repo",
                "comment_authors": ["outsider", "maintainer"]}})
    elif event in ("issues",):
        for actor, author in itertools.product(actors, ["maintainer", "outsider"]):
            if action == "opened" and author != actor["login"]:
                continue
            # Nothing can be commented on an issue before it is opened.
            earlier = [] if action == "opened" else ["outsider"]
            out.append({**base, "actor": actor, "content": {"issue_author": author, "comment_authors": earlier}})
    elif event in ("pull_request", "pull_request_target", "pull_request_review", "pull_request_review_comment"):
        for actor, author in itertools.product(actors, ["maintainer", "outsider"]):
            if action == "opened" and author != actor["login"]:
                continue
            head = "same-repo" if author == "maintainer" else "fork"
            earlier = [] if action == "opened" else ["outsider"]
            out.append({**base, "actor": actor, "content": {
                "pr_author": author, "pr_head": head, "comment_authors": earlier, "review_authors": earlier}})
    elif event in ("workflow_dispatch", "schedule", "repository_dispatch"):
        out.append({**base, "actor": actors[0]})
    elif event == "workflow_run":
        for actor in actors:
            out.append({**base, "actor": actor})
    else:
        out.append({**base, "actor": actors[0]})
    return out


def scenarios():
    seen = set()
    for seed_path in sorted(SEEDS.glob("*.yml")):
        seed_text = seed_path.read_text()
        seed_doc = load(seed_text)
        found = agent_step(seed_doc)
        if not found:
            continue
        variants = [("seed", seed_text)]
        for knob, options in KNOBS.items():
            for label, edit in options:
                doc = copy.deepcopy(seed_doc)
                job, idx, step = agent_step(doc)
                edit(doc, job, step)
                text = dump(doc)
                if load(text) != seed_doc:
                    variants.append((f"{knob}={label}", text))
        for variant, text in variants:
            doc = load(text)
            for event, acts in triggers(doc):
                for action in acts:
                    for ev in events_for(event, action):
                        sc = {"workflow": text, **ev}
                        digest = hashlib.sha256(json.dumps(sc, sort_keys=True).encode()).hexdigest()[:10]
                        if digest in seen:
                            continue
                        seen.add(digest)
                        who = ev["actor"]["login"]
                        content = ev.get("content", {})
                        tail = ",".join(f"{k}={v if not isinstance(v, list) else '+'.join(v)}" for k, v in sorted(content.items()))
                        sc = {"id": f"{seed_path.stem}|{variant}|{event}.{action}|{who}|{tail}|{digest}",
                              "description": f"{seed_path.name} with {variant}", **sc}
                        yield sc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    items = list(scenarios())
    if args.stats:
        by_seed, by_knob = {}, {}
        for s in items:
            seed, variant = s["id"].split("|")[:2]
            by_seed[seed] = by_seed.get(seed, 0) + 1
            by_knob[variant.split("=")[0]] = by_knob.get(variant.split("=")[0], 0) + 1
        print(json.dumps({"scenarios": len(items), "by_seed": by_seed, "by_knob": by_knob}, indent=2))
        return
    for s in items:
        print(json.dumps(s))


if __name__ == "__main__":
    main()
