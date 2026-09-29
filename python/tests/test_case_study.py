"""The coding-agent case study in examples/coding-agent."""

from vcore import smt
from vcore.policy import parse_authority
from vcore.runtime import Session


def test_runtime_enforces_the_trifecta_rule(vcore, example):
    session = Session([vcore.compile(example("policy.json"))])
    assert session.run("mcp/github/get_issue").allow
    assert session.run("Read/customers/export.csv").allow
    assert session.context == frozenset({"private", "untrusted"})
    denied = session.run("mcp/github/create_pull_request")
    assert not denied.allow and denied.reasons == ("blocked after private, untrusted",)
    assert not session.run("Read/.env").allow
    assert not session.run("Read/./.env").allow
    assert session.run("Edit/src/app.py").allow


def test_trusting_issues_opens_a_three_step_escalation(vcore, example):
    old, new = example("policy.json"), example("trust-issues.json")
    witness = vcore.check(old, new)
    assert witness is not None and len(witness) == 3
    assert witness[0] == "mcp/github/get_issue"
    # Neither a per-request diff nor a two-step search can see it.
    assert smt.stateless_escalation(parse_authority(old), parse_authority(new)) is None
    assert vcore.check(old, new, depth=2) is None
    assert len(smt.escalation(parse_authority(old), parse_authority(new))) == 3


def test_removing_a_dead_rule_is_safe(vcore, example):
    old, new = example("policy.json"), example("cleanup.json")
    assert vcore.check(old, new) is None
    # A per-request diff reports it: it cannot tell that no session ever has `secret`.
    assert smt.stateless_escalation(parse_authority(old), parse_authority(new)) is not None


def test_rule_of_two(vcore, example):
    policy, rule = example("policy.json"), example("rule-of-two.json")
    witness = vcore.check([rule, policy], policy)
    assert witness is not None and len(witness) == 3 and witness[-1].startswith("Edit/")
    hardened = example("hardened.json")
    assert vcore.check([rule, hardened], hardened) is None
    assert vcore.check(policy, hardened) is None


def test_subagent_alone_escalates_but_delegated_does_not(vcore, example):
    old = example("policy.json")
    assert vcore.check(old, example("docs-agent.json")) is not None
    assert vcore.check(old, example("docs-agent-delegated.json")) is None
