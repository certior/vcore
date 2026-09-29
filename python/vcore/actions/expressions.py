"""Evaluating GitHub Actions expressions for a concrete run.

The subset used by workflow ``if:`` conditions and step inputs in practice: literals
(``null``, booleans, numbers, single-quoted strings), context access (``a.b``, ``a['b']``,
``a[0]``), ``!``, ``==``, ``!=``, ``<``, ``<=``, ``>``, ``>=``, ``&&``, ``||``, parentheses,
and the functions ``contains``, ``startsWith``, ``endsWith``, ``format``, ``join``,
``toJSON``, ``fromJSON``, ``success``, ``always``, ``failure``, ``cancelled``.

Semantics follow GitHub's documentation: ``&&`` and ``||`` return an operand; comparison
of different types converts both to numbers; string comparison ignores case; missing
properties are ``null``. Anything outside the subset raises :class:`Unsupported`, so a
caller never guesses.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any


class Unsupported(ValueError):
    """An expression outside the supported subset."""


_TOKEN = re.compile(r"""\s*(?:
    (?P<num>0x[0-9a-fA-F]+|-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)
  | (?P<str>'(?:[^']|'')*')
  | (?P<op>==|!=|<=|>=|&&|\|\||[()\[\].,!<>])
  | (?P<id>[A-Za-z_][\w-]*)
  | (?P<star>\*)
)""", re.X)


def _tokens(src: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    src = src.strip()
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m or m.end() == pos:
            raise Unsupported(f"cannot tokenize {src[pos:]!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
        while pos < len(src) and src[pos].isspace():
            pos += 1
    return out


def _num(v: Any) -> float:
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return 0.0
        try:
            return float(int(s, 16)) if s.lower().startswith("0x") else float(s)
        except ValueError:
            return math.nan
    return math.nan


def truthy(v: Any) -> bool:
    if v is None or v is False:
        return False
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return not (v == 0 or math.isnan(v))
    if isinstance(v, str):
        return v != ""
    return True


def _eq(a: Any, b: Any) -> bool:
    if type(a) is type(b) or (isinstance(a, (int, float)) and isinstance(b, (int, float))
                              and not isinstance(a, bool) and not isinstance(b, bool)):
        if isinstance(a, str):
            return a.lower() == b.lower()
        if isinstance(a, (dict, list)):
            return a is b
        return a == b
    if isinstance(a, (dict, list)) or isinstance(b, (dict, list)):
        return False
    x, y = _num(a), _num(b)
    return not (math.isnan(x) or math.isnan(y)) and x == y


def _str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (dict, list)):
        return "Object" if isinstance(v, dict) else "Array"
    return str(v)


class _Parser:
    def __init__(self, src: str, contexts: dict):
        self.toks = _tokens(src)
        self.i = 0
        self.ctx = contexts

    def peek(self, value: str | None = None, kind: str | None = None) -> bool:
        if self.i >= len(self.toks):
            return False
        k, v = self.toks[self.i]
        return (value is None or v == value) and (kind is None or k == kind)

    def take(self) -> tuple[str, str]:
        if self.i >= len(self.toks):
            raise Unsupported("unexpected end of expression")
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect(self, value: str) -> None:
        if self.take()[1] != value:
            raise Unsupported(f"expected {value!r}")

    def parse(self) -> Any:
        v = self.or_()
        if self.i != len(self.toks):
            raise Unsupported(f"trailing tokens: {self.toks[self.i:]}")
        return v

    def or_(self) -> Any:
        v = self.and_()
        while self.peek("||"):
            self.take()
            rhs = self.and_()
            v = v if truthy(v) else rhs
        return v

    def and_(self) -> Any:
        v = self.cmp()
        while self.peek("&&"):
            self.take()
            rhs = self.cmp()
            v = rhs if truthy(v) else v
        return v

    def cmp(self) -> Any:
        v = self.unary()
        while self.i < len(self.toks) and self.toks[self.i][1] in ("==", "!=", "<", "<=", ">", ">="):
            op = self.take()[1]
            rhs = self.unary()
            if op == "==":
                v = _eq(v, rhs)
            elif op == "!=":
                v = not _eq(v, rhs)
            else:
                if isinstance(v, str) and isinstance(rhs, str):
                    a, b = v.lower(), rhs.lower()
                else:
                    a, b = _num(v), _num(rhs)
                    if math.isnan(a) or math.isnan(b):
                        v = False
                        continue
                v = {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
        return v

    def unary(self) -> Any:
        if self.peek("!"):
            self.take()
            return not truthy(self.unary())
        return self.postfix()

    def postfix(self) -> Any:
        v = self.primary()
        while True:
            if self.peek("."):
                self.take()
                k, name = self.take()
                if k == "star":
                    raise Unsupported("object filters (.*) are not supported")
                if k not in ("id", "num"):
                    raise Unsupported("bad property name")
                v = self._index(v, name)
            elif self.peek("["):
                self.take()
                key = self.or_()
                self.expect("]")
                v = self._index(v, key)
            else:
                return v

    @staticmethod
    def _index(v: Any, key: Any) -> Any:
        if isinstance(v, dict):
            if isinstance(key, str):
                for k in v:
                    if k.lower() == key.lower():
                        return v[k]
            return None
        if isinstance(v, list) and isinstance(key, (int, float)) and not isinstance(key, bool):
            i = int(key)
            return v[i] if 0 <= i < len(v) else None
        return None

    def primary(self) -> Any:
        k, v = self.take()
        if k == "num":
            return float(int(v, 16)) if v.lower().startswith("0x") else float(v)
        if k == "str":
            return v[1:-1].replace("''", "'")
        if v == "(":
            inner = self.or_()
            self.expect(")")
            return inner
        if k == "id":
            if v in ("true", "false"):
                return v == "true"
            if v == "null":
                return None
            if self.peek("("):
                self.take()
                args = []
                if not self.peek(")"):
                    args.append(self.or_())
                    while self.peek(","):
                        self.take()
                        args.append(self.or_())
                self.expect(")")
                return self.call(v, args)
            if v not in self.ctx:
                raise Unsupported(f"unknown context {v!r}")
            return self.ctx[v]
        raise Unsupported(f"unexpected token {v!r}")

    @staticmethod
    def call(name: str, args: list) -> Any:
        n = name.lower()
        if n in ("success", "always"):
            return True
        if n in ("failure", "cancelled"):
            return False
        if n == "contains" and len(args) == 2:
            hay, needle = args
            if isinstance(hay, list):
                return any(_eq(x, needle) for x in hay)
            return _str(needle).lower() in _str(hay).lower()
        if n == "startswith" and len(args) == 2:
            return _str(args[0]).lower().startswith(_str(args[1]).lower())
        if n == "endswith" and len(args) == 2:
            return _str(args[0]).lower().endswith(_str(args[1]).lower())
        if n == "format" and args:
            fmt, rest = _str(args[0]), args[1:]

            def sub(m: "re.Match") -> str:
                i = int(m.group(1))
                if i >= len(rest):
                    raise Unsupported(f"format: no argument {{{i}}}")
                return _str(rest[i])
            return re.sub(r"\{(\d+)\}", sub, fmt.replace("{{", "\x00").replace("}}", "\x01")) \
                .replace("\x00", "{").replace("\x01", "}")
        if n == "join" and args:
            sep = _str(args[1]) if len(args) > 1 else ","
            return sep.join(_str(x) for x in args[0]) if isinstance(args[0], list) else _str(args[0])
        if n == "tojson" and len(args) == 1:
            return json.dumps(args[0], indent=2)
        if n == "fromjson" and len(args) == 1:
            try:
                return json.loads(_str(args[0]))
            except ValueError as e:
                raise Unsupported(f"fromJSON: {e}") from None
        raise Unsupported(f"function {name}/{len(args)} is not supported")


def evaluate(expression: str, contexts: dict) -> Any:
    """The value of one expression (the text between ``${{`` and ``}}``)."""
    return _Parser(expression, contexts).parse()


def condition(cond: str | None, contexts: dict) -> bool:
    """An ``if:`` value: with or without ``${{ }}``; absent means ``success()``."""
    if cond is None or str(cond).strip() == "":
        return True
    c = str(cond).strip()
    m = re.fullmatch(r"\$\{\{(.*)\}\}", c, re.S)
    return truthy(evaluate(m.group(1) if m else c, contexts))


def interpolate(text: str, contexts: dict) -> str:
    """A string with every ``${{ }}`` replaced by its value."""
    return re.sub(r"\$\{\{(.*?)\}\}", lambda m: _str(evaluate(m.group(1), contexts)), text, flags=re.S)
