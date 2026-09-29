"""Differential checks against the Lean implementation, shared by the tests and the evaluation."""

from __future__ import annotations

import random
from dataclasses import dataclass

from vcore import smt
from vcore.policy import PolicyError, parse_authority, width
from vcore.runtime import Session

import generate


@dataclass
class Disagreement:
    seed: int
    detail: str


def as_list(auth) -> list:
    return auth if isinstance(auth, list) else [auth]


def runtime_vs_lean(vcore, seed: int) -> Disagreement | None:
    """The Python runtime on Lean-compiled indices against the Lean source semantics."""
    rng = random.Random(seed)
    auth = generate.authority(rng)
    trace = generate.requests(rng, auth, rng.randint(1, 6))
    expected = vcore.eval(auth, trace)
    session = Session([vcore.compile(p) for p in as_list(auth)])
    actual = [(d.allow, d.context) for d in map(session.run, trace)]
    if actual != expected:
        return Disagreement(seed, f"auth={auth} trace={trace} lean={expected} python={actual}")
    return None


def accepts(vcore, auth, trace: list[str]) -> bool:
    return all(ok for ok, _ in vcore.eval(auth, trace))


def pair(seed: int) -> tuple:
    rng = random.Random(seed)
    old = generate.authority(rng)
    new = generate.mutate(rng, old) if rng.random() < 0.6 else generate.authority(rng)
    return old, new


def smt_vs_lean(vcore, seed: int) -> Disagreement | None:
    """The Z3 checker against the Lean checker: verdicts, witness validity, witness length.

    Z3 searches two requests deeper than the proven bound, so a longer counterexample that
    the theorem rules out would show up as a disagreement.
    """
    old, new = pair(seed)
    lean_w = vcore.check(old, new)
    z3_w = smt.escalation(parse_authority(old), parse_authority(new),
                          depth=width(parse_authority(old)) + 3)
    ctx = f"old={old} new={new} lean={lean_w} z3={z3_w}"
    if (lean_w is None) != (z3_w is None):
        return Disagreement(seed, "verdicts differ: " + ctx)
    for w in (lean_w, z3_w):
        if w is not None and not (accepts(vcore, new, w) and not accepts(vcore, old, w)
                                  and accepts(vcore, old, w[:-1])):
            return Disagreement(seed, "invalid witness: " + ctx)
    if lean_w is not None and len(lean_w) != len(z3_w):
        return Disagreement(seed, "witness lengths differ: " + ctx)
    return None


def parser_vs_lean(vcore, seed: int) -> Disagreement | None:
    """The Python parser against the Lean parser on mostly malformed documents: both must
    accept the same documents and read the same statements from them."""
    doc = generate.malformed(random.Random(seed))
    lean = vcore.parse(doc)
    try:
        python = [sorted((st.effect, str(st.pat), st.tag if st.effect == "label" else st.after)
                         for st in p) for p in parse_authority(doc)]
    except PolicyError as e:
        python = str(e)
    if isinstance(lean, str) != isinstance(python, str):
        return Disagreement(seed, f"doc={doc} lean={lean} python={python}")
    if not isinstance(lean, str) and [sorted(p, key=repr) for p in lean] != \
            [sorted(p, key=repr) for p in python]:
        return Disagreement(seed, f"doc={doc} lean={lean} python={python}")
    return None
