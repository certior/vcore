# Enforcing a policy in Claude Code

[`vcore_hook.py`](vcore_hook.py) is a `PreToolUse` hook. For every tool call it builds the
request (see [`toolcalls.py`](../../python/vcore/toolcalls.py)), decides it against a
compiled policy in the context of everything the session has run, and records the tags of
allowed calls in `.claude/vcore/sessions/<session id>.json`.

* A denied call is refused, and Claude sees the reason
  (`mcp/github/create_pull_request denied: blocked after private, untrusted`).
* An allowed call gets no answer from the hook, so Claude Code's own permission rules still
  apply. The hook can only narrow what the agent may do.
* If the hook cannot read the policy or its input, the call is denied.

## Setup

From your project, with this repository at `$VCORE_REPO`:

```bash
pip install "$VCORE_REPO"                       # the zero-dependency runtime
mkdir -p .claude/vcore
cp "$VCORE_REPO/examples/claude-code/vcore_hook.py" .claude/
"$VCORE_REPO/.lake/build/bin/vcore" compile my-policy.json > .claude/vcore/policy.index.json
```

Then add the hook to `.claude/settings.json` (merging with any hooks you already have):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "*",
        "hooks": [
          { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.claude/vcore_hook.py" }
        ]
      }
    ]
  }
}
```

Before changing `my-policy.json`, check the change:

```bash
vcore check my-policy.json my-policy.new.json
```

and recompile the index after accepting it. Add `.claude/vcore/sessions/` to `.gitignore`.

## Settings

| Variable | Default | |
|---|---|---|
| `VCORE_INDEX` | `$CLAUDE_PROJECT_DIR/.claude/vcore/policy.index.json` | compiled policy |
| `VCORE_STATE` | `$CLAUDE_PROJECT_DIR/.claude/vcore/sessions` | per-session tags |
| `VCORE_CASEFOLD` | unset | set to `1` on macOS and Windows, where file names are case-insensitive |

## Limits

* **Tags are recorded before the call runs.** If you reject an allowed call in Claude Code's
  own prompt, the session keeps its tags. This errs on the strict side.
* **Shell commands.** A command with pipes, redirection, substitution or globbing becomes
  `Bash/@shell`; a simple command becomes `Bash/<arguments>`, and arguments with `..` or an
  absolute path are always denied. What a permitted command does is outside the policy's
  view: permit specific commands, not `Bash/**`.
* **Search tools** map to the directory they search (`Grep/src`). Label and forbid them
  like reads of everything below that directory.
* **Concurrency.** Parallel tool calls in one session are serialized with a file lock on
  POSIX systems, not on Windows.
