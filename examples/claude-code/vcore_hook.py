#!/usr/bin/env python3
"""Claude Code PreToolUse hook: enforce a compiled vcore policy, with session memory.

For every tool call, the hook turns the call into a request, decides it against the
policy in the context of everything the session has run so far, and records the tags of
allowed calls. A denied call is refused with the reason; an allowed call gets no answer
from the hook, so Claude Code's own permission rules still apply. The hook only narrows.

Configuration, by environment variable:

  VCORE_INDEX      compiled policy (vcore compile policy.json > policy.index.json)
                   default: $CLAUDE_PROJECT_DIR/.claude/vcore/policy.index.json
  VCORE_STATE      directory for per-session tags
                   default: $CLAUDE_PROJECT_DIR/.claude/vcore/sessions
  VCORE_CASEFOLD   set to 1 on case-insensitive file systems (macOS, Windows)

Anything unexpected (unreadable policy, malformed input) denies the call.

Tags are recorded when a call is allowed, before it runs. If you then reject the call in
Claude Code's own prompt, the session keeps its tags: the hook errs on the strict side.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path

from vcore import Session, load_index, request_for_tool_call

try:
    import fcntl
except ImportError:  # Windows: concurrent tool calls in one session are not serialized.
    fcntl = None


def respond_deny(reason: str) -> None:
    json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                      "permissionDecision": "deny",
                                      "permissionDecisionReason": f"vcore: {reason}"}}, sys.stdout)


@contextmanager
def session_state(state_dir: Path, session_id: str):
    """The session's tags, locked for the duration of one decision."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
        session_id = hashlib.sha256(session_id.encode()).hexdigest()
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / f"{session_id}.json"
    with open(path, "a+") as f:
        if fcntl:
            fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        text = f.read()
        tags = set(json.loads(text)["context"]) if text else set()
        state = {"context": tags}
        yield state
        f.seek(0)
        f.truncate()
        json.dump({"context": sorted(state["context"])}, f)


def main() -> int:
    try:
        event = json.load(sys.stdin)
        project = os.environ.get("CLAUDE_PROJECT_DIR") or event["cwd"]
        index_path = Path(os.environ.get("VCORE_INDEX", Path(project, ".claude/vcore/policy.index.json")))
        state_dir = Path(os.environ.get("VCORE_STATE", Path(project, ".claude/vcore/sessions")))
        request = request_for_tool_call(event["tool_name"], event.get("tool_input") or {}, project,
                                        casefold=os.environ.get("VCORE_CASEFOLD") == "1")
        index = load_index(index_path)
        with session_state(state_dir, str(event["session_id"])) as state:
            session = Session([index], state["context"])
            decision = session.run(request)
            state["context"] = session.context
    except Exception as e:  # fail closed
        respond_deny(f"hook error: {type(e).__name__}: {e}")
        return 0
    if not decision.allow:
        respond_deny(f"{request} denied: {'; '.join(decision.reasons)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
