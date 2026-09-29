# Fidelity log

Each iteration runs the oracle on the scenarios and compares with the model's predictions
(`bench/compare.py`). Every disagreement is resolved by reading the action's source or
GitHub's documentation, and the fix goes where the error was: the model, the predictor's
runner emulation, or the oracle's runner emulation. The action itself is never modified.

## Iteration 0 (smoke set, 20 scenarios; partial generated set, 826 scenarios)

| Disagreement | Resolution | Where |
|---|---|---|
| Fork runs on `pull_request_review_comment` received secrets in the oracle, contradicting assumption A8 | GitHub Docs, *Events that trigger workflows*: secrets are withheld for runs "triggered from a forked repository" on `pull_request`, `pull_request_review` and `pull_request_review_comment` | oracle emulation (`oracle/src/scenario.ts`) |
| Workflows with a custom `trigger_phrase` and a job `if:` still testing `@claude` ran in the oracle | The oracle decided `if:` on its first payload pass, built with the default phrase; conditions are now decided on the fixpoint payload | oracle emulation |
| The model reported no run for workload-identity workflows (`claude-wif.yml`) | `base-action/src/validate-env.ts:9-41` accepts a federation rule id with an organization id instead of an API key | predictor emulation |
| Without `github_token` and without `id-token: write` the action stops | `src/github/token.ts:158-185`: the app token comes from an OIDC exchange; added `oidc-unavailable` to the model | model |
| Tag mode prompt includes the head branch name and the trigger user's display name | `src/github/data/formatter.ts:24`, `src/create-prompt/index.ts:702`; added as sources | model |

## Iteration 1 (generated set, 3,229 scenarios)

| Disagreement | Resolution | Where |
|---|---|---|
| `--dangerously-skip-permissions` reported as permission mode `bypassPermissions` | Claude Code receives it as its own flag; `--permission-mode` stays `acceptEdits`. The model now keeps the two apart (both still bypass permissions in the envelope) | model |
| Title and body of an `issue_comment` on a pull request | The action takes them from the webhook's `issue` object (`src/github/data/fetcher.ts:462`) | predictor canary mapping |

## Final (generated set)

3,229 scenarios; 74 excluded because the workflow's prompt reads outputs of an earlier
`actions/github-script` step the oracle does not execute (`ci-failure-auto-fix.yml`).
3,143 of the remaining 3,155 agree on every compared field. The 12 disagreements:

* 10 × **runs**: the action aborts with *Resource not accessible by integration* because
  the workflow token lacks a write scope for the tracking comment; the model predicts a
  run. This is the over-approximation stated as assumption A9 (scopes are not modeled).
* 2 × **prompt_canaries**: in agent mode with `use_commit_signing`, the head branch name is
  passed to the commit server in `--mcp-config`, so it appears in Claude Code's arguments;
  the model does not count server configuration as text in the session.

Both kinds of disagreement leave the envelope at least as permissive as the real session.
