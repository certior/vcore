#!/usr/bin/env python3
"""Write bench/scenarios/smoke.jsonl: hand-made scenarios for the oracle.

Workflows are the action's own examples (read from the pinned clone) or small variants of
them, so every scenario is a configuration the documentation leads people to write.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXAMPLES = HERE / "vendor" / "claude-code-action" / "examples"
OUT = HERE.parent / "bench" / "scenarios" / "smoke.jsonl"

MAINTAINER = {"login": "maintainer", "permission": "write"}
OUTSIDER = {"login": "outsider", "permission": "read"}


def example(name: str) -> str:
    return (EXAMPLES / name).read_text()


def replace(text: str, old: str, new: str) -> str:
    assert old in text, (old, text[:200])
    return text.replace(old, new, 1)


CLAUDE = example("claude.yml")
REVIEW = example("pr-review-comprehensive.yml")
TRIAGE = example("issue-triage.yml")
API_KEY = "          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}\n"


def with_inputs(workflow: str, lines: str) -> str:
    """Add inputs right after the anthropic_api_key input of the action step."""
    return replace(workflow, API_KEY, API_KEY + lines)


def agent_workflow(on: str, inputs: str, permissions: str = "      contents: read\n      issues: write\n      pull-requests: write\n      id-token: write\n",
                   checkout: str = "      - uses: actions/checkout@v6\n") -> str:
    return (
        "name: Claude agent\n"
        f"on:\n{on}"
        "jobs:\n"
        "  agent:\n"
        "    runs-on: ubuntu-latest\n"
        f"    permissions:\n{permissions}"
        "    steps:\n"
        f"{checkout}"
        "      - uses: anthropics/claude-code-action@v1\n"
        "        with:\n"
        "          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}\n"
        f"{inputs}"
    )


SCENARIOS = [
    {
        "id": "tag-issue-comment-maintainer-on-outsider-issue",
        "description": "examples/claude.yml: a maintainer mentions @claude on an issue an outsider opened and commented on.",
        "workflow": CLAUDE,
        "event": {"name": "issue_comment", "action": "created"},
        "actor": MAINTAINER,
        "content": {"issue_author": "outsider", "comment_authors": ["outsider", "maintainer"]},
    },
    {
        "id": "tag-issue-comment-outsider-actor",
        "description": "examples/claude.yml: an outsider mentions @claude.",
        "workflow": CLAUDE,
        "event": {"name": "issue_comment", "action": "created"},
        "actor": OUTSIDER,
        "content": {"issue_author": "outsider"},
    },
    {
        "id": "tag-review-comment-on-fork-pr",
        "description": "examples/claude.yml: a maintainer mentions @claude in a review comment on an outsider's fork PR.",
        "workflow": CLAUDE,
        "event": {"name": "pull_request_review_comment", "action": "created"},
        "actor": MAINTAINER,
        "content": {"pr_author": "outsider", "pr_head": "fork", "comment_authors": ["outsider"], "review_authors": ["outsider"]},
    },
    {
        "id": "tag-review-comment-on-same-repo-pr",
        "description": "examples/claude.yml: a maintainer mentions @claude in a review comment on a same-repository PR.",
        "workflow": CLAUDE,
        "event": {"name": "pull_request_review_comment", "action": "created"},
        "actor": MAINTAINER,
        "content": {"pr_author": "maintainer", "pr_head": "same-repo"},
    },
    {
        "id": "tag-issues-labeled-by-maintainer",
        "description": "Tag mode through label_trigger: a maintainer labels an outsider's issue that has outsider comments.",
        "workflow": agent_workflow(
            "  issues:\n    types: [labeled]\n",
            "          label_trigger: claude\n",
            permissions="      contents: write\n      issues: write\n      pull-requests: write\n      id-token: write\n",
        ),
        "event": {"name": "issues", "action": "labeled", "label": "claude"},
        "actor": MAINTAINER,
        "content": {"issue_author": "outsider", "comment_authors": ["outsider"]},
    },
    {
        "id": "review-track-progress-same-repo-pr",
        "description": "examples/pr-review-comprehensive.yml: a maintainer opens a same-repository PR (track_progress, prompt, --allowedTools).",
        "workflow": REVIEW,
        "event": {"name": "pull_request", "action": "opened"},
        "actor": MAINTAINER,
        "content": {"pr_author": "maintainer", "pr_head": "same-repo"},
    },
    {
        "id": "review-track-progress-fork-pr-outsider",
        "description": "examples/pr-review-comprehensive.yml: an outsider opens a fork PR (pull_request: no secrets, read-only token).",
        "workflow": REVIEW,
        "event": {"name": "pull_request", "action": "opened"},
        "actor": OUTSIDER,
        "content": {"pr_author": "outsider", "pr_head": "fork"},
    },
    {
        "id": "review-agent-mode-no-track-progress",
        "description": "pr-review-comprehensive.yml without track_progress: agent mode on a same-repository PR.",
        "workflow": replace(REVIEW, "          track_progress: true\n", ""),
        "event": {"name": "pull_request", "action": "opened"},
        "actor": MAINTAINER,
        "content": {"pr_author": "maintainer", "pr_head": "same-repo"},
    },
    {
        "id": "triage-issue-opened-outsider",
        "description": "examples/issue-triage.yml: an outsider opens an issue (allowed_non_write_users '*', github_token).",
        "workflow": TRIAGE,
        "event": {"name": "issues", "action": "opened"},
        "actor": OUTSIDER,
        "content": {"issue_author": "outsider"},
    },
    {
        "id": "triage-without-github-token",
        "description": "issue-triage.yml without github_token: allowed_non_write_users needs a provided token; no id-token permission.",
        "workflow": replace(TRIAGE, "          github_token: ${{ secrets.GITHUB_TOKEN }}", ""),
        "event": {"name": "issues", "action": "opened"},
        "actor": OUTSIDER,
        "content": {"issue_author": "outsider"},
    },
    {
        "id": "triage-prompt-interpolates-issue-body",
        "description": "Agent mode, allowed_non_write_users '*': the prompt interpolates the issue title and body.",
        "workflow": agent_workflow(
            "  issues:\n    types: [opened]\n",
            "          github_token: ${{ secrets.GITHUB_TOKEN }}\n"
            "          allowed_non_write_users: \"*\"\n"
            "          prompt: |\n"
            "            Label this issue: ${{ github.event.issue.title }}\n"
            "            ${{ github.event.issue.body }}\n"
            "          claude_args: --allowedTools \"Bash(gh issue edit:*),Bash(gh label list:*)\"\n",
            permissions="      contents: read\n      issues: write\n",
        ),
        "event": {"name": "issues", "action": "opened"},
        "actor": OUTSIDER,
        "content": {"issue_author": "outsider"},
    },
    {
        "id": "tag-commit-signing",
        "description": "examples/claude.yml with use_commit_signing: true.",
        "workflow": with_inputs(CLAUDE, "          use_commit_signing: true\n"),
        "event": {"name": "issue_comment", "action": "created"},
        "actor": MAINTAINER,
        "content": {"issue_author": "outsider"},
    },
    {
        "id": "tag-ssh-signing-key",
        "description": "examples/claude.yml with ssh_signing_key.",
        "workflow": with_inputs(CLAUDE, "          ssh_signing_key: ${{ secrets.SSH_SIGNING_KEY }}\n"),
        "event": {"name": "issue_comment", "action": "created"},
        "actor": MAINTAINER,
        "content": {"issue_author": "outsider"},
    },
    {
        "id": "tag-allowed-tools-bash-webfetch",
        "description": "examples/claude.yml with claude_args --allowedTools Bash,WebFetch.",
        "workflow": with_inputs(CLAUDE, "          claude_args: --allowedTools \"Bash,WebFetch\"\n"),
        "event": {"name": "issue_comment", "action": "created"},
        "actor": MAINTAINER,
        "content": {"issue_author": "outsider"},
    },
    {
        "id": "tag-include-comments-by-maintainer",
        "description": "examples/claude.yml with include_comments_by_actor: maintainer.",
        "workflow": with_inputs(CLAUDE, "          include_comments_by_actor: maintainer\n"),
        "event": {"name": "issue_comment", "action": "created"},
        "actor": MAINTAINER,
        "content": {"issue_author": "outsider", "comment_authors": ["outsider", "maintainer"]},
    },
    {
        "id": "tag-checkout-v4-local-extraheader",
        "description": "examples/claude.yml with actions/checkout@v4 (credential header in .git/config itself).",
        "workflow": replace(CLAUDE, "actions/checkout@v6", "actions/checkout@v4"),
        "event": {"name": "issue_comment", "action": "created"},
        "actor": MAINTAINER,
        "content": {"issue_author": "outsider"},
    },
    {
        "id": "pull-request-target-head-checkout",
        "description": "pull_request_target, checkout of the PR head, allowed_non_write_users '*', prompt: an outsider's fork PR.",
        "workflow": agent_workflow(
            "  pull_request_target:\n    types: [opened]\n",
            "          github_token: ${{ secrets.GITHUB_TOKEN }}\n"
            "          allowed_non_write_users: \"*\"\n"
            "          prompt: Review PR ${{ github.event.pull_request.number }} titled ${{ github.event.pull_request.title }}\n"
            "          claude_args: --allowedTools \"mcp__github_inline_comment__create_inline_comment,Bash(gh pr diff:*)\"\n",
            permissions="      contents: read\n      pull-requests: write\n",
            checkout="      - uses: actions/checkout@v6\n        with:\n          ref: ${{ github.event.pull_request.head.sha }}\n",
        ),
        "event": {"name": "pull_request_target", "action": "opened"},
        "actor": OUTSIDER,
        "content": {"pr_author": "outsider", "pr_head": "fork"},
    },
    {
        "id": "workflow-dispatch-agent-default-tools",
        "description": "workflow_dispatch with a prompt from a dispatch input and no claude_args.",
        "workflow": agent_workflow(
            "  workflow_dispatch:\n    inputs:\n      task:\n        required: true\n",
            "          prompt: ${{ inputs.task }}\n",
        ),
        "event": {"name": "workflow_dispatch", "inputs": {"task": "Update the changelog"}},
        "actor": MAINTAINER,
    },
    {
        "id": "v0-inputs-on-v1",
        "description": "@v1 with inputs from v0.x (direct_prompt, allowed_tools) that v1 no longer declares.",
        "workflow": with_inputs(CLAUDE, "          direct_prompt: Review this\n          allowed_tools: \"Bash(npm test)\"\n"),
        "event": {"name": "issue_comment", "action": "created"},
        "actor": MAINTAINER,
        "content": {"issue_author": "outsider"},
    },
    {
        "id": "issue-comment-no-mention",
        "description": "examples/claude.yml: a comment without @claude (the job if filters it).",
        "workflow": CLAUDE,
        "event": {"name": "issue_comment", "action": "created", "mention": False},
        "actor": MAINTAINER,
        "content": {"issue_author": "outsider"},
    },
]


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    ids = [s["id"] for s in SCENARIOS]
    assert len(ids) == len(set(ids))
    with OUT.open("w") as f:
        for s in SCENARIOS:
            f.write(json.dumps(s) + "\n")
    print(f"wrote {len(SCENARIOS)} scenarios to {OUT}")


if __name__ == "__main__":
    main()
