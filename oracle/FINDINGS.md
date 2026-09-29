# Findings from executing claude-code-action v1.0.225

Observed by running the unmodified action (`bf38e86`) on the smoke scenarios in
`bench/scenarios/smoke.jsonl`, with the source line that explains each behavior. Line
numbers are at `bf38e86`.

1. **The write check gates the actor, not the content.** `src/entrypoints/run.ts:196-208`
   checks the triggering account. Tag mode's prompt then includes the issue or pull request
   title and body, earlier comments, reviews and review comments, the head branch name and
   the trigger user's display name, whoever wrote them (`src/github/data/fetcher.ts:435,462,531,571,624`).
   `include_comments_by_actor` filters comments and reviews only; titles, bodies and the
   branch name are not filtered.
2. **The token is in `.git/config` unless `allowed_non_write_users` is set.** The origin URL
   becomes `https://x-access-token:<token>@…` (`src/github/operations/git-config.ts:130-134`),
   in both modes and with API commit signing (`src/modes/tag/index.ts:97,107`,
   `src/modes/agent/index.ts:61,71`). With `allowed_non_write_users` set, a credential helper
   reading `GH_TOKEN` is installed instead (`git-config.ts:108-128`). The checkout's own
   credential header is removed in both checkout layouts (`git-config.ts:78-98`).
3. **Credentials are in Claude Code's environment.** The SDK environment copies
   `process.env` minus the OIDC request variables and `ALL_INPUTS`
   (`base-action/src/parse-sdk-options.ts:279,289-294`): `GITHUB_TOKEN` and `GH_TOKEN`
   (`run.ts:190-191`), `DEFAULT_WORKFLOW_TOKEN` (`action.yml:308`), the API key, and
   `OVERRIDE_GITHUB_TOKEN` when `github_token` is given. With `ssh_signing_key`, also
   `SSH_SIGNING_KEY` and `~/.ssh/claude_signing_key` (`git-config.ts:166`).
   `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` is set only when `allowed_non_write_users` is set
   (`action.yml:301`); what the scrub removes is not observable here. MCP server
   configurations carry the token too (`src/mcp/install-mcp-server.ts:121,137,160,203,227`).
4. **Tool lists.** Tag mode passes a fixed list and `--permission-mode acceptEdits`
   (`tag/index.ts:134-162,186`) and appends the user's `claude_args` (`tag/index.ts:189-191`),
   so `--allowedTools "Bash"` adds unrestricted Bash to it. `track_progress: true` forces tag
   mode even with a prompt (`src/modes/detector.ts:21-32`). The `mcp__github_ci__*` tools are
   always listed in tag mode; their server exists only when the workflow token has
   `actions: read` (`install-mcp-server.ts:176-196`). The `ALLOWED_TOOLS`/`DISALLOWED_TOOLS`
   exports in `src/create-prompt/index.ts:983-997` are unused.
5. **Pull request working tree.** Tag mode checks out the pull request head itself, through
   `refs/pull/N/head` for forks (`src/github/operations/branch.ts:207-216`). `.claude/`,
   `.mcp.json`, `CLAUDE.md` and other startup paths are restored from the base branch
   (`run.ts:263-276`, `src/github/operations/restore-config.ts:26-35`); the pull request's
   copies remain readable under `.claude-pr/` (`restore-config.ts:280-293`).
   `pull_request_target` is parsed as `pull_request` (`src/github/context.ts:191-201`).
   Settings sources are `user,project,local` and `enableAllProjectMcpServers` is set
   (`parse-sdk-options.ts:340`, `base-action/src/setup-claude-code-settings.ts:63`).
6. **Other gates.** `allowed_non_write_users` bypasses the write check only when
   `github_token` is given (`src/github/validation/permissions.ts:90`). Without
   `github_token`, the action exchanges an OIDC token, which needs `id-token: write`
   (`src/github/token.ts:158-185`). The tracking comment is posted with the app token before
   Claude runs (`tag/index.ts:46`).

## Environment emulation corrected after review

GitHub withholds secrets and caps the token for runs triggered from a fork not only on
`pull_request` but also on `pull_request_review` and `pull_request_review_comment` (GitHub
Docs, *Events that trigger workflows*, "Workflows in forked repositories"). The first
version of the oracle applied this to `pull_request` only; `src/scenario.ts` now applies it
to all three, and the smoke observations were regenerated.
