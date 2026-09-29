"""The `vcore` command line: exit status, messages, JSON output."""

import json
import subprocess

from conftest import EXAMPLES
from vcore.lean import find_vcore


def vcore(*args, cwd=EXAMPLES):
    return subprocess.run([str(find_vcore()), *args], cwd=cwd, capture_output=True, text=True)


def test_check_exit_status():
    assert vcore("check", "policy.json", "cleanup.json").returncode == 0
    result = vcore("check", "policy.json", "trust-issues.json")
    assert result.returncode == 1
    assert "does not refine" in result.stdout and "blocked after private, untrusted" in result.stdout


def test_check_json():
    out = json.loads(vcore("check", "policy.json", "trust-issues.json", "--json").stdout)
    assert out["refines"] is False
    steps = out["counterexample"]
    assert [s["request"] for s in steps][0] == "mcp/github/get_issue"
    assert steps[-1]["old"]["allow"] is False and steps[-1]["new"]["allow"] is True
    assert json.loads(vcore("check", "policy.json", "hardened.json", "--json").stdout) == {"refines": True}


def test_satisfies():
    result = vcore("satisfies", "policy.json", "rule-of-two.json")
    assert result.returncode == 1 and "violates rule-of-two.json" in result.stdout
    assert vcore("satisfies", "hardened.json", "rule-of-two.json").returncode == 0


def test_eval():
    result = vcore("eval", "policy.json", "WebFetch/example.com", "Read/customers/x", "WebFetch/example.com",
                   "Read/../.env")
    lines = result.stdout.splitlines()
    assert result.returncode == 0
    assert lines[0].split()[0] == "allow" and lines[2].split()[0] == "deny"
    assert "not a canonical request" in lines[3]


def test_errors_exit_2(tmp_path):
    bad = tmp_path / "bad.json"
    for text, message in [("[]", "at least one policy"), ('{"permit": "Read/**"}', "list of patterns"),
                          ('{"permit": ["a/**/b"]}', "first or last"), ("{", "invalid JSON"),
                          ('{"block": [{"on": "a", "after": []}]}', "at least one tag")]:
        bad.write_text(text)
        result = vcore("check", str(bad), "policy.json")
        assert result.returncode == 2, text
        assert result.stderr.startswith("vcore: error:") and message in result.stderr, result.stderr
    assert vcore("check", "missing.json", "policy.json").returncode == 2
    assert vcore("frobnicate").returncode == 2
