"""The Claude Code hook in examples/claude-code, end to end."""

import json
import os
import subprocess
import sys

from conftest import REPO

HOOK = REPO / "examples" / "claude-code" / "vcore_hook.py"


def call(project, tool_name, tool_input, session="s1", index=None):
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project), "PYTHONPATH": str(REPO / "python")}
    if index:
        env["VCORE_INDEX"] = str(index)
    event = {"session_id": session, "cwd": str(project), "hook_event_name": "PreToolUse",
             "tool_name": tool_name, "tool_input": tool_input}
    result = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event), env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    if not result.stdout:
        return None
    out = json.loads(result.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse" and out["permissionDecision"] == "deny"
    return out["permissionDecisionReason"]


def test_hook_blocks_the_third_step_of_a_toxic_flow(vcore, example, tmp_path):
    project = tmp_path / "project"
    (project / ".claude" / "vcore").mkdir(parents=True)
    (project / "customers").mkdir()
    index = project / ".claude" / "vcore" / "policy.index.json"
    index.write_text(json.dumps(vcore.compile_json(example("policy.json"))))

    assert call(project, "mcp__github__get_issue", {"issue_number": 7}) is None
    assert call(project, "Read", {"file_path": str(project / "customers" / "export.csv")}) is None
    reason = call(project, "mcp__github__create_pull_request", {"title": "fix"})
    assert "blocked after private, untrusted" in reason
    # Another session is unaffected.
    assert call(project, "mcp__github__create_pull_request", {"title": "fix"}, session="s2") is None
    # Secrets stay unreadable whatever the spelling.
    assert "forbidden" in call(project, "Read", {"file_path": "customers/../.env"})
    state = json.loads((project / ".claude" / "vcore" / "sessions" / "s1.json").read_text())
    assert state == {"context": ["private", "untrusted"]}


def test_hook_fails_closed(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    reason = call(project, "Read", {"file_path": "src/app.py"}, index=tmp_path / "missing.json")
    assert reason.startswith("vcore: hook error")
