# Oracle: what claude-code-action actually sets up

The oracle runs the real, unmodified
[anthropics/claude-code-action](https://github.com/anthropics/claude-code-action) at
**v1.0.225 (commit `bf38e86e58df9ebf3420326d019f955bb3be64dd`)** on a *scenario* (a workflow, an
event, who fires it, who wrote the content it touches). It reports an *observation*: whether
Claude Code starts, and with which tools, permission mode, MCP servers, credentials and
prompt content. The model in `python/vcore/actions/` predicts the same observation from the
workflow text alone, so the two can be compared scenario by scenario.

Schemas: [`bench/schema/scenario.schema.json`](../bench/schema/scenario.schema.json),
[`bench/schema/observation.schema.json`](../bench/schema/observation.schema.json).

## Usage

```sh
oracle/fetch.sh                                   # clone the pinned action, bun install --frozen-lockfile
bun oracle/run.ts bench/scenarios/smoke.jsonl > bench/observations/smoke.jsonl
bun oracle/run.ts SCENARIOS.jsonl --jobs 8 --timeout 90 --keep   # keep per-scenario dirs and logs
(cd oracle && bun test)                           # expression evaluator tests
python3 oracle/smoke_scenarios.py                 # regenerate bench/scenarios/smoke.jsonl
```

Requires bun (tested with 1.3.14, the version `action.yml` pins), git and Python 3 for the
scenario generator. A scenario takes about half a second; output is in input order and
identical across runs (checked on the smoke set). Oracle diagnostics are under `extra` and are
normalized (`<workdir>`, `<timestamp>`); they are not part of the comparison.

## What is executed

For each scenario, in its own temporary directory:

1. **Workflow up to the step** (oracle code, following GitHub's documented semantics). Does
   the event fire the workflow (`on:` and activity `types`)? Is the job `if:` true? The step
   `if:`? The step's `with:` and `env:` values, workflow/job/step `env:`, the job's
   `GITHUB_TOKEN` permissions, the secrets that exist, and a preceding `actions/checkout`
   step (`ref`, `persist-credentials`, `token`, major version) are evaluated with
   [`src/expr.ts`](src/expr.ts).
2. **Runner state.** A synthesized webhook payload at `GITHUB_EVENT_PATH`; a git "origin" with
   the default branch and the PR head (fork PRs only under `refs/pull/N/head`); a workspace left
   the way actions/checkout leaves it (credential header in `.git/config`, or for checkout v6+
   in a file under `RUNNER_TEMP` pulled in by `include.path`); `RUNNER_TEMP`, `GITHUB_OUTPUT`,
   `GITHUB_ENV`, a fresh `HOME`.
3. **The action.** The environment of `action.yml`'s `Run Claude Code Action` step is computed
   by evaluating that step's own `env:` block (declared inputs with their defaults, `env`,
   `github`, `runner`), and the step's command is run as written:
   `bun --no-env-file --config=$GITHUB_ACTION_PATH/bunfig.toml run $GITHUB_ACTION_PATH/src/entrypoints/run.ts`,
   in the workspace. That is the whole preparation path: mode detection, token setup, the write
   permission and trigger checks, tracking comment, GitHub data fetch and prompt creation,
   branch setup, git credential configuration, MCP server configuration, restoring `.claude/`
   and friends from the base branch on PRs, user settings, and the Agent SDK call.
4. **Claude Code.** `PATH_TO_CLAUDE_CODE_EXECUTABLE` points at
   [`fake-claude/claude`](fake-claude/claude). The SDK spawns it exactly as it would spawn the
   CLI; it records its arguments, environment, working tree, git config and settings, then
   exits. The action fails at that point and runs its normal cleanup.

## What is substituted

| Real | Oracle | Why |
|---|---|---|
| Claude Code CLI (installed with `curl https://claude.ai/install.sh`) | `fake-claude/claude` | No model, no network. The SDK computes arguments and environment before spawning, so they are unaffected. |
| `api.github.com` REST and GraphQL | [`src/mock.ts`](src/mock.ts) via `GITHUB_API_URL` / `GITHUB_GRAPHQL_URL`, which the action reads (`src/github/api/config.ts`) | Synthesized issue/PR/comment data with canaries. |
| Actions OIDC endpoint | mock, via `ACTIONS_ID_TOKEN_REQUEST_URL/TOKEN`, set only when the job has `id-token: write` | |
| `https://api.anthropic.com/api/github/github-app-token-exchange` (hard-coded, `src/github/token.ts:120`) | [`src/preload.ts`](src/preload.ts), loaded with `bun --preload`, redirects that URL to the mock and refuses every other non-local request | The only instrumentation inside the action's process; no action code is patched. |
| github.com git remote | local bare repository through `url.<base>.insteadOf` in `GIT_CONFIG_GLOBAL` | The remote URL the action writes to `.git/config` is unchanged, so credential placement is observed as is. |

Tokens and secrets are placeholders (`ghs_ORACLE...`, `ORACLESECRET_<NAME>_0000`); any value
matching them is a credential in the observation.

## Canaries

Every free-text field the mock serves or the payload carries contains
`CANARY-<field>-<author>`, with `<author>` one of `maintainer` (login `maintainer`, write
access), `outsider` (login `outsider`, read access) or `actor` (whoever fires the event).
Fields: `issue.title`, `issue.body`, `comment.body`, `trigger.body` (the triggering comment or
review), `pr.title`, `pr.body`, `pr.head_ref`, `commit.message`, `review.body`,
`review_comment.body`, `user.name`, `dispatch.client_payload`, and in the repository
`checkout.claude_md` and `checkout.change`. `prompt_canaries` lists those found in the prompt
file, the user-request file and Claude Code's arguments. `extra.canaries_in_env` and
`extra.workspace_canaries` report the environment and the working tree.

## Assumptions (GitHub behavior the oracle implements rather than observes)

- **Token permissions.** Undeclared `permissions` default to *restricted* (contents and
  packages read) unless the scenario says *permissive* (write to everything but `id-token`).
  `pull_request` runs from forks get read-only scopes, no `id-token`, and no secrets other than
  `GITHUB_TOKEN`. The mock applies scopes only to comment writes and `actions/runs`; public
  repositories are readable by any token.
- **Inputs.** Only inputs declared in `action.yml` reach the action; others (for example v0.x
  inputs such as `direct_prompt` or `allowed_tools` on `@v1`) are dropped and listed in
  `extra.unexpected_inputs`.
- **Checkout.** Only the last `actions/checkout` step before the action is emulated, at the
  repository root; `path`, `repository`, and refs other than the default branch, the PR head
  (sha, head ref, `refs/pull/N/head|merge`) are reported in `extra` and fall back to the
  default branch. A PR's merge ref is approximated by its head. Other steps (setup, scripts,
  other actions) are not run.
- **Runner environment.** A minimal set of `GITHUB_*` and `RUNNER_*` variables; no
  `ACTIONS_RUNTIME_TOKEN`, cache or tool-cache services.
- **Secret formats.** Secrets whose names look like private keys get a PEM frame (the action
  checks `ssh_signing_key` for one).

## Limits (what the oracle cannot observe)

- **Claude Code's permission engine.** The oracle observes the `--allowedTools`,
  `--disallowedTools`, `--permission-mode` and settings Claude Code is started with, not how
  the CLI decides a particular call: which tools are available without an allow rule, what
  `acceptEdits` allows, whether `Read` can open `.git/config`, what
  `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` removes, the bubblewrap sandbox.
- **Model behavior.** Whether a model follows injected text is out of scope; the oracle
  reports which text reaches the session, not what the session does with it.
- **The action's MCP servers** are configured but not started; their own behavior (for
  example what `github_file_ops` commits or what `update_claude_comment` can write) is not
  exercised.
- **Post steps** of `action.yml` after the run step (SSH key cleanup, buffered inline comments)
  are not run.
- **Expressions.** [`src/expr.ts`](src/expr.ts) supports literals, property access (`a.b`,
  `a['b']`, `a.*`), `! == != < <= > >= && ||`, and `contains`, `startsWith`, `endsWith`,
  `format`, `join`, `toJSON`, `fromJSON`, `success`, `always`, `failure`, `cancelled`, with the
  `github`, `env`, `vars`, `secrets`, `inputs`, `runner` contexts. `steps`, `needs`, `matrix`,
  `job`, `strategy`, `hashFiles` and objects interpolated into strings stop the scenario with
  `stop_reason: "unsupported-expression"`.
- **Mock fidelity.** The mock serves the fields the action's queries read at this commit;
  unknown routes return 404 and appear in `extra.api_calls`.
