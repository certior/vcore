"""Turning an agent's tool calls into requests.

The verified core decides requests; it cannot know what a tool call touches. This module
is the (tested, not proved) mapping for Claude Code's tools. It follows two rules:

1. **One spelling per resource.** File paths are resolved (symlinks, ``.``, ``..``)
   before they become segments, so ``src/../.env`` and a symlink to ``.env`` both become
   ``Read/.env``. URLs are reduced to host and path.
2. **Fail closed.** Anything the mapping cannot represent faithfully becomes a request
   that policies do not permit by accident: a non-canonical request (always denied by the
   core), or ``Bash/@shell`` for commands with shell syntax.

Requests produced:

========================  ==================================================
Tool call                 Request
========================  ==================================================
Read, Edit, Write, ...    ``Read/src/app.py`` (inside the project)
                          ``Read/~/.ssh/id_rsa`` (under the home directory)
                          ``Read/@/etc/passwd`` (elsewhere)
Grep, Glob                ``Grep/src`` (the directory searched)
WebFetch                  ``WebFetch/pypi.org/simple/requests`` (host and path)
WebSearch                 ``WebSearch``
Bash                      ``Bash/git/diff/HEAD`` (argv), or ``Bash/@shell``
MCP tools                 ``mcp/github/get_issue``
anything else             the tool name
========================  ==================================================
"""

from __future__ import annotations

import os
import shlex
from urllib.parse import unquote, urlsplit

PATH_TOOLS = {"Read": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
              "Write": "file_path", "NotebookEdit": "notebook_path", "NotebookRead": "notebook_path"}
SEARCH_TOOLS = {"Grep", "Glob", "LS"}
SHELL_SYNTAX = set("|&;<>()$`\\\n{}*?[]~!#")
DENIED = "@invalid/."
"""A non-canonical request: always denied."""


def _segments(path: str) -> list[str]:
    return [s for s in path.split(os.sep) if s]


def file_request(tool: str, path: str, project_dir: str, home: str | None = None,
                 casefold: bool = False) -> str:
    """``tool/<segments>``, relative to the project, the home directory (``~``) or ``/`` (``@``).

    Set ``casefold`` on case-insensitive file systems (macOS, Windows) so that
    ``Read/SECRETS/key`` and ``Read/secrets/key`` are the same request.
    """
    if not path:
        return DENIED
    root = os.path.realpath(project_dir)
    full = os.path.realpath(os.path.join(root, os.path.expanduser(path)))
    home = os.path.realpath(home or os.path.expanduser("~"))
    if full == root or full.startswith(root + os.sep):
        segs = _segments(os.path.relpath(full, root)) if full != root else []
    elif full == home or full.startswith(home + os.sep):
        segs = ["~"] + _segments(os.path.relpath(full, home))
    else:
        segs = ["@"] + _segments(full)
    if casefold:
        segs = [s.casefold() for s in segs]
    return "/".join([tool] + segs)


def url_request(tool: str, url: str) -> str:
    """``tool/<host>/<path segments>``. Percent-escapes are decoded, so ``%2e%2e`` is ``..``
    and the request is denied."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.port:
            host = f"{host}:{parts.port}"
    except ValueError:
        return DENIED
    if parts.scheme not in ("http", "https") or not host:
        return DENIED
    segs = [unquote(s) for s in parts.path.split("/")]
    # Collapse "//" and a trailing "/", which name the same resource; keep "." and "..".
    segs = [s for s in segs if s]
    if any("/" in s for s in segs):  # an encoded slash
        return DENIED
    return "/".join([tool, host] + segs)


def shell_request(command: str) -> str:
    """``Bash/<argv>`` for simple commands; ``Bash/@shell`` if the command uses shell syntax
    (pipes, redirection, substitution, globbing, ...), which a request cannot describe."""
    if not command.strip() or any(c in SHELL_SYNTAX for c in command):
        return "Bash/@shell"
    try:
        argv = shlex.split(command)
    except ValueError:
        return "Bash/@shell"
    # Arguments that look like paths become several segments, so "../.env" is refused.
    return "/".join(["Bash"] + [seg for arg in argv for seg in arg.split("/")])


def request_for_tool_call(tool_name: str, tool_input: dict, project_dir: str,
                          home: str | None = None, casefold: bool = False) -> str:
    """The request for one Claude Code tool call."""
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__")
        return "/".join(["mcp"] + parts[1:]) if all(parts[1:]) else DENIED
    if tool_name in PATH_TOOLS:
        path = tool_input.get(PATH_TOOLS[tool_name]) or tool_input.get("path")
        return file_request(tool_name, path, project_dir, home, casefold) if isinstance(path, str) \
            else DENIED
    if tool_name in SEARCH_TOOLS:
        path = tool_input.get("path") or "."
        return file_request(tool_name, path, project_dir, home, casefold) if isinstance(path, str) \
            else DENIED
    if tool_name == "WebFetch":
        url = tool_input.get("url")
        return url_request("WebFetch", url) if isinstance(url, str) else DENIED
    if tool_name == "Bash":
        command = tool_input.get("command")
        return shell_request(command) if isinstance(command, str) else DENIED
    return tool_name if tool_name and "/" not in tool_name else DENIED
