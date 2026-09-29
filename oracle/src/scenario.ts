/**
 * Run one scenario: evaluate the workflow up to the claude-code-action step, prepare the
 * runner state that step would see, run the action's unmodified entrypoint
 * (src/entrypoints/run.ts, as action.yml's "Run Claude Code Action" step does) with a
 * stand-in Claude Code executable, and read off the Observation.
 */
import { mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync, existsSync, readdirSync } from "fs";
import { tmpdir } from "os";
import { dirname, join } from "path";
import { condition, interpolate, UnsupportedExpression, evaluate, str, type Contexts } from "./expr";
import { eventFires, locateStep, parseWorkflow, tokenScopes, type Step, type Workflow } from "./workflow";
import { ACTION_COMMIT, CREDENTIAL, TOKENS, secretValue, type Observation, type Scenario } from "./types";
import { CANARY_RE, decodeCanary, eventPayload, resolve, HEAD_SHA, BASE_SHA, ISSUE_NUMBER, type Resolved } from "./payload";
import { startMock } from "./mock";
import { setupRepo, workspaceGitConfig, type CheckoutSpec } from "./git";

const ORACLE_DIR = dirname(import.meta.dir);
export const ACTION_DIR = join(ORACLE_DIR, "vendor", "claude-code-action");
const FAKE_CLAUDE = join(ORACLE_DIR, "fake-claude", "claude");
const PRELOAD = join(ORACLE_DIR, "src", "preload.ts");

type ActionYml = { inputs: Record<string, { default?: string }>; runs: { steps: (Step & { name?: string; id?: string })[] } };
let actionYml: ActionYml | undefined;
function loadActionYml(): ActionYml {
  actionYml ??= Bun.YAML.parse(readFileSync(join(ACTION_DIR, "action.yml"), "utf8")) as ActionYml;
  return actionYml;
}

export type RunOptions = { bun: string; timeoutMs: number; keep: boolean };

function empty(id: string): Observation {
  return {
    id, action_commit: ACTION_COMMIT, runs: false, stop_reason: null, mode: null, allowed_tools: [], disallowed_tools: [],
    permission_mode: null, mcp_servers: [], credentials: { token_in_git_config: false, credential_helper: false, claude_env: [] },
    env_scrub: false, prompt_canaries: [], extra: {},
  };
}

function stringMap(obj: unknown, ctx: Contexts): Record<string, string> {
  const out: Record<string, string> = {};
  if (obj && typeof obj === "object") for (const [k, v] of Object.entries(obj)) out[k] = interpolate(v, ctx);
  return out;
}

function referencedSecrets(text: string): string[] {
  return [...new Set([...text.matchAll(/secrets\.([A-Za-z_][A-Za-z0-9_]*)/gi)].map((m) => m[1]!.toUpperCase()))];
}

function checkoutMajor(uses: string): number | null {
  const m = /@v(\d+)/.exec(uses);
  return m ? Number(m[1]) : null;
}

export async function runScenario(s: Scenario, opts: RunOptions): Promise<Observation> {
  const obs = empty(s.id);
  const extra = obs.extra!;
  let wf: Workflow;
  let loc: ReturnType<typeof locateStep>;
  try {
    wf = parseWorkflow(s.workflow);
    loc = locateStep(wf, s.job, s.step);
  } catch (e) {
    obs.stop_reason = `error:workflow: ${(e as Error).message}`;
    return obs;
  }
  const r = resolve(s);
  extra.job = loc.jobId;
  extra.step = loc.index;
  extra.action_ref = loc.step.uses;

  if (!eventFires(wf, s.event.name, s.event.action)) {
    obs.stop_reason = "event-not-in-workflow";
    return obs;
  }

  // GitHub withholds secrets and caps the token for runs triggered from a fork on these
  // events ("Workflows in forked repositories", docs: events that trigger workflows).
  const forkPr = ["pull_request", "pull_request_review", "pull_request_review_comment"].includes(s.event.name) && r.fork;
  const scopes = tokenScopes(wf, loc.job, s.repo?.default_token_permissions ?? "restricted", forkPr);
  extra.token_scopes = Object.fromEntries(Object.entries(scopes).filter(([, v]) => v !== "none"));

  // Secrets: those the workflow references exist unless the scenario lists them; fork
  // runs (see forkPr) receive none except GITHUB_TOKEN.
  const secretNames = (s.secrets ?? referencedSecrets(s.workflow)).map((n) => n.toUpperCase()).filter((n) => n !== "GITHUB_TOKEN");
  const secrets: Record<string, string> = { GITHUB_TOKEN: TOKENS.workflow };
  if (!forkPr) for (const n of secretNames) secrets[n] = secretValue(n);

  const root = mkdtempSync(join(tmpdir(), "oracle-"));
  const dirs = { home: join(root, "home"), temp: join(root, "runner_temp"), action: join(root, "action"), files: join(root, "files") };
  for (const d of Object.values(dirs)) mkdirSync(d, { recursive: true });
  const workspacePath = join(root, "workspace");
  extra.workdir = opts.keep ? root : undefined;

  const ymlInputs = loadActionYml().inputs;
  const githubCtx = (payload: Record<string, unknown>) => {
    // GITHUB_REF is the merge ref for pull_request and review events, the base branch for
    // pull_request_target; head_ref/base_ref are set only for pull_request(_target).
    const pr = (payload.pull_request ?? null) as { head?: { ref: string }; base?: { ref: string } } | null;
    const mergeRef = pr !== null && s.event.name !== "pull_request_target";
    const headBase = s.event.name === "pull_request" || s.event.name === "pull_request_target";
    const ref = mergeRef ? `refs/pull/${ISSUE_NUMBER}/merge` : `refs/heads/${r.defaultBranch}`;
    return {
      event: payload, event_name: s.event.name, actor: s.actor.login, triggering_actor: s.actor.login,
      repository: `${r.owner}/${r.repo}`, repository_owner: r.owner, ref, ref_name: mergeRef ? `${ISSUE_NUMBER}/merge` : r.defaultBranch,
      sha: mergeRef ? HEAD_SHA : BASE_SHA, head_ref: headBase ? pr?.head?.ref ?? "" : "", base_ref: headBase ? pr?.base?.ref ?? "" : "",
      run_id: "4242", run_number: "1", run_attempt: "1", job: loc.jobId, workflow: wf.name ?? "workflow",
      token: TOKENS.workflow, server_url: "https://github.com", api_url: "https://api.github.com",
      graphql_url: "https://api.github.com/graphql", workspace: workspacePath, action_path: dirs.action,
      event_path: join(dirs.files, "event.json"), retention_days: "90",
    };
  };
  const runnerCtx = { os: "Linux", arch: "X64", name: "oracle", temp: dirs.temp, tool_cache: join(root, "toolcache"), environment: "github-hosted" };

  let payload: Record<string, unknown> = {};
  let ctx: Contexts = {};
  let callerEnv: Record<string, string> = {};
  let withInputs: Record<string, string> = {};
  try {
    let phrases = { triggerPhrase: ymlInputs.trigger_phrase?.default ?? "@claude", labelTrigger: ymlInputs.label_trigger?.default ?? "claude", assigneeTrigger: "" };
    // The payload's texts depend on the step's trigger inputs, and the job and step
    // conditions read the payload: iterate to a fixpoint, then decide the conditions on the
    // final payload.
    let jobIf = true;
    let stepIf = true;
    for (let pass = 0; pass < 3; pass++) {
      payload = eventPayload(r, phrases);
      const dispatchInputs = s.event.name === "workflow_dispatch" ? Object.fromEntries(Object.entries(s.event.inputs ?? {}).map(([k, v]) => [k, v as string])) : {};
      const base: Contexts = { github: githubCtx(payload), secrets, vars: s.vars ?? {}, inputs: dispatchInputs, runner: runnerCtx, env: {} };
      const wfEnv = stringMap(wf.env, base);
      const jobEnv = stringMap(loc.job.env, { ...base, env: wfEnv });
      jobIf = condition(loc.job.if, { ...base, env: {} });
      const stepEnv = stringMap(loc.step.env, { ...base, env: { ...wfEnv, ...jobEnv } });
      callerEnv = { ...wfEnv, ...jobEnv, ...stepEnv };
      ctx = { ...base, env: callerEnv };
      stepIf = condition(loc.step.if, { ...base, env: { ...wfEnv, ...jobEnv } });
      withInputs = stringMap(loc.step.with, ctx);
      const next = {
        triggerPhrase: withInputs.trigger_phrase ?? phrases.triggerPhrase,
        labelTrigger: withInputs.label_trigger ?? phrases.labelTrigger,
        assigneeTrigger: withInputs.assignee_trigger ?? "",
      };
      if (JSON.stringify(next) === JSON.stringify(phrases)) break;
      phrases = next;
    }
    if (!jobIf) { obs.stop_reason = "job-if"; return finish(); }
    if (!stepIf) { obs.stop_reason = "step-if"; return finish(); }
  } catch (e) {
    if (e instanceof UnsupportedExpression) { obs.stop_reason = "unsupported-expression"; extra.expression_error = e.message; return finish(); }
    throw e;
  }

  // Inputs as the composite action sees them: declared inputs only, defaults applied.
  const unexpected = Object.keys(withInputs).filter((k) => !(k in ymlInputs));
  if (unexpected.length) extra.unexpected_inputs = unexpected;
  const inputs: Record<string, string> = {};
  for (const [name, spec] of Object.entries(ymlInputs)) inputs[name] = name in withInputs ? withInputs[name]! : (spec.default ?? "");

  // actions/checkout before the step.
  const steps = loc.job.steps ?? [];
  const co = [...steps.slice(0, loc.index)].reverse().find((st) => typeof st.uses === "string" && st.uses.toLowerCase().startsWith("actions/checkout@"));
  let checkout: CheckoutSpec = { present: false, ref: "default", persistCredentials: false, token: TOKENS.workflow, include: false };
  try {
    if (co) {
      const w = stringMap(co.with, ctx);
      const refText = (w.ref ?? "").trim();
      const pr = payload.pull_request as { head: { ref: string; sha: string } } | undefined;
      let ref: CheckoutSpec["ref"];
      const prDefault = ["pull_request", "pull_request_review", "pull_request_review_comment"].includes(s.event.name);
      if (refText === "") ref = prDefault ? "head" : "default";
      else if (pr && (refText === pr.head.sha || refText === `refs/pull/${ISSUE_NUMBER}/head` || refText === `refs/pull/${ISSUE_NUMBER}/merge`)) ref = "head";
      else if (pr && refText === pr.head.ref) ref = r.fork ? refText : "head";
      else if (refText === r.defaultBranch || refText === `refs/heads/${r.defaultBranch}`) ref = "default";
      else { ref = "default"; extra.checkout_ref_unresolved = refText; }
      if (w.repository && w.repository.toLowerCase() !== `${r.owner}/${r.repo}`) extra.checkout_other_repository = w.repository;
      if (w.path) extra.checkout_path = w.path;
      const major = checkoutMajor(co.uses!);
      checkout = { present: true, ref, persistCredentials: (w["persist-credentials"] ?? "true") !== "false", token: w.token || TOKENS.workflow, include: major !== null && major >= 6 };
      extra.checkout = { uses: co.uses, ref, persist_credentials: checkout.persistCredentials, layout: checkout.include ? "include" : "local" };
    }
  } catch (e) {
    if (e instanceof UnsupportedExpression) { obs.stop_reason = "unsupported-expression"; extra.expression_error = e.message; return finish(); }
    throw e;
  }

  // Token scopes the mock applies: secret-backed tokens (PATs) are unrestricted.
  const tokenScopesMap = new Map<string, null>(Object.entries(secrets).filter(([n]) => n !== "GITHUB_TOKEN").map(([, v]) => [v, null]));
  const mock = startMock({ r, payload: payload as Record<string, any>, workflowScopes: scopes, tokenScopes: tokenScopesMap });

  try {
    try {
      setupRepo(root, r, [TOKENS.workflow, TOKENS.app, ...Object.values(secrets).filter((v) => !/\s/.test(v))], checkout, dirs.temp);
    } catch (e) {
      obs.stop_reason = `error:checkout: ${(e as Error).message.split("\n")[0]}`;
      return finish();
    }
    const workspace = workspacePath;

    // The action directory: links to the pinned clone, so files the action writes there
    // (the credential helper) stay per scenario.
    for (const entry of readdirSync(ACTION_DIR)) {
      if (entry === ".git") continue;
      symlinkSync(join(ACTION_DIR, entry), join(dirs.action, entry));
    }

    const eventPath = join(dirs.files, "event.json");
    writeFileSync(eventPath, JSON.stringify(payload, null, 2));
    const files = { output: join(dirs.files, "output"), env: join(dirs.files, "env"), path: join(dirs.files, "path"), summary: join(dirs.files, "summary"), state: join(dirs.files, "state"), capture: join(dirs.files, "capture.json") };
    for (const f of Object.values(files)) if (f !== files.capture) writeFileSync(f, "");

    const g = githubCtx(payload);
    const runnerEnv: Record<string, string> = {
      HOME: dirs.home, PATH: `${dirname(opts.bun)}:/usr/local/bin:/usr/bin:/bin`, CI: "true", GITHUB_ACTIONS: "true", LANG: "C.UTF-8",
      GITHUB_WORKSPACE: workspace, RUNNER_TEMP: dirs.temp, RUNNER_OS: "Linux", RUNNER_ARCH: "X64", RUNNER_NAME: "oracle", RUNNER_TOOL_CACHE: runnerCtx.tool_cache,
      GITHUB_EVENT_NAME: s.event.name, GITHUB_EVENT_PATH: eventPath, GITHUB_REPOSITORY: `${r.owner}/${r.repo}`, GITHUB_REPOSITORY_OWNER: r.owner,
      GITHUB_ACTOR: s.actor.login, GITHUB_TRIGGERING_ACTOR: s.actor.login, GITHUB_RUN_ID: g.run_id, GITHUB_RUN_NUMBER: "1", GITHUB_RUN_ATTEMPT: "1",
      GITHUB_JOB: loc.jobId, GITHUB_WORKFLOW: g.workflow, GITHUB_SHA: g.sha, GITHUB_REF: g.ref, GITHUB_REF_NAME: g.ref_name,
      GITHUB_HEAD_REF: g.head_ref, GITHUB_BASE_REF: g.base_ref, GITHUB_SERVER_URL: "https://github.com",
      GITHUB_API_URL: mock.url, GITHUB_GRAPHQL_URL: `${mock.url}/graphql`,
      GITHUB_OUTPUT: files.output, GITHUB_ENV: files.env, GITHUB_PATH: files.path, GITHUB_STEP_SUMMARY: files.summary, GITHUB_STATE: files.state,
      GITHUB_ACTION_PATH: dirs.action, GITHUB_ACTION_REPOSITORY: "anthropics/claude-code-action", GITHUB_ACTION_REF: ACTION_COMMIT, GITHUB_ACTION: "__anthropics_claude-code-action",
      GIT_CONFIG_GLOBAL: join(root, "gitconfig-global"), GIT_CONFIG_NOSYSTEM: "1", GIT_TERMINAL_PROMPT: "0",
      ORACLE_CAPTURE: files.capture, ORACLE_MOCK_URL: mock.url,
    };
    const oidc = scopes["id-token"] === "write";
    if (oidc) {
      runnerEnv.ACTIONS_ID_TOKEN_REQUEST_URL = `${mock.url}/_oidc/token?api-version=2.0`;
      runnerEnv.ACTIONS_ID_TOKEN_REQUEST_TOKEN = TOKENS.oidcRequest;
    }

    // The "Run Claude Code Action" step's env block, evaluated with the composite action's contexts.
    const runStep = loadActionYml().runs.steps.find((st) => st.id === "run");
    if (!runStep) throw new Error("action.yml has no step with id 'run'");
    const inputsCtx: Record<string, string> = inputs;
    const compositeCtx: Contexts = { ...ctx, inputs: inputsCtx, env: callerEnv, github: g, runner: runnerCtx };
    let compositeEnv: Record<string, string>;
    try {
      compositeEnv = stringMap(runStep.env, compositeCtx);
    } catch (e) {
      if (e instanceof UnsupportedExpression) { obs.stop_reason = "unsupported-expression"; extra.expression_error = `action.yml: ${e.message}`; return finish(); }
      throw e;
    }
    // The oracle's only substitution: the Claude Code executable.
    compositeEnv.PATH_TO_CLAUDE_CODE_EXECUTABLE = FAKE_CLAUDE;

    const env = { ...runnerEnv, ...callerEnv, ...compositeEnv };
    const proc = Bun.spawn([opts.bun, "--no-env-file", `--config=${dirs.action}/bunfig.toml`, `--preload=${PRELOAD}`, "run", `${dirs.action}/src/entrypoints/run.ts`], {
      cwd: workspace, env, stdout: "pipe", stderr: "pipe",
    });
    const timer = setTimeout(() => proc.kill("SIGKILL"), opts.timeoutMs);
    const [stdout, stderr] = await Promise.all([new Response(proc.stdout).text(), new Response(proc.stderr).text()]);
    const exit = await proc.exited;
    clearTimeout(timer);
    const logs = `${stdout}\n${stderr}`;
    if (opts.keep) writeFileSync(join(dirs.files, "action.log"), logs);
    extra.exit_code = exit;

    const mode = /Auto-detected mode: (tag|agent)/.exec(logs);
    obs.mode = mode ? (mode[1] as "tag" | "agent") : null;

    const capture = existsSync(files.capture) ? JSON.parse(readFileSync(files.capture, "utf8")) as {
      argv: string[]; env: Record<string, string>; cwd: string; claudeMd: string | null; userSettings: string | null;
      files: Record<string, string>; gitConfig: string | null; sshKeyFile: boolean; stdin: string;
    } : null;

    if (capture) {
      obs.runs = true;
      readArgs(obs, capture.argv, dirs.action);
      obs.credentials.claude_env = Object.entries(capture.env).filter(([k, v]) => !k.startsWith("ORACLE_") && CREDENTIAL.test(v ?? "")).map(([k]) => k).sort();
      const scrub = capture.env.CLAUDE_CODE_SUBPROCESS_ENV_SCRUB;
      obs.env_scrub = !!scrub && scrub !== "0" && scrub.toLowerCase() !== "false";
      extra.claude_env_names = Object.keys(capture.env).filter((k) => !k.startsWith("ORACLE_")).sort();
      extra.claude_md = canariesIn(capture.claudeMd ?? "");
      extra.workspace_canaries = Object.fromEntries(Object.entries(capture.files).map(([f, t]) => [f, canariesIn(t)]).filter(([, c]) => c.length));
      extra.ssh_key_file = capture.sshKeyFile;
      extra.user_settings = capture.userSettings ? JSON.parse(capture.userSettings) : null;
      extra.claude_cwd_is_workspace = capture.cwd === workspace;
    } else {
      obs.stop_reason = stopReason(logs, exit, opts.timeoutMs);
    }

    // Canaries that reach Claude Code: prompt files and process arguments.
    const promptDir = join(dirs.temp, "claude-prompts");
    const promptText = ["claude-prompt.txt", "claude-user-request.txt"].map((f) => { try { return readFileSync(join(promptDir, f), "utf8"); } catch { return ""; } }).join("\n");
    if (capture) {
      obs.prompt_canaries = canariesIn(`${promptText}\n${capture.argv.join("\n")}\n${capture.stdin}`);
      extra.canaries_in_env = canariesIn(Object.values(capture.env).join("\n"));
    }

    // Git config as Claude Code starts (after the run when it never started).
    const gc = capture?.gitConfig != null
      ? { text: capture.gitConfig, helper: /^credential\.helper=/m.test(capture.gitConfig) }
      : workspaceGitConfig(workspace, join(root, "gitconfig-global"));
    // Credentials describe the Claude Code session, so they stay empty when none starts;
    // the git config state is still reported in extra.
    if (capture) {
      obs.credentials.token_in_git_config = /ghs_ORACLE|ORACLESECRET_/.test(gc.text) || /extraheader=AUTHORIZATION: basic/i.test(gc.text);
      obs.credentials.credential_helper = gc.helper;
    }
    extra.git_config_credentials = gitConfigCredentials(gc.text);
    extra.helper_file_in_action_path = existsSync(join(dirs.action, ".git-credential-gh-token"));
    extra.api_calls = mock.log.map((l) => `${l.method} ${l.path.replace(/\?.*$/, "")} ${l.status}${l.token ? ` [${l.token}]` : ""}`);
    const blocked = [...logs.matchAll(/\[oracle\] blocked network request: (\S+)/g)].map((m) => m[1]);
    if (blocked.length) extra.blocked_network = blocked;
    extra.outputs = parseGithubFile(readFileSync(files.output, "utf8"));
    normalize(extra, root);
    const failure = /Action failed with error: (.*)/.exec(logs);
    if (failure) extra.action_error = failure[1]!.slice(0, 300);
    return finish();
  } finally {
    mock.stop();
  }

  function finish(): Observation {
    if (!opts.keep) rmSync(root, { recursive: true, force: true });
    for (const k of Object.keys(extra)) if (extra[k] === undefined) delete extra[k];
    return obs;
  }
}

function readArgs(obs: Observation, argv: string[], actionPath: string) {
  const norm = (t: string) => t.split(actionPath).join("${GITHUB_ACTION_PATH}");
  const values = (flag: string) => {
    const out: string[] = [];
    argv.forEach((a, i) => {
      if (a === flag && i + 1 < argv.length) out.push(argv[i + 1]!);
      else if (a.startsWith(`${flag}=`)) out.push(a.slice(flag.length + 1));
    });
    return out;
  };
  obs.allowed_tools = values("--allowedTools").flatMap((v) => v.split(",")).map((t) => norm(t.trim())).filter(Boolean);
  obs.disallowed_tools = values("--disallowedTools").flatMap((v) => v.split(",")).map((t) => norm(t.trim())).filter(Boolean);
  obs.permission_mode = values("--permission-mode").at(-1) ?? null;
  const servers = new Set<string>();
  const serverCreds: Record<string, string[]> = {};
  for (const cfg of values("--mcp-config")) {
    try {
      const parsed = JSON.parse(cfg) as { mcpServers?: Record<string, { env?: Record<string, string> }> };
      for (const [name, spec] of Object.entries(parsed.mcpServers ?? {})) {
        servers.add(name);
        const creds = Object.entries(spec.env ?? {}).filter(([, v]) => CREDENTIAL.test(String(v))).map(([k]) => k);
        if (creds.length) serverCreds[name] = creds;
      }
    } catch {
      servers.add(`@file:${norm(cfg)}`);
    }
  }
  obs.mcp_servers = [...servers].sort();
  obs.extra!.mcp_server_credentials = serverCreds;
  obs.extra!.setting_sources = values("--setting-sources").at(-1) ?? null;
  const known = new Set(["--allowedTools", "--disallowedTools", "--permission-mode", "--mcp-config", "--setting-sources"]);
  obs.extra!.other_flags = argv.filter((a) => a.startsWith("--") && !known.has(a.split("=")[0]!)).map((a) => a.split("=")[0]);
}

/** Make diagnostics reproducible: scenario directory and generated branch timestamps. */
function normalize(extra: Record<string, unknown>, root: string) {
  const fix = (v: unknown): unknown => {
    if (typeof v === "string") return v.split(root).join("<workdir>").replace(/\d{8}-\d{4}/g, "<timestamp>");
    if (Array.isArray(v)) return v.map(fix);
    if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, fix(x)]));
    return v;
  };
  for (const k of Object.keys(extra)) if (k !== "workdir") extra[k] = fix(extra[k]);
}

function canariesIn(text: string): string[] {
  return [...new Set([...text.matchAll(CANARY_RE)].map((m) => decodeCanary(m[1]!, m[2]!)))].sort();
}

function gitConfigCredentials(text: string): string[] {
  const out: string[] = [];
  for (const line of text.split("\n")) {
    if (/ghs_ORACLEWORKFLOWTOKEN/.test(line)) out.push(`${line.split("=")[0]}: workflow token`);
    if (/ghs_ORACLEAPPTOKEN/.test(line)) out.push(`${line.split("=")[0]}: app token`);
    const secret = /ORACLESECRET_([A-Z0-9_]+?)_0000/.exec(line);
    if (secret) out.push(`${line.split("=")[0]}: secret ${secret[1]}`);
    const header = /extraheader=AUTHORIZATION: basic ([A-Za-z0-9+/=]+)/i.exec(line);
    if (header) {
      const decoded = Buffer.from(header[1]!, "base64").toString();
      out.push(`${line.split("=")[0]}: checkout ${/WORKFLOW/.test(decoded) ? "workflow token" : /APP/.test(decoded) ? "app token" : "token"}`);
    }
  }
  return out;
}

function parseGithubFile(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const heredoc = /^([^=<]+)<<(.+)$/.exec(lines[i]!);
    if (heredoc) {
      const [, name, delim] = heredoc;
      const value: string[] = [];
      while (++i < lines.length && lines[i] !== delim) value.push(lines[i]!);
      out[name!] = value.join("\n");
    } else if (lines[i]!.includes("=")) {
      const [name, ...rest] = lines[i]!.split("=");
      out[name!] = rest.join("=");
    }
  }
  for (const k of Object.keys(out)) if (CREDENTIAL.test(out[k]!)) out[k] = "<credential>";
  return out;
}

function stopReason(logs: string, exit: number, timeoutMs: number): string {
  const rules: [RegExp, string][] = [
    [/Unsupported event type/, "unsupported-event"],
    [/Exiting due to workflow validation skip/, "workflow-validation-skip"],
    [/Could not fetch an OIDC token/, "oidc-unavailable"],
    [/Actor does not have write permissions/, "actor-permission"],
    [/Workflow initiated by non-human actor/, "non-human-actor"],
    [/No trigger found, skipping remaining steps/, "no-trigger"],
    [/Environment variable validation failed/, "missing-credentials"],
  ];
  for (const [re, reason] of rules) if (re.test(logs)) return reason;
  if (exit === 137 || exit === null) return `error:timeout after ${timeoutMs}ms`;
  const failure = /Action failed with error: (.*)/.exec(logs);
  return `error:${failure ? failure[1]!.slice(0, 200) : `exit ${exit}`}`;
}

// Re-exported for tests.
export { evaluate, str, type Resolved };
