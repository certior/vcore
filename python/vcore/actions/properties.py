"""Session properties for agents in workflows, and verdicts from the verified checker.

A property is a vcore policy that permits every request and blocks sinks once a session
holds certain tags. An envelope *satisfies* a property when composing the two denies
nothing the envelope allows. ``vcore`` decides this for sessions of every length
(``refines_iff_escalation_eq_none``), and a counterexample needs at most ``width + 1``
requests (``short_counterexample``): three for P1, two for P2.

Sinks come from :mod:`vcore.actions.catalog`. Where a tool family mixes reads and writes
(the GitHub MCP server, the ``gh`` CLI) only the writing tools are sinks, so a read-only
grant such as ``Bash(gh issue view:*)`` yields no counterexample.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..lean import VCore
from . import catalog
from .model import SECRET, UNTRUSTED


@dataclass(frozen=True)
class Property:
    id: str
    title: str
    sinks: tuple[str, ...]
    after: tuple[str, ...]

    def policy(self) -> dict:
        return {"permit": ["**"],
                "block": [{"on": s, "after": list(self.after)} for s in self.sinks]}


P1 = Property("exfiltration",
              "no network egress or public write once the session holds outsider text and a credential",
              tuple(dict.fromkeys(catalog.EGRESS + catalog.PUBLIC_WRITE)), (UNTRUSTED, SECRET))
P2 = Property("untrusted-write",
              "no write to the repository once the session holds outsider text",
              tuple(dict.fromkeys(catalog.REPO_WRITE)), (UNTRUSTED,))
PROPERTIES = (P1, P2)


@dataclass(frozen=True)
class TraceStep:
    request: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class Verdict:
    property: str
    satisfied: bool
    counterexample: tuple[TraceStep, ...] | None

    def to_json(self) -> dict:
        return {"property": self.property, "satisfied": self.satisfied,
                "counterexample": None if self.counterexample is None else
                [{"request": s.request, "tags": list(s.tags)} for s in self.counterexample]}


def verdict(vc: VCore, policy: dict, prop: Property) -> Verdict:
    """Whether ``policy`` satisfies ``prop`` (``vcore satisfies``), decided by the Lean
    checker. A counterexample is replayed on ``policy`` to report the tags after each step."""
    witness = vc.check([prop.policy(), policy], policy)
    if witness is None:
        return Verdict(prop.id, True, None)
    steps = vc.eval(policy, witness)
    assert all(ok for ok, _ in steps), "a counterexample must run under the envelope"
    return Verdict(prop.id, False, tuple(TraceStep(r, tags) for r, (_, tags) in zip(witness, steps)))


def widening(vc: VCore, old: dict, new: dict) -> list[str] | None:
    """A request sequence ``new`` allows and ``old`` does not, or ``None`` when ``new``
    refines ``old``."""
    return vc.check(old, new)
