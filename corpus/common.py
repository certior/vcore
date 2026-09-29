"""Shared helpers: reading workflow YAML the way GitHub does, and naming action references."""

from __future__ import annotations

import re
from typing import Any, Iterator

import yaml


class WorkflowLoader(yaml.SafeLoader):
    """PyYAML's safe loader with YAML 1.2 booleans.

    PyYAML follows YAML 1.1, where ``on``, ``off``, ``yes`` and ``no`` are booleans, so the
    key ``on:`` of every workflow would load as ``True``. GitHub's parser only treats
    ``true`` and ``false`` as booleans; this loader does the same.
    """


WorkflowLoader.yaml_implicit_resolvers = {
    ch: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:bool"]
    for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
WorkflowLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def load_workflow(text: str) -> Any:
    return yaml.load(text, Loader=WorkflowLoader)


ROOT_WORKFLOW = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$")
SHA_REF = re.compile(r"^[0-9a-fA-F]{40}$")
SEMVER_EXACT = re.compile(r"^v?\d+\.\d+\.\d+([-+].*)?$")
SEMVER_MINOR = re.compile(r"^v?\d+\.\d+$")
SEMVER_MAJOR = re.compile(r"^v?\d+$")


def split_uses(uses: str) -> tuple[str, str, str | None]:
    """``owner/repo[/subpath]@ref`` -> (``owner/repo`` lowercased, subpath, ref or None)."""
    target, _, ref = uses.strip().partition("@")
    parts = target.split("/")
    repo = "/".join(parts[:2]).lower()
    sub = "/".join(parts[2:])
    return repo, sub, (ref or None)


def ref_class(ref: str | None) -> str:
    if ref is None:
        return "none"
    if SHA_REF.match(ref):
        return "sha"
    if SEMVER_EXACT.match(ref):
        return "exact-version"
    if SEMVER_MINOR.match(ref):
        return "minor-version"
    if SEMVER_MAJOR.match(ref):
        return "major-version"
    if ref.lower() in ("main", "master"):
        return "default-branch"
    if ref.lower() == "beta":
        return "beta"
    return "other"


def jobs(doc: Any) -> Iterator[tuple[str, dict]]:
    if not isinstance(doc, dict) or not isinstance(doc.get("jobs"), dict):
        return
    for job_id, job in doc["jobs"].items():
        if isinstance(job, dict):
            yield str(job_id), job


def steps(job: dict) -> Iterator[tuple[int, dict]]:
    if isinstance(job.get("steps"), list):
        for i, step in enumerate(job["steps"]):
            if isinstance(step, dict):
                yield i, step


def agent_steps(doc: Any, action: str) -> Iterator[tuple[str, dict, int, dict]]:
    """Steps whose ``uses:`` names ``action`` itself (not a sub-path action inside its repo)."""
    action = action.lower()
    for job_id, job in jobs(doc):
        for i, step in steps(job):
            uses = step.get("uses")
            if isinstance(uses, str):
                repo, sub, _ = split_uses(uses)
                if repo == action and not sub:
                    yield job_id, job, i, step


def uses_in_repo(doc: Any, action: str) -> list[str]:
    """Every ``uses:`` (steps and reusable-workflow jobs) that points into ``action``'s repo."""
    action = action.lower()
    out = []
    for _, job in jobs(doc):
        if isinstance(job.get("uses"), str) and split_uses(job["uses"])[0] == action:
            out.append(job["uses"])
        for _, step in steps(job):
            if isinstance(step.get("uses"), str) and split_uses(step["uses"])[0] == action:
                out.append(step["uses"])
    return out
