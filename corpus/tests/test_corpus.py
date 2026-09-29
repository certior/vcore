"""Unit tests for the corpus helpers (run: ``python3 -m pytest corpus/tests``)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clean import resolve_ref, version_family  # noqa: E402
from common import agent_steps, load_workflow, ref_class, split_uses, uses_in_repo  # noqa: E402
from describe import (effective_write_scopes, parse_cli_args, ref_expressions, split_tools,  # noqa: E402
                      tool_category, triggers)
from fetch import git_blob_sha  # noqa: E402

WORKFLOW = """
on:
  issue_comment:
    types: [created]
  pull_request: {}
permissions:
  contents: read
jobs:
  claude:
    permissions:
      pull-requests: write
      id-token: write
    steps:
      - uses: actions/checkout@v4
      - uses: Anthropics/Claude-Code-Action@v1   # case differs, still the action
        with:
          track_progress: yes
          prompt: "Review ${{ github.event.pull_request.title }} #${{ github.event.pull_request.number }}"
      - uses: anthropics/claude-code-action/base-action@beta
  reuse:
    uses: anthropics/claude-code-action/.github/workflows/x.yml@main
"""


def test_yaml_12_booleans():
    doc = load_workflow(WORKFLOW)
    assert "on" in doc and True not in doc
    step = doc["jobs"]["claude"]["steps"][1]
    assert step["with"]["track_progress"] == "yes"
    assert load_workflow("a: true\nb: off\n") == {"a": True, "b": "off"}


def test_agent_steps_exclude_subpaths_and_reusable_workflows():
    doc = load_workflow(WORKFLOW)
    found = list(agent_steps(doc, "anthropics/claude-code-action"))
    assert [(job, i) for job, _, i, _ in found] == [("claude", 1)]
    assert len(uses_in_repo(doc, "anthropics/claude-code-action")) == 3


def test_uses_and_refs():
    assert split_uses("Owner/Repo/sub/dir@v2") == ("owner/repo", "sub/dir", "v2")
    assert split_uses("owner/repo") == ("owner/repo", "", None)
    assert [ref_class(r) for r in (None, "v1", "v1.0", "1.2.3", "beta", "main", "a" * 40, "x")] == [
        "none", "major-version", "minor-version", "exact-version", "beta", "default-branch", "sha", "other"]


def test_resolve_ref():
    commits = {"b" * 40: ["v1", "v1.0.183"], "c" * 40: ["v0.0.63"]}
    assert resolve_ref("B" * 40, commits) == ("v1.0.183", "v1")
    assert resolve_ref("c" * 40, commits) == ("v0.0.63", "v0")
    assert resolve_ref("d" * 40, commits) == (None, "untagged commit")
    assert resolve_ref("beta", commits) == ("beta", "beta")
    assert version_family("release-2") == "other"


def test_triggers_and_permissions():
    doc = load_workflow(WORKFLOW)
    assert triggers(doc) == {"issue_comment": ["created"], "pull_request": None}
    assert triggers({"on": "push"}) == {"push": None}
    assert triggers({"on": ["push", "issues"]}) == {"push": None, "issues": None}
    level, scopes = effective_write_scopes(doc["permissions"], doc["jobs"]["claude"]["permissions"])
    assert level == "job" and scopes == {"pull-requests", "id-token"}
    assert effective_write_scopes(None, None) == ("unspecified", set())
    assert "contents" in effective_write_scopes("write-all", None)[1]


def test_claude_args_and_tools():
    args = '--allowedTools "Bash(gh pr comment:*),Bash(gh pr diff:*) Read" --model claude-opus-4-1\n--max-turns=5'
    flags = parse_cli_args(args)
    assert flags["--model"] == ["claude-opus-4-1"] and flags["--max-turns"] == ["5"]
    tools = split_tools(flags["--allowedTools"][0])
    assert tools == ["Bash(gh pr comment:*)", "Bash(gh pr diff:*)", "Read"]
    assert [tool_category(t) for t in ("Bash", "Bash(*)", "Bash(git push:*)", "WebFetch(domain:x.io)",
                                       "mcp__github_inline_comment__create_inline_comment", "Edit")] == [
        "Bash (unrestricted)", "Bash (unrestricted)", "Bash(git ...)", "WebFetch(restricted)",
        "mcp__github_inline_comment", "Edit/Write"]


def test_interpolations():
    refs = ref_expressions("Fix ${{ github.event.issue.body }} for ${{ github.event.issue.user.login }} "
                           "${{ github.event.commits[0].message }}")
    assert refs == ["github.event.issue.body", "github.event.issue.user.login", "github.event.commits[0].message"]


def test_git_blob_sha():
    # `printf 'hello\n' | git hash-object --stdin`
    assert git_blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"
