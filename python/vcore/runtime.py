"""Zero-dependency enforcement runtime.

Evaluates indices produced by the verified compiler (``vcore compile``). The theorems
``compile_allows`` and ``compile_step`` (VCore/Compile.lean) prove that a compiled
authority makes the same decisions and adds the same tags as its source policies; this
module mirrors ``Index.allows`` and ``Index.step`` and is checked against the Lean
semantics by the differential tests.

    session = Session([load_index("policy.index.json")])
    session.run("Read/customers/export.csv")    # allowed; context now {private}
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from os import PathLike

from .policy import Pat, Path, PolicyError, is_canonical, parse_path


@dataclass(frozen=True)
class Summary:
    """Everything one policy says about a request."""

    permit: bool = False
    forbid: bool = False
    labels: frozenset[str] = frozenset()
    blocks: frozenset[frozenset[str]] = frozenset()

    def merge(self, other: Summary) -> Summary:
        return Summary(self.permit or other.permit, self.forbid or other.forbid,
                       self.labels | other.labels, self.blocks | other.blocks)


EMPTY = Summary()


def keys(x: Path) -> Iterator[tuple[str, Path]]:
    """The only patterns that can cover ``x`` (``VCore.keys``)."""
    yield ("exact", x)
    for i in range(len(x) + 1):
        yield ("prefixed", x[:i])
        yield ("suffixed", x[i:])


class Index:
    """A compiled policy: pattern -> merged summary."""

    def __init__(self, table: dict[tuple[str, Path], Summary]):
        self.table = table

    @classmethod
    def from_json(cls, obj: dict) -> Index:
        if not isinstance(obj, dict) or not isinstance(obj.get("index"), list):
            raise PolicyError('index: expected {"index": [...]}')
        table: dict[tuple[str, Path], Summary] = {}
        for entry in obj["index"]:
            try:
                pattern, s = entry
                pat = Pat.parse(pattern)
                summary = Summary(bool(s["permit"]), bool(s["forbid"]), frozenset(s["labels"]),
                                  frozenset(frozenset(b) for b in s["blocks"]))
            except (TypeError, ValueError, KeyError) as e:
                raise PolicyError(f"index: malformed entry {entry!r}") from e
            key = (pat.kind, pat.path)
            table[key] = table.get(key, EMPTY).merge(summary)
        return cls(table)

    def summary(self, x: Path) -> Summary:
        s = EMPTY
        for key in keys(x):
            entry = self.table.get(key)
            if entry is not None:
                s = s.merge(entry)
        return s


def load_index(path: str | PathLike) -> Index:
    with open(path) as f:
        return Index.from_json(json.load(f))


@dataclass(frozen=True)
class Decision:
    allow: bool
    context: tuple[str, ...]
    """The session's tags after the request (unchanged if it was denied), sorted."""
    reasons: tuple[str, ...]


class Session:
    """One agent session under an authority: compiled policies that must all allow."""

    def __init__(self, authority: Iterable[Index], context: Iterable[str] = ()):
        self.authority = list(authority)
        if not self.authority:
            raise ValueError("an authority must have at least one policy")
        self.context = frozenset(context)

    def decide(self, request: str) -> tuple[Decision, frozenset[str]]:
        x = parse_path(request)
        if not is_canonical(x):
            return Decision(False, tuple(sorted(self.context)),
                            ("not a canonical request (empty, '.' or '..' segment)",)), self.context
        reasons: list[str] = []
        taint: set[str] = set()
        many = len(self.authority) > 1
        for i, index in enumerate(self.authority):
            s = index.summary(x)
            name = f"[{i}]: " if many else ""
            if not s.permit:
                reasons.append(name + "no permit")
            if s.forbid:
                reasons.append(name + "forbidden")
            for b in sorted(s.blocks, key=sorted):
                if b <= self.context:
                    reasons.append(name + "blocked after " + ", ".join(sorted(b)))
            taint |= s.labels
        after = self.context | taint if not reasons else self.context
        return Decision(not reasons, tuple(sorted(after)), tuple(reasons)), after

    def check(self, request: str) -> Decision:
        """Decide a request without running it."""
        return self.decide(request)[0]

    def run(self, request: str) -> Decision:
        """Decide a request and, if it is allowed, add its tags to the session."""
        decision, self.context = self.decide(request)
        return decision
