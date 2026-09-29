#!/usr/bin/env bun
/**
 * Differential oracle for anthropics/claude-code-action (pinned, see fetch.sh).
 *
 *   bun oracle/run.ts SCENARIOS.jsonl [--jobs N] [--timeout SECONDS] [--keep] > OBSERVATIONS.jsonl
 *
 * One Observation per Scenario, in input order. Scenarios run in parallel, each in its own
 * temporary directories with its own mock API server. --keep leaves those directories
 * (path in extra.workdir) with the action's log for inspection.
 */
import { existsSync, readFileSync } from "fs";
import { cpus } from "os";
import { join } from "path";
import { runScenario, ACTION_DIR } from "./src/scenario";
import type { Observation, Scenario } from "./src/types";

const args = Bun.argv.slice(2);
const flag = (name: string) => { const i = args.indexOf(name); return i >= 0 ? args.splice(i, 2)[1] : undefined; };
const keep = args.includes("--keep");
if (keep) args.splice(args.indexOf("--keep"), 1);
const jobs = Number(flag("--jobs") ?? Math.max(1, Math.min(6, cpus().length - 1)));
const timeoutMs = Number(flag("--timeout") ?? 90) * 1000;
const file = args[0];
if (!file) {
  console.error("usage: bun oracle/run.ts SCENARIOS.jsonl [--jobs N] [--timeout SECONDS] [--keep]");
  process.exit(2);
}
if (!existsSync(join(ACTION_DIR, "node_modules"))) {
  console.error(`${ACTION_DIR} is not set up; run oracle/fetch.sh`);
  process.exit(2);
}

const scenarios: Scenario[] = readFileSync(file, "utf8").split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l));
const results: (Observation | undefined)[] = new Array(scenarios.length);
let next = 0;
let printed = 0;

function flush() {
  while (printed < results.length && results[printed]) process.stdout.write(`${JSON.stringify(results[printed++])}\n`);
}

async function worker() {
  for (;;) {
    const i = next++;
    if (i >= scenarios.length) return;
    const s = scenarios[i]!;
    try {
      results[i] = await runScenario(s, { bun: process.execPath, timeoutMs, keep });
    } catch (e) {
      results[i] = {
        id: s.id, action_commit: "bf38e86e58df9ebf3420326d019f955bb3be64dd", runs: false, stop_reason: `error:oracle: ${(e as Error).message}`,
        mode: null, allowed_tools: [], disallowed_tools: [], permission_mode: null, mcp_servers: [],
        credentials: { token_in_git_config: false, credential_helper: false, claude_env: [] }, env_scrub: false, prompt_canaries: [], extra: {},
      };
    }
    console.error(`[oracle] ${s.id}: ${results[i]!.runs ? `runs (${results[i]!.mode})` : results[i]!.stop_reason}`);
    flush();
  }
}

await Promise.all(Array.from({ length: Math.min(jobs, scenarios.length) }, worker));
flush();
