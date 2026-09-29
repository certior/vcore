"""Every seeded bug in the unverified code must be caught."""

import pytest
from vcore import smt
from vcore.policy import parse_authority

import mutants
from conftest import CASES
from harness import runtime_vs_lean, smt_vs_lean


def first_disagreement(check, vcore, limit):
    return next((seed for seed in range(limit) if check(vcore, seed)), None)


@pytest.mark.parametrize("mutant", mutants.RUNTIME, ids=lambda m: m.__name__)
def test_runtime_mutant_is_caught(vcore, mutant):
    with mutant():
        assert first_disagreement(runtime_vs_lean, vcore, CASES) is not None


@pytest.mark.parametrize("mutant", mutants.CHECKER, ids=lambda m: m.__name__)
def test_checker_mutant_is_caught(vcore, mutant):
    with mutant():
        assert first_disagreement(smt_vs_lean, vcore, CASES) is not None


def test_fresh_segment_needed(vcore):
    """`no_fresh_segment` survives random testing. Here every segment that could follow
    `Read` is a literal the old policy permits, so the only escalation goes through a
    segment neither policy mentions, which is what the finite-representatives theorem
    accounts for."""
    old = {"permit": ["Read", "Read/Read/**", "Read/src/**"]}
    new = {"permit": ["Read/**"]}
    assert vcore.check(old, new) == ["Read/_"]
    assert smt.escalation(parse_authority(old), parse_authority(new)) is not None
    with mutants.no_fresh_segment():
        assert smt.escalation(parse_authority(old), parse_authority(new)) is None
