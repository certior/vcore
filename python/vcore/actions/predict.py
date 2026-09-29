"""Predicting what the action sets up for one concrete run, from the model.

:func:`predict` takes a benchmark scenario (``bench/schema/scenario.schema.json``) and
returns an observation in the oracle's format (``observation.schema.json``). It runs the
same layer-1 functions as the static analysis in :mod:`vcore.actions.claude_code_action`,
with the workflow's expressions evaluated for the run, so that comparing predictions with
the oracle's observations tests the model the static analysis uses.

What is emulated here is the GitHub runner, not the action: which event fires the
workflow, ``if:`` conditions, expression values, secrets (withheld for runs triggered from
a fork on ``pull_request`` and review events), and token scopes. The scenario's synthetic
payload follows ``oracle/src/payload.ts`` so both sides see the same texts.
"""

from __future__ import annotations

import dataclasses
import re

from . import claude_code_action as cca
from .expressions import Unsupported, condition, interpolate
from .workflow import Job, Step, Workflow, load

ACTORS = {"maintainer": "write", "outsider": "read"}
TRIGGER_EVENTS_WITH_COMMENT = ("issue_comment", "pull_request_review_comment", "pull_request_review")


def canary(field: str, author: str) -> str:
    return f"CANARY-{field.replace('.', '__')}-{author}"


def _defaults(sc: dict) -> dict:
    c = sc.get("content") or {}
    ev = sc["event"]["name"]
    pr_event = ev in ("pull_request", "pull_request_target", "pull_request_review", "pull_request_review_comment")
    return {
        "target": "pr" if pr_event else c.get("target", "issue"),
        "issue_author": c.get("issue_author", "outsider"),
        "comment_authors": c.get("comment_authors", []),
        "pr_author": c.get("pr_author", "outsider"),
        "fork": c.get("pr_head", "fork") == "fork",
        "review_authors": c.get("review_authors", []),
    }


def _payload(sc: dict, r: dict, trigger_phrase: str, label_trigger: str, assignee_trigger: str) -> dict:
    e = sc["event"]
    name, actor = e["name"], sc["actor"]
    mention = f"{trigger_phrase} " if e.get("mention", True) else ""
    sender = {"login": actor["login"], "type": actor.get("type", "User")}
    assoc = {"admin": "OWNER", "maintain": "MEMBER", "write": "MEMBER"}.get(actor["permission"], "NONE")
    head_ref = f"feature/{canary('pr.head_ref', r['pr_author'])}"

    def issue(with_mention: bool) -> dict:
        out = {"number": 7, "title": f"Issue title {canary('issue.title', r['issue_author'])}",
               "body": f"{mention if with_mention else ''}Issue body {canary('issue.body', r['issue_author'])}",
               "user": {"login": r["issue_author"]}, "labels": []}
        if r["target"] == "pr":
            out["pull_request"] = {"url": "https://api.github.com/repos/acme/widget/pulls/7"}
        return out

    def pr(with_mention: bool) -> dict:
        return {"number": 7, "title": f"PR title {canary('pr.title', r['pr_author'])}",
                "body": f"{mention if with_mention else ''}PR body {canary('pr.body', r['pr_author'])}",
                "user": {"login": r["pr_author"]}, "head": {"ref": head_ref, "sha": "1" * 40},
                "base": {"ref": "main", "sha": "2" * 40},
                "author_association": "MEMBER" if r["pr_author"] == "maintainer" else "CONTRIBUTOR"}

    trigger_body = f"{mention}Please help {canary('trigger.body', 'actor')}"
    base = {"repository": {"full_name": "acme/widget", "default_branch": "main"}, "sender": sender}
    action = e.get("action")
    if name == "issues":
        action = action or "opened"
        out = {"action": action, "issue": issue(action == "opened"), **base}
        if action == "labeled":
            out["label"] = {"name": e.get("label", label_trigger)}
        if action == "assigned":
            out["assignee"] = {"login": e.get("assignee", assignee_trigger.removeprefix("@"))}
        return out
    if name == "issue_comment":
        return {"action": action or "created", "issue": issue(False),
                "comment": {"body": trigger_body, "user": sender, "author_association": assoc}, **base}
    if name in ("pull_request", "pull_request_target"):
        return {"action": action or "opened", "number": 7, "pull_request": pr(True), **base}
    if name == "pull_request_review":
        return {"action": action or "submitted", "pull_request": pr(False),
                "review": {"body": trigger_body, "user": sender, "author_association": assoc}, **base}
    if name == "pull_request_review_comment":
        return {"action": action or "created", "pull_request": pr(False),
                "comment": {"body": trigger_body, "user": sender, "author_association": assoc}, **base}
    if name == "workflow_dispatch":
        return {"inputs": e.get("inputs", {}), **base}
    if name == "repository_dispatch":
        return {"action": action or "custom",
                "client_payload": e.get("client_payload", {"text": canary("dispatch.client_payload", "actor")}), **base}
    if name == "workflow_run":
        return {"action": action or "completed", **base,
                "workflow_run": {"head_branch": head_ref, "display_title": f"PR title {canary('pr.title', r['pr_author'])}",
                                 "actor": {"login": r["pr_author"]}, "conclusion": "failure", "event": "pull_request"}}
    return base


def _scopes(job: Job, default: str, fork_run: bool) -> dict[str, str]:
    """Token scopes: the job's permissions, else the repository default; fork runs are capped
    at read and never get id-token."""
    p = job.permissions
    if p.specified:
        scopes = {s: p.level(s) or "none" for s in ("actions", "contents", "id-token", "issues", "pull-requests")}
        if p.shorthand == "read-all":
            scopes["id-token"] = "none"
    elif default == "permissive":
        scopes = {s: "write" for s in ("actions", "contents", "issues", "pull-requests")} | {"id-token": "none"}
    else:
        scopes = {"actions": "none", "contents": "read", "issues": "none", "pull-requests": "none", "id-token": "none"}
    if fork_run:
        scopes = {k: ("read" if v == "write" else v) for k, v in scopes.items()} | {"id-token": "none"}
    return scopes


def _filter_actors(authors: list[str], include: str, exclude: str) -> list[str]:
    inc = [a.strip() for a in include.split(",") if a.strip()]
    exc = [a.strip() for a in exclude.split(",") if a.strip()]

    def match(login: str, pats: list[str]) -> bool:
        return any(re.fullmatch(re.escape(p).replace(r"\*", ".*"), login, re.I) for p in pats)
    return [a for a in authors if (not inc or match(a, inc)) and not match(a, exc)]


def _locate(w: Workflow, sc: dict) -> tuple[Job, Step]:
    candidates = [(j, s) for j, s in w.steps_using(cca.ACTION)
                  if (sc.get("job") in (None, j.id)) and (sc.get("step") in (None, s.index))]
    if not candidates:
        raise ValueError("no claude-code-action step")
    return candidates[0]


def predict(sc: dict) -> dict:
    """The predicted observation for a scenario."""
    return predict_run(sc)[0]


def predict_run(sc: dict) -> tuple[dict, tuple | None]:
    """The predicted observation, and when Claude runs the concrete configuration
    ``(run configuration, workflow, job, step)`` that :func:`claude_code_action.envelope` takes."""
    obs = {"id": sc["id"], "action_commit": cca.COMMIT[:7], "runs": False, "stop_reason": None, "mode": None,
           "allowed_tools": [], "disallowed_tools": [], "permission_mode": None, "mcp_servers": [],
           "credentials": {"token_in_git_config": False, "credential_helper": False, "claude_env": []},
           "env_scrub": False, "prompt_canaries": []}
    w = load(sc["workflow"])
    job, step = _locate(w, sc)
    e = sc["event"]
    name, action = e["name"], e.get("action")
    trig = w.trigger(name)
    if trig is None or (action is not None and action not in cca.activities(name, trig.types)
                        and name in cca.DEFAULT_ACTIVITIES):
        obs["stop_reason"] = "event-not-in-workflow"
        return obs, None

    r = _defaults(sc)
    fork_run = name in cca.FORK_WITHOUT_SECRETS and r["fork"] and r["target"] == "pr"
    referenced = set(re.findall(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)", sc["workflow"]))
    secret_names = [n.upper() for n in (sc.get("secrets") or referenced) if n.upper() != "GITHUB_TOKEN"]
    secrets = {"GITHUB_TOKEN": "ghs_workflow"} | ({} if fork_run else {n: f"secret-{n}" for n in secret_names})
    raw_with = step.with_
    payload = _payload(sc, r, raw_with.get("trigger_phrase", "@claude") if "${{" not in raw_with.get("trigger_phrase", "") else "@claude",
                       raw_with.get("label_trigger", "claude"), raw_with.get("assignee_trigger", ""))
    pr = payload.get("pull_request")
    github = {"event_name": name, "event": payload, "actor": sc["actor"]["login"],
              "triggering_actor": sc["actor"]["login"], "repository": "acme/widget",
              "repository_owner": "acme", "token": "ghs_workflow", "run_id": "1", "sha": "2" * 40,
              "ref": "refs/pull/7/merge" if pr and name != "pull_request_target" else "refs/heads/main",
              "head_ref": pr["head"]["ref"] if pr and name in ("pull_request", "pull_request_target") else "",
              "base_ref": "main" if pr and name in ("pull_request", "pull_request_target") else ""}
    ctx = {"github": github, "secrets": secrets, "vars": sc.get("vars", {}), "inputs": e.get("inputs", {}),
           "runner": {"os": "Linux", "temp": "/tmp"}, "env": {}, "steps": {}, "needs": {}, "matrix": {},
           "strategy": {}, "job": {}}
    try:
        if not condition(job.if_, ctx):
            obs["stop_reason"] = "job-if"
            return obs, None
        env = {k: interpolate(v, ctx) for k, v in {**w.env, **job.env}.items()}
        ctx["env"] = env
        if not condition(step.if_, ctx):
            obs["stop_reason"] = "step-if"
            return obs, None
        env |= {k: interpolate(v, ctx) for k, v in step.env.items()}
        with_ = {k: interpolate(v, ctx) for k, v in step.with_.items()}
    except Unsupported:
        obs["stop_reason"] = "unsupported-expression"
        return obs, None

    # Rebuild the step with evaluated, literal values; the model then decides definitely.
    # Literal "${{" cannot survive evaluation, so the static functions see no expressions.
    concrete = dataclasses.replace(step, with_={k: v.replace("${{", "$ {{") for k, v in with_.items()}, env=env)
    wf2 = dataclasses.replace(w, env={}, jobs=tuple(dataclasses.replace(j, env={}) if j is job else j for j in w.jobs))
    job2 = next(j for j in wf2.jobs if j.id == job.id)
    inputs = cca.Inputs.of(wf2, job2, concrete)

    if name not in cca.ENTITY_EVENTS + cca.AUTOMATION_EVENTS + ("pull_request_target",):
        obs["stop_reason"] = "unsupported-event"
        return obs, None
    found, stop = cca.modes(inputs, name, action, mention=e.get("mention", True),
                            label=(payload.get("label") or {}).get("name"),
                            assignee=(payload.get("assignee") or {}).get("login"))
    if stop == "track-progress-invalid-event":
        obs["stop_reason"] = "track-progress-invalid-event"
        return obs, None
    entity = cca.as_parsed(name) in cca.ENTITY_EVENTS
    mode = next(iter(found)) if found else ("tag" if entity and cca.equals(inputs.track_progress, "true") else "agent")
    obs["mode"] = mode

    scopes = _scopes(job, (sc.get("repo") or {}).get("default_token_permissions", "restricted"), fork_run)
    token_given = inputs.github_token != ""
    if not token_given and scopes.get("id-token") != "write":
        obs["stop_reason"] = "oidc-unavailable"
        return obs, None

    def has_write(login: str, permission: str) -> bool:
        anw = inputs.allowed_non_write_users.strip()
        if anw and token_given and (anw == "*" or login in [u.strip() for u in anw.split(",")]):
            return True
        return login.endswith("[bot]") or permission in ("admin", "write")
    if entity or name == "workflow_run":
        ok = has_write(sc["actor"]["login"], sc["actor"]["permission"])
        if name == "workflow_run" and ok:
            up = r["pr_author"]
            ok = up == sc["actor"]["login"] or has_write(up, ACTORS.get(up, "none"))
        if not ok:
            obs["stop_reason"] = "actor-permission"
            return obs, None
    if not found:
        obs["stop_reason"] = "no-trigger"
        return obs, None
    if sc["actor"].get("type", "User") != "User":
        bots = inputs.allowed_bots.strip()
        login = sc["actor"]["login"].lower().removesuffix("[bot]")
        if not (bots == "*" or login in [b.strip().lower().removesuffix("[bot]") for b in bots.split(",") if b.strip()]):
            obs["stop_reason"] = "non-human-actor"
            return obs, None
    # base-action/src/validate-env.ts:9-41 (direct API needs a key, an OAuth token, or both
    # workload identity ids; cloud providers are not emulated and assumed configured).
    direct = not any(with_.get(k) == "true" for k in ("use_bedrock", "use_vertex", "use_foundry"))
    if direct and not (with_.get("anthropic_api_key") or env.get("ANTHROPIC_API_KEY")
                       or with_.get("claude_code_oauth_token") or env.get("CLAUDE_CODE_OAUTH_TOKEN")
                       or (with_.get("anthropic_federation_rule_id") and with_.get("anthropic_organization_id"))):
        obs["stop_reason"] = "missing-credentials"
        return obs, None

    is_pr = r["target"] == "pr"
    job_scoped = dataclasses.replace(job2, permissions=dataclasses.replace(
        job2.permissions, scopes={k: v for k, v in scopes.items()}, shorthand=None))
    rc = cca.configure(wf2, job_scoped, concrete, name, action, mode, is_pr=is_pr)
    obs["runs"] = True
    obs["allowed_tools"] = [t.replace("$GITHUB_ACTION_PATH", "${GITHUB_ACTION_PATH}") for t in rc.allowed_tools]
    obs["disallowed_tools"] = rc.disallowed_tools
    obs["permission_mode"] = rc.permission_mode or "default"
    obs["mcp_servers"] = sorted(s for s, v in rc.mcp_servers.items() if v)
    obs["credentials"] = {"token_in_git_config": bool(rc.token_in_git_config),
                          "credential_helper": bool(rc.credential_helper),
                          "claude_env": sorted({"GITHUB_TOKEN", "GH_TOKEN"})}
    obs["env_scrub"] = bool(rc.env_scrub)

    # Prompt canaries, from the model's sources and the run's authors.
    canaries: set[str] = set()
    include, exclude = inputs.include_comments_by_actor, inputs.exclude_comments_by_actor
    for src in rc.sources:
        req = src.request
        if req == "Input/entity/body":
            # For issue_comment the title and body come from the webhook's issue object, which
            # for a pull request thread is the pull request (src/github/data/fetcher.ts:462).
            if is_pr and name != "issue_comment":
                canaries |= {f"pr.title@{r['pr_author']}", f"pr.body@{r['pr_author']}"}
            else:
                canaries |= {f"issue.title@{r['issue_author']}", f"issue.body@{r['issue_author']}"}
        elif req == "Input/pr/head_ref":
            canaries.add(f"pr.head_ref@{r['pr_author']}")
        elif req == "Input/entity/comments":
            canaries |= {f"comment.body@{a}" for a in _filter_actors(r["comment_authors"], include, exclude)}
        elif req == "Input/pr/review_comments":
            for a in _filter_actors(r["review_authors"], include, exclude):
                canaries |= {f"review.body@{a}", f"review_comment.body@{a}"}
        elif req == "Input/trigger/comment":
            canaries.add("trigger.body@actor")
        elif req == "Input/trigger/display_name":
            canaries.add("user.name@actor")
    if mode == "agent":
        text = with_.get("prompt", "") + "\n" + with_.get("claude_args", "")
        canaries |= {f"{m.group(1).replace('__', '.')}@{m.group(2)}"
                     for m in re.finditer(r"CANARY-([a-z_]+)-(maintainer|outsider|actor)", text)}
    obs["prompt_canaries"] = sorted(canaries)
    return obs, (rc, wf2, job_scoped, concrete)
