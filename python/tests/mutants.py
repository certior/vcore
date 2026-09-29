"""Seeded bugs in the unverified Python runtime and Z3 checker.

Each mutant is a context manager that patches in one plausible mistake. ``test_mutants.py``
requires the differential harness to catch every one of them.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest import mock

from vcore import runtime, smt
from vcore.policy import is_canonical, parse_path
from vcore.runtime import EMPTY, Decision, Index, Session, keys


def _decide(*, forbid=True, all_tags=True, all_policies=True, taint_denied=False, canonical=True):
    """``Session.decide`` with one configurable mistake."""

    def decide(self, request):
        x = parse_path(request)
        verdicts, taint = [], set()
        for index in self.authority:
            s = index.summary(x)
            present = all if all_tags else any
            blocked = any(present(t in self.context for t in b) for b in s.blocks)
            verdicts.append(s.permit and not (forbid and s.forbid) and not blocked)
            taint |= s.labels
        allow = (all if all_policies else any)(verdicts)
        if canonical:
            allow = allow and is_canonical(x)
        after = self.context | taint if (allow or taint_denied) else self.context
        return Decision(allow, tuple(sorted(after if allow else self.context)), ()), after

    return decide


def _patch_decide(**mistake):
    return mock.patch.object(Session, "decide", _decide(**mistake))


@contextmanager
def forbid_ignored():
    """`forbid` does not override `permit`"""
    with _patch_decide(forbid=False):
        yield


@contextmanager
def block_on_any_tag():
    """A block fires when any tag of its combination is present, not all"""
    with _patch_decide(all_tags=False):
        yield


@contextmanager
def chain_any():
    """An authority allows a request if any of its policies does"""
    with _patch_decide(all_policies=False):
        yield


@contextmanager
def denied_request_taints():
    """A denied request still adds its tags"""
    with _patch_decide(taint_denied=True):
        yield


@contextmanager
def noncanonical_allowed():
    """Requests with empty, `.` or `..` segments are evaluated instead of denied"""
    with _patch_decide(canonical=False):
        yield


@contextmanager
def keys_off_by_one():
    """`a/b/**` is not looked up for the request `a/b` itself"""

    def short_keys(x):
        yield ("exact", x)
        for i in range(len(x)):
            yield ("prefixed", x[:i])
            yield ("suffixed", x[i:])

    with mock.patch.object(runtime, "keys", short_keys):
        yield


@contextmanager
def suffix_as_prefix():
    """Suffix entries are looked up under prefixes"""

    def wrong_keys(x):
        yield ("exact", x)
        for i in range(len(x) + 1):
            yield ("prefixed", x[:i])
            yield ("suffixed", x[:i])

    with mock.patch.object(runtime, "keys", wrong_keys):
        yield


@contextmanager
def character_glob():
    """`secrets/**` also covers `secrets-public`: prefix matching on the joined string"""

    def summary(self, x):
        s = EMPTY
        for key in keys(x):
            if key in self.table:
                s = s.merge(self.table[key])
        joined = "/".join(x)
        for (kind, path), entry in self.table.items():
            if kind == "prefixed" and path and joined.startswith("/".join(path)) \
                    and x[: len(path)] != path:
                s = s.merge(entry)
        return s

    with mock.patch.object(Index, "summary", summary):
        yield


@contextmanager
def checker_depth_two():
    """The checker searches two requests deep, the bound for single-tag rules"""
    original = smt.escalation

    def shallow(old, new, **kwargs):
        kwargs["depth"] = 2
        return original(old, new, **kwargs)

    with mock.patch.object(smt, "escalation", shallow):
        yield


@contextmanager
def no_fresh_segment():
    """The Z3 encoding only considers segments that appear in the policies"""
    original = smt.Segments.request

    def request(self, name):
        (n, slots), domain = original(self, name)
        return (n, slots), domain + [s >= 1 for s in slots]

    with mock.patch.object(smt.Segments, "request", request):
        yield


RUNTIME = [forbid_ignored, block_on_any_tag, chain_any, denied_request_taints,
           noncanonical_allowed, keys_off_by_one, suffix_as_prefix, character_glob]
CHECKER = [checker_depth_two]
RARE = [no_fresh_segment]
"""Real bugs that random testing rarely exposes; each has a targeted test."""
