"""Bridge to the Lean ``vcore`` executable (``vcore batch``: JSON lines in, JSON lines out)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path as FsPath
from typing import Any

from .runtime import Index

REPO = FsPath(__file__).resolve().parents[2]


def find_vcore() -> FsPath:
    candidate = FsPath(os.environ.get("VCORE", REPO / ".lake" / "build" / "bin" / "vcore"))
    if not candidate.exists():
        raise FileNotFoundError(f"{candidate} not found; run `lake build` (or set VCORE)")
    return candidate


class VCore:
    """A long-running ``vcore batch`` process."""

    def __init__(self, binary: FsPath | None = None):
        self._proc = subprocess.Popen([str(binary or find_vcore()), "batch"],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    def call(self, request: dict) -> dict:
        assert self._proc.stdin and self._proc.stdout
        self._proc.stdin.write(json.dumps(request) + "\n")
        self._proc.stdin.flush()
        return json.loads(self._proc.stdout.readline())

    def _ok(self, request: dict) -> dict:
        response = self.call(request)
        if "error" in response:
            raise ValueError(response["error"])
        return response

    def parse(self, authority: Any) -> list[list[tuple]] | str:
        """The statements Lean reads from a document, or its error message."""
        response = self.call({"op": "parse", "authority": authority})
        if "error" in response:
            return response["error"]
        return [[(e, p, tuple(a) if isinstance(a, list) else a) for e, p, a in policy]
                for policy in response["policies"]]

    def eval(self, authority: Any, trace: list[str]) -> list[tuple[bool, tuple[str, ...]]]:
        """Decide each request in turn (``Authority.allows`` and ``Authority.step``)."""
        steps = self._ok({"op": "eval", "authority": authority, "trace": trace})["steps"]
        return [(s["allow"], tuple(s["context"])) for s in steps]

    def check(self, old: Any, new: Any, depth: int | None = None) -> list[str] | None:
        """``escalation old new`` (or ``escalationWithin depth``): a counterexample or None."""
        request = {"op": "check", "old": old, "new": new}
        if depth is not None:
            request["depth"] = depth
        response = self._ok(request)
        return None if response["refines"] else response["witness"]

    def compile_json(self, policy: Any) -> dict:
        return self._ok({"op": "compile", "policy": policy})

    def compile(self, policy: Any) -> Index:
        return Index.from_json(self.compile_json(policy))

    def width(self, authority: Any) -> int:
        return self._ok({"op": "width", "authority": authority})["width"]

    def close(self) -> None:
        if self._proc.stdin:
            self._proc.stdin.close()
        self._proc.wait()

    def __enter__(self) -> VCore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
