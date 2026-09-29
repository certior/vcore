# vcore

**Verified checking for AI agent policies that remember.**

🤗 [Benchmark](https://huggingface.co/datasets/paulibo/vcore-actions-benchmark) · 🤗 [Corpus](https://huggingface.co/datasets/paulibo/vcore-workflow-envelopes)

The rules recommended for AI agents today are about sessions, not single calls: no
external communication once the agent has seen both private data and untrusted content
(Simon Willison's [lethal trifecta](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/)),
never all three of untrusted input, sensitive data and outside effects in one session
(Meta's [Rule of Two](https://ai.meta.com/blog/practical-ai-agent-security/)). A policy
with that kind of rule has memory, and so a change to it can open an attack path without
changing the decision on any single request.

`vcore` answers two questions about such policies, for sessions of any length and every
possible request:

* `vcore check OLD NEW`: does the new policy allow anything the old one would stop?
* `vcore satisfies POLICY PROPERTY`: does the policy already enforce a rule like the Rule of Two?

It answers with a proof or a counterexample. The decision procedure, the theorem that
makes it finite, and the compiler behind the runtime are proved correct in Lean 4.

The write-up, [*The lethal trifecta takes three steps*](site/index.html), explains the problem,
the two ideas behind the checker and the evaluation; it is a static page that GitHub Pages
can serve from `site/`.

## Example

A coding agent may read the repository, fetch web pages, and read issues and open pull
requests on GitHub. Customer data and production configuration are `private`; web pages
and issues are `untrusted`, because anyone can write them. Pull requests are blocked once a
session has both (an excerpt of [`policy.json`](examples/coding-agent/policy.json)):

```json
{
  "permit": ["Read/**", "WebFetch/**", "mcp/github/get_issue", "mcp/github/create_pull_request"],
  "label": {
    "Read/customers/**": "private",
    "Read/config/prod/**": "private",
    "WebFetch/**": "untrusted",
    "mcp/github/get_issue": "untrusted"
  },
  "block": [
    { "on": "mcp/github/create_pull_request", "after": ["private", "untrusted"] }
  ]
}
```

Someone decides the team's own issue tracker is safe and deletes the `untrusted` label on
`mcp/github/get_issue`. That is one line, and the shape of the
[GitHub MCP exploit](https://invariantlabs.ai/blog/mcp-github-vulnerability):

```
$ vcore check policy.json trust-issues.json
trust-issues.json does not refine policy.json: it allows this sequence of 3 requests, which policy.json denies.

  #  request                         policy.json                              trust-issues.json
  1  mcp/github/get_issue            allow  {untrusted}                       allow  {}
  2  Read/config/prod/_              allow  {private, untrusted}              allow  {private}
  3  mcp/github/create_pull_request  DENY   blocked after private, untrusted  allow  {private}

  '_' stands for any path segment that neither policy mentions.
```

No single request changes its decision, so a per-request policy diff reports nothing, and
a search over two-request sessions finds nothing either. The full case study, with a safe
change that a per-request diff wrongly flags and a Rule of Two check, is in
[`examples/coding-agent`](examples/coding-agent).

## Agents in GitHub workflows

`python -m vcore.actions scan .github/workflows/` computes, for every event that can start
[`anthropics/claude-code-action`](https://github.com/anthropics/claude-code-action) (v1.0.225),
the authority of the agent's session as a vcore policy, and checks two session properties with
the verified checker: no public write or egress once the session holds outsider text and a
credential, and no repository write after outsider text. Violations come with a trace of at most
three tool calls and, with `--repairs`, single workflow edits verified to restore the property.
`diff OLD NEW` checks that a workflow edit adds no behaviour.

```
$ python -m vcore.actions scan --repairs .github/workflows/claude.yml
  pull_request_review_comment (created), tag mode
    exfiltration     counterexample (3 requests)
      1  Input/entity/comments                                {untrusted}
      2  Read/.git/config                                     {secret, untrusted}
      3  mcp/github_comment/update_claude_comment             {secret, untrusted}
      repair: add --disallowedTools "Read(./.git/**)" to claude_args (verified; only removes behaviors)
```

The model of the action is checked against the action's own code
([`oracle/`](oracle/)): on 3,155 generated scenarios the two agree on every compared field in
3,143. [`docs/actions.md`](docs/actions.md) describes the model and its assumptions,
[`bench/`](bench/) the benchmark and [`corpus/`](corpus/) the study of public workflows. The
write-up is [*What the agent may do*](https://paulinebourigault.github.io/blog/2026/what-the-agent-may-do/).

## Why this can be decided, and what is proved

Sessions are unbounded and there are infinitely many possible requests. Two observations
make the question finite:

1. **Short sessions suffice.** Tags only accumulate and decisions only get stricter as they
   do. If a longer session ends in an escalation, then keeping one request per tag of the
   combination that blocks the last request still does. So a counterexample never needs
   more than *k* + 1 requests, where *k* is the size of the largest combination. The bound
   is tight: the trifecta rule (*k* = 2) needs three.
2. **Few requests suffice.** Patterns compare whole segments, so any request behaves like
   one of finitely many representatives built from the policies' literals and one segment
   (`_`) that no policy mentions.

| | Theorem | |
|---|---|---|
| The checker decides refinement, for sessions of every length and all requests | `refines_iff_escalation_eq_none` | [Check.lean](VCore/Check.lean) |
| Counterexamples need at most *k* + 1 requests | `short_counterexample` | [Check.lean](VCore/Check.lean) |
| …and the bound is attained | `Examples.escalation_needs_three`, `Examples.no_shorter` | [Examples.lean](VCore/Examples.lean) |
| Finitely many representative requests | `Pat.exists_rep` | [Representatives.lean](VCore/Representatives.lean) |
| The runtime's index decides exactly as the policy | `compile_correct`, `compile_allows` | [Compile.lean](VCore/Compile.lean) |
| A delegate never does more than its delegator | `delegate_refines` | [Refinement.lean](VCore/Refinement.lean) |
| The language means what [the reference](docs/language.md) says | `forbid_denies`, `block_denies`, `allows_canonical`, … | [Policy.lean](VCore/Policy.lean) |

1462 lines of Lean, 102 theorems, no `sorry`, no axioms beyond Lean's standard three, no
dependencies. The Python runtime, the parsers and an independent Z3 checker are tested
against the Lean core; [docs/assurance.md](docs/assurance.md) draws the line between proved,
tested and out of scope.

## Quick start

Requires [elan](https://github.com/leanprover/elan) (Lean 4.34 is installed on first build)
and Python 3.10 or later; the tests also need `pip install pytest z3-solver`.

```bash
lake build                 # checks every proof, builds .lake/build/bin/vcore
make verify                # proofs, axiom audit, tests, mutation study

cd examples/coding-agent
vcore=../../.lake/build/bin/vcore
$vcore check policy.json trust-issues.json
$vcore satisfies policy.json rule-of-two.json
$vcore eval policy.json mcp/github/get_issue Read/customers/export.csv mcp/github/create_pull_request
```

`check` and `satisfies` exit with 0 when there is no counterexample, 1 when there is one
and 2 on invalid input; `--json` prints machine-readable output for CI.

**Enforcing a policy.** `vcore compile` produces the index that the zero-dependency Python
runtime evaluates:

```python
from vcore import Session, load_index

session = Session([load_index("policy.index.json")])
session.run("mcp/github/get_issue")             # allow; tags {untrusted}
session.run("Read/customers/export.csv")        # allow; tags {private, untrusted}
session.run("mcp/github/create_pull_request")   # deny: blocked after private, untrusted
```

[`examples/claude-code`](examples/claude-code) wires this into Claude Code as a
`PreToolUse` hook with per-session memory.

## The language, briefly

A request is a path with the tool first (`Read/src/app.py`, `WebFetch/pypi.org/simple`).
Patterns match whole segments: `a/b`, `a/b/**`, `**/a/b`. A policy has four kinds of
statement:

| | |
|---|---|
| `permit` | requests it covers may run |
| `forbid` | requests it covers never run |
| `label` | running a request it covers adds tags to the session |
| `block` | a request it covers may not run once the session has every tag in `after` |

A list of policies must all allow a request; that is how delegation and properties work.
Requests with an empty, `.` or `..` segment are always denied. The
[language reference](docs/language.md) has the details and idioms.

## Evaluation

From [docs/evaluation.md](docs/evaluation.md), generated by `scripts/evaluate.py`:

* **The obvious alternatives fail.** On 1000 random policy edits, a per-request diff with
  the session's tags left free missed 29 of 402 escalations and flagged 94 of 598 safe
  edits; a search over two-request sessions missed 19.
* **The unverified code agrees with the proofs.** No disagreement between the Python runtime
  and the Lean semantics, the Z3 and Lean checkers, or the Python and Lean parsers, over
  1000, 1000 and 2000 random cases. 9 of 10 seeded bugs in that code are found by random
  testing; the tenth needs a targeted test, and its Lean counterpart breaks a proof.
* **The proofs are not vacuous.** Each of 11 seeded bugs in the Lean definitions breaks the
  build.
* **Cost.** Runtime decisions take 4–13 µs from 10 to 10,000 statements. The verified
  checker takes under 200 ms up to 256 statements and 14 s at 1024, where Z3 is faster.

## Layout

```
VCore/
  Pattern.lean          requests, patterns, canonical paths
  Policy.lean           semantics of one request, and the contract theorems
  Refinement.lean       sessions, refinement, short counterexamples, delegation
  Representatives.lean  finitely many representative requests
  Check.lean            the decision procedure and its correctness
  Compile.lean          compilation to a lookup index
  Examples.lean         the bound is attained
Main.lean               the vcore command line
python/vcore/   runtime, tool-call mapping, Lean bridge, Z3 checker
python/tests/           differential, mutation, case study, CLI and hook tests
scripts/                axiom audit, Lean mutation study, evaluation
examples/               coding-agent case study, Claude Code hook
docs/                   language reference, assurance, evaluation
site/                   the write-up
```

## Limitations

* Tags only accumulate: there is no declassification, approval, count or expiry. Each
  would break the monotonicity the bound relies on.
* Patterns have no wildcards inside them and no regular expressions.
* What a tool call touches is decided by an integration, which is tested, not proved.
  Shell commands in particular cannot be described faithfully by a request.
* The verified checker slows down on policies with many hundreds of distinct patterns.

## Related work

* **Cedar** (AWS) develops an authorization language against a Lean model, with
  differential testing of the production engine
  ([Cutler et al., OOPSLA 2024](https://arxiv.org/abs/2403.04651);
  [Disselkoen et al., 2024](https://arxiv.org/abs/2407.01688)). vcore follows that method for
  policies with session state.
* **Margrave** ([Fisler et al., ICSE 2005](https://cs.brown.edu/people/sk/Publications/Papers/Published/fkmt-verif-change-impact-xacml/))
  and **Zelkova** ([Backes et al., FMCAD 2018](https://www.amazon.science/publications/semantic-based-automated-reasoning-for-aws-access-policies-using-smt))
  compare access-control policies request by request. The per-request baseline in the
  evaluation is that analysis, extended with a free session state.
* **Security automata** ([Schneider, TISSEC 2000](https://dl.acm.org/doi/10.1145/353323.353382))
  characterise the policies a monitor can enforce; vcore's policies are a monotone class of
  them for which refinement has a small, tight counterexample bound.
* **CaMeL** ([Debenedetti et al., 2025](https://arxiv.org/abs/2503.18813)),
  **FIDES** ([Costa et al., 2025](https://arxiv.org/abs/2505.23643)) and
  **Progent** ([Shi et al., 2025](https://arxiv.org/abs/2504.11703)) enforce capability,
  information-flow and privilege policies around LLM agents; Invariant Labs'
  [toxic flow analysis](https://invariantlabs.ai/blog/toxic-flow-analysis) looks for
  dangerous tool combinations. vcore is complementary: it verifies what a change to a
  session policy does.

## Citation

See [CITATION.cff](CITATION.cff).

## License

Apache-2.0. From [Certior](https://certior.io).
