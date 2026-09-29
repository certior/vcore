"""Model of ``anthropics/claude-code-action`` v1.0.225 (commit ``bf38e86``).

Two layers, kept apart so each can be checked on its own terms:

1. :func:`configure` computes a **run configuration**: whether the action proceeds for an
   event, in which mode, with which Claude Code tool rules, MCP servers and credential
   placement, and which texts it puts in the prompt. The action's own code decides all
   of this, so it can be compared with an execution of that code (``oracle/``).
2. :func:`envelope` turns a run configuration into an **authority envelope** (a vcore
   policy) by adding trust labels. That step rests on the attacker model and the named
   assumptions in :data:`ASSUMPTIONS`, not on the action's code.

Every rule cites the line of the action it follows (``path:line``, at :data:`COMMIT`).

Values a workflow file does not fix (an input read from a secret, whether an
``issue_comment`` is on a pull request, whether a comment contains the trigger phrase) are
three-valued: ``True``, ``False`` or ``None`` ("depends on the run"). The static analysis
keeps every configuration a ``None`` permits, so its envelopes cover every run.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from ..policy import Pat, parse_path
from . import catalog
from .conditions import event_excluded, restricted_roles
from .model import (EDIT_TOOLS, READ_ONLY_TOOLS, SECRET, UNTRUSTED, Assumption, Envelope,
                    map_rule, parse_claude_args, tool_list)
from .workflow import Job, Step, Workflow, expressions, has_literal_text, is_literal, references

ACTION = "anthropics/claude-code-action"
VERSION = "v1.0.225"
COMMIT = "bf38e86e58df9ebf3420326d019f955bb3be64dd"

Tri = Optional[bool]


def cite(where: str) -> str:
    return f"{where} @ {COMMIT[:7]}"


ASSUMPTIONS = {a.id: a for a in [
    Assumption("A1", "Claude Code runs Read, Glob, Grep and LS without a permission rule; any other "
                     "tool needs an allow rule, and in the headless SDK a call that would ask for "
                     "permission is denied (src/modes/tag/index.ts:183-184). Confirmed for the tag-mode configuration by running Claude Code 2.1.272 offline; see oracle/probe/."),
    Assumption("A2", "Claude Code enforces allow and deny rules as documented: a Bash prefix rule "
                     "covers only commands with that prefix, compound commands are checked per "
                     "subcommand, and a deny rule overrides any allow rule. The Read part of the default exfiltration counterexample is confirmed in oracle/probe/."),
    Assumption("A3", "An outsider (an account without write access) can author issues, pull "
                     "requests, comments and reviews on a public repository, web content, and "
                     "whatever GitHub read tools return."),
    Assumption("A4", "The Read tool can read files outside the workspace, such as "
                     "/proc/self/environ. Conservative; off unless requested."),
    Assumption("A5", "With CLAUDE_CODE_SUBPROCESS_ENV_SCRUB set, subprocesses of Claude Code do "
                     "not see the credentials in its environment (docs/security.md:16 calls the "
                     "scrub best effort)."),
    Assumption("A6", "No permission rules come from settings the workflow file does not show "
                     "(the base branch's .claude/settings.json, runner user settings)."),
    Assumption("A7", "The action's MCP servers do not return the token they hold."),
    Assumption("A8", "Runs triggered by pull_request, pull_request_review or "
                     "pull_request_review_comment for a fork receive no secrets, so the action "
                     "cannot authenticate; a pull request that reaches Claude on these events "
                     "comes from a branch of the repository, whose author has write access."),
    Assumption("A9", "Writes made with the action's GitHub token succeed. Token scopes are not "
                     "modeled: without github_token the action uses a GitHub App installation "
                     "token (src/github/token.ts:158-185), which the workflow's permissions do "
                     "not restrict; with it, missing scopes would only remove behaviors."),
]}


# --- Three-valued logic --------------------------------------------------------------------

def t_and(*xs: Tri) -> Tri:
    if any(x is False for x in xs):
        return False
    return True if all(x is True for x in xs) else None


def t_or(*xs: Tri) -> Tri:
    if any(x is True for x in xs):
        return True
    return False if all(x is False for x in xs) else None


def t_not(x: Tri) -> Tri:
    return None if x is None else not x


def possible(x: Tri) -> bool:
    return x is not False


_AUTO_TOKEN = re.compile(r"^(secrets\.GITHUB_TOKEN|github\.token)$")


def nonempty(raw: str) -> Tri:
    """Whether an input renders non-empty. The action tests ``process.env.X`` for
    truthiness, so whitespace counts."""
    if raw == "":
        return False
    if has_literal_text(raw) or not expressions(raw):
        return True
    if all(_AUTO_TOKEN.match(e) for e in expressions(raw)):
        return True
    return None


def equals(raw: str, value: str) -> Tri:
    return (raw == value) if is_literal(raw) else None


def literal_list(raw: str) -> list[str] | None:
    return [u.strip() for u in raw.split(",") if u.strip()] if is_literal(raw) else None


# --- Inputs and events ---------------------------------------------------------------------

@dataclass
class Inputs:
    """The step inputs the model reads, as raw strings, with action.yml defaults."""
    prompt: str = ""
    trigger_phrase: str = "@claude"          # action.yml:11
    assignee_trigger: str = ""
    label_trigger: str = "claude"            # action.yml:18
    claude_args: str = ""
    settings: str = ""
    allowed_non_write_users: str = ""
    allowed_bots: str = ""
    github_token: str = ""
    use_commit_signing: str = "false"        # action.yml:123
    ssh_signing_key: str = ""
    track_progress: str = "false"
    include_comments_by_actor: str = ""
    exclude_comments_by_actor: str = ""
    display_report: str = "false"            # action.yml:155
    env_scrub: str = ""                      # env.CLAUDE_CODE_SUBPROCESS_ENV_SCRUB

    @staticmethod
    def of(workflow: Workflow, job: Job, step: Step) -> Inputs:
        names = Inputs.__dataclass_fields__
        env = {**workflow.env, **job.env, **step.env}
        return Inputs(**{k: v for k, v in step.with_.items() if k in names and k != "env_scrub"},
                      env_scrub=env.get("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", ""))


ENTITY_EVENTS = ("issues", "issue_comment", "pull_request", "pull_request_review",
                 "pull_request_review_comment")                       # src/github/context.ts:55-61
AUTOMATION_EVENTS = ("workflow_dispatch", "repository_dispatch", "schedule", "workflow_run")
FORK_WITHOUT_SECRETS = ("pull_request", "pull_request_review", "pull_request_review_comment")
DEFAULT_ACTIVITIES = {
    "issues": ("opened", "edited", "deleted", "transferred", "pinned", "unpinned", "closed",
               "reopened", "assigned", "unassigned", "labeled", "unlabeled", "locked", "unlocked",
               "milestoned", "demilestoned", "typed", "untyped"),
    "issue_comment": ("created", "edited", "deleted"),
    "pull_request": ("opened", "synchronize", "reopened"),
    "pull_request_target": ("opened", "synchronize", "reopened"),
    "pull_request_review": ("submitted", "edited", "dismissed"),
    "pull_request_review_comment": ("created", "edited", "deleted"),
}


def as_parsed(event: str) -> str:
    """The event name the action works with: pull_request_target is parsed as
    pull_request (src/github/context.ts:191-200)."""
    return "pull_request" if event == "pull_request_target" else event


def activities(event: str, types: tuple[str, ...] | None) -> tuple[str | None, ...]:
    return types or DEFAULT_ACTIVITIES.get(event, (None,))


# --- Layer 1: run configuration ------------------------------------------------------------

ROLES = ("actor", "issue-author", "pr-author", "commenters", "named", "workflow", "unknown")


@dataclass(frozen=True)
class Source:
    """A text placed in the session: ``request`` names it, ``role`` is who writes it."""
    request: str
    role: str
    why: str
    source: str


@dataclass
class RunConfig:
    event: str
    activity: str | None
    mode: str | None
    runs: Tri
    stop_reason: str | None = None
    is_pr: Tri = False
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    permission_mode: str | None = None
    skip_permissions: bool = False
    mcp_servers: dict[str, Tri] = field(default_factory=dict)
    token_in_git_config: Tri = None
    credential_helper: Tri = None
    env_scrub: Tri = None
    trusted_roles: set[str] = field(default_factory=set)
    sources: list[Source] = field(default_factory=list)
    workspace_role: str = "workflow"
    notes: list[str] = field(default_factory=list)

    def outsider_writes(self, role: str) -> Tri:
        """Whether an outsider can author text written by ``role`` (assumption A3)."""
        if role in self.trusted_roles:
            return False
        return None if role == "unknown" else True


def modes(inputs: Inputs, event: str, activity: str | None, mention: Tri = None,
          label: str | None = None, assignee: str | None = None) -> tuple[set[str], str | None]:
    """Possible modes after mode detection and the trigger check, or a stop reason.

    ``mention`` (the triggering text contains the trigger phrase), ``label`` and
    ``assignee`` are unknown in a static analysis and known for a concrete run."""
    ev = as_parsed(event)
    entity = ev in ENTITY_EVENTS
    prompt = nonempty(inputs.prompt)
    track = equals(inputs.track_progress, "true")                    # src/github/context.ts:162
    if event not in ENTITY_EVENTS + AUTOMATION_EVENTS + ("pull_request_target",):
        return set(), "unsupported-event"                            # src/github/context.ts:250-251
    if track is True:                                                # src/modes/detector.ts:83-110
        if not entity or (ev == "pull_request" and activity not in
                          (None, "opened", "synchronize", "ready_for_review", "reopened", "labeled")):
            return set(), "track-progress-invalid-event"
    phrase = nonempty(inputs.trigger_phrase) is not False and mention is not False
    # checkContainsTrigger (src/github/validation/trigger.ts:14-146); tag mode needs it.
    if ev in ("issue_comment", "pull_request_review_comment"):
        trigger = phrase
    elif ev == "pull_request_review":
        trigger = phrase and activity in (None, "submitted", "edited")
    elif ev == "issues":
        wanted_assignee = inputs.assignee_trigger.removeprefix("@") if is_literal(inputs.assignee_trigger) else None
        wanted_label = inputs.label_trigger if is_literal(inputs.label_trigger) else None
        trigger = ((activity in (None, "assigned") and nonempty(inputs.assignee_trigger) is not False
                    and (assignee is None or wanted_assignee is None or assignee == wanted_assignee))
                   or (activity in (None, "labeled") and nonempty(inputs.label_trigger) is not False
                       and (label is None or wanted_label is None or label.lower() == wanted_label.lower()))
                   or (activity in (None, "opened") and phrase))
    elif ev == "pull_request":
        trigger = phrase
    else:
        trigger = False
    found: set[str] = set()
    # detectMode (src/modes/detector.ts:14-78): track_progress forces tag mode for entities;
    # otherwise a prompt means agent mode, a trigger without a prompt means tag mode for
    # comment, review and issue events, and everything else is agent mode.
    if entity and track is not False:
        if trigger or prompt is not False:
            found.add("tag")
    if track is not True:
        if prompt is not False:
            found.add("agent")                                       # src/entrypoints/run.ts:207-210
        if prompt is not True and trigger and ev in ("issue_comment", "pull_request_review_comment",
                                                     "pull_request_review", "issues"):
            found.add("tag")
    if not found:
        return set(), "no-trigger"
    return found, None


def trusted_roles(workflow: Workflow, job: Job, step: Step, inputs: Inputs,
                  event: str, activity: str | None) -> tuple[set[str], list[str]]:
    """Roles an outsider cannot fill, with the reasons."""
    ev = as_parsed(event)
    notes: list[str] = []
    trusted = {"workflow", "named"}
    # The actor gate: src/entrypoints/run.ts:196-205, src/github/validation/permissions.ts:90-137,
    # src/github/validation/actor.ts:70-80 (bots need allowed_bots).
    gated = ev in ENTITY_EVENTS or ev == "workflow_run"
    token = nonempty(inputs.github_token)
    anw = inputs.allowed_non_write_users.strip() if is_literal(inputs.allowed_non_write_users) \
        else inputs.allowed_non_write_users
    bots = inputs.allowed_bots.strip() if is_literal(inputs.allowed_bots) else inputs.allowed_bots
    # A non-empty allowed_non_write_users (with a github_token) lets the listed accounts, which
    # lack write access, trigger the action (permissions.ts:90-107 returns true for any listed
    # login, not only '*'). Since the model cannot tell whether the actual triggering login is
    # one of them, and those accounts are outsiders by the attacker model, the actor is untrusted
    # whenever the list is non-empty, not only for '*'. allowed_bots is consulted only for bot
    # actors, so '*' there also admits an outsider bot.
    anw_bypass = t_and(nonempty(inputs.allowed_non_write_users), token)
    bypass = t_or(anw_bypass, equals(bots, "*"))
    if gated and possible(bypass):
        reason = []
        if possible(anw_bypass):
            reason.append("allowed_non_write_users='*'" if equals(anw, "*") is not False and anw
                          else "a named allowed_non_write_users list")
        if equals(bots, "*") is not False and bots:
            reason.append("allowed_bots='*'")
        notes.append("the actor gate admits accounts without write access (" + ", ".join(reason) + ")")
    else:
        trusted.add("actor")
    if inputs.allowed_non_write_users and token is False:
        notes.append("allowed_non_write_users has no effect without github_token "
                     "(src/github/validation/permissions.ts:90)")
    for cond in (job.if_, step.if_):
        roles = restricted_roles(cond, event)
        if "comment-author" in roles and activity == "created":
            roles |= {"actor"}
        if "review-author" in roles and activity == "submitted":
            roles |= {"actor"}
        if "entity-author" in roles:
            roles |= {"issue-author"}
        trusted |= roles & set(ROLES)
        if roles:
            notes.append(f"if: condition restricts {sorted(roles)}")
    if "actor" in trusted and ((ev == "issues" and activity == "opened")):
        trusted.add("issue-author")
    if "actor" in trusted and ev == "pull_request" and activity == "opened":
        trusted.add("pr-author")
    # A8 trusts the pull request author on fork events because a fork gets no secrets and so
    # cannot authenticate. That holds only when the model credential comes from a secret: an
    # anthropic_api_key or OAuth token given as a literal or from vars/inputs is available to a
    # fork run too, so the author is then a genuine outsider.
    key_reachable_on_fork = False
    for k in ("anthropic_api_key", "claude_code_oauth_token"):
        raw = step.with_.get(k, "")
        if raw and (is_literal(raw) or any(r.startswith(("vars.", "inputs.", "github.event.inputs"))
                                           for e in expressions(raw) for r in references(e))):
            key_reachable_on_fork = True
    if event in FORK_WITHOUT_SECRETS and not key_reachable_on_fork:
        trusted.add("pr-author")                                     # A8
    elif event in FORK_WITHOUT_SECRETS:
        notes.append("a non-secret model credential is configured, so a fork pull request can run; "
                     "the pull request author is treated as an outsider")
    include = literal_list(inputs.include_comments_by_actor)
    if include and all("*" not in u or u.endswith("[bot]") for u in include):
        trusted.add("commenters")
        notes.append("include_comments_by_actor limits comments to named accounts")
    return trusted, notes


def _checkout_role(job: Job, step: Step, event: str) -> tuple[str, str]:
    """Who authored the files in the workspace when an agent-mode session starts.

    actions/checkout checks out ``github.ref`` unless given ``ref``: the merge commit for
    ``pull_request`` and review events, the base or default branch otherwise.
    """
    checkouts = [s for s in job.steps[:step.index] if s.uses and s.uses.slug == "actions/checkout"]
    if not checkouts:
        return "workflow", "no checkout before the agent step"
    ref = checkouts[-1].with_.get("ref", "")
    refs = [r for e in expressions(ref) for r in references(e)]
    if any(r.startswith(("github.event.pull_request.head", "github.head_ref")) for r in refs) \
            or re.search(r"refs/pull/", ref):
        return "pr-author", f"checkout of {ref!r}"
    if any(r.startswith(("steps.", "needs.", "env.", "inputs.", "github.event.workflow_run")) for r in refs):
        return "unknown", f"checkout of {ref!r}"
    if not ref and event in FORK_WITHOUT_SECRETS:
        return "pr-author", "checkout of the pull request merge commit"
    return "workflow", f"checkout of {ref or 'the default ref'}"


def _field_role(ref: str, event: str, activity: str | None) -> str | None:
    """Who writes a context field (``None`` if nobody controls it as text)."""
    if ref.startswith(("secrets.", "vars.", "runner.", "matrix.", "strategy.", "job.")):
        return None
    if re.fullmatch(r"github\.(repository|repository_owner|run_id|run_number|run_attempt|sha|ref|"
                    r"workflow|event_name|server_url|api_url|job|action_path|workspace)", ref):
        return None
    if re.fullmatch(r"github\.event\.(issue|pull_request|comment|review)\.(number|id|html_url|url|"
                    r"created_at|updated_at|author_association|state)", ref) \
            or re.fullmatch(r"github\.event\.(repository|number|action)(\..*)?", ref):
        return None
    if ref.startswith(("inputs.", "github.event.inputs.", "github.event.client_payload")):
        return "workflow"
    if ref in ("github.actor", "github.triggering_actor") or ref.startswith("github.event.sender"):
        return "actor"
    if ref.startswith(("github.event.comment", "github.event.review")):
        return "actor" if activity in ("created", "submitted") else "commenters"
    if ref.startswith("github.event.issue"):
        return "issue-author"
    if ref.startswith("github.event.pull_request") or ref in ("github.head_ref", "github.base_ref"):
        return "pr-author"
    if ref.startswith("github.event.workflow_run"):
        return "unknown"
    if ref.startswith(("steps.", "needs.", "env.", "github.event.")):
        return "unknown"
    return None


def _tools(rc: RunConfig, inputs: Inputs, job: Job) -> None:
    args = parse_claude_args(inputs.claude_args)
    user_allowed = tool_list(args.get("allowedTools"))
    signing = equals(inputs.use_commit_signing, "true")               # src/github/context.ts:157
    api_signing = t_and(signing, t_not(nonempty(inputs.ssh_signing_key)))
    if rc.mode == "tag":                                               # src/modes/tag/index.ts:123-186
        tools = ["Glob", "Grep", "LS", "Read", "mcp__github_comment__update_claude_comment",
                 "mcp__github_ci__get_ci_status", "mcp__github_ci__get_workflow_run_details",
                 "mcp__github_ci__download_job_log",
                 *[t for t in user_allowed if t.startswith("mcp__github_")]]
        if possible(t_not(api_signing)):
            tools += ["Bash(git add:*)", "Bash(git commit:*)",
                      "Bash($GITHUB_ACTION_PATH/scripts/git-push.sh:*)", "Bash(git rm:*)"]
        if possible(api_signing):
            tools += ["mcp__github_file_ops__commit_files", "mcp__github_file_ops__delete_files"]
        rc.permission_mode = "acceptEdits"
        rc.allowed_tools = list(dict.fromkeys(tools + user_allowed))
    else:
        rc.allowed_tools = user_allowed                                # src/modes/agent/index.ts:94
    rc.disallowed_tools = tool_list(args.get("disallowedTools"))
    if isinstance(args.get("permission-mode"), str):                   # the last flag wins
        rc.permission_mode = args["permission-mode"]
    # Passed through to Claude Code as its own flag (base-action/src/parse-sdk-options.ts:334).
    rc.skip_permissions = "dangerously-skip-permissions" in args

    def has(server: str) -> bool:
        return any(t == server or t.startswith(server + "__") for t in rc.allowed_tools)
    agent = rc.mode == "agent"                                         # src/mcp/install-mcp-server.ts:86-230
    servers: dict[str, Tri] = {}
    if not agent or has("mcp__github_comment"):
        servers["github_comment"] = True
    if possible(signing):
        servers["github_file_ops"] = signing
    if possible(rc.is_pr) and (has("mcp__github") or has("mcp__github_inline_comment")):
        servers["github_inline_comment"] = rc.is_pr
    actions = job.permissions.level("actions")
    if (not agent or has("mcp__github_ci")) and possible(rc.is_pr) and actions != "none":
        servers["github_ci"] = t_and(rc.is_pr, None if actions is None else True)
    if has("mcp__github"):
        servers["github"] = True
    rc.mcp_servers = servers


def _sources(rc: RunConfig, inputs: Inputs, job: Job, step: Step) -> None:
    ev, act = as_parsed(rc.event), rc.activity
    if rc.mode == "tag":                                               # src/create-prompt/index.ts:640-712
        body_role = "issue-author" if ev == "issues" or (ev == "issue_comment" and rc.is_pr is not True) \
            else "pr-author"
        rc.sources.append(Source("Input/entity/body", body_role, "issue or pull request title and body",
                                 cite("src/create-prompt/index.ts:670")))
        if act != "opened":  # only comments older than the trigger (src/github/data/fetcher.ts:244-270)
            rc.sources.append(Source("Input/entity/comments", "commenters", "earlier comments",
                                     cite("src/create-prompt/index.ts:674")))
            if possible(rc.is_pr):
                rc.sources.append(Source("Input/pr/review_comments", "commenters", "review comments",
                                         cite("src/create-prompt/index.ts:680")))
        if possible(rc.is_pr):
            rc.sources.append(Source("Input/pr/head_ref", "pr-author", "head branch name",
                                     cite("src/github/data/formatter.ts:24")))
            rc.sources.append(Source("Input/pr/changed_files", "pr-author", "changed file names",
                                     cite("src/create-prompt/index.ts:688")))
            # Tag mode checks out the pull request head (src/github/operations/branch.ts:163-216).
            rc.workspace_role = "pr-author"
        if ev in ("issue_comment", "pull_request_review_comment", "pull_request_review"):
            rc.sources.append(Source("Input/trigger/comment", "actor", "the trigger comment",
                                     cite("src/create-prompt/index.ts:709")))
        rc.sources.append(Source("Input/trigger/display_name", "actor", "the trigger user's display name",
                                 cite("src/create-prompt/index.ts:702")))
    else:
        for expr in expressions(inputs.prompt):
            for ref in references(expr):
                role = _field_role(ref, rc.event, act)
                if role:
                    rc.sources.append(Source("Input/prompt/" + ref.replace(".", "/"), role,
                                             f"prompt interpolates {ref}", "action.yml:293"))
        rc.workspace_role, why = _checkout_role(job, step, rc.event)
        rc.notes.append(why)


def configure(workflow: Workflow, job: Job, step: Step, event: str, activity: str | None,
              mode: str, is_pr: Tri = None) -> RunConfig:
    """The run configuration for one event, activity type and mode."""
    inputs = Inputs.of(workflow, job, step)
    ev = as_parsed(event)
    if ev != "issue_comment":
        is_pr = ev in ("pull_request", "pull_request_review", "pull_request_review_comment")
    rc = RunConfig(event, activity, mode, None, is_pr=is_pr)
    rc.trusted_roles, notes = trusted_roles(workflow, job, step, inputs, event, activity)
    rc.notes.extend(notes)
    anw = nonempty(inputs.allowed_non_write_users)
    rc.token_in_git_config = t_not(anw)                                # src/github/operations/git-config.ts:108-135
    rc.credential_helper = anw
    rc.env_scrub = False if inputs.env_scrub == "0" else t_or(nonempty(inputs.env_scrub), anw)  # action.yml:301
    _tools(rc, inputs, job)
    _sources(rc, inputs, job, step)
    return rc


def oidc_unavailable(inputs: Inputs, job: Job) -> bool:
    """Without github_token the action exchanges a GitHub OIDC token for its app token
    (src/github/token.ts:158-185), which requires ``id-token: write``; no default grants it."""
    return nonempty(inputs.github_token) is False and job.permissions.level("id-token") != "write"


def configurations(workflow: Workflow, job: Job, step: Step) -> list[RunConfig]:
    """Every run configuration the workflow file allows for a step."""
    inputs = Inputs.of(workflow, job, step)
    out = []
    for trig in workflow.triggers:
        for act in activities(trig.event, trig.types):
            if event_excluded(job.if_, trig.event) or event_excluded(step.if_, trig.event):
                out.append(RunConfig(trig.event, act, None, False, "job-if"))
                continue
            found, stop = modes(inputs, trig.event, act)
            if not stop and oidc_unavailable(inputs, job):
                stop = "oidc-unavailable"
            if stop:
                out.append(RunConfig(trig.event, act, None, False, stop))
                continue
            for mode in sorted(found):
                out.append(configure(workflow, job, step, trig.event, act, mode))
    return out


# --- Layer 2: envelope ---------------------------------------------------------------------

def _permits(env: Envelope, request: str) -> bool:
    x = parse_path(request)
    permitted = any(Pat.parse(s.pattern).covers(x) for s in env.statements if s.kind == "permit")
    return permitted and not any(Pat.parse(s.pattern).covers(x) for s in env.statements if s.kind == "forbid")


def envelope(rc: RunConfig, workflow: Workflow, job: Job, step: Step,
             conservative: Iterable[str] = (), trust_unknown: bool = False) -> Envelope:
    """The authority envelope of a run configuration.

    ``trust_unknown`` treats text whose author the model cannot determine (outputs of earlier
    steps, environment values) as trusted. It is not sound; it exists to measure how much
    the verdicts depend on that choice."""
    inputs = Inputs.of(workflow, job, step)
    env = Envelope(ACTION, COMMIT, rc.event, rc.activity, rc.mode,
                   "maybe" if rc.runs is None else rc.runs)
    env.notes.extend(rc.notes)
    if rc.runs is False:
        return env
    env.assumptions.update({"A1", "A2", "A3", "A6", "A7", "A9"})

    def outsider(role: str) -> bool:
        return not (trust_unknown and role == "unknown") and possible(rc.outsider_writes(role))

    for s in rc.sources:
        env.permit(s.request, s.why, s.source)
        if outsider(s.role):
            env.label(s.request, UNTRUSTED, f"{s.why}, written by {s.role}", "A3")

    if rc.permission_mode == "bypassPermissions" or rc.skip_permissions:
        env.permit("**", "permissions bypassed (bypassPermissions or --dangerously-skip-permissions)", "A2")
    for t in READ_ONLY_TOOLS:
        env.permit(f"{t}/**", "read-only tool", "A1")
    edits_everywhere = False
    for rule in rc.allowed_tools:
        m = map_rule(rule.replace("$GITHUB_ACTION_PATH/", "@action/"))
        if m.pattern:
            env.permit(m.pattern, f"allowed tool {rule}" + (f" ({m.note})" if m.note else ""), "A2")
            edits_everywhere |= m.pattern in {f"{t}/**" for t in EDIT_TOOLS}
    if rc.permission_mode == "acceptEdits":
        for t in EDIT_TOOLS:
            env.permit(f"{t}/**", "acceptEdits: edits in the workspace", cite("src/modes/tag/index.ts:183-186"))
            if not edits_everywhere:
                for outside in ("~", "@"):
                    env.forbid(f"{t}/{outside}/**", "acceptEdits: no edits outside the workspace",
                               cite("src/modes/tag/index.ts:128-131"))
    for rule in rc.disallowed_tools:
        m = map_rule(rule, allow=False)
        if m.pattern:
            env.forbid(m.pattern, f"disallowed tool {rule}", "A2")
            if m.pattern.startswith("Read/"):
                # Claude Code applies Read deny rules to the other file-reading tools (A2).
                for t in ("Grep", "Glob", "LS"):
                    env.forbid(t + m.pattern[4:], f"disallowed tool {rule}", "A2")
        else:
            env.notes.append(f"disallowed rule {rule!r} not modeled ({m.note}); dropping it widens the envelope")
    if inputs.settings.strip().startswith("{") and is_literal(inputs.settings):
        try:
            perms = json.loads(inputs.settings).get("permissions", {}) or {}
            for rule in perms.get("allow", []):
                if (m := map_rule(rule)).pattern:
                    env.permit(m.pattern, f"settings allow {rule}", "A2")
            for rule in perms.get("deny", []):
                if (m := map_rule(rule, allow=False)).pattern:
                    env.forbid(m.pattern, f"settings deny {rule}", "A2")
            if perms.get("defaultMode") == "bypassPermissions":
                env.permit("**", "settings defaultMode bypassPermissions", "A2")
        except (ValueError, AttributeError, TypeError):
            env.notes.append("settings input is not a JSON object")
    elif inputs.settings:
        env.notes.append("settings input is a file path or expression; its rules are not modeled (A6)")
    installed = {s for s, v in rc.mcp_servers.items() if possible(v)}
    for st in [s for s in env.statements if s.kind == "permit" and s.pattern.startswith("mcp/github")]:
        server = st.pattern.split("/")[1]
        if server not in installed:
            env.statements.remove(st)
            env.notes.append(f"{st.pattern} is allowed but its server is not installed")
    if possible(equals(inputs.display_report, "true")):
        env.permit("Output/step-summary", "display_report publishes Claude's report", "action.yml:152-155")

    # Outsider text reached through tools.
    ws = outsider(rc.workspace_role)
    if not ws and any(_permits(env, r) for r in catalog.CHECKOUT_REQUESTS):
        ws = True
        env.notes.append("the agent can check out pull request code")
    if ws:
        for p in (*(f"{t}/**" for t in READ_ONLY_TOOLS), *catalog.bash(catalog.FILE_PRINTERS)):
            env.label(p, UNTRUSTED, "the workspace can hold pull request content", "A3")
    for p in catalog.UNTRUSTED_TOOL_OUTPUT:
        env.label(p, UNTRUSTED, "returns text an outsider can author", "A3")
    if possible(rc.is_pr) and possible(rc.outsider_writes("pr-author")):
        for p in catalog.ACTION_MCP_READ:
            env.label(p, UNTRUSTED, "CI logs of pull request code", "A3")

    # Credentials.
    if possible(rc.token_in_git_config):
        for p in catalog.GIT_CONFIG_READERS:
            env.label(p, SECRET, "the token is in the remote URL in .git/config",
                      cite("src/github/operations/git-config.ts:130-133"))
    if rc.env_scrub is True:
        env.assumptions.add("A5")
    else:
        for p in catalog.ENV_SECRET_READERS:
            env.label(p, SECRET, "GITHUB_TOKEN and GH_TOKEN are in Claude's environment",
                      cite("src/entrypoints/run.ts:190-191"))
    if "A4" in set(conservative):
        env.label("Read/@/proc/**", SECRET, "process environments", "A4")
    return env


def envelopes(workflow: Workflow, conservative: Iterable[str] = ()) -> list[tuple[Job, Step, RunConfig, Envelope]]:
    """Every (job, step, configuration, envelope) for the action in a workflow."""
    return [(job, step, rc, envelope(rc, workflow, job, step, conservative))
            for job, step in workflow.steps_using(ACTION)
            for rc in configurations(workflow, job, step)]
