/**
 * Reading a workflow file: does the event fire it, which step runs the action, what the
 * job's GITHUB_TOKEN may do, and which actions/checkout step precedes the action.
 */

export const ACTION_REPO = "anthropics/claude-code-action";

export type Step = Record<string, unknown> & { uses?: string; with?: Record<string, unknown>; env?: Record<string, unknown>; if?: unknown };
export type Job = Record<string, unknown> & { steps?: Step[]; env?: Record<string, unknown>; permissions?: unknown; if?: unknown };
export type Workflow = { name?: string; on?: unknown; env?: Record<string, unknown>; permissions?: unknown; jobs?: Record<string, Job> };

export function parseWorkflow(text: string): Workflow {
  const doc = Bun.YAML.parse(text) as Workflow;
  if (!doc || typeof doc !== "object" || !doc.jobs) throw new Error("workflow has no jobs");
  return doc;
}

export function isClaudeStep(step: Step): boolean {
  return typeof step.uses === "string" && step.uses.toLowerCase().startsWith(`${ACTION_REPO}@`);
}

export function locateStep(wf: Workflow, job?: string, step?: number): { jobId: string; job: Job; index: number; step: Step } {
  const jobs = Object.entries(wf.jobs ?? {});
  for (const [id, j] of jobs) {
    if (job !== undefined && id !== job) continue;
    const steps = j.steps ?? [];
    const index = step ?? steps.findIndex(isClaudeStep);
    if (index >= 0 && index < steps.length) return { jobId: id, job: j, index, step: steps[index]! };
  }
  throw new Error(`no claude-code-action step${job ? ` in job ${job}` : ""}`);
}

/** The workflow's `on:` as event -> activity types (null: all types). */
export function triggers(wf: Workflow): Map<string, string[] | null> {
  const on = wf.on;
  const out = new Map<string, string[] | null>();
  if (typeof on === "string") out.set(on, null);
  else if (Array.isArray(on)) for (const e of on) out.set(String(e), null);
  else if (on && typeof on === "object") {
    for (const [e, cfg] of Object.entries(on as Record<string, unknown>)) {
      const types = cfg && typeof cfg === "object" ? (cfg as { types?: unknown }).types : undefined;
      out.set(e, types === undefined ? null : ([] as string[]).concat(types as string[]).map(String));
    }
  }
  return out;
}

/** Default activity types GitHub uses when `types:` is omitted (only where not "all"). */
const DEFAULT_TYPES: Record<string, string[]> = {
  pull_request: ["opened", "synchronize", "reopened"],
  pull_request_target: ["opened", "synchronize", "reopened"],
};

export function eventFires(wf: Workflow, name: string, action: string | null | undefined): boolean {
  const t = triggers(wf);
  if (!t.has(name)) return false;
  const types = t.get(name) ?? DEFAULT_TYPES[name] ?? null;
  return types === null || action == null || types.includes(action);
}

export type Scopes = Record<string, "read" | "write" | "none">;
const SCOPES = ["actions", "attestations", "checks", "contents", "deployments", "discussions", "id-token", "issues",
  "models", "packages", "pages", "pull-requests", "repository-projects", "security-events", "statuses"];

function expand(p: unknown): Scopes | undefined {
  if (p === undefined || p === null) return undefined;
  if (p === "read-all" || p === "write-all") {
    const level = p === "read-all" ? "read" : "write";
    return Object.fromEntries(SCOPES.map((s) => [s, s === "id-token" && level === "read" ? "none" : level])) as Scopes;
  }
  if (typeof p === "object") {
    const out: Scopes = Object.fromEntries(SCOPES.map((s) => [s, "none"])) as Scopes;
    for (const [k, v] of Object.entries(p as Record<string, string>)) out[k] = v as "read" | "write" | "none";
    return out;
  }
  if (p === "" || (typeof p === "object" && Object.keys(p as object).length === 0)) return {};
  throw new Error(`unsupported permissions ${JSON.stringify(p)}`);
}

/**
 * Effective GITHUB_TOKEN scopes for the job. Assumptions (GitHub documentation, not
 * observed): the restricted default grants contents/packages read; the permissive default
 * grants write to every scope except id-token; pull_request runs from forks are capped
 * at read and get no id-token.
 */
export function tokenScopes(wf: Workflow, job: Job, defaults: "restricted" | "permissive", forkPr: boolean): Scopes {
  let scopes = expand(job.permissions) ?? expand(wf.permissions);
  if (!scopes) {
    scopes = Object.fromEntries(SCOPES.map((s) => [s, "none"])) as Scopes;
    if (defaults === "restricted") { scopes.contents = "read"; scopes.packages = "read"; }
    else for (const s of SCOPES) if (s !== "id-token") scopes[s] = "write";
  }
  if (forkPr) {
    for (const s of Object.keys(scopes)) if (scopes[s] === "write") scopes[s] = "read";
    scopes["id-token"] = "none";
  }
  return scopes;
}

export type Checkout = { present: boolean; ref: string; persistCredentials: boolean; token: string; include: boolean; path: string };
