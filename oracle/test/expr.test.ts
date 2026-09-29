import { describe, expect, test } from "bun:test";
import { condition, evaluate, interpolate, UnsupportedExpression } from "../src/expr";

const ctx = {
  github: {
    event_name: "issue_comment",
    event: { comment: { body: "Hi @Claude please" }, issue: { labels: [{ name: "bug" }, { name: "claude" }], number: 7 } },
    repository: "acme/widget",
  },
  env: { MODE: "" },
  secrets: { KEY: "s3cret" },
  inputs: {},
  vars: {},
};

describe("expressions", () => {
  test("contains is case-insensitive", () => {
    expect(evaluate("contains(github.event.comment.body, '@claude')", ctx)).toBe(true);
  });
  test("object filter", () => {
    expect(evaluate("contains(github.event.issue.labels.*.name, 'CLAUDE')", ctx)).toBe(true);
    expect(evaluate("github.event.issue.labels[1].name", ctx)).toBe("claude");
  });
  test("loose equality and operand-valued && ||", () => {
    expect(evaluate("github.event.issue.number == '7'", ctx)).toBe(true);
    expect(evaluate("env.MODE || 'tag'", ctx)).toBe("tag");
    expect(evaluate("github.event_name == 'issues' && 'x'", ctx)).toBe(false);
    expect(evaluate("(env.MODE != '' && '1') || ''", ctx)).toBe("");
  });
  test("property names ignore case; missing properties are null", () => {
    expect(evaluate("GITHUB.Repository", ctx)).toBe("acme/widget");
    expect(evaluate("github.event.pull_request.head.sha", ctx)).toBe(null);
  });
  test("format, toJSON, fromJSON", () => {
    expect(evaluate("format('{0}/{1}{{x}}', 'a', 2)", ctx)).toBe("a/2{x}");
    expect(evaluate("fromJSON('[1,2]')[1]", ctx)).toBe(2);
    expect(evaluate("toJSON(secrets)", ctx)).toBe('{\n  "KEY": "s3cret"\n}');
  });
  test("interpolation", () => {
    expect(interpolate("PR ${{ github.event.issue.number }} in ${{ github.repository }}", ctx)).toBe("PR 7 in acme/widget");
    expect(interpolate(true, ctx)).toBe("true");
  });
  test("conditions without ${{ }} and multi-line", () => {
    expect(condition("github.event_name == 'issue_comment'", ctx)).toBe(true);
    expect(condition("${{ github.event_name == 'issues' }}", ctx)).toBe(false);
    expect(condition("(github.event_name == 'issues') ||\n(github.event_name == 'issue_comment' && contains(github.event.comment.body, '@claude'))\n", ctx)).toBe(true);
    expect(condition(undefined, ctx)).toBe(true);
  });
  test("unsupported forms are refused, not guessed", () => {
    expect(() => evaluate("steps.x.outputs.y", ctx)).toThrow(UnsupportedExpression);
    expect(() => evaluate("hashFiles('**/x')", ctx)).toThrow(UnsupportedExpression);
    expect(() => interpolate("${{ github.event }}", ctx)).toThrow(UnsupportedExpression);
  });
});
