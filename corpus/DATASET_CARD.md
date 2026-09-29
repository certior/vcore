---
license: cc-by-4.0
pretty_name: claude-code-action public workflow envelopes (de-identified)
tags:
- security
- github-actions
- ai-agents
- formal-verification
size_categories:
- 10K<n<100K
configs:
- config_name: claude-code-action
  data_files: claude-code-action.jsonl
---

# Authority envelopes of public workflows using claude-code-action (de-identified)

## Links

- Blog post: https://paulinebourigault.github.io/blog/2026/what-the-agent-may-do/
- Code (vcore): https://github.com/certior/vcore
- Companion dataset (benchmark): https://huggingface.co/datasets/paulibo/vcore-actions-benchmark

Derived analysis of 16,346 distinct public GitHub workflow files that invoke
`anthropics/claude-code-action`, collected through GitHub code search on 2026-09-15 and
analysed with a model of the action at v1.0.225 and the Lean-verified vcore checker.

## What is in a record

* `id`: an opaque identifier (a keyed hash; the key is not published)
* `copies`: identical copies in the collection; `stars_bucket`
* per agent step: reference class, resolved version and family (only `v1` steps are analysed)
* per configuration (event, activity type, mode): whether the agent can run and why not,
  whether the triggering account can lack write access, which prompt sources an outsider can
  write, whether the workspace can hold pull request content, whether the token is in
  `.git/config`, subprocess scrubbing, permission mode, tool categories, the verdicts of P1
  (exfiltration) and P2 (untrusted write) with abstract counterexample shapes, and the single
  workflow edits verified to restore each property

`claude-code-action.manifest.json` has the collection funnel, the search query, the model
commit and the aggregates reported in the write-up.

## What is not in a record

Repository names, owners, paths, commit or blob ids, content hashes, dates, licences, languages
and workflow contents. Records cannot be joined to repositories through the data.

## Interpretation

A violated property means the configuration permits a sequence of tool calls, under named
assumptions; it is not evidence that any repository was or can be attacked. Unrestricted shell
access and unknown text provenance are treated conservatively; the manifest includes a
sensitivity run that trusts text of unknown authorship.
