# Case study: a coding agent with GitHub access

[`policy.json`](policy.json) governs a coding agent that reads the repository, edits source
and tests, runs tests, browses the web, and reads issues and opens pull requests through
the GitHub MCP server. Customer data and production configuration are tagged `private`;
web pages and issues are tagged `untrusted`, because anyone can write them. The agent may
not open a pull request or fetch a URL once a session has touched both, the
[lethal trifecta](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/) rule.

Every command below is run from this directory with `vcore=../../.lake/build/bin/vcore`.
[`python/tests/test_case_study.py`](../../python/tests/test_case_study.py) checks each claim.

## A session

```
$ $vcore eval policy.json mcp/github/get_issue Read/customers/export.csv mcp/github/create_pull_request
  allow  mcp/github/get_issue            context {untrusted}
  allow  Read/customers/export.csv       context {private, untrusted}
  deny   mcp/github/create_pull_request  blocked after private, untrusted
```

## Four changes to the policy

**Trusting the issue tracker** ([`trust-issues.json`](trust-issues.json)). Someone decides
the team's own issues are safe and removes the `untrusted` label from
`mcp/github/get_issue`. This is the shape of the
[GitHub MCP exploit](https://invariantlabs.ai/blog/mcp-github-vulnerability), where a
public issue instructed an agent to leak private repositories.

```
$ $vcore check policy.json trust-issues.json
trust-issues.json does not refine policy.json: it allows this sequence of 3 requests, which policy.json denies.

  #  request                         policy.json                              trust-issues.json
  1  mcp/github/get_issue            allow  {untrusted}                       allow  {}
  2  Read/config/prod/_              allow  {private, untrusted}              allow  {private}
  3  mcp/github/create_pull_request  DENY   blocked after private, untrusted  allow  {private}

  '_' stands for any path segment that neither policy mentions.
```

No single request changes its decision, so a per-request diff reports nothing, and a search
over two-request sessions finds nothing either.

**Removing a dead rule** ([`cleanup.json`](cleanup.json)). The policy forbids reading
`Read/secrets/**`, so the `secret` tag it labels there can never be acquired, and the rule
blocking edits after `secret` never fires. Removing both is safe:

```
$ $vcore check policy.json cleanup.json
cleanup.json refines policy.json: it allows no sequence of requests that policy.json denies.
```

A per-request diff flags it: in a context holding `secret`, edits used to be denied. It
cannot tell that no session reaches that context.

**A sub-agent** ([`docs-agent.json`](docs-agent.json)). A documentation agent with its own
narrow policy is not a refinement of the parent on its own (it may, for example, fetch URLs
ending in `.env`). Run as a delegate, under
[`docs-agent-delegated.json`](docs-agent-delegated.json) = `[docs-agent, policy]`, it is:

```
$ $vcore check policy.json docs-agent-delegated.json
docs-agent-delegated.json refines policy.json: it allows no sequence of requests that policy.json denies.
```

**Hardening** ([`hardened.json`](hardened.json)) adds blocks on `Edit/**` and `Bash/**` after
`private` and `untrusted`. Adding blocks only removes behaviour, so it refines the policy.

## A property: the Rule of Two

[Meta's Rule of Two](https://ai.meta.com/blog/practical-ai-agent-security/) asks that a
session never combine untrusted input, sensitive data, and the ability to change state or
communicate. [`rule-of-two.json`](rule-of-two.json) writes it with the policy's own tags:

```
$ $vcore satisfies policy.json rule-of-two.json
policy.json does not satisfy rule-of-two.json: it allows this sequence of 3 requests, which rule-of-two.json forbids.

  #  request               policy.json
  1  mcp/github/get_issue  allow  {untrusted}
  2  Read/config/prod/_    allow  {private, untrusted}
  3  Edit/src/_            allow  {private, untrusted}

  Request 3 violates rule-of-two.json: blocked after private, untrusted.

  '_' stands for any path segment that neither policy mentions.

$ $vcore satisfies hardened.json rule-of-two.json
hardened.json satisfies rule-of-two.json: every sequence of requests it allows also satisfies rule-of-two.json.
```

The original policy stops exfiltration but still lets an agent that has read an issue and
production configuration edit the source tree.
