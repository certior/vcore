"""The claude-code-action model: run configurations, envelopes, verdicts."""

from pathlib import Path

import pytest

from vcore.actions import claude_code_action as cca
from vcore.actions.properties import P1, P2, PROPERTIES, verdict
from vcore.actions.report import diff
from vcore.actions.workflow import load

REPO = Path(__file__).resolve().parents[2]
SEEDS = REPO / "bench" / "seeds" / "claude-code-action"


def workflow(on: str, with_: str = "", job_if: str = "", before: str = "", oidc: bool = True) -> str:
    lines = [f"on: {on}", "jobs:", "  agent:", "    runs-on: ubuntu-latest"]
    if oidc:
        lines += ["    permissions:", "      contents: write", "      issues: write", "      pull-requests: write",
                  "      id-token: write"]
    if job_if:
        lines.append(f"    if: {job_if}")
    lines.append("    steps:")
    lines += [f"      {l}" for l in before.splitlines()]
    lines.append("      - uses: anthropics/claude-code-action@v1")
    lines.append("        with:")
    lines.append("          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}")
    lines += [f"          {l}" for l in with_.splitlines()]
    return "\n".join(lines) + "\n"


def configs(text: str) -> list[cca.RunConfig]:
    w = load(text)
    (job, step), = w.steps_using(cca.ACTION)
    return cca.configurations(w, job, step)


def only(text: str, event: str, activity=None, mode=None) -> cca.RunConfig:
    found = [c for c in configs(text) if c.event == event and c.runs is not False
             and (activity is None or c.activity == activity) and (mode is None or c.mode == mode)]
    assert len(found) == 1, found
    return found[0]


# --- Mode detection (src/modes/detector.ts) ---------------------------------------------

@pytest.mark.parametrize("on,with_,expected", [
    ("{issue_comment: {types: [created]}}", "", {("created", "tag")}),
    ("{issue_comment: {types: [created]}}", "prompt: review", {("created", "agent")}),
    ("{issue_comment: {types: [created]}}", "prompt: ${{ inputs.p }}", {("created", "agent"), ("created", "tag")}),
    ("{pull_request: {types: [opened]}}", "", {("opened", None)}),
    ("{pull_request: {types: [opened]}}", "track_progress: true", {("opened", "tag")}),
    ("{pull_request: {types: [closed]}}", "track_progress: true\nprompt: x", {("closed", None)}),
    ("{issues: {types: [assigned]}}", "", {("assigned", None)}),
    ("{issues: {types: [assigned]}}", "assignee_trigger: claude-bot", {("assigned", "tag")}),
    ("{issues: {types: [labeled]}}", "", {("labeled", "tag")}),
    ("{issues: {types: [labeled]}}", "label_trigger: ''", {("labeled", None)}),
    ("workflow_dispatch", "", {(None, None)}),
    ("workflow_dispatch", "prompt: x", {(None, "agent")}),
    ("push", "prompt: x", {(None, None)}),
])
def test_modes(on, with_, expected):
    assert {(c.activity, c.mode) for c in configs(workflow(on, with_))} == expected


def test_stop_reasons():
    assert {c.stop_reason for c in configs(workflow("push", "prompt: x"))} == {"unsupported-event"}
    assert {c.stop_reason for c in configs(workflow("workflow_dispatch", "track_progress: true\nprompt: x"))} \
        == {"track-progress-invalid-event"}
    text = workflow("[issues, issue_comment]", job_if="github.event_name == 'issue_comment'")
    assert {c.stop_reason for c in configs(text) if c.event == "issues"} == {"job-if"}
    # No github_token and no id-token: write: the OIDC exchange fails (src/github/token.ts:158-185).
    assert {c.stop_reason for c in configs(workflow("workflow_dispatch", "prompt: x", oidc=False))} == {"oidc-unavailable"}
    assert {c.runs for c in configs(workflow("workflow_dispatch", "prompt: x\ngithub_token: ${{ github.token }}",
                                             oidc=False))} == {None}


# --- The actor gate (src/github/validation/permissions.ts, actor.ts) -------------------

def test_actor_gate():
    on = "{issue_comment: {types: [created]}}"
    assert "actor" in only(workflow(on), "issue_comment").trusted_roles
    star = "allowed_non_write_users: '*'\ngithub_token: ${{ secrets.GITHUB_TOKEN }}"
    assert "actor" not in only(workflow(on, star), "issue_comment").trusted_roles
    # Without github_token the bypass is inert (permissions.ts:90).
    rc = only(workflow(on, "allowed_non_write_users: '*'"), "issue_comment")
    assert "actor" in rc.trusted_roles and any("no effect" in n for n in rc.notes)
    # A named list with a token also opens the gate (review finding #1).
    assert "actor" not in only(workflow(on, "allowed_non_write_users: alice\ngithub_token: ${{ github.token }}"),
                               "issue_comment").trusted_roles
    assert "actor" not in only(workflow(on, "allowed_bots: '*'"), "issue_comment").trusted_roles
    rc = only(workflow(on, star, job_if="github.event.comment.author_association == 'OWNER'"), "issue_comment")
    assert "actor" in rc.trusted_roles


def test_author_roles():
    rc = only(workflow("{issues: {types: [opened]}}"), "issues")
    assert {"actor", "issue-author"} <= rc.trusted_roles
    rc = only(workflow("{issues: {types: [labeled]}}"), "issues")
    assert "issue-author" not in rc.trusted_roles
    # A8: a pull_request run that reaches Claude comes from a branch of the repository.
    assert "pr-author" in only(workflow("{pull_request: {types: [synchronize]}}", "prompt: x"), "pull_request").trusted_roles
    assert "pr-author" not in only(workflow("{pull_request_target: {types: [synchronize]}}", "prompt: x"),
                                   "pull_request_target").trusted_roles


# --- Tools, servers, credentials -----------------------------------------------------------

def test_tag_mode_tools():
    rc = only(workflow("{issue_comment: {types: [created]}}"), "issue_comment")
    assert rc.permission_mode == "acceptEdits"
    assert "Bash($GITHUB_ACTION_PATH/scripts/git-push.sh:*)" in rc.allowed_tools
    assert "mcp__github_file_ops__commit_files" not in rc.allowed_tools
    assert rc.mcp_servers["github_comment"] is True and "github_file_ops" not in rc.mcp_servers
    rc = only(workflow("{issue_comment: {types: [created]}}", "use_commit_signing: true"), "issue_comment")
    assert "mcp__github_file_ops__commit_files" in rc.allowed_tools and not any(t.startswith("Bash(") for t in rc.allowed_tools)
    rc = only(workflow("{issue_comment: {types: [created]}}", 'claude_args: --allowedTools "Bash(npm test)"'), "issue_comment")
    assert rc.allowed_tools[-1] == "Bash(npm test)"


def test_agent_mode_tools_and_servers():
    rc = only(workflow("workflow_dispatch", "prompt: x"), "workflow_dispatch")
    assert rc.allowed_tools == [] and rc.permission_mode is None and rc.mcp_servers == {}
    rc = only(workflow("{pull_request: {types: [opened]}}",
                       'prompt: x\nclaude_args: --allowedTools "mcp__github_inline_comment__create_inline_comment,mcp__github__get_pull_request"'),
              "pull_request")
    assert set(rc.mcp_servers) == {"github_inline_comment", "github"}


def test_credentials():
    rc = only(workflow("{issue_comment: {types: [created]}}"), "issue_comment")
    assert (rc.token_in_git_config, rc.credential_helper, rc.env_scrub) == (True, False, False)
    rc = only(workflow("{issue_comment: {types: [created]}}", "allowed_non_write_users: alice"), "issue_comment")
    assert (rc.token_in_git_config, rc.credential_helper, rc.env_scrub) == (False, True, True)
    text = workflow("{issue_comment: {types: [created]}}", "allowed_non_write_users: alice").replace(
        "    runs-on: ubuntu-latest", "    runs-on: ubuntu-latest\n    env:\n      CLAUDE_CODE_SUBPROCESS_ENV_SCRUB: '0'")
    assert only(text, "issue_comment").env_scrub is False


# --- Envelopes and verdicts ----------------------------------------------------------------

def verdicts(vc, text, event, **kw):
    w = load(text)
    (job, step), = w.steps_using(cca.ACTION)
    rc = only(text, event, **kw)
    env = cca.envelope(rc, w, job, step)
    return {p.id: verdict(vc, env.policy(), p) for p in PROPERTIES}


def test_default_tag_mode_admits_both_traces(vcore):
    v = verdicts(vcore, workflow("{issue_comment: {types: [created]}}"), "issue_comment")
    assert not v["exfiltration"].satisfied and not v["untrusted-write"].satisfied
    trace = [s.request for s in v["exfiltration"].counterexample]
    assert trace[-1] == "mcp/github_comment/update_claude_comment"
    assert any(r == "Read/.git/config" for r in trace)


def test_trusted_issue_opened_is_clean(vcore):
    v = verdicts(vcore, workflow("{issues: {types: [opened]}}"), "issues")
    assert v["exfiltration"].satisfied and v["untrusted-write"].satisfied


def test_documented_hardening_satisfies(vcore):
    """docs/security.md:18 recommends minimal tools with allowed_non_write_users."""
    text = workflow("{issues: {types: [opened]}}",
                    "prompt: triage\nallowed_non_write_users: '*'\ngithub_token: ${{ secrets.GITHUB_TOKEN }}\n"
                    "claude_args: --allowedTools \"Bash(gh issue view:*),Bash(gh issue edit:*)\"")
    v = verdicts(vcore, text, "issues")
    assert v["exfiltration"].satisfied and v["untrusted-write"].satisfied


def test_unscrubbed_bash_reveals_environment(vcore):
    text = workflow("{issues: {types: [opened]}}",
                    "prompt: triage\nallowed_non_write_users: '*'\ngithub_token: ${{ secrets.GITHUB_TOKEN }}\n"
                    "claude_args: --allowedTools \"Bash\"").replace(
        "    runs-on: ubuntu-latest", "    runs-on: ubuntu-latest\n    env:\n      CLAUDE_CODE_SUBPROCESS_ENV_SCRUB: '0'")
    v = verdicts(vcore, text, "issues")
    assert not v["exfiltration"].satisfied


def test_prompt_interpolation_is_a_source(vcore):
    text = workflow("{issues: {types: [labeled]}}", "prompt: 'Summarize ${{ github.event.issue.body }}'\n"
                    'claude_args: --allowedTools "mcp__github_comment__update_claude_comment"')
    rc = only(text, "issues")
    assert [(s.request, s.role) for s in rc.sources] == [("Input/prompt/github/event/issue/body", "issue-author")]
    v = verdicts(vcore, text, "issues")
    trace = [s.request for s in v["exfiltration"].counterexample]
    assert trace == ["Input/prompt/github/event/issue/body", "Read/.git/config",
                     "mcp/github_comment/update_claude_comment"]


@pytest.mark.parametrize("seed", sorted(p.name for p in SEEDS.glob("*.yml")))
def test_seed_counterexamples_are_short(vcore, seed):
    """short_counterexample: width + 1 requests at most (3 for P1, 2 for P2)."""
    w = load((SEEDS / seed).read_text())
    for job, step, rc, env in cca.envelopes(w):
        if rc.runs is False:
            continue
        for p, bound in ((P1, 3), (P2, 2)):
            v = verdict(vcore, env.policy(), p)
            assert v.satisfied or len(v.counterexample) <= bound


@pytest.mark.parametrize("seed", sorted(p.name for p in SEEDS.glob("*.yml")))
def test_z3_agrees_on_seed_verdicts(vcore, seed):
    """The independent Z3 encoding reaches the same verdicts as the Lean checker."""
    smt = pytest.importorskip("vcore.smt")
    from vcore.policy import parse_authority
    w = load((SEEDS / seed).read_text())
    for job, step, rc, env in cca.envelopes(w):
        if rc.runs is False:
            continue
        policy = env.policy()
        for p in PROPERTIES:
            old, new = parse_authority([p.policy(), policy]), parse_authority(policy)
            assert (smt.escalation(old, new) is None) == verdict(vcore, policy, p).satisfied


def test_diff(vcore):
    base = workflow("workflow_dispatch", 'prompt: x\nclaude_args: --allowedTools "Bash(npm test)"')
    assert diff(base, base, vcore) == []
    wider = base.replace('"Bash(npm test)"', '"Bash(npm test),Bash(curl:*)"')
    found = diff(base, wider, vcore)
    assert found and found[0]["sequence"][-1].startswith("Bash/curl/")
    assert diff(wider, base, vcore) == []


def test_repairs_for_default_tag_mode(vcore):
    from vcore.actions.repairs import suggest
    text = (SEEDS / "claude.yml").read_text()
    out = suggest(vcore, text, "claude", 1, "issue_comment", "created", "tag", P1)
    assert [(o.repair, o.refines) for o in out] == [("deny-git-credentials", True)]
    assert suggest(vcore, text, "claude", 1, "issues", "opened", "tag", P1) == []  # already satisfied


def test_named_nonwrite_user_is_untrusted(vcore):
    """Review finding #1: a named allowed_non_write_users list also opens the actor gate.

    On issues:opened the issue author is the triggering account, so untrusting the actor makes
    its issue body untrusted. allowed_non_write_users also moves the token to a credential helper
    and scrubs the environment, so P1 (which needs a credential) stays satisfied, but P2
    (untrusted then repository write) is now violated."""
    on = "{issues: {types: [opened]}}"
    with_ = "allowed_non_write_users: alice\ngithub_token: ${{ secrets.GITHUB_TOKEN }}"
    rc = only(workflow(on, with_), "issues")
    assert "actor" not in rc.trusted_roles and "issue-author" not in rc.trusted_roles
    v = verdicts(vcore, workflow(on, with_), "issues")
    assert not v["untrusted-write"].satisfied
    # Without a github_token the list is inert, so the actor stays trusted and the run is clean.
    assert "actor" in only(workflow(on, "allowed_non_write_users: alice"), "issues").trusted_roles
    assert verdicts(vcore, workflow(on, "allowed_non_write_users: alice"), "issues")["untrusted-write"].satisfied


def test_literal_api_key_untrusts_fork_pr_author():
    """Review finding #2: a non-secret model credential lets a fork PR run, so A8 does not apply."""
    on = "{pull_request: {types: [synchronize]}}"
    secret = only(workflow(on, "prompt: review"), "pull_request")
    assert "pr-author" in secret.trusted_roles
    literal = only(workflow(on, "prompt: review").replace(
        "anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}", "anthropic_api_key: sk-ant-literal"),
        "pull_request")
    assert "pr-author" not in literal.trusted_roles
