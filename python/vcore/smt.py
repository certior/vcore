"""An independent refinement checker on Z3.

Shares no code with the Lean checker. It searches sequences of requests of increasing
length for one that ``new`` runs and ``old`` stops. The Lean theorem
``short_counterexample`` shows that ``width(old) + 1`` requests always suffice, which is
what makes the default depth a complete method rather than a heuristic; the tests search
deeper to check that theorem independently.

A request is encoded as a length and a fixed number of segment slots, each an integer
naming one literal segment of the two authorities or "any other segment" (``Segments``),
so everything is linear integer arithmetic over booleans. ``Strings``, the natural
encoding as one string with prefix and suffix constraints, is kept for comparison.

``stateless_escalation`` is the per-request policy diff used for stateless authorization
languages, with the session context left free. It is the baseline in the evaluation.
"""

from __future__ import annotations

from z3 import And, Bool, BoolRef, BoolVal, Contains, Int, Not, Or, PrefixOf, Solver, String, \
    StringVal, SuffixOf, is_true, sat, unknown

from .policy import Authority, Pat, fresh_segment, literals, tags, width


class Segments:
    """A request is a length ``n`` and slots ``s[0..bound)``: slot value ``i > 0`` is the
    i-th literal segment, ``0`` a segment no policy mentions. Paths of up to ``bound``
    segments are searched, which includes every representative path."""

    def __init__(self, old: Authority, new: Authority):
        pats = [st.pat for policy in old + new for st in policy]
        self.segments = sorted(literals(old) | literals(new))
        self.ids = {seg: i + 1 for i, seg in enumerate(self.segments)}
        longest = lambda kind: max((len(p.path) for p in pats if p.kind == kind), default=0)
        self.bound = max(longest("exact"), longest("prefixed") + 1 + longest("suffixed"))
        self.fresh = fresh_segment(set(self.segments))

    def request(self, name: str):
        n = Int(f"{name}_len")
        slots = [Int(f"{name}_{i}") for i in range(self.bound)]
        domain = [1 <= n, n <= self.bound] + [And(0 <= s, s <= len(self.segments)) for s in slots]
        return (n, slots), domain

    def covers(self, pat: Pat, x) -> BoolRef:
        n, slots = x
        p = pat.path
        match = lambda offset: And(BoolVal(True), *[
            slots[offset + i] == self.ids[seg] for i, seg in enumerate(p)])
        if pat.kind == "exact":
            return And(n == len(p), match(0))
        if pat.kind == "prefixed":
            return And(n >= len(p), match(0))
        return Or(BoolVal(False), *[
            And(n == m, match(m - len(p))) for m in range(max(len(p), 1), self.bound + 1)])

    def decode(self, model, x) -> str:
        n, slots = x
        length = model.eval(n, model_completion=True).as_long()
        values = [model.eval(s, model_completion=True).as_long() for s in slots[:length]]
        return "/".join(self.segments[v - 1] if v else self.fresh for v in values)


class Strings:
    """A request is one string; segments are joined by ``/``, which no segment contains."""

    def __init__(self, old: Authority, new: Authority):
        pass

    def request(self, name: str):
        x = String(name)
        canonical = [x != StringVal(""), x != StringVal("."), x != StringVal("..")]
        canonical += [Not(PrefixOf(StringVal(s), x)) for s in ("/", "./", "../")]
        canonical += [Not(SuffixOf(StringVal(s), x)) for s in ("/", "/.", "/..")]
        canonical += [Not(Contains(x, StringVal(s))) for s in ("//", "/./", "/../")]
        return x, canonical

    def covers(self, pat: Pat, x) -> BoolRef:
        if not pat.path:
            return BoolVal(pat.kind != "exact")
        lit = "/".join(pat.path)
        if pat.kind == "exact":
            return x == StringVal(lit)
        if pat.kind == "prefixed":
            return Or(x == StringVal(lit), PrefixOf(StringVal(lit + "/"), x))
        return Or(x == StringVal(lit), SuffixOf(StringVal("/" + lit), x))

    def decode(self, model, x) -> str:
        return model.eval(x, model_completion=True).as_string()


def _any(enc, policy, effect: str, x) -> BoolRef:
    return Or(BoolVal(False), *[enc.covers(st.pat, x) for st in policy if st.effect == effect])


def allows(enc, authority: Authority, ctx: dict, x) -> BoolRef:
    """``Authority.allows``: every policy permits, none forbids, no combination is present."""
    return And(BoolVal(True), *[
        And(_any(enc, p, "permit", x), Not(_any(enc, p, "forbid", x)),
            *[Not(And(enc.covers(st.pat, x), *[ctx[t] for t in st.after]))
              for st in p if st.effect == "block"])
        for p in authority])


def step(enc, authority: Authority, ctx: dict, x) -> dict:
    """``Authority.step``: a tag is present after ``x`` if it was, or some label adds it."""
    return {t: Or(ctx[t], *[enc.covers(st.pat, x) for p in authority for st in p
                            if st.effect == "label" and st.tag == t])
            for t in ctx}


def escalation(old: Authority, new: Authority, *, depth: int | None = None,
               encoding: type = Segments, timeout_ms: int = 60_000) -> list[str] | None:
    """A shortest sequence of at most ``depth`` requests (default ``width(old) + 1``) that
    ``new`` runs and ``old`` stops, or None. Raises TimeoutError if Z3 gives up."""
    depth = width(old) + 1 if depth is None else depth
    enc = encoding(old, new)
    universe = sorted(tags(old) | tags(new))
    solver = Solver()
    solver.set("timeout", timeout_ms)
    co = {t: BoolVal(False) for t in universe}
    cn = dict(co)
    xs = []
    for i in range(depth):
        x, domain = enc.request(f"x{i}")
        xs.append(x)
        solver.add(*domain)
        solver.push()
        solver.add(allows(enc, new, cn, x), Not(allows(enc, old, co, x)))
        result = solver.check()
        if result == sat:
            model = solver.model()
            return [enc.decode(model, v) for v in xs]
        if result == unknown:
            raise TimeoutError(f"z3 returned unknown at length {i + 1}")
        solver.pop()
        solver.add(allows(enc, old, co, x), allows(enc, new, cn, x))
        co_next = {t: Bool(f"co{i}_{t}") for t in universe}
        cn_next = {t: Bool(f"cn{i}_{t}") for t in universe}
        so, sn = step(enc, old, co, x), step(enc, new, cn, x)
        solver.add(*[co_next[t] == so[t] for t in universe], *[cn_next[t] == sn[t] for t in universe])
        co, cn = co_next, cn_next
    return None


def stateless_escalation(old: Authority, new: Authority, *,
                         encoding: type = Segments) -> tuple[list[str], str] | None:
    """Per-request diff with a free context: ``(context, request)`` such that ``new``
    allows the request in that context and ``old`` denies it, or None."""
    enc = encoding(old, new)
    universe = sorted(tags(old) | tags(new))
    ctx = {t: Bool(f"ctx_{t}") for t in universe}
    solver = Solver()
    x, domain = enc.request("x")
    solver.add(*domain, allows(enc, new, ctx, x), Not(allows(enc, old, ctx, x)))
    if solver.check() != sat:
        return None
    model = solver.model()
    present = [t for t in universe if is_true(model.eval(ctx[t], model_completion=True))]
    return present, enc.decode(model, x)
