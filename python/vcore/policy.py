"""Policy syntax: parsing and validation.

Mirrors the parser in ``Main.lean``; both accept and reject the same documents, which
the differential tests check.

A policy is a JSON object::

    {
      "permit": ["Read/**", "WebFetch/**"],
      "forbid": ["**/.env"],
      "label":  {"Read/customers/**": "private", "WebFetch/**": ["untrusted"]},
      "block":  [{"on": "WebFetch/**", "after": ["private", "untrusted"]}]
    }

An authority is a policy, or a non-empty list of policies that must all allow a request.

A request is a ``/``-separated path whose first segment names the tool. Patterns match
whole segments: ``a/b`` exactly, ``a/b/**`` that path and everything below it, ``**/a/b``
every path ending in those segments.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Path = tuple[str, ...]
Kind = Literal["exact", "prefixed", "suffixed"]
Effect = Literal["permit", "forbid", "label", "block"]


class PolicyError(ValueError):
    """A document that is not a valid policy."""


def parse_path(s: str) -> Path:
    return tuple(s.split("/"))


def render_path(x: Path) -> str:
    return "/".join(x)


def canonical_segment(s: str) -> bool:
    return s not in ("", ".", "..")


def is_canonical(x: Path) -> bool:
    """At least one segment, none empty, ``.`` or ``..``. Other requests are always denied."""
    return len(x) > 0 and all(canonical_segment(s) for s in x)


@dataclass(frozen=True)
class Pat:
    kind: Kind
    path: Path

    @staticmethod
    def parse(s: str) -> Pat:
        segs = parse_path(s)
        if segs == ("**",):
            return Pat("prefixed", ())
        if segs[0] == "**":
            kind, lits = "suffixed", segs[1:]
        elif segs[-1] == "**":
            kind, lits = "prefixed", segs[:-1]
        else:
            kind, lits = "exact", segs
        for seg in lits:
            if seg == "**":
                raise PolicyError(f"pattern {s!r}: '**' is only allowed as the first or last segment")
            if not canonical_segment(seg):
                raise PolicyError(f"pattern {s!r}: segments must be non-empty and not '.' or '..'")
        return Pat(kind, lits)

    def covers(self, x: Path) -> bool:
        n = len(self.path)
        if self.kind == "exact":
            return x == self.path
        if self.kind == "prefixed":
            return x[:n] == self.path
        return n <= len(x) and x[len(x) - n:] == self.path

    def __str__(self) -> str:
        if self.kind == "exact":
            return render_path(self.path)
        if self.kind == "prefixed":
            return render_path(self.path) + "/**" if self.path else "**"
        return "**/" + render_path(self.path)


@dataclass(frozen=True)
class Stmt:
    pat: Pat
    effect: Effect
    tag: str | None = None
    after: tuple[str, ...] | None = None


Policy = list[Stmt]
Authority = list[Policy]


def _tag(where: str, value: Any) -> str:
    if not isinstance(value, str):
        raise PolicyError(f"{where}: expected a tag (a string)")
    if not value:
        raise PolicyError(f"{where}: tags must be non-empty")
    return value


def _tags(where: str, value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise PolicyError(f"{where}: expected a list of tags")
    if not value:
        raise PolicyError(f"{where}: expected at least one tag")
    return tuple(_tag(where, v) for v in value)


def _fields(where: str, obj: Any, allowed: tuple[str, ...]) -> dict:
    if not isinstance(obj, dict):
        raise PolicyError(f"{where}: expected an object")
    for key in obj:
        if key not in allowed:
            raise PolicyError(f"{where}: unknown field {key!r} (expected one of {', '.join(allowed)})")
    return obj


def parse_policy(obj: Any, where: str = "policy") -> Policy:
    obj = _fields(where, obj, ("permit", "forbid", "label", "block"))
    stmts: Policy = []
    for effect in ("permit", "forbid"):
        patterns = obj.get(effect, [])
        if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
            raise PolicyError(f"{where}.{effect}: expected a list of patterns")
        stmts += [Stmt(Pat.parse(p), effect) for p in patterns]
    labels = obj.get("label", {})
    if not isinstance(labels, dict):
        raise PolicyError(f"{where}.label: expected an object mapping patterns to tags")
    for p, value in labels.items():
        pat = Pat.parse(p)
        tags = (_tag(f"{where}.label.{p!r}", value),) if isinstance(value, str) \
            else _tags(f"{where}.label.{p!r}", value)
        stmts += [Stmt(pat, "label", tag=t) for t in tags]
    rules = obj.get("block", [])
    if not isinstance(rules, list):
        raise PolicyError(f"{where}.block: expected a list of rules")
    for i, rule in enumerate(rules):
        w = f"{where}.block[{i}]"
        rule = _fields(w, rule, ("on", "after"))
        if not isinstance(rule.get("on"), str):
            raise PolicyError(f'{w}: expected "on": a pattern')
        if "after" not in rule:
            raise PolicyError(f'{w}: expected "after": a list of tags')
        stmts.append(Stmt(Pat.parse(rule["on"]), "block", after=_tags(f"{w}.after", rule["after"])))
    return stmts


def parse_authority(obj: Any) -> Authority:
    if isinstance(obj, list):
        if not obj:
            raise PolicyError("an authority must have at least one policy")
        return [parse_policy(p, f"[{i}]") for i, p in enumerate(obj)]
    return [parse_policy(obj)]


def width(authority: Authority) -> int:
    """The largest combination in any ``block`` rule."""
    return max((len(st.after) for p in authority for st in p if st.effect == "block"), default=0)


def tags(authority: Authority) -> set[str]:
    return {t for p in authority for st in p
            for t in ((st.tag,) if st.tag else st.after or ())}


def literals(authority: Authority) -> set[str]:
    """Every path segment an authority mentions."""
    return {seg for p in authority for st in p for seg in st.pat.path}


def fresh_segment(segments: set[str]) -> str:
    """The shortest run of underscores that is not in ``segments`` (``Pat.freshFor``)."""
    n = 1
    while "_" * n in segments:
        n += 1
    return "_" * n
