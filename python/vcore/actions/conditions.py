"""What a job or step ``if:`` condition says about who triggered the run.

Only two facts are extracted, and only from shapes whose meaning is unambiguous:

* :func:`event_excluded`: every disjunct requires ``github.event_name == 'X'`` for some
  other event, so the condition is false for this event;
* :func:`restricted_roles`: every disjunct that can be true for this event requires the
  author of a field to hold an ``OWNER``, ``MEMBER`` or ``COLLABORATOR`` association, or
  the actor to be a fixed login.

Anything else is treated as restricting nothing, which can only widen an envelope.
"""

from __future__ import annotations

import re

TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


def _strip(cond: str) -> str:
    c = cond.strip()
    m = re.fullmatch(r"\$\{\{(.*)\}\}", c, re.S)
    return (m.group(1) if m else c).strip()


def _split(expr: str, op: str) -> list[str]:
    """Split on a binary operator at parenthesis depth 0, outside string literals."""
    parts, depth, i, start, quote = [], 0, 0, 0, False
    while i < len(expr):
        ch = expr[i]
        if ch == "'":
            quote = not quote
        elif not quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif depth == 0 and expr.startswith(op, i):
                parts.append(expr[start:i])
                i += len(op)
                start = i
                continue
        i += 1
    parts.append(expr[start:])
    return [p.strip() for p in parts]


def _unwrap(expr: str) -> str:
    """Remove parentheses that enclose the whole expression."""
    e = expr.strip()
    while e.startswith("(") and e.endswith(")"):
        depth, quote = 0, False
        for i, ch in enumerate(e):
            if ch == "'":
                quote = not quote
            elif not quote and ch in "()":
                depth += 1 if ch == "(" else -1
                if depth == 0 and i < len(e) - 1:
                    return e
        e = e[1:-1].strip()
    return e


def _disjuncts(cond: str) -> list[list[str]]:
    """Disjunctive structure, one level deep: ``a && (b || c)`` stays one disjunct whose
    second conjunct is opaque."""
    return [[_unwrap(c) for c in _split(_unwrap(d), "&&")] for d in _split(_unwrap(_strip(cond)), "||")]


_EVENT = re.compile(r"^github\.event_name\s*==\s*'([a-z_]+)'$")
_ASSOC = re.compile(r"^github\.event\.(comment|issue|pull_request|review)\.author_association\s*==\s*'([A-Z_]+)'$")
_ASSOC_IN = re.compile(r"^contains\(\s*fromJSON\(\s*'\[([^\]]*)\]'\s*\)\s*,\s*"
                       r"github\.event\.(comment|issue|pull_request|review)\.author_association\s*\)$")
_ACTOR = re.compile(r"^github\.(actor|triggering_actor)\s*==\s*'[^']+'$")
ROLE = {"comment": "comment-author", "issue": "entity-author", "pull_request": "pr-author",
        "review": "review-author"}


def _events(conjuncts: list[str]) -> set[str] | None:
    """The events a disjunct is limited to, or ``None`` if it names none."""
    names = [m.group(1) for c in conjuncts if (m := _EVENT.match(c))]
    return set(names) if names else None


def event_excluded(cond: str | None, event: str) -> bool:
    if not cond:
        return False
    for conj in _disjuncts(cond):
        events = _events(conj)
        if events is None or event in events:
            return False
    return True


def _roles(conj: list[str]) -> set[str]:
    roles = set()
    for c in conj:
        if m := _ASSOC.match(c):
            if m.group(2) in TRUSTED_ASSOCIATIONS:
                roles.add(ROLE[m.group(1)])
        elif m := _ASSOC_IN.match(c):
            values = {v.strip().strip('"') for v in m.group(1).split(",") if v.strip()}
            if values and values <= TRUSTED_ASSOCIATIONS:
                roles.add(ROLE[m.group(2)])
        elif _ACTOR.match(c):
            roles.add("actor")
    return roles


def restricted_roles(cond: str | None, event: str) -> set[str]:
    """Roles every applicable disjunct restricts to trusted accounts."""
    if not cond:
        return set()
    applicable = [conj for conj in _disjuncts(cond)
                  if (ev := _events(conj)) is None or event in ev]
    if not applicable:
        return set()
    out = _roles(applicable[0])
    for conj in applicable[1:]:
        out &= _roles(conj)
    return out
