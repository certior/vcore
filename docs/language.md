# The policy language

This page defines the language `vcore` checks and enforces. Each rule links to the Lean
theorem that states it against the formal definitions in
[`VCore/Policy.lean`](../VCore/Policy.lean); those theorems, not this page,
are the reference.

## Requests

An agent's tool call becomes a **request**: a path of segments separated by `/`, with the
tool first.

```
Read/src/main.py
WebFetch/pypi.org/simple/requests
Bash/git/diff/HEAD
mcp/github/get_issue
```

What a tool call touches is decided outside the language, by an integration.
[`python/vcore/toolcalls.py`](../python/vcore/toolcalls.py) is the mapping
for Claude Code, and [`assurance.md`](assurance.md) discusses why it matters.

A request is **canonical** if it has at least one segment and no segment is empty, `.` or
`..`. Every non-canonical request is denied, whatever the policy says
([`allows_canonical`](../VCore/Policy.lean)). This makes `Read/./.env`,
`Read//.env` and `Read/src/../.env` fail closed instead of slipping past a rule written for
`Read/.env`.

## Patterns

| Pattern | Covers |
|---|---|
| `a/b` | exactly `a/b` |
| `a/b/**` | `a/b` and every path below it |
| `**/a/b` | every path ending in `a/b` |
| `**` | every path |

Patterns compare whole segments. `Read/secrets/**` covers `Read/secrets/key` but not
`Read/secrets-public/key`, which a character-level glob would. `**` may only be the first
or the last segment, and literal segments must be canonical.

## Statements

A **policy** is a JSON object with four optional fields.

```json
{
  "permit": ["Read/**", "WebFetch/**", "mcp/github/create_pull_request"],
  "forbid": ["**/.env", "Read/secrets/**"],
  "label": {
    "Read/customers/**": "private",
    "WebFetch/**": "untrusted"
  },
  "block": [
    { "on": "WebFetch/**", "after": ["private", "untrusted"] },
    { "on": "mcp/github/create_pull_request", "after": ["private", "untrusted"] }
  ]
}
```

| Field | Meaning |
|---|---|
| `permit` | Requests these patterns cover may run. |
| `forbid` | Requests these patterns cover never run. |
| `label` | Running a request these patterns cover adds the tags to the session. A value is a tag or a list of tags. |
| `block` | A request `on` covers may not run once the session holds every tag in `after`. |

A session starts with no tags. A request **runs** if and only if:

1. it is canonical;
2. some `permit` covers it ([`permitted_of_allows`](../VCore/Policy.lean));
3. no `forbid` covers it ([`forbid_denies`](../VCore/Policy.lean));
4. no `block` that covers it has all of its `after` tags in the session
   ([`block_denies`](../VCore/Policy.lean)).

When a request runs, the session gains every tag labelled on it
([`label_mem_step`](../VCore/Policy.lean)). Tags are never removed
([`context_grows`](../VCore/Policy.lean)). A denied request changes nothing.

Statement order never matters ([`summary_perm`](../VCore/Policy.lean)): this is not
a first-match rule list.

**Tags** name what a session has been exposed to. Their names mean nothing to the checker;
a `block` rule gives them meaning. A rule with one tag (`after: ["secret"]`) says "never
after this"; a rule with several says "never after all of these together", which is how
the rules below are written.

## Authorities

A JSON **list** of policies is an **authority**: a request runs only if every policy in
the list allows it, and it gains the tags of every policy. Use it for:

* **Delegation.** A sub-agent runs under `[its own policy, its parent's policy]`, and so can
  never do more than its parent ([`delegate_refines`](../VCore/Refinement.lean)).
* **Properties.** `vcore satisfies POLICY PROPERTY` checks POLICY against
  `[PROPERTY, POLICY]`; see below.

An authority with no policies allows nothing ([`allows_nil`](../VCore/Policy.lean)),
and the parsers reject an empty list.

## Questions `vcore` answers

**`vcore check OLD NEW`: does NEW refine OLD?** NEW refines OLD if every sequence of
requests that NEW runs to completion, OLD runs too
([`Refines`](../VCore/Refinement.lean)). If not, `vcore` prints a counterexample:
a sequence of requests NEW runs and OLD stops, with the tags each policy holds after each
step. The answer covers sessions of any length and every possible
request ([`refines_iff_escalation_eq_none`](../VCore/Check.lean)), and a
counterexample never needs more than `width(OLD) + 1` requests, where the width is the size
of OLD's largest `after` list ([`short_counterexample`](../VCore/Check.lean)).

Adding a capability is never a refinement, by definition. For a change meant to grant
something new, ask the second question instead.

**`vcore satisfies POLICY PROPERTY`: does POLICY already enforce PROPERTY?** A property is a
policy that permits everything (`"permit": ["**"]`) and blocks what must not happen. POLICY
satisfies it if adding it would deny nothing POLICY allows, which is exactly
`vcore check [PROPERTY, POLICY] POLICY`. A property refers to the tags POLICY uses.

## Idioms

**The lethal trifecta** ([Willison](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/)):
no external communication once the session has seen private data and untrusted content.

```json
"label": { "Read/customers/**": "private", "WebFetch/**": "untrusted" },
"block": [ { "on": "WebFetch/**", "after": ["private", "untrusted"] } ]
```

A fetch is both a source of untrusted content and a way out. The first fetch after reading
private data is allowed, because nothing untrusted has been read yet; every fetch after it
is not.

**The Rule of Two** ([Meta](https://ai.meta.com/blog/practical-ai-agent-security/)): no state
change or external communication once the session has both. Block every such tool, as in
[`examples/coding-agent/rule-of-two.json`](../examples/coding-agent/rule-of-two.json).

**Once tainted, read only**: `{ "on": "Edit/**", "after": ["secret"] }`.

**One repository per session** (a mitigation suggested for the
[GitHub MCP exploit](https://invariantlabs.ai/blog/mcp-github-vulnerability)): if the
integration puts the repository in the request (`mcp/github/get_file/acme/website`), label
reads of public repositories `public` and block reads of private ones after `public`.

## What the language cannot say

* **Removing tags.** No declassification or sanitization; a session's tags only grow. The
  checker's bound depends on it.
* **Approvals, counts, budgets, time.** A request is allowed or not; there is no "ask", no
  "at most three times", no expiry.
* **Anything outside the request.** A rule sees a call only through its request. A URL's
  query string or a file's contents matter only if the integration puts them in the path.
* **Wildcards inside a pattern.** No `a/*/b`, `*.py` or regular expressions.
