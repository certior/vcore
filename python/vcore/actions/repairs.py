"""Repairs: small workflow edits, checked by the verified checker.

For a configuration whose envelope violates a property, each candidate edit is applied to
the workflow file, the workflow is analyzed again, and the edit is kept if the same
configuration now satisfies the property. A kept edit is also classified by whether the
new envelope **refines** the old one (``vcore check``): a refining repair only removes
behaviors, so it cannot enable anything the workflow did not already allow; a
non-refining repair trades one capability for another and deserves a closer look.

Edits change the workflow file only; they are suggestions to a maintainer, stated in
workflow syntax.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Callable

import yaml

from ..lean import VCore
from . import catalog
from . import claude_code_action as cca
from .model import map_rule, parse_claude_args, split_args, tool_list
from .properties import Property, verdict, widening
from .workflow import _Loader, load

Edit = Callable[[dict, str, dict], bool]
"""Mutates (document, job id, step) in place; returns False when it does not apply."""


def _step_with(step: dict) -> dict:
    step.setdefault("with", {})
    return step["with"]


def _append_args(step: dict, text: str) -> None:
    w = _step_with(step)
    w["claude_args"] = (str(w.get("claude_args", "")).rstrip() + "\n" + text).strip()


def deny_git_credentials(doc: dict, job: str, step: dict) -> bool:
    _append_args(step, '--disallowedTools "Read(./.git/**)"')
    return True


def scrub_subprocess_env(doc: dict, job: str, step: dict) -> bool:
    env = doc["jobs"][job].setdefault("env", {})
    if str(env.get("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", "")) == "1":
        return False
    env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] = "1"
    return True


def _rewrite_allowed(step: dict, keep: Callable[[str], bool]) -> bool:
    w = _step_with(step)
    raw = str(w.get("claude_args", ""))
    if "${{" in raw:
        return False
    args = split_args(raw)
    allowed = tool_list(parse_claude_args(raw).get("allowedTools"))
    kept = [t for t in allowed if keep(t)]
    if kept == allowed:
        return False
    out, i = [], 0
    while i < len(args):
        if args[i] in ("--allowedTools", "--allowed-tools"):
            i += 1
            while i < len(args) and not args[i].startswith("--"):
                i += 1
            continue
        out.append(args[i])
        i += 1
    if kept:
        out += ["--allowedTools", ",".join(kept)]
    w["claude_args"] = shlex.join(out)
    return True


def _grants(rule: str, sinks: tuple[str, ...]) -> bool:
    from ..policy import Pat
    m = map_rule(rule)
    if not m.pattern:
        return False
    granted = Pat.parse(m.pattern)
    # A grant matters if some sink pattern and the grant can cover a common request.
    for s in sinks:
        sink = Pat.parse(s)
        probe_a = "/".join(sink.path + (("_",) if sink.kind == "prefixed" else ()))
        probe_b = "/".join(granted.path + (("_",) if granted.kind == "prefixed" else ()))
        from ..policy import parse_path
        if (probe_a and granted.covers(parse_path(probe_a))) or (probe_b and sink.covers(parse_path(probe_b))):
            return True
    return False


def drop_egress_tools(doc: dict, job: str, step: dict) -> bool:
    return _rewrite_allowed(step, lambda t: not _grants(t, catalog.EGRESS))


def drop_repo_write_tools(doc: dict, job: str, step: dict) -> bool:
    return _rewrite_allowed(step, lambda t: not _grants(t, catalog.REPO_WRITE))


def drop_nonwrite_bypass(doc: dict, job: str, step: dict) -> bool:
    w = _step_with(step)
    changed = False
    for key in ("allowed_non_write_users", "allowed_bots"):
        if str(w.get(key, "")).strip() == "*":
            del w[key]
            changed = True
    return changed


def comments_from_maintainers(doc: dict, job: str, step: dict) -> bool:
    w = _step_with(step)
    if w.get("include_comments_by_actor"):
        return False
    w["include_comments_by_actor"] = "MAINTAINER_LOGINS"
    return True


def restrict_to_members(doc: dict, job: str, step: dict) -> bool:
    j = doc["jobs"][job]
    guard = ("contains(fromJSON('[\"OWNER\",\"MEMBER\",\"COLLABORATOR\"]'), "
             "github.event.comment.author_association || github.event.review.author_association || "
             "github.event.issue.author_association)")
    cond = str(j.get("if", "")).strip()
    if "author_association" in cond:
        return False
    j["if"] = f"({cond.removeprefix('${{').removesuffix('}}').strip()}) && {guard}" if cond else guard
    return True


@dataclass(frozen=True)
class Repair:
    id: str
    text: str
    edit: Edit


REPAIRS = (
    Repair("deny-git-credentials", 'add --disallowedTools "Read(./.git/**)" to claude_args', deny_git_credentials),
    Repair("scrub-subprocess-env", "set CLAUDE_CODE_SUBPROCESS_ENV_SCRUB: 1 in the job env", scrub_subprocess_env),
    Repair("drop-egress-tools", "remove allowed tools that reach the network", drop_egress_tools),
    Repair("drop-repo-write-tools", "remove allowed tools that write to the repository", drop_repo_write_tools),
    Repair("drop-nonwrite-bypass", "remove allowed_non_write_users/allowed_bots '*'", drop_nonwrite_bypass),
    Repair("comments-from-maintainers", "set include_comments_by_actor to the maintainers' logins", comments_from_maintainers),
)


@dataclass
class Outcome:
    repair: str
    text: str
    satisfied: bool
    refines: bool


def _doc(text: str) -> dict:
    return yaml.load(text, Loader=_Loader)


def suggest(vc: VCore, text: str, job_id: str, step_index: int, event: str, activity: str | None,
            mode: str, prop: Property) -> list[Outcome]:
    """Single edits after which the configuration satisfies ``prop``."""
    def envelope_of(t: str):
        w = load(t)
        job = next(j for j in w.jobs if j.id == job_id)
        step = job.steps[step_index]
        for rc in cca.configurations(w, job, step):
            if (rc.event, rc.activity, rc.mode) == (event, activity, mode):
                return cca.envelope(rc, w, job, step).policy()
        return None

    original = envelope_of(text)
    if original is None or verdict(vc, original, prop).satisfied:
        return []
    out = []
    for r in REPAIRS:
        doc = _doc(text)
        step = doc["jobs"][job_id]["steps"][step_index]
        if not r.edit(doc, job_id, step):
            continue
        edited = yaml.safe_dump(doc, sort_keys=False, width=1000)
        policy = envelope_of(edited)
        if policy is None:
            continue  # the edit stops the agent for this event entirely
        if verdict(vc, policy, prop).satisfied:
            out.append(Outcome(r.id, r.text, True, widening(vc, original, policy) is None))
    return out
