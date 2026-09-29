"""Authority envelopes: what an agent session in a workflow may do, as a vcore authority.

An **envelope** over-approximates one agent session started by one workflow step for one
triggering event. It is a vcore policy (see ``docs/language.md``) whose statements carry
provenance:

* ``permit``/``forbid``: the requests the agent's tool configuration lets it make;
* ``label``: what running a request exposes the session to (``untrusted``, ``secret``).

The envelope has no ``block`` statements, because the actions modeled here enforce none;
session rules enter only through a *property* (:mod:`vcore.actions.properties`) or a
hardening policy composed with the envelope as a vcore authority.

**Direction of approximation.** An envelope must allow at least every request the real
session can make, and label a request at least as much as the real system taints it.
When a permission rule cannot be written exactly as a pattern, a ``permit`` is widened
(to the longest literal prefix) and a ``forbid`` is dropped. Both moves only add
behaviors, so a verdict of "satisfies" on the envelope transfers to the real session
under the named assumptions, and a counterexample is a trace the *configuration* admits,
not a demonstrated attack.

Request vocabulary (the conventions of :mod:`vcore.toolcalls`, plus two families):

==============================  =======================================================
``Input/<source...>``           text placed in the session before the first tool call
``Read/<path>``, ``Edit/...``   workspace-relative file tools (``~``: home, ``@``: root)
``Bash/<argv...>``              a simple command; ``Bash/@shell`` any command with
                                shell syntax (pipes, substitution, ...)
``Bash/@action/<path...>``      a script shipped inside the action (e.g. its push wrapper)
``WebFetch/<host>/<path...>``   a URL fetch
``mcp/<server>/<tool>``         an MCP tool
``Output/<channel>``            text the action publishes after the session
==============================  =======================================================
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Iterable, Literal

UNTRUSTED = "untrusted"
SECRET = "secret"

Kind = Literal["permit", "forbid", "label"]


@dataclass(frozen=True)
class Statement:
    """One envelope statement and why it is there.

    ``source`` is a citation (``path:line @ commit``) or an assumption id (``A3``).
    """
    kind: Kind
    pattern: str
    why: str
    source: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Assumption:
    id: str
    text: str


@dataclass
class Envelope:
    """The authority of one agent session, with provenance."""
    action: str
    commit: str
    event: str
    activity: str | None
    mode: str | None
    runs: bool | Literal["maybe"]
    statements: list[Statement] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    assumptions: set[str] = field(default_factory=set)

    def add(self, kind: Kind, pattern: str, why: str, source: str, tags: Iterable[str] = ()) -> None:
        st = Statement(kind, pattern, why, source, tuple(tags))
        if st not in self.statements:
            self.statements.append(st)
        if re.fullmatch(r"A\d+", source):
            self.assumptions.add(source)

    def permit(self, pattern: str, why: str, source: str) -> None:
        self.add("permit", pattern, why, source)

    def forbid(self, pattern: str, why: str, source: str) -> None:
        self.add("forbid", pattern, why, source)

    def label(self, pattern: str, tag: str, why: str, source: str) -> None:
        self.add("label", pattern, why, source, (tag,))

    def policy(self) -> dict:
        """The envelope as a vcore policy document."""
        permit = sorted({s.pattern for s in self.statements if s.kind == "permit"})
        forbid = sorted({s.pattern for s in self.statements if s.kind == "forbid"})
        labels: dict[str, set[str]] = {}
        for s in self.statements:
            if s.kind == "label":
                labels.setdefault(s.pattern, set()).update(s.tags)
        doc: dict = {"permit": permit}
        if forbid:
            doc["forbid"] = forbid
        if labels:
            doc["label"] = {p: sorted(t) if len(t) > 1 else next(iter(t))
                            for p, t in sorted(labels.items())}
        return doc

    def tagged(self, tag: str) -> list[Statement]:
        return [s for s in self.statements if s.kind == "label" and tag in s.tags]


# --- Claude Code permission rules --------------------------------------------------------

READ_ONLY_TOOLS = ("Read", "Glob", "Grep", "LS")
"""Tools Claude Code runs without a permission rule (assumption A1)."""

FILE_TOOLS = {"Read", "Edit", "MultiEdit", "Write", "NotebookEdit", "NotebookRead", "Glob", "Grep", "LS"}
EDIT_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")

_SEG = re.compile(r"^[^/]+$")


def _canonical(segs: list[str]) -> bool:
    return bool(segs) and all(s not in ("", ".", "..") for s in segs)


@dataclass(frozen=True)
class RuleMapping:
    """A Claude Code rule and the vcore pattern that stands for it."""
    rule: str
    pattern: str | None  # None: no sound pattern (only possible for a forbid)
    exact: bool
    note: str = ""


def _bash_pattern(spec: str, allow: bool) -> RuleMapping:
    """``Bash(<spec>)``. ``cmd:*`` and a trailing `` *`` are prefix rules; any other ``*`` or
    shell syntax makes the literal part before it a prefix (widened)."""
    rule = f"Bash({spec})"
    body = spec.strip()
    prefix = False
    if body.endswith(":*"):
        body, prefix = body[:-2], True
    elif body.endswith(" *") or body == "*":
        body, prefix = body[:-1].rstrip(), True
    exact = True
    if "*" in body or any(c in body for c in "|&;<>()$`\n"):
        cut = min([i for i, c in enumerate(body) if c in "*|&;<>()$`\n"])
        body, prefix, exact = body[:cut].rstrip(), True, False
    try:
        argv = shlex.split(body)
    except ValueError:
        argv, exact = body.split(), False
    segs = [seg for arg in argv for seg in arg.split("/")]
    if segs and segs[0] == "" and len(argv) and argv[0].startswith("/"):
        segs = ["@"] + segs[1:]
    if not segs:
        if allow:
            return RuleMapping(rule, "Bash/**", False, "empty command prefix: any command")
        return RuleMapping(rule, None, False, "cannot represent; dropped")
    if not _canonical(segs):
        if allow:
            return RuleMapping(rule, "Bash/**", False, "non-canonical argv: widened to any command")
        return RuleMapping(rule, None, False, "non-canonical argv; dropped")
    pattern = "Bash/" + "/".join(segs) + ("/**" if prefix else "")
    return RuleMapping(rule, pattern, exact)


def _path_pattern(tool: str, spec: str, allow: bool) -> RuleMapping:
    """``Read(src/**)``-style gitignore rules: keep the literal directory prefix."""
    rule = f"{tool}({spec})"
    s = spec.strip()
    root: list[str] = []
    if s.startswith("//"):
        root, s = ["@"], s[2:]
    elif s.startswith("~/"):
        root, s = ["~"], s[2:]
    elif s.startswith("./"):
        s = s[2:]
    elif s.startswith("/"):
        s = s[1:]  # relative to the settings file: the workspace root here
    parts = [p for p in s.split("/") if p != ""]
    literal: list[str] = []
    exact = True
    for p in parts:
        if any(c in p for c in "*?[]{}!"):
            exact = p == "**" and p is parts[-1]
            break
        literal.append(p)
    else:
        exact = True
    segs = root + literal
    if not segs:
        if allow:
            return RuleMapping(rule, f"{tool}/**", exact and not parts, "")
        return RuleMapping(rule, None, False, "pattern starts with a wildcard; dropped")
    if not _canonical(segs):
        return RuleMapping(rule, f"{tool}/**" if allow else None, False, "non-canonical path")
    whole = len(literal) == len(parts)
    pattern = f"{tool}/" + "/".join(segs) + ("" if whole and exact else "/**")
    if not allow and not (whole or (exact and parts and parts[-1] == "**")):
        return RuleMapping(rule, None, False, "glob inside a forbid; dropped")
    return RuleMapping(rule, pattern, exact)


def map_rule(rule: str, allow: bool = True) -> RuleMapping:
    """The vcore pattern for one ``--allowedTools``/``--disallowedTools`` entry."""
    r = rule.strip()
    m = re.fullmatch(r"([A-Za-z_][\w-]*)\((.*)\)", r, re.S)
    if r.startswith("mcp__"):
        parts = r.split("__")
        server = parts[1] if len(parts) > 1 else ""
        tool = "__".join(parts[2:])
        if not server or "/" in r:
            return RuleMapping(r, None if not allow else "mcp/**", False, "malformed MCP rule")
        if tool in ("", "*"):
            return RuleMapping(r, f"mcp/{server}/**", True)
        if "*" in tool:
            return RuleMapping(r, f"mcp/{server}/**" if allow else None, False, "wildcard tool name")
        return RuleMapping(r, f"mcp/{server}/{tool}", True)
    if m:
        tool, spec = m.group(1), m.group(2)
        if tool == "Bash":
            return _bash_pattern(spec, allow)
        if tool in FILE_TOOLS:
            return _path_pattern(tool, spec, allow)
        if tool == "WebFetch":
            dm = re.fullmatch(r"\s*domain:([A-Za-z0-9.-]+)\s*", spec)
            if dm:
                return RuleMapping(r, f"WebFetch/{dm.group(1).lower()}/**", True)
            return RuleMapping(r, "WebFetch/**" if allow else None, False, "unrecognized WebFetch rule")
        return RuleMapping(r, f"{tool}/**" if allow else None, False, "specifier not modeled")
    if re.fullmatch(r"[A-Za-z_][\w-]*", r):
        if r in FILE_TOOLS or r in ("Bash", "WebFetch"):
            return RuleMapping(r, f"{r}/**", True)
        return RuleMapping(r, r if r != "*" else "**", True)
    return RuleMapping(r, "**" if allow else None, False, "unparseable rule")


# --- Command lines -------------------------------------------------------------------------

def split_args(text: str) -> list[str]:
    """Tokenize a ``claude_args`` value: drop full-line ``#`` comments, then POSIX shell
    words (the action uses shell-quote with metacharacters escaped, which agrees on the
    inputs seen in practice)."""
    kept = "\n".join(l for l in (text or "").split("\n") if not l.strip().startswith("#"))
    try:
        return shlex.split(kept, comments=False, posix=True)
    except ValueError:
        return kept.split()


ACCUMULATING = {"allowedTools", "allowed-tools", "disallowedTools", "disallowed-tools",
                "mcp-config", "add-dir"}


def parse_claude_args(text: str) -> dict[str, list[str] | str | None]:
    """Flags as the action's ``parseClaudeArgsToExtraArgs`` reads them: accumulating flags
    take every following non-flag word, others take one value (last occurrence wins), and a
    flag followed by a flag is boolean (``None``)."""
    args = split_args(text)
    out: dict[str, list[str] | str | None] = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--"):
            flag = a[2:]
            nxt = args[i + 1] if i + 1 < len(args) else None
            if nxt is not None and not nxt.startswith("--"):
                if flag in ACCUMULATING:
                    vals = []
                    while i + 1 < len(args) and not args[i + 1].startswith("--"):
                        i += 1
                        vals.append(args[i])
                    key = {"allowed-tools": "allowedTools",
                           "disallowed-tools": "disallowedTools"}.get(flag, flag)
                    prev = out.get(key)
                    out[key] = (prev if isinstance(prev, list) else []) + vals
                else:
                    out[flag] = nxt
                    i += 1
            else:
                out[flag] = None
        i += 1
    return out


def tool_list(values: list[str] | str | None) -> list[str]:
    """Comma-separated rule lists, flattened, trimmed, deduplicated in order."""
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    seen, out = set(), []
    for v in values:
        for t in v.split(","):
            t = t.strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    return out
