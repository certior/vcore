"""Descriptive statistics of the cleaned corpus that need no model of the action.

The unit is a distinct workflow file (``workflows.jsonl``). Every count is also given
weighted by the number of identical copies (``copies``), because template copies are a
large share of real usage. Step-level counts (inputs, tools) count agent steps.

What is measured, per target:

* triggers (events and activity types), reference pinning, runner type
* ``permissions`` at workflow and job level, and the effective scopes of agent jobs
* how often each ``with:`` input of the action is set
* ``claude_args`` flags, ``--allowedTools`` entries by category, legacy ``allowed_tools``
* ``allowed_non_write_users`` and ``allowed_bots`` values (``*`` vs lists)
* ``${{ ... }}`` interpolations inside ``prompt``-like inputs, by context field
* checkout steps before the agent step that select the pull request head
* ``if:`` conditions on agent jobs and steps that restrict who can run them

Output: ``data/<target>/describe.json`` and ``data/<target>/describe.md``. Neither names a
repository.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from collections import Counter
from statistics import median
from typing import Any

from collect import DATA, TARGETS, log
from common import agent_steps, load_workflow, ref_class, split_uses, steps
from fetch import FILES, read_jsonl

EXPR = re.compile(r"\$\{\{(.*?)\}\}", re.S)
CONTEXT_REF = re.compile(r"\b(github\.event(?:\.[A-Za-z0-9_\-]+|\[[^\]]*\])+|github\.head_ref|github\.actor|"
                         r"github\.triggering_actor|inputs\.[A-Za-z0-9_\-]+|steps\.[A-Za-z0-9_\-]+\.outputs\.[A-Za-z0-9_\-]+|"
                         r"needs\.[A-Za-z0-9_\-]+\.outputs\.[A-Za-z0-9_\-]+|env\.[A-Za-z0-9_\-]+|secrets\.[A-Za-z0-9_\-]+|vars\.[A-Za-z0-9_\-]+)")
# Event fields whose value a person without write access can author (issue, comment, PR and
# review text, branch names, commit messages, labels they cannot set are excluded).
TEXT_FIELD = re.compile(r"(\.body|\.title|head_ref|\.head\.ref|\.head\.label|\.message|\.discussion\.body|\.review\.body)$")
PROMPT_KEYS = ("prompt", "direct_prompt", "override_prompt", "custom_instructions")
PR_HEAD_REF = re.compile(r"(pull_request\.head|github\.head_ref|refs/pull/|\.head_sha|head\.sha|head\.ref|workflow_run\.head)", re.I)
WRITE_SCOPES = ("contents", "pull-requests", "issues", "id-token", "actions", "checks", "statuses",
                "discussions", "packages", "deployments", "security-events", "pages")
IF_PATTERNS = {
    "author_association": re.compile(r"author_association", re.I),
    "actor_login_equals": re.compile(r"(github\.(actor|triggering_actor)|\.user\.login|sender\.login)\s*==|==\s*(github\.(actor|triggering_actor)|[\w.]*\.user\.login|[\w.]*sender\.login)\b|contains\([^)]*,\s*(github\.(actor|triggering_actor)|[\w.]*\.user\.login|[\w.]*sender\.login)\s*\)", re.I),
    "actor_login_not_equals": re.compile(r"(github\.(actor|triggering_actor)|\.user\.login|sender\.login)\s*!=", re.I),
    "trigger_phrase_contains": re.compile(r"contains\([^)]*@", re.I),
    "same_repo_pr": re.compile(r"head\.repo\.(full_name|fork)|github\.repository\b", re.I),
    "repository_owner": re.compile(r"repository_owner", re.I),
    "label_check": re.compile(r"label", re.I),
    "bot_exclusion": re.compile(r"\[bot\]|type\s*!=\s*'Bot'|user\.type", re.I),
}


class Tally:
    """A counter kept twice: per distinct file and weighted by identical copies."""

    def __init__(self) -> None:
        self.distinct: Counter = Counter()
        self.weighted: Counter = Counter()

    def add(self, key: Any, w: int) -> None:
        self.distinct[key] += 1
        self.weighted[key] += w

    def to_json(self, top: int | None = None) -> dict:
        return {"distinct": dict(self.distinct.most_common(top)),
                "weighted": dict(self.weighted.most_common(top))}


class Stats:
    def __init__(self) -> None:
        self.tallies: dict[str, Tally] = {}

    def __getitem__(self, name: str) -> Tally:
        if name not in self.tallies:
            self.tallies[name] = Tally()
        return self.tallies[name]


def as_text(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return "" if v is None else str(v)


def triggers(doc: dict) -> dict[str, list[str] | None]:
    on = doc.get("on")
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return {str(e): None for e in on}
    if isinstance(on, dict):
        out = {}
        for e, cfg in on.items():
            types = cfg.get("types") if isinstance(cfg, dict) else None
            if isinstance(types, str):
                types = [types]
            out[str(e)] = [str(t) for t in types] if isinstance(types, list) else None
        return out
    return {}


def permissions_class(p: Any) -> str:
    if p is None:
        return "absent"
    if isinstance(p, str):
        return p if p in ("read-all", "write-all") else f"string:{p}"
    if isinstance(p, dict):
        return "empty ({})" if not p else "explicit scopes"
    return "other"


def effective_write_scopes(workflow_p: Any, job_p: Any) -> tuple[str, set[str]]:
    p = job_p if job_p is not None else workflow_p
    level = "job" if job_p is not None else ("workflow" if workflow_p is not None else "unspecified")
    if p is None:
        return level, set()
    if p == "write-all":
        return level, set(WRITE_SCOPES)
    if isinstance(p, dict):
        return level, {str(k) for k, v in p.items() if v == "write"}
    return level, set()


def split_tools(value: str) -> list[str]:
    """Split a tool list on commas and whitespace outside parentheses."""
    out, depth, cur = [], 0, []
    for ch in value:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if depth == 0 and (ch == "," or ch.isspace()):
            if cur:
                out.append("".join(cur))
                cur = []
            continue
        cur.append(ch)
    if cur:
        out.append("".join(cur))
    return [t.strip().strip("'\"") for t in out if t.strip().strip("'\"")]


def parse_cli_args(text: str) -> dict[str, list[str]]:
    """``--flag value...`` pairs from a ``claude_args`` string (values up to the next flag)."""
    try:
        tokens = shlex.split(text, comments=False, posix=True)
    except ValueError:
        tokens = text.split()
    flags: dict[str, list[str]] = {}
    current = None
    for tok in tokens:
        if tok.startswith("--"):
            name, eq, val = tok.partition("=")
            current = name
            flags.setdefault(current, [])
            if eq:
                flags[current].append(val)
        elif tok.startswith("-") and len(tok) == 2:
            current = tok
            flags.setdefault(current, [])
        elif current is not None:
            flags[current].append(tok)
    return flags


def tool_category(tool: str) -> str:
    base = tool.split("(", 1)[0]
    arg = tool[len(base) + 1:-1] if "(" in tool and tool.endswith(")") else None
    if base == "Bash":
        if arg is None or arg.strip() in ("", "*", ":*"):
            return "Bash (unrestricted)"
        return f"Bash({arg.split()[0].rstrip(':*')} ...)"
    if base in ("WebFetch", "WebSearch"):
        return base if arg is None else f"{base}(restricted)"
    if base in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
        return "Edit/Write" if arg is None else "Edit/Write(restricted)"
    if base in ("Read", "Glob", "Grep", "LS"):
        return "Read/Glob/Grep/LS"
    if base.startswith("mcp__"):
        server = base.split("__")[1] if len(base.split("__")) > 1 else "?"
        return f"mcp__{server}"
    return base


def ref_expressions(value: Any) -> list[str]:
    refs = []
    for body in EXPR.findall(as_text(value)):
        refs.extend(m.group(1) for m in CONTEXT_REF.finditer(body))
    return refs


def normalize_field(ref: str) -> str:
    return re.sub(r"\[[^\]]*\]", "[*]", ref)


def describe(target: str) -> dict:
    action = TARGETS[target]
    rows = read_jsonl(DATA / target / "workflows.jsonl")
    s = Stats()
    stars, pushed = [], []
    n_steps = n_steps_w = n_jobs = n_jobs_w = 0
    headline = Tally()

    for row in rows:
        w = row["copies"]
        text = (FILES / row["content_sha256"][:2] / row["content_sha256"]).read_bytes().decode("utf-8", errors="replace")
        doc = load_workflow(text)
        stars.append(row["stars"] or 0)
        pushed.append(row["pushed_at"][:7] if row.get("pushed_at") else "unknown")

        star = row["stars"] or 0
        s["repo_stars"].add("0" if star == 0 else "1-9" if star < 10 else "10-99" if star < 100
                            else "100-999" if star < 1000 else "1000+", w)
        s["copies"].add("unique" if row["copies"] == 1 else "2-9 copies" if row["copies"] < 10 else "10+ copies", w)

        trig = triggers(doc)
        s["trigger_form"].add(type(doc.get("on")).__name__, w)
        for event, types in trig.items():
            s["events"].add(event, w)
            for t in (types or ["(all types)"]):
                s[f"event_types:{event}"].add(t, w)
        s["n_events"].add(min(len(trig), 5), w)

        s["workflow_permissions"].add(permissions_class(doc.get("permissions")), w)
        wf_perm = doc.get("permissions")

        found = list(agent_steps(doc, action))
        step_info = {(st["job"], st["step"]): st for st in row["agent_steps"]}
        jobs_seen = set()
        wf_flags: set[str] = set()
        for job_id, job, idx, step in found:
            n_steps += 1
            n_steps_w += w
            _, _, ref = split_uses(step["uses"])
            s["ref_class"].add(ref_class(ref), w)
            s["ref"].add(ref or "(none)", w)
            info = step_info.get((job_id, idx), {})
            family = info.get("family", "unknown")
            s["version_family"].add(family, w)
            version = info.get("version") or ""
            if family == "v1" and version.count(".") == 2:
                s["pinned_v1_patch"].add(int(version.split(".")[2]), w)

            if job_id not in jobs_seen:
                jobs_seen.add(job_id)
                n_jobs += 1
                n_jobs_w += w
                s["job_permissions"].add(permissions_class(job.get("permissions")), w)
                level, scopes = effective_write_scopes(wf_perm, job.get("permissions"))
                s["effective_permissions_level"].add(level, w)
                for scope in WRITE_SCOPES:
                    if scope in scopes:
                        s["effective_write_scopes"].add(scope, w)
                if level == "unspecified":
                    wf_flags.add("permissions unspecified")
                runs_on = job.get("runs-on")
                runs_on_text = json.dumps(runs_on) if not isinstance(runs_on, str) else runs_on
                s["runner"].add("self-hosted" if "self-hosted" in runs_on_text else
                                "expression" if "${{" in runs_on_text else "github-hosted label", w)
                if job.get("environment") is not None:
                    wf_flags.add("job environment")
                job_if = as_text(job.get("if"))
                s["job_if"].add("present" if job_if else "absent", w)
                for name, rx in IF_PATTERNS.items():
                    if rx.search(job_if):
                        s["job_if_patterns"].add(name, w)

            step_if = as_text(step.get("if"))
            if step_if:
                s["step_if"].add("present", w)
                for name, rx in IF_PATTERNS.items():
                    if rx.search(step_if):
                        s["step_if_patterns"].add(name, w)
            conditions = as_text(job.get("if")) + "\n" + step_if
            if IF_PATTERNS["author_association"].search(conditions):
                wf_flags.add("if: references author_association")
            if IF_PATTERNS["actor_login_equals"].search(conditions):
                wf_flags.add("if: actor/login equality or allowlist")

            with_ = step.get("with") if isinstance(step.get("with"), dict) else {}
            s["with_keys_set"].add(len(with_), w)
            for key in with_:
                s["with_keys"].add(str(key), w)

            prompt_keys = [k for k in PROMPT_KEYS if as_text(with_.get(k)).strip()]
            s["prompt_input"].add(",".join(prompt_keys) if prompt_keys else "(none)", w)
            # v1 chooses agent mode when `prompt` is set and tag mode otherwise
            # (src/modes/detector.ts); earlier versions used `mode` and `direct_prompt`.
            if family == "v1":
                s["v1_mode_proxy"].add("prompt set (agent mode)" if as_text(with_.get("prompt")).strip()
                                       else "no prompt (tag mode on entity events)", w)
            if "mode" in with_:
                s["mode_input"].add(as_text(with_["mode"]), w)

            text_fields = False
            for key, value in with_.items():
                for ref_ in ref_expressions(value):
                    field = normalize_field(ref_)
                    s["interpolations_all_inputs"].add(f"{key}: {field}", w)
                    if key in PROMPT_KEYS:
                        s["prompt_interpolations"].add(field, w)
                        if field.startswith("github.") and TEXT_FIELD.search(field):
                            text_fields = True
            if text_fields:
                wf_flags.add("prompt interpolates outsider-authorable event text")

            for key in ("allowed_non_write_users", "allowed_bots"):
                value = as_text(with_.get(key)).strip()
                if key in with_:
                    cls = ("'*'" if value == "*" else "expression" if "${{" in value
                           else "empty" if not value else "list")
                    s[key].add(cls, w)
                    if value:
                        wf_flags.add(f"{key} set")
                    if value == "*":
                        wf_flags.add(f"{key}='*'")
                else:
                    s[key].add("absent", w)

            for key in ("use_commit_signing", "track_progress", "use_sticky_comment", "github_token",
                        "anthropic_api_key", "claude_code_oauth_token", "use_bedrock", "use_vertex",
                        "use_foundry", "settings", "plugins", "trigger_phrase", "label_trigger",
                        "assignee_trigger", "include_comments_by_actor", "exclude_comments_by_actor"):
                if key in with_:
                    s["inputs_detail"].add(f"{key}={'set' if as_text(with_[key]).strip() else 'empty'}", w)

            tools: list[str] = []
            disallowed: list[str] = []
            claude_args = as_text(with_.get("claude_args"))
            if claude_args.strip():
                flags = parse_cli_args(claude_args)
                for flag, values in flags.items():
                    s["claude_args_flags"].add(flag, w)
                    if flag == "--permission-mode":
                        s["permission_mode"].add(" ".join(values), w)
                    if flag == "--model":
                        s["model"].add(" ".join(values), w)
                for flag in ("--allowedTools", "--allowed-tools"):
                    for v in flags.get(flag, []):
                        tools.extend(split_tools(v))
                for flag in ("--disallowedTools", "--disallowed-tools"):
                    for v in flags.get(flag, []):
                        disallowed.extend(split_tools(v))
                if "--dangerously-skip-permissions" in flags:
                    wf_flags.add("--dangerously-skip-permissions")
            for key in ("allowed_tools",):
                if as_text(with_.get(key)).strip():
                    tools.extend(split_tools(as_text(with_[key])))
            if as_text(with_.get("disallowed_tools")).strip():
                disallowed.extend(split_tools(as_text(with_["disallowed_tools"])))

            s["allowed_tools_specified"].add("yes" if tools else "no", w)
            for cat in sorted({tool_category(t) for t in tools}):
                s["allowed_tool_categories"].add(cat, w)
            for t in sorted(set(tools)):
                s["allowed_tools_entries"].add(t, w)
            for t in sorted(set(disallowed)):
                s["disallowed_tools_entries"].add(t, w)
            if any(tool_category(t) == "Bash (unrestricted)" for t in tools):
                wf_flags.add("unrestricted Bash allowed")
            if any(tool_category(t).startswith("WebFetch") for t in tools):
                wf_flags.add("WebFetch allowed")

            job_steps = list(steps(job))
            for j, prior in job_steps[:idx]:
                uses = prior.get("uses")
                if not isinstance(uses, str):
                    continue
                repo, _, _ = split_uses(uses)
                if repo == "actions/checkout":
                    pw = prior.get("with") if isinstance(prior.get("with"), dict) else {}
                    ref_value = as_text(pw.get("ref"))
                    repo_value = as_text(pw.get("repository"))
                    head = bool(PR_HEAD_REF.search(ref_value) or "head.repo" in repo_value)
                    s["checkout_ref"].add("pull request head" if head else "set (other)" if ref_value else "default", w)
                    s["checkout_persist_credentials"].add(as_text(pw.get("persist-credentials")) or "default (true)", w)
                    if head:
                        wf_flags.add("checkout of PR head before agent")
                        if "pull_request_target" in trig or "workflow_run" in trig:
                            wf_flags.add("privileged trigger + PR head checkout")
                if repo in ("actions/create-github-app-token", "tibdex/github-app-token"):
                    wf_flags.add("GitHub App token step")

        event_flags = {
            "event: issue_comment": "issue_comment" in trig,
            "event: issues": "issues" in trig,
            "event: pull_request": "pull_request" in trig,
            "event: pull_request_target": "pull_request_target" in trig,
            "event: pull_request_review(_comment)": "pull_request_review" in trig or "pull_request_review_comment" in trig,
            "event: workflow_dispatch": "workflow_dispatch" in trig,
            "event: schedule": "schedule" in trig,
            "event: workflow_run": "workflow_run" in trig,
        }
        for k, v in event_flags.items():
            if v:
                wf_flags.add(k)
        for f in wf_flags:
            headline.add(f, w)

    total = len(rows)
    total_w = sum(r["copies"] for r in rows)
    out = {
        "target": target,
        "action": action,
        "unit": "distinct workflow file content; 'weighted' counts identical copies",
        "workflows_distinct": total,
        "workflows_weighted": total_w,
        "agent_steps_distinct": n_steps,
        "agent_steps_weighted": n_steps_w,
        "agent_jobs_distinct": n_jobs,
        "agent_jobs_weighted": n_jobs_w,
        "repo_stars_median": median(stars) if stars else None,
        "pushed_month": dict(Counter(pushed).most_common()),
        "headline_flags": headline.to_json(),
        "tallies": {name: t.to_json(top=None if not name.endswith("entries") and not name.startswith("interpolations")
                                    and name not in ("ref", "model") else 60)
                    for name, t in sorted(s.tallies.items())},
    }
    (DATA / target / "describe.json").write_text(json.dumps(out, indent=1) + "\n")
    (DATA / target / "describe.md").write_text(render_md(out))
    log(f"[{target}] describe: {total} distinct workflows ({total_w} with copies), {n_steps} agent steps")
    return out


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.1f}%" if d else "n/a"


def render_md(out: dict) -> str:
    d, dw = out["workflows_distinct"], out["workflows_weighted"]
    sd, sw = out["agent_steps_distinct"], out["agent_steps_weighted"]
    lines = [f"# Corpus description: `{out['action']}`", "",
             f"Distinct workflow files: **{d}** ({dw} counting identical copies). "
             f"Agent steps: **{sd}** ({sw} with copies). Median repository stars: {out['repo_stars_median']}.",
             "", "Percentages are of distinct files (of all copies in parentheses); job-level tables use jobs "
             "that contain an agent step, and step-level tables use agent steps.",
             "", "## Headline flags (share of workflows)", "", "| flag | distinct | with copies |", "|---|---:|---:|"]
    hf = out["headline_flags"]
    for k, v in sorted(hf["distinct"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| {k} | {v} ({pct(v, d)}) | {hf['weighted'][k]} ({pct(hf['weighted'][k], dw)}) |")
    job_level = {"job_permissions", "effective_permissions_level", "effective_write_scopes", "runner",
                 "job_if", "job_if_patterns"}
    step_level = {"ref_class", "ref", "with_keys", "prompt_input", "mode_input", "interpolations_all_inputs",
                  "prompt_interpolations", "allowed_non_write_users", "allowed_bots", "inputs_detail",
                  "claude_args_flags", "permission_mode", "model", "allowed_tools_specified",
                  "allowed_tool_categories", "allowed_tools_entries", "disallowed_tools_entries",
                  "checkout_ref", "checkout_persist_credentials", "step_if", "step_if_patterns", "with_keys_set",
                  "version_family", "pinned_v1_patch", "v1_mode_proxy"}
    for name, t in out["tallies"].items():
        denom, denom_w = ((sd, sw) if name in step_level else
                          (out["agent_jobs_distinct"], out["agent_jobs_weighted"]) if name in job_level else (d, dw))
        lines += ["", f"## {name}", "", "| value | distinct | with copies |", "|---|---:|---:|"]
        for k, v in list(t["distinct"].items())[:25]:
            lines.append(f"| `{k}` | {v} ({pct(v, denom)}) | {t['weighted'].get(k, 0)} ({pct(t['weighted'].get(k, 0), denom_w)}) |")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> None:
    targets = argv or [t for t in TARGETS if (DATA / t / "workflows.jsonl").exists()]
    for t in targets:
        describe(t)


if __name__ == "__main__":
    main(sys.argv[1:])
