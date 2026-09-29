"""Workflow parsing, expressions, if-conditions and Claude Code rule mapping."""

import pytest

from vcore.actions.conditions import event_excluded, restricted_roles
from vcore.actions.model import map_rule, parse_claude_args, tool_list
from vcore.actions.workflow import (Uses, WorkflowError, expressions, has_literal_text, load,
                                    references)


def test_on_is_a_string_key():
    w = load("on: push\njobs: {}\n")
    assert [t.event for t in w.triggers] == ["push"]
    w = load("on:\n  issues:\n    types: [opened, labeled]\n  issue_comment:\njobs: {}\n")
    assert [(t.event, t.types) for t in w.triggers] == [("issues", ("opened", "labeled")), ("issue_comment", None)]
    w = load("on: [push, pull_request]\njobs: {}\n")
    assert [t.event for t in w.triggers] == ["push", "pull_request"]


def test_yes_no_stay_strings():
    w = load("on: push\njobs:\n  a:\n    steps:\n      - uses: x/y@v1\n        with:\n          k: yes\n          b: true\n")
    assert w.jobs[0].steps[0].with_ == {"k": "yes", "b": "true"}


def test_malformed():
    for text in ["[]", "jobs: {}", "on: push\njobs: [1]", "on: push\npermissions: all\njobs: {}"]:
        with pytest.raises(WorkflowError):
            load(text)


def test_permissions_inheritance():
    w = load("on: push\npermissions: read-all\njobs:\n  a: {steps: []}\n  b:\n    permissions:\n      contents: write\n    steps: []\n")
    a, b = w.jobs
    assert a.permissions.level("actions") == "read"
    assert b.permissions.level("contents") == "write" and b.permissions.level("actions") == "none"
    assert load("on: push\njobs:\n  a: {steps: []}\n").jobs[0].permissions.level("contents") is None


def test_uses():
    u = Uses.parse("anthropics/claude-code-action@v1")
    assert (u.slug, u.ref, u.ref_kind) == ("anthropics/claude-code-action", "v1", "major")
    assert Uses.parse("a/b/sub@" + "0" * 40).ref_kind == "sha"
    assert Uses.parse("a/b@v1.0.3").ref_kind == "exact"
    assert Uses.parse("a/b@beta").ref_kind == "branch"
    assert Uses.parse("./local").slug == ""


def test_expressions_and_references():
    text = "Review ${{ github.event.pull_request.title }} and ${{ github.event['issue'].body || 'x' }}"
    assert expressions(text) == ["github.event.pull_request.title", "github.event['issue'].body || 'x'"]
    assert references("github.event['issue'].body || 'secrets.X'") == ["github.event.issue.body"]
    assert references("contains(github.event.comment.body, '@claude')") == ["github.event.comment.body"]
    assert has_literal_text("a ${{ x }}") and not has_literal_text(" ${{ x }} ")


def test_conditions():
    c = "github.event_name == 'issue_comment' && contains(github.event.comment.body, '@claude')"
    assert event_excluded(c, "issues") and not event_excluded(c, "issue_comment")
    assert not event_excluded("contains(github.event.comment.body, '@claude')", "issues")
    c = ("(github.event_name == 'issue_comment' && github.event.comment.author_association == 'OWNER') || "
         "(github.event_name == 'issues' && contains(github.event.issue.body, '@claude'))")
    assert restricted_roles(c, "issue_comment") == {"comment-author"}
    assert restricted_roles(c, "issues") == set()
    c = "${{ contains(fromJSON('[\"OWNER\", \"MEMBER\"]'), github.event.comment.author_association) }}"
    assert restricted_roles(c, "issue_comment") == {"comment-author"}
    c = "contains(fromJSON('[\"OWNER\", \"CONTRIBUTOR\"]'), github.event.comment.author_association)"
    assert restricted_roles(c, "issue_comment") == set()
    assert restricted_roles("github.actor == 'alice' || github.event.comment.author_association == 'OWNER'",
                            "issue_comment") == set()
    assert restricted_roles("github.actor == 'alice'", "issues") == {"actor"}


@pytest.mark.parametrize("rule,allow,pattern,exact", [
    ("Read", True, "Read/**", True),
    ("Bash", True, "Bash/**", True),
    ("Bash(git add:*)", True, "Bash/git/add/**", True),
    ("Bash(gh issue view:*)", True, "Bash/gh/issue/view/**", True),
    ("Bash(npm run test)", True, "Bash/npm/run/test", True),
    ("Bash(git diff *)", True, "Bash/git/diff/**", True),
    ("Bash(gh pr view * --json *)", True, "Bash/gh/pr/view/**", False),
    ("Bash(echo $(cat x))", True, "Bash/echo/**", False),
    ("Bash(@action/scripts/git-push.sh:*)", True, "Bash/@action/scripts/git-push.sh/**", True),
    ("WebFetch(domain:docs.github.com)", True, "WebFetch/docs.github.com/**", True),
    ("Read(src/**)", True, "Read/src/**", True),
    ("Read(**/*.env)", True, "Read/**", False),
    ("Read(**/*.env)", False, None, False),
    ("Edit(//etc/hosts)", False, "Edit/@/etc/hosts", True),
    ("mcp__github__get_issue", True, "mcp/github/get_issue", True),
    ("mcp__github", True, "mcp/github/**", True),
    ("mcp__github__*", True, "mcp/github/**", True),
    ("TodoWrite", True, "TodoWrite", True),
])
def test_map_rule(rule, allow, pattern, exact):
    m = map_rule(rule, allow)
    assert (m.pattern, m.exact) == (pattern, exact)


def test_claude_args():
    args = parse_claude_args('--max-turns 5\n# --allowedTools "Bash"\n--allowedTools "Read,Grep" "mcp__github__get_issue"'
                             ' --allowed-tools Edit --permission-mode acceptEdits --permission-mode plan --verbose')
    assert tool_list(args["allowedTools"]) == ["Read", "Grep", "mcp__github__get_issue", "Edit"]
    assert args["permission-mode"] == "plan" and args["verbose"] is None and args["max-turns"] == "5"
