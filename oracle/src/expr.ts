/**
 * A subset of GitHub Actions expressions, evaluated exactly or not at all.
 *
 * Supported: literals (null, true, false, numbers, 'strings'), property access (a.b,
 * a['b'], a.*), ! == != < <= > >= && || and parentheses, and the functions contains,
 * startsWith, endsWith, format, join, toJSON, fromJSON, success, always, failure,
 * cancelled. Contexts are whatever the caller passes (github, env, vars, secrets, inputs,
 * runner). Anything else (hashFiles, the steps/needs/matrix/job/strategy contexts, an
 * object interpolated into a string) throws UnsupportedExpression: the oracle reports
 * that instead of guessing.
 *
 * Semantics follow https://docs.github.com/actions/reference/evaluate-expressions-in-workflows-and-actions:
 * loose equality coerces mismatched types to numbers, string comparison ignores case,
 * property names ignore case, && and || return an operand.
 */

export class UnsupportedExpression extends Error {}

type Value = null | boolean | number | string | Value[] | { [k: string]: Value } | Filtered;
class Filtered {
  constructor(readonly items: Value[]) {}
}

type Tok = { t: "num" | "str" | "id" | "op"; v: string };

function tokenize(src: string): Tok[] {
  const out: Tok[] = [];
  let i = 0;
  while (i < src.length) {
    const c = src[i]!;
    if (/\s/.test(c)) { i++; continue; }
    if (c === "'") {
      let s = "";
      i++;
      for (;;) {
        if (i >= src.length) throw new UnsupportedExpression(`unterminated string in ${src}`);
        if (src[i] === "'") {
          if (src[i + 1] === "'") { s += "'"; i += 2; continue; }
          i++;
          break;
        }
        s += src[i++];
      }
      out.push({ t: "str", v: s });
      continue;
    }
    const num = /^(0x[0-9a-fA-F]+|-?\d+(\.\d+)?([eE][+-]?\d+)?)/.exec(src.slice(i));
    if (num && (c !== "-" || /\d/.test(src[i + 1] ?? ""))) {
      out.push({ t: "num", v: num[0] });
      i += num[0].length;
      continue;
    }
    const id = /^[A-Za-z_][A-Za-z0-9_-]*/.exec(src.slice(i));
    if (id) { out.push({ t: "id", v: id[0] }); i += id[0].length; continue; }
    const op = /^(==|!=|<=|>=|&&|\|\||[()[\].!<>,*])/.exec(src.slice(i));
    if (op) { out.push({ t: "op", v: op[0] }); i += op[0].length; continue; }
    throw new UnsupportedExpression(`unexpected character ${JSON.stringify(c)} in ${src}`);
  }
  return out;
}

export type Contexts = Record<string, Value | undefined>;

const UNSUPPORTED_CONTEXTS = new Set(["steps", "needs", "matrix", "job", "jobs", "strategy"]);

class Parser {
  private i = 0;
  constructor(private toks: Tok[], private ctx: Contexts, private src: string) {}

  parse(): Value {
    const v = this.or();
    if (this.i !== this.toks.length) throw new UnsupportedExpression(`trailing tokens in ${this.src}`);
    return v;
  }
  private peek(v?: string): Tok | undefined {
    const t = this.toks[this.i];
    return v === undefined || (t && t.t === "op" && t.v === v) ? t : undefined;
  }
  private eat(v: string): boolean {
    if (this.peek(v)) { this.i++; return true; }
    return false;
  }
  private expect(v: string) {
    if (!this.eat(v)) throw new UnsupportedExpression(`expected ${v} in ${this.src}`);
  }
  private or(): Value {
    let l = this.and();
    while (this.eat("||")) { const r = this.and(); l = truthy(l) ? l : r; }
    return l;
  }
  private and(): Value {
    let l = this.eq();
    while (this.eat("&&")) { const r = this.eq(); l = truthy(l) ? r : l; }
    return l;
  }
  private eq(): Value {
    let l = this.cmp();
    for (;;) {
      if (this.eat("==")) l = looseEq(l, this.cmp());
      else if (this.eat("!=")) l = !looseEq(l, this.cmp());
      else return l;
    }
  }
  private cmp(): Value {
    let l = this.unary();
    for (;;) {
      const t = this.peek();
      if (t && t.t === "op" && ["<", "<=", ">", ">="].includes(t.v)) {
        this.i++;
        l = compare(l, this.unary(), t.v);
      } else return l;
    }
  }
  private unary(): Value {
    if (this.eat("!")) return !truthy(this.unary());
    return this.postfix();
  }
  private postfix(): Value {
    let v = this.primary();
    for (;;) {
      if (this.eat(".")) {
        if (this.eat("*")) { v = star(v); continue; }
        const t = this.toks[this.i++];
        if (!t || t.t !== "id") throw new UnsupportedExpression(`expected property name in ${this.src}`);
        v = prop(v, t.v);
      } else if (this.eat("[")) {
        if (this.eat("*")) { this.expect("]"); v = star(v); continue; }
        const k = this.or();
        this.expect("]");
        v = index(v, k);
      } else return v;
    }
  }
  private primary(): Value {
    const t = this.toks[this.i++];
    if (!t) throw new UnsupportedExpression(`unexpected end of ${this.src}`);
    if (t.t === "num") return Number(t.v);
    if (t.t === "str") return t.v;
    if (t.t === "op" && t.v === "(") { const v = this.or(); this.expect(")"); return v; }
    if (t.t === "id") {
      if (this.eat("(")) {
        const args: Value[] = [];
        if (!this.eat(")")) {
          do args.push(this.or()); while (this.eat(","));
          this.expect(")");
        }
        return call(t.v, args, this.src);
      }
      const name = t.v.toLowerCase();
      if (name === "true") return true;
      if (name === "false") return false;
      if (name === "null") return null;
      if (UNSUPPORTED_CONTEXTS.has(name)) throw new UnsupportedExpression(`context ${t.v} in ${this.src}`);
      const key = Object.keys(this.ctx).find((k) => k.toLowerCase() === name);
      if (key === undefined) throw new UnsupportedExpression(`unknown context ${t.v} in ${this.src}`);
      return this.ctx[key] ?? null;
    }
    throw new UnsupportedExpression(`unexpected ${t.v} in ${this.src}`);
  }
}

function unwrap(v: Value): Exclude<Value, Filtered> {
  return v instanceof Filtered ? v.items : v;
}

function prop(v: Value, name: string): Value {
  if (v instanceof Filtered) {
    return new Filtered(v.items.map((x) => prop(x, name)).filter((x) => x !== null));
  }
  if (v && typeof v === "object" && !Array.isArray(v)) {
    const key = Object.keys(v).find((k) => k.toLowerCase() === name.toLowerCase());
    return key === undefined ? null : (v as Record<string, Value>)[key] ?? null;
  }
  return null;
}

function index(v: Value, k: Value): Value {
  const base = unwrap(v);
  if (Array.isArray(base)) {
    const n = toNumber(k);
    return Number.isInteger(n) && n >= 0 && n < base.length ? base[n]! : null;
  }
  return typeof k === "string" ? prop(base, k) : null;
}

function star(v: Value): Value {
  const base = unwrap(v);
  if (Array.isArray(base)) return new Filtered(base);
  if (base && typeof base === "object") return new Filtered(Object.values(base));
  return new Filtered([]);
}

export function truthy(v: Value): boolean {
  if (v instanceof Filtered) return true;
  if (v === null || v === false || v === "") return false;
  if (typeof v === "number") return !(v === 0 || Number.isNaN(v));
  return true;
}

function toNumber(v: Value): number {
  if (v === null) return 0;
  if (typeof v === "boolean") return v ? 1 : 0;
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    if (v.trim() === "") return 0;
    const n = Number(v.trim());
    return Number.isNaN(n) ? NaN : n;
  }
  return NaN;
}

function looseEq(a: Value, b: Value): boolean {
  a = unwrap(a); b = unwrap(b);
  const ta = kind(a), tb = kind(b);
  if (ta === tb) {
    if (ta === "string") return (a as string).toLowerCase() === (b as string).toLowerCase();
    if (ta === "number") return a === b;
    if (ta === "object") return a === b;
    return a === b;
  }
  const na = toNumber(a), nb = toNumber(b);
  return !Number.isNaN(na) && !Number.isNaN(nb) && na === nb;
}

function compare(a: Value, b: Value, op: string): boolean {
  a = unwrap(a); b = unwrap(b);
  let x: number | string, y: number | string;
  if (kind(a) === "string" && kind(b) === "string") {
    x = (a as string).toLowerCase(); y = (b as string).toLowerCase();
  } else {
    x = toNumber(a); y = toNumber(b);
    if (Number.isNaN(x) || Number.isNaN(y)) return false;
  }
  switch (op) {
    case "<": return x < y;
    case "<=": return x <= y;
    case ">": return x > y;
    default: return x >= y;
  }
}

function kind(v: Value): "null" | "boolean" | "number" | "string" | "object" {
  if (v === null) return "null";
  if (typeof v === "boolean") return "boolean";
  if (typeof v === "number") return "number";
  if (typeof v === "string") return "string";
  return "object";
}

/** String conversion used by functions and interpolation. */
export function str(v: Value, where = "expression"): string {
  v = unwrap(v);
  if (v === null) return "";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") return Number.isNaN(v) ? "NaN" : String(v);
  if (typeof v === "string") return v;
  throw new UnsupportedExpression(`object or array converted to a string in ${where}`);
}

function call(name: string, args: Value[], src: string): Value {
  const n = name.toLowerCase();
  const arity = (lo: number, hi = lo) => {
    if (args.length < lo || args.length > hi) throw new UnsupportedExpression(`${name} arity in ${src}`);
  };
  switch (n) {
    case "contains": {
      arity(2);
      const hay = unwrap(args[0]!);
      if (Array.isArray(hay)) return hay.some((x) => looseEq(x, args[1]!));
      return str(hay, src).toLowerCase().includes(str(args[1]!, src).toLowerCase());
    }
    case "startswith": arity(2); return str(args[0]!, src).toLowerCase().startsWith(str(args[1]!, src).toLowerCase());
    case "endswith": arity(2); return str(args[0]!, src).toLowerCase().endsWith(str(args[1]!, src).toLowerCase());
    case "format": {
      if (args.length < 1) throw new UnsupportedExpression(`format arity in ${src}`);
      const fmt = str(args[0]!, src);
      return fmt.replace(/\{\{|\}\}|\{(\d+)\}/g, (m, d) => {
        if (m === "{{") return "{";
        if (m === "}}") return "}";
        const k = Number(d) + 1;
        if (k >= args.length) throw new UnsupportedExpression(`format index out of range in ${src}`);
        return str(args[k]!, src);
      });
    }
    case "join": {
      arity(1, 2);
      const sep = args.length > 1 ? str(args[1]!, src) : ",";
      const a = unwrap(args[0]!);
      return Array.isArray(a) ? a.map((x) => str(x, src)).join(sep) : str(a, src);
    }
    case "tojson": arity(1); return JSON.stringify(unwrap(args[0]!), null, 2);
    case "fromjson": arity(1); return JSON.parse(str(args[0]!, src)) as Value;
    case "success": case "always": arity(0); return true;
    case "failure": case "cancelled": arity(0); return false;
    default: throw new UnsupportedExpression(`function ${name} in ${src}`);
  }
}

/** Evaluate one expression body (the text inside ${{ }}). */
export function evaluate(src: string, ctx: Contexts): Value {
  return unwrap(new Parser(tokenize(src), ctx, src).parse());
}

const TEMPLATE = /\$\{\{([\s\S]*?)\}\}/g;

/** Evaluate a string that may contain ${{ }} templates; the result is a string, as for `with:` and `env:` values. */
export function interpolate(text: unknown, ctx: Contexts): string {
  if (text === null || text === undefined) return "";
  if (typeof text !== "string") return str(text as Value);
  return text.replace(TEMPLATE, (_m, body: string) => str(evaluate(body, ctx), body));
}

/**
 * Evaluate a job or step `if:`. A condition may omit ${{ }}; without a status function
 * it means `success() && (condition)`.
 */
export function condition(text: unknown, ctx: Contexts): boolean {
  if (text === undefined || text === null) return true;
  if (typeof text === "boolean") return text;
  let s = String(text).trim();
  const m = /^\$\{\{([\s\S]*)\}\}$/.exec(s);
  if (m && !m[1]!.includes("}}")) s = m[1]!;
  else if (s.includes("${{")) {
    // Mixed text and templates: GitHub evaluates the interpolated string's truthiness.
    return truthy(interpolate(s, ctx));
  }
  if (!/\b(success|failure|always|cancelled)\s*\(/i.test(s)) s = `success() && (${s})`;
  return truthy(evaluate(s, ctx));
}
