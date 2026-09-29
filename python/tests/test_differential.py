"""Agreement between the verified Lean core and the unverified Python and Z3 code."""

import random

import generate
from conftest import CASES
from harness import parser_vs_lean, runtime_vs_lean, smt_vs_lean


def test_runtime_matches_lean_semantics(vcore):
    failures = [d for seed in range(CASES) if (d := runtime_vs_lean(vcore, seed))]
    assert not failures, failures[0].detail


def test_smt_checker_matches_lean_checker(vcore):
    failures = [d for seed in range(CASES) if (d := smt_vs_lean(vcore, seed))]
    assert not failures, failures[0].detail


def test_parsers_agree(vcore):
    failures = [d for seed in range(CASES * 2) if (d := parser_vs_lean(vcore, seed))]
    assert not failures, failures[0].detail


def test_delegation_never_escalates(vcore):
    """``delegate_refines``, observed: attaching any policy to an authority is a refinement."""
    for seed in range(CASES // 4):
        rng = random.Random(seed)
        parent, child = generate.policy(rng), generate.flow_policy(rng)
        assert vcore.check(parent, [child, parent]) is None


def test_bound_is_reached(vcore):
    """Some random pairs need exactly ``width + 1`` requests, for widths 1 to 3."""
    needed = set()
    for seed in range(CASES * 3):
        rng = random.Random(seed)
        old = generate.flow_policy(rng)
        new = generate.mutate(rng, old)
        w = vcore.check(old, new)
        if w is not None and len(w) == vcore.width(old) + 1:
            needed.add(len(w))
    assert {2, 3} <= needed
