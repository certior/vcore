"""GitHub Actions workflow files: the parts an agent envelope depends on.

This module reads a workflow document and nothing else: triggers, token permissions, jobs,
steps, ``if:`` conditions and the ``${{ }}`` expressions inside step inputs. It does not
model any particular action; see :mod:`vcore.actions.claude_code_action` for that.

Two details of GitHub's YAML dialect matter here:

* ``on`` is a plain string key. YAML 1.1 loaders (PyYAML's default) read it as the boolean
  ``True``, and ``yes``/``no``/``off`` likewise; :func:`load` resolves only ``true`` and
  ``false`` as booleans.
* Every input value reaches an action as a string, so scalar ``with:`` values are
  normalized to strings the way the runner renders them (``true`` becomes ``"true"``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml


class WorkflowError(ValueError):
    """A document that is not a workflow this module can read."""


class _Loader(yaml.SafeLoader):
    """SafeLoader with YAML 1.2 booleans: only true/false (any case)."""


_Loader.yaml_implicit_resolvers = {
    ch: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:bool"]
    for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))


def _string(value: Any) -> str:
    """How the runner renders a scalar input value."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        raise WorkflowError(f"expected a scalar, got {type(value).__name__}")
    return str(value)


# --- Expressions ---------------------------------------------------------------------------

_EXPR = re.compile(r"\$\{\{(.*?)\}\}", re.S)
_REF = re.compile(
    r"(?<![\w.'\"])(github|secrets|inputs|vars|env|steps|needs|matrix|job|jobs|runner|strategy)"
    r"((?:\.[A-Za-z_][\w-]*|\.\*|\[\s*'[^']*'\s*\]|\[\s*\"[^\"]*\"\s*\])*)")


def expressions(text: str) -> list[str]:
    """The bodies of the ``${{ ... }}`` spans in ``text``, stripped."""
    return [m.group(1).strip() for m in _EXPR.finditer(text or "")]


def references(expression: str) -> list[str]:
    """Context references in an expression, normalized to dotted form.

    ``github.event['issue'].body`` becomes ``github.event.issue.body``. String literals are
    skipped, so ``contains(github.event.comment.body, 'secrets.X')`` yields only the first.
    """
    dotted = re.sub(r"\[\s*['\"]([\w-]+)['\"]\s*\]", r".\1", expression)
    stripped = re.sub(r"'(?:[^']|'')*'", "''", dotted)
    return [m.group(1) + m.group(2) for m in _REF.finditer(stripped)]


def is_literal(text: str) -> bool:
    """True if ``text`` has no ``${{ }}`` expression."""
    return not _EXPR.search(text or "")


def has_literal_text(text: str) -> bool:
    """True if something other than whitespace remains once expressions are removed.

    A value with literal text can never evaluate to the empty string.
    """
    return bool(_EXPR.sub("", text or "").strip())


# --- Documents -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Uses:
    """A ``uses:`` reference: ``owner/repo[/path]@ref``, a local ``./path`` or ``docker://``."""
    raw: str
    owner: str = ""
    repo: str = ""
    path: str = ""
    ref: str = ""

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}".lower() if self.owner else ""

    @property
    def ref_kind(self) -> str:
        """``sha`` (a full commit id), ``major`` (``v1``), ``exact`` (``v1.0.3``), ``branch``
        (anything else, e.g. ``main`` or ``beta``), or ``none``."""
        if not self.ref:
            return "none"
        if re.fullmatch(r"[0-9a-f]{40}", self.ref):
            return "sha"
        if re.fullmatch(r"v?\d+", self.ref):
            return "major"
        if re.fullmatch(r"v?\d+(\.\d+){1,2}([-+.].*)?", self.ref):
            return "exact"
        return "branch"

    @staticmethod
    def parse(raw: str) -> Uses:
        raw = raw.strip()
        if raw.startswith(("./", "docker://")) or "@" not in raw:
            return Uses(raw)
        target, ref = raw.rsplit("@", 1)
        parts = target.split("/")
        if len(parts) < 2 or not parts[0] or not parts[1]:
            return Uses(raw)
        return Uses(raw, parts[0], parts[1], "/".join(parts[2:]), ref)


@dataclass(frozen=True)
class Trigger:
    event: str
    types: tuple[str, ...] | None  # None: every activity type GitHub sends for the event
    config: dict = field(default_factory=dict, compare=False, hash=False)


@dataclass(frozen=True)
class Permissions:
    """``permissions:``. ``None`` scopes means the key is absent: the repository or
    organization default applies, which a workflow file cannot tell."""
    scopes: dict[str, str] | None
    shorthand: str | None = None  # "read-all", "write-all", or None

    @staticmethod
    def parse(value: Any) -> Permissions:
        if value is None:
            return Permissions(None)
        if isinstance(value, str):
            if value not in ("read-all", "write-all"):
                raise WorkflowError(f"permissions: unknown shorthand {value!r}")
            return Permissions({}, value)
        if isinstance(value, dict):
            return Permissions({str(k): _string(v) for k, v in value.items()})
        raise WorkflowError("permissions: expected a mapping or read-all/write-all")

    @property
    def specified(self) -> bool:
        return self.scopes is not None

    def level(self, scope: str) -> str | None:
        """``write``, ``read`` or ``none`` for a scope; ``None`` when unspecified."""
        if self.scopes is None:
            return None
        if self.shorthand == "write-all":
            return "write"
        if self.shorthand == "read-all":
            return "read"
        return self.scopes.get(scope, "none")


@dataclass(frozen=True)
class Step:
    index: int
    id: str | None
    name: str | None
    uses: Uses | None
    run: str | None
    with_: dict[str, str]
    env: dict[str, str]
    if_: str | None


@dataclass(frozen=True)
class Job:
    id: str
    permissions: Permissions  # effective: the job's own, else the workflow's
    if_: str | None
    env: dict[str, str]
    steps: tuple[Step, ...]
    uses: Uses | None  # reusable workflow call
    environment: str | None


@dataclass(frozen=True)
class Workflow:
    name: str | None
    triggers: tuple[Trigger, ...]
    permissions: Permissions
    env: dict[str, str]
    jobs: tuple[Job, ...]

    def trigger(self, event: str) -> Trigger | None:
        return next((t for t in self.triggers if t.event == event), None)

    def steps_using(self, slug: str) -> list[tuple[Job, Step]]:
        slug = slug.lower()
        return [(j, s) for j in self.jobs for s in j.steps if s.uses and s.uses.slug == slug]


def _mapping(where: str, value: Any) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise WorkflowError(f"{where}: expected a mapping")
    return value


def _scalars(where: str, value: Any) -> dict[str, str]:
    out = {}
    for k, v in _mapping(where, value).items():
        try:
            out[str(k)] = _string(v)
        except WorkflowError as e:
            raise WorkflowError(f"{where}.{k}: {e}") from None
    return out


def _condition(value: Any) -> str | None:
    if value is None:
        return None
    return _string(value)


def _triggers(value: Any) -> tuple[Trigger, ...]:
    if isinstance(value, str):
        return (Trigger(value, None),)
    if isinstance(value, list):
        return tuple(Trigger(str(e), None) for e in value)
    if isinstance(value, dict):
        out = []
        for event, config in value.items():
            config = config if isinstance(config, dict) else {}
            types = config.get("types")
            if isinstance(types, str):
                types = [types]
            out.append(Trigger(str(event), tuple(str(t) for t in types) if types else None, config))
        return tuple(out)
    raise WorkflowError("on: expected an event name, a list or a mapping")


def load(text: str) -> Workflow:
    """Parse a workflow file. Raises :class:`WorkflowError` on anything malformed."""
    try:
        doc = yaml.load(text, Loader=_Loader)
    except yaml.YAMLError as e:
        raise WorkflowError(f"invalid YAML: {e}") from None
    if not isinstance(doc, dict):
        raise WorkflowError("a workflow is a mapping")
    if "on" not in doc:
        raise WorkflowError("missing 'on'")
    workflow_permissions = Permissions.parse(doc.get("permissions"))
    jobs = []
    for job_id, job in _mapping("jobs", doc.get("jobs")).items():
        job = _mapping(f"jobs.{job_id}", job)
        steps = []
        raw_steps = job.get("steps") or []
        if not isinstance(raw_steps, list):
            raise WorkflowError(f"jobs.{job_id}.steps: expected a list")
        for i, step in enumerate(raw_steps):
            step = _mapping(f"jobs.{job_id}.steps[{i}]", step)
            uses = step.get("uses")
            steps.append(Step(
                index=i,
                id=_condition(step.get("id")),
                name=_condition(step.get("name")),
                uses=Uses.parse(str(uses)) if uses else None,
                run=_condition(step.get("run")),
                with_=_scalars(f"jobs.{job_id}.steps[{i}].with", step.get("with")),
                env=_scalars(f"jobs.{job_id}.steps[{i}].env", step.get("env"))
                if isinstance(step.get("env"), (dict, type(None))) else {},
                if_=_condition(step.get("if")),
            ))
        environment = job.get("environment")
        if isinstance(environment, dict):
            environment = environment.get("name")
        jobs.append(Job(
            id=str(job_id),
            permissions=Permissions.parse(job["permissions"]) if "permissions" in job
            else workflow_permissions,
            if_=_condition(job.get("if")),
            env=_scalars(f"jobs.{job_id}.env", job.get("env"))
            if isinstance(job.get("env"), (dict, type(None))) else {},
            steps=tuple(steps),
            uses=Uses.parse(str(job["uses"])) if job.get("uses") else None,
            environment=_condition(environment),
        ))
    return Workflow(
        name=_condition(doc.get("name")),
        triggers=_triggers(doc["on"]),
        permissions=workflow_permissions,
        env=_scalars("env", doc.get("env")) if isinstance(doc.get("env"), (dict, type(None))) else {},
        jobs=tuple(jobs),
    )
