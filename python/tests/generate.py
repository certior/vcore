"""Seeded random policies, request sequences and malformed documents for differential testing.

Two families of policy:

* ``policy``: arbitrary patterns over an adversarial segment alphabet (``secrets`` next to
  ``secrets-public``, and ``_``, which collides with the checker's preferred fresh
  segment), with tags and combination rules of size 1 to 3.
* ``flow_policy``: a small tool/resource vocabulary where most of the behaviour is in
  labels and combinations, so that escalations often need several requests.
"""

from __future__ import annotations

import copy
import json
import random

SEGMENTS = ["Read", "WebFetch", "src", "secrets", "secrets-public", ".env", "a", "_"]
TAGS = ["private", "untrusted", "secret", "prod"]
TOOLS = ["Read", "WebFetch", "Edit", "Send"]
RESOURCES = ["src", "secrets", "customers", "docs"]


def path(rng: random.Random, lo: int, hi: int) -> list[str]:
    return [rng.choice(SEGMENTS) for _ in range(rng.randint(lo, hi))]


def pattern(rng: random.Random) -> str:
    kind = rng.choice(["exact", "prefixed", "suffixed"])
    if kind == "exact":
        return "/".join(path(rng, 1, 3))
    if kind == "prefixed":
        p = path(rng, 0, 2)
        return "/".join(p + ["**"]) if p else "**"
    return "/".join(["**"] + path(rng, 1, 2))


def combination(rng: random.Random) -> list[str]:
    return rng.sample(TAGS, rng.choice([1, 1, 2, 2, 3]))


def policy(rng: random.Random, size: int = 3) -> dict:
    permit = [pattern(rng) for _ in range(rng.randint(1, size + 1))]
    if rng.random() < 0.3:
        permit.append("**")
    return {
        "permit": permit,
        "forbid": [pattern(rng) for _ in range(rng.randint(0, size - 1))],
        "label": {pattern(rng): rng.sample(TAGS, rng.randint(1, 2)) for _ in range(rng.randint(0, size))},
        "block": [{"on": pattern(rng), "after": combination(rng)} for _ in range(rng.randint(0, size))],
    }


def flow_policy(rng: random.Random) -> dict:
    def pat() -> str:
        t, r = rng.choice(TOOLS), rng.choice(RESOURCES)
        return rng.choice([f"{t}/**", f"{t}/{r}/**", f"**/{r}", f"{t}/{r}"])

    return {
        "permit": ["**"] if rng.random() < 0.6 else [pat() for _ in range(3)],
        "forbid": [pat() for _ in range(rng.randint(0, 1))],
        "label": {pat(): rng.choice(TAGS[:3]) for _ in range(rng.randint(1, 4))},
        "block": [{"on": pat(), "after": rng.sample(TAGS[:3], rng.choice([1, 2, 2, 3]))}
                  for _ in range(rng.randint(1, 3))],
    }


def authority(rng: random.Random) -> dict | list:
    make = (lambda: policy(rng)) if rng.random() < 0.5 else (lambda: flow_policy(rng))
    return make() if rng.random() < 0.7 else [make(), make()]


def mutate(rng: random.Random, auth: dict | list) -> dict | list:
    """A nearby authority: a few statements added, dropped or changed."""
    new = copy.deepcopy(auth)
    target = rng.choice(new) if isinstance(new, list) else new
    for _ in range(rng.randint(1, 2)):
        field = rng.choices(["permit", "forbid", "label", "block"], [2, 2, 3, 3])[0]
        items = target.setdefault(field, {} if field == "label" else [])
        if field == "label":
            if items and rng.random() < 0.5:
                del items[rng.choice(sorted(items))]
            else:
                items[pattern(rng)] = rng.sample(TAGS, rng.randint(1, 2))
        elif field == "block":
            if items and rng.random() < 0.5:
                items.pop(rng.randrange(len(items)))
            else:
                items.append({"on": pattern(rng), "after": combination(rng)})
        elif items and rng.random() < 0.5:
            items.pop(rng.randrange(len(items)))
        else:
            items.append(pattern(rng))
    return new


def patterns_of(auth: dict | list) -> list[str]:
    policies = auth if isinstance(auth, list) else [auth]
    return [pat for p in policies for pat in p.get("permit", []) + p.get("forbid", [])
            + list(p.get("label", {})) + [r["on"] for r in p.get("block", [])]]


def requests(rng: random.Random, auth: dict | list, n: int) -> list[str]:
    """Requests near the literals of the authority, random ones, and non-canonical ones."""
    lits = patterns_of(auth)
    out = []
    for _ in range(n):
        k = rng.random()
        if lits and k < 0.65:
            segs = [s for s in rng.choice(lits).split("/") if s != "**"]
            extra = path(rng, 0, 2)
            segs = segs + extra if rng.random() < 0.5 else extra + segs
            out.append("/".join(segs) if segs else rng.choice(SEGMENTS))
        elif k < 0.9:
            out.append("/".join(path(rng, 1, 4)))
        else:
            segs = path(rng, 1, 3)
            segs.insert(rng.randrange(len(segs) + 1), rng.choice(["", ".", ".."]))
            out.append("/".join(segs))
    return out


def malformed(rng: random.Random) -> object:
    """A policy document, usually broken in one place."""
    doc = authority(rng)
    if rng.random() < 0.15:
        return doc
    target = rng.choice(doc) if isinstance(doc, list) else doc
    breakages = [
        lambda: target.__setitem__("permit", "Read/**"),
        lambda: target.__setitem__("permits", ["**"]),
        lambda: target.__setitem__("forbid", [1, 2]),
        lambda: target["permit"].append(rng.choice(["a/**/b", "", "a//b", "./a", "a/..", "**/**", "/**", "**/"])),
        lambda: target.__setitem__("label", ["Read/**", "private"]),
        lambda: target.__setitem__("label", {"Read/**": rng.choice(["", [], [""], 3, None, ["ok", 1]])}),
        lambda: target.__setitem__("block", {"on": "a", "after": ["x"]}),
        lambda: target.__setitem__("block", [rng.choice([
            {"on": "a"}, {"after": ["x"]}, {"on": "a", "after": []}, {"on": "a", "after": "x"},
            {"on": 3, "after": ["x"]}, {"on": "a", "after": ["x"], "when": 1}, "a"])]),
        lambda: None,
    ]
    rng.choice(breakages)()
    if rng.random() < 0.05:
        return rng.choice([[], 42, "policy", None, [doc, 3]])
    return json.loads(json.dumps(doc))
