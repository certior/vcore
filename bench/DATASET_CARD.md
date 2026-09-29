---
license: cc-by-4.0
pretty_name: claude-code-action authority envelopes benchmark
tags:
- security
- github-actions
- ai-agents
- formal-verification
- prompt-injection
size_categories:
- 1K<n<10K
configs:
- config_name: scenarios
  data_files: scenarios.jsonl
- config_name: observations
  data_files: observations.jsonl
- config_name: labels
  data_files: labels.jsonl
---

# Authority envelopes of AI agents in GitHub workflows: benchmark

## Links

- Blog post: https://paulinebourigault.github.io/blog/2026/what-the-agent-may-do/
- Code (vcore): https://github.com/certior/vcore
- Companion dataset (corpus): https://huggingface.co/datasets/paulibo/vcore-workflow-envelopes

3,229 runs of GitHub Actions workflows that use
[`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action), pinned
at v1.0.225 (`bf38e86`). Each run pairs what the action itself did with what an
authority model derives for it.

## Files

| File | One record per scenario |
|---|---|
| `scenarios.jsonl` | the workflow file, the event and activity, the triggering account's permission and type, the authors of the thread, whether the pull request comes from a fork; `seed` and `knob`/`knob_value` say how it was generated |
| `observations.jsonl` | **observed** by executing the action's unmodified code against a mock GitHub API: whether Claude Code starts and why not, mode, `--allowedTools`/`--disallowedTools`, permission mode, MCP servers, whether the token is in `.git/config`, credential helper, credential variables in Claude Code's environment, subprocess scrubbing, and which canary-tagged texts reach the prompt |
| `labels.jsonl` | **derived**: the authority envelope (a vcore policy) of the run's configuration, and the verdicts of two session properties from the Lean-verified vcore checker, with counterexamples and the assumptions used |
| `schema/` | JSON schemas for scenarios and observations |
| `seeds/` | the action's official example workflows (MIT, © Anthropic, PBC) |
| `fidelity.json`, `fidelity-log.md` | agreement between the model and the observations (3,143 of 3,155 comparable scenarios on every field), and how each disagreement was resolved |
| `sensitivity.json` | verdict changes caused by each single configuration knob |

## Properties

* **P1 exfiltration**: no network egress or public write once the session holds text an
  outsider can write and a request that can reveal a credential (counterexamples ≤ 3 steps).
* **P2 untrusted write**: no repository write once the session holds outsider text (≤ 2 steps).

A counterexample is a sequence of tool calls the configuration permits, not a demonstrated
attack. Verdicts rest on the named assumptions listed in each label.

## Uses

Score a workflow scanner on observed facts (does it know what the action grants and where the
token goes?), on verdicts, or both. Regenerate everything at another action commit with the
generator and oracle in the vcore repository.

## Generation

Seeds are the action's official examples; mutants change exactly one knob (41 knob values: prompt,
tool rules, deny rules, permission modes, actor-gate inputs, commit signing, progress tracking,
comment filters, trigger inputs, subprocess scrubbing, permissions, settings, job condition,
checkout ref, and two combinations). Each workflow is crossed with its declared activity types,
a triggering account with and without write access, and maintainer or outsider content. All
data is synthetic; no real repository is referenced.
