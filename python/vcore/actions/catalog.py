"""What tools do: which requests read outsider text, reveal credentials, or write outward.

The classification is part of the attacker model (assumption A3 in
:mod:`vcore.actions.claude_code_action`); each table says where it comes from. All names
are vcore request segments (``mcp/<server>/<tool>``, ``Bash/<argv...>``).
"""

from __future__ import annotations

GITHUB_MCP_SERVER = "github/github-mcp-server v0.17.1 (23fa0dd)"
"""The server image claude-code-action v1.0.225 starts for ``mcp__github__*`` tools
(src/mcp/install-mcp-server.ts:222)."""

# Tools declared with ReadOnlyHint true in github-mcp-server v0.17.1 (pkg/github/*.go);
# get_teams and get_team_members carry no hint and only read, so they are listed here.
GITHUB_MCP_READ = (
    "download_workflow_run_artifact", "enable_toolset", "get_code_scanning_alert", "get_commit",
    "get_dependabot_alert", "get_discussion", "get_discussion_comments", "get_file_contents",
    "get_global_security_advisory", "get_issue", "get_issue_comments", "get_job_logs",
    "get_latest_release", "get_me", "get_notification_details", "get_pull_request",
    "get_pull_request_diff", "get_pull_request_files", "get_pull_request_review_comments",
    "get_pull_request_reviews", "get_pull_request_status", "get_release_by_tag",
    "get_secret_scanning_alert", "get_tag", "get_team_members", "get_teams", "get_toolset_tools",
    "get_workflow_run", "get_workflow_run_logs", "get_workflow_run_usage",
    "list_available_toolsets", "list_branches", "list_code_scanning_alerts", "list_commits",
    "list_dependabot_alerts", "list_discussion_categories", "list_discussions", "list_gists",
    "list_global_security_advisories", "list_issue_types", "list_issues", "list_notifications",
    "list_org_repository_security_advisories", "list_pull_requests", "list_releases",
    "list_repository_security_advisories", "list_secret_scanning_alerts",
    "list_starred_repositories", "list_sub_issues", "list_tags", "list_workflow_jobs",
    "list_workflow_run_artifacts", "list_workflow_runs", "list_workflows", "search_code",
    "search_issues", "search_orgs", "search_pull_requests", "search_repositories", "search_users",
)

# ReadOnlyHint false in the same version.
GITHUB_MCP_WRITE = (
    "add_comment_to_pending_review", "add_issue_comment", "add_sub_issue", "assign_copilot_to_issue",
    "cancel_workflow_run", "create_and_submit_pull_request_review", "create_branch", "create_gist",
    "create_issue", "create_or_update_file", "create_pending_pull_request_review",
    "create_pull_request", "create_repository", "delete_file", "delete_pending_pull_request_review",
    "delete_workflow_run_logs", "dismiss_notification", "fork_repository",
    "manage_notification_subscription", "manage_repository_notification_subscription",
    "mark_all_notifications_read", "merge_pull_request", "push_files", "remove_sub_issue",
    "reprioritize_sub_issue", "request_copilot_review", "rerun_failed_jobs", "rerun_workflow_run",
    "run_workflow", "star_repository", "submit_pending_pull_request_review", "unstar_repository",
    "update_gist", "update_issue", "update_pull_request", "update_pull_request_branch",
)

GITHUB_MCP_REPO_WRITE = (
    "create_branch", "create_or_update_file", "delete_file", "merge_pull_request", "push_files",
    "update_pull_request_branch", "run_workflow", "rerun_failed_jobs", "rerun_workflow_run",
)

# The action's own MCP servers (src/mcp/*.ts at bf38e86).
ACTION_MCP_PUBLIC_WRITE = (
    "mcp/github_comment/update_claude_comment",          # src/mcp/github-comment-server.ts
    "mcp/github_inline_comment/create_inline_comment",   # src/mcp/github-inline-comment-server.ts
    "mcp/github_file_ops/commit_files",                  # src/mcp/github-file-ops-server.ts
    "mcp/github_file_ops/delete_files",
)
ACTION_MCP_REPO_WRITE = ("mcp/github_file_ops/commit_files", "mcp/github_file_ops/delete_files")
ACTION_MCP_READ = ("mcp/github_ci/get_ci_status", "mcp/github_ci/get_workflow_run_details",
                   "mcp/github_ci/download_job_log")  # src/mcp/github-actions-server.ts
PUSH_WRAPPER = "Bash/@action/scripts/git-push.sh"
"""``scripts/git-push.sh``: pushes ``origin <ref>`` only (scripts/git-push.sh:1-40)."""

# gh CLI (manual for gh 2.x). Reads return repository text anyone can author.
GH_READ = ("issue/view", "issue/list", "issue/status", "pr/view", "pr/list", "pr/diff",
           "pr/checks", "pr/status", "search", "run/view", "run/list", "run/download",
           "repo/view", "release/view", "release/list", "release/download", "gist/view",
           "api", "workflow/view", "cache/list", "label/list")
GH_PUBLIC_WRITE = ("api", "issue/comment", "issue/create", "issue/edit", "issue/close",
                   "issue/reopen", "pr/comment", "pr/create", "pr/edit", "pr/review", "pr/close",
                   "pr/merge", "gist", "release/create", "release/edit", "release/upload",
                   "label/create", "label/edit", "repo/create", "repo/fork")
GH_REPO_WRITE = ("api", "pr/merge", "release/create", "release/upload", "workflow/run",
                 "run/rerun", "repo/edit", "secret", "variable")
GH_CHECKOUT = ("pr/checkout",)
GH_TOKEN = ("auth/token", "auth/status")

# Commands that can send data off the runner or run arbitrary code (attacker model).
EGRESS_COMMANDS = ("curl", "wget", "nc", "ncat", "socat", "ssh", "scp", "sftp", "rsync", "ftp",
                   "telnet", "dig", "nslookup", "host", "ping")
INTERPRETERS = ("python", "python3", "node", "bun", "deno", "npx", "npm", "pnpm", "yarn",
                "pip", "pip3", "uv", "perl", "ruby", "php", "sh", "bash", "zsh", "dash",
                "make", "cargo", "go", "java", "docker", "xargs", "find", "env", "eval", "exec")
# Commands that print files (such as .git/config).
FILE_PRINTERS = ("cat", "head", "tail", "less", "more", "grep", "rg", "sed", "awk", "strings",
                 "xxd", "od", "hexdump", "base64", "cp", "tee", "diff", "sort", "uniq", "nl")
ENV_PRINTERS = ("env", "printenv", "set", "export", "declare")
GIT_REVEAL = ("git/config", "git/remote", "git/credential")
GIT_WRITE = ("git/push",)
GIT_CHECKOUT = ("git/fetch", "git/checkout", "git/switch", "git/pull", "git/worktree")


def bash(prefixes: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"Bash/{p}/**" for p in prefixes)


# Requests whose results an outsider can author (labels "untrusted").
UNTRUSTED_TOOL_OUTPUT = (
    "WebFetch/**", "WebSearch", "WebSearch/**", "Bash/@shell",
    *(f"mcp/github/{t}" for t in GITHUB_MCP_READ),
    *bash(tuple(f"gh/{c}" for c in GH_READ)),
    *bash(EGRESS_COMMANDS), *bash(INTERPRETERS),
)

# Requests that can reveal a credential held in Claude's environment (labels "secret").
ENV_SECRET_READERS = ("Bash/@shell", *bash(ENV_PRINTERS), *bash(INTERPRETERS),
                      *bash(tuple(f"gh/{c}" for c in GH_TOKEN)))
# Requests that can reveal the token in .git/config.
GIT_CONFIG_READERS = ("Read/.git/config", "Grep/.git/**", "Bash/@shell", *bash(FILE_PRINTERS),
                      *bash(GIT_REVEAL), *bash(INTERPRETERS))
# Requests that can replace the workspace with pull request content.
CHECKOUT_REQUESTS = ("Bash/@shell", "Bash/gh/pr/checkout/1", "Bash/git/fetch/origin",
                     "Bash/git/checkout/pr", "Bash/git/pull/origin")

# Sinks.
EGRESS = ("WebFetch/**", "WebSearch", "WebSearch/**", "Bash/@shell", *bash(EGRESS_COMMANDS),
          *bash(INTERPRETERS), *bash(GIT_WRITE), "Bash/git/remote/**")
PUBLIC_WRITE = (*ACTION_MCP_PUBLIC_WRITE, f"{PUSH_WRAPPER}/**", "Output/**",
                *(f"mcp/github/{t}" for t in GITHUB_MCP_WRITE),
                *bash(tuple(f"gh/{c}" for c in GH_PUBLIC_WRITE)))
REPO_WRITE = (*ACTION_MCP_REPO_WRITE, f"{PUSH_WRAPPER}/**", "Bash/@shell", *bash(GIT_WRITE),
              *(f"mcp/github/{t}" for t in GITHUB_MCP_REPO_WRITE),
              *bash(tuple(f"gh/{c}" for c in GH_REPO_WRITE)), *bash(INTERPRETERS))
