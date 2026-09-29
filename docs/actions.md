# Agents in GitHub workflows

`python -m vcore.actions` reads a GitHub Actions workflow that runs an AI coding agent and
answers, for every event that can start the agent: **what can this session do, and does
that let text an outsider wrote reach a credential and a public channel, or a repository
write?** The answer is a proof or a counterexample from the verified checker.

It models [`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action)
at **v1.0.225** (commit `bf38e86`). Other actions and versions are not modeled.

```
$ python -m vcore.actions scan .github/workflows/claude.yml
.github/workflows/claude.yml  job claude, step 2: anthropics/claude-code-action@v1
  issue_comment (created), tag mode
    outsider text in the session: entity/body (the issue author); entity/comments (other commenters); ...
    token in .git/config: yes; subprocess env scrub: no
    exfiltration     counterexample (3 requests)
      1  Input/entity/comments                                {untrusted}
      2  Read/.git/config                                     {secret, untrusted}
      3  mcp/github_comment/update_claude_comment             {secret, untrusted}
    untrusted-write  counterexample (2 requests)
      1  Input/entity/comments                                {untrusted}
      2  Bash/@action/scripts/git-push.sh/_                   {untrusted}
```

A counterexample is a sequence of requests the **configuration** permits. It is not a
demonstrated attack: whether a model follows injected instructions, whether GitHub masks a
token in a comment, and how long the token lives are all outside the envelope.

## Pipeline

```
workflow.yml ──► run configurations ──► envelopes ──► vcore (Lean) ──► verdicts
                 (what the action        (vcore policy     satisfies P1, P2
                  decides; checked        + trust labels)   refinement for diffs
                  against oracle/)
```

1. **Workflow** ([`workflow.py`](../python/vcore/actions/workflow.py)). Triggers, permissions,
   jobs, steps, `${{ }}` references, with GitHub's YAML dialect (`on` is a key).
2. **Run configuration** ([`claude_code_action.py`](../python/vcore/actions/claude_code_action.py),
   layer 1). For each trigger event, activity type and possible mode: mode detection, the
   actor gate, the Claude Code tool rules the action passes, MCP servers, where the GitHub
   token is placed, subprocess environment scrubbing, and which texts the prompt contains
   and who writes them. Each rule cites the action's source line. Values a workflow does
   not fix are three-valued, and the analysis keeps every configuration they allow.
3. **Envelope** (layer 2). A vcore policy: `permit` and `forbid` from the tool rules,
   `label` from the attacker model and the tool catalog
   ([`catalog.py`](../python/vcore/actions/catalog.py)): `untrusted` on requests whose
   result an outsider can write, `secret` on requests that can reveal a credential. Rules
   that cannot be written exactly are widened (a permit) or dropped (a forbid), so the
   envelope over-approximates the session.
4. **Verdicts** ([`properties.py`](../python/vcore/actions/properties.py)). Through the Lean
   binary:

   | Property | Rule | Width | Longest counterexample |
   |---|---|---|---|
   | P1 `exfiltration` | no egress or public write after `untrusted` and `secret` | 2 | 3 requests |
   | P2 `untrusted-write` | no repository write after `untrusted` | 1 | 2 requests |

   `diff OLD NEW` asks, per event and mode, whether NEW's envelope refines OLD's
   (`refines_iff_escalation_eq_none`), and prints a sequence NEW allows otherwise.

## Attacker model and assumptions

The attacker is any GitHub account **without write access** to a public repository. Roles
in a run: the triggering account (`actor`), the issue author, the pull request author,
other commenters, named accounts (listed in the workflow), the workflow itself.

| Id | Assumption |
|---|---|
| A1 | Claude Code runs Read, Glob, Grep and LS without a rule; other tools need an allow rule; in the headless SDK a call that would ask is denied. |
| A2 | Claude Code enforces allow and deny rules as documented (prefix rules, per-subcommand checks, deny overrides allow). |
| A3 | An outsider can author issues, pull requests, comments, reviews, web content, and whatever GitHub read tools return. |
| A4 | *(conservative, opt-in)* Read can read outside the workspace, e.g. `/proc/self/environ`. |
| A5 | With `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`, subprocesses do not see the credentials in Claude's environment. |
| A6 | No permission rules come from settings the workflow does not show. |
| A7 | The action's MCP servers do not return the token they hold. |
| A8 | `pull_request`, `pull_request_review` and `pull_request_review_comment` runs for forks get no secrets, so a pull request reaching Claude on these events comes from a branch of the repository. |
| A9 | Writes with the action's GitHub token succeed; token scopes are not modeled (by default the token is a GitHub App installation token the workflow's `permissions` do not restrict). Missing scopes could only remove behaviors. |

`if:` conditions are read only for two facts, from unambiguous shapes: that the job cannot
run for an event (`github.event_name == 'X'` in every disjunct), and that an author must
be `OWNER`, `MEMBER` or `COLLABORATOR`, or the actor a fixed login
([`conditions.py`](../python/vcore/actions/conditions.py)).

## What is verified, what is checked, what is assumed

* **Verified.** The verdicts: satisfaction and refinement are decided by the Lean-verified
  checker, for sessions of every length ([assurance.md](assurance.md)).
* **Checked against the implementation.** Layer 1, by running the action's own code on the
  benchmark scenarios ([`oracle/`](../oracle/), [`bench/`](../bench/)).
* **Assumed.** Layer 2's labels and A1–A9. They are explicit, and the report lists the ones
  each verdict uses. The load-bearing pair A1/A2 for the default exfiltration counterexample
  (Read opens `.git/config`, the model then calls the comment tool) is confirmed by running the
  real Claude Code CLI offline; see [`oracle/probe/`](../oracle/probe/).
