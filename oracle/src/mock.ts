/**
 * Local stand-in for the GitHub REST and GraphQL APIs, the Actions OIDC endpoint and
 * Anthropic's GitHub App token exchange. Serves the scenario's synthesized data, applies a
 * minimal model of token scopes to writes, and logs every request. Unknown routes answer
 * 404 and are reported, never guessed.
 */
import type { Scopes } from "./workflow";
import { TOKENS } from "./types";
import {
  type Resolved, LOGIN, ISSUE_NUMBER, TRIGGER_TIME, HEAD_SHA, BASE_SHA, canary,
  graphqlIssue, graphqlPullRequest, permissionOf,
} from "./payload";

export type MockLog = { method: string; path: string; status: number; token: string | null; body?: unknown }[];

export type MockConfig = {
  r: Resolved;
  payload: Record<string, any>;
  workflowScopes: Scopes;
  /** Token value -> scopes (null: unrestricted, e.g. a personal access token). */
  tokenScopes: Map<string, Scopes | null>;
};

const APP_DEFAULT: Scopes = { contents: "write", "pull-requests": "write", issues: "write" };

export function startMock(cfg: MockConfig) {
  const log: MockLog = [];
  let nextComment = 9100;
  const comments = new Map<number, Record<string, unknown>>();
  let appScopes: Scopes = { ...APP_DEFAULT };

  const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  const forbidden = () => json(403, { message: "Resource not accessible by integration", status: "403" });
  const notFound = () => json(404, { message: "Not Found", status: "404" });

  function scopesFor(token: string | null): Scopes | null | undefined {
    if (!token) return undefined;
    if (token === TOKENS.app) return appScopes;
    if (token === TOKENS.workflow) return cfg.workflowScopes;
    return cfg.tokenScopes.has(token) ? cfg.tokenScopes.get(token)! : undefined;
  }
  function can(token: string | null, scope: string, level: "read" | "write"): boolean {
    const s = scopesFor(token);
    if (s === null) return true; // unrestricted token
    if (s === undefined) return false;
    const have = s[scope] ?? "none";
    return level === "read" ? have !== "none" || cfg.r.visibility === "public" : have === "write";
  }
  const canComment = (token: string | null) => can(token, "issues", "write") || can(token, "pull-requests", "write");

  const server = Bun.serve({
    port: 0,
    hostname: "127.0.0.1",
    async fetch(req) {
      const url = new URL(req.url);
      const auth = req.headers.get("authorization");
      const token = auth ? auth.replace(/^(token|bearer|basic)\s+/i, "") : null;
      let body: any;
      if (req.method !== "GET" && req.method !== "HEAD") {
        const text = await req.text();
        try { body = text ? JSON.parse(text) : undefined; } catch { body = text; }
      }
      const res = route(req.method, url, token, body);
      log.push({ method: req.method, path: url.pathname + (url.search || ""), status: res.status, token: tokenKind(token), ...(body !== undefined ? { body } : {}) });
      return res;
    },
  });

  function tokenKind(token: string | null): string | null {
    if (!token) return null;
    if (token === TOKENS.app) return "app";
    if (token === TOKENS.workflow) return "workflow";
    if (token === TOKENS.oidcRequest) return "oidc-request";
    if (token === TOKENS.oidc) return "oidc";
    return cfg.tokenScopes.has(token) ? "secret" : "unknown";
  }

  function route(method: string, url: URL, token: string | null, body: any): Response {
    const p = decodeURIComponent(url.pathname);
    const { r } = cfg;
    const repoPrefix = `/repos/${r.owner}/${r.repo}`;
    let m: RegExpExecArray | null;

    // Actions OIDC token endpoint (ACTIONS_ID_TOKEN_REQUEST_URL).
    if (p === "/_oidc/token") return token === TOKENS.oidcRequest ? json(200, { value: TOKENS.oidc }) : json(401, { message: "bad request token" });

    // Anthropic GitHub App token exchange (redirected by oracle/src/preload.ts).
    if (p === "/_anthropic/api/github/github-app-token-exchange" && method === "POST") {
      if (token !== TOKENS.oidc) return json(401, { error: { message: "invalid OIDC token" } });
      if (body && typeof body === "object" && body.permissions) {
        appScopes = Object.fromEntries(Object.entries(body.permissions as Record<string, string>).map(([k, v]) => [k.replace(/_/g, "-"), v])) as Scopes;
      }
      return json(200, { token: TOKENS.app });
    }

    if (p === "/graphql" && method === "POST") {
      const q = String(body?.query ?? "");
      if (q.includes("pullRequest(number")) return json(200, { data: { repository: { pullRequest: graphqlPullRequest(r) } } });
      if (q.includes("issue(number")) return json(200, { data: { repository: { issue: graphqlIssue(r, cfg.payload.issue ?? { title: "", body: "" }) } } });
      if (q.includes("user(login")) {
        const login = String(body?.variables?.login ?? "");
        return json(200, { data: { user: { name: `Display ${canary("user.name", login === r.s.actor.login ? "actor" : login === LOGIN.maintainer ? "maintainer" : "outsider")}` } } });
      }
      return json(200, { errors: [{ message: "oracle mock: unknown query" }] });
    }

    if ((m = /^\/users\/([^/]+)$/.exec(p)) && method === "GET") {
      const login = decodeURIComponent(m[1]!);
      if (login.endsWith("[bot]")) return notFound();
      const type = login === r.s.actor.login ? (r.s.actor.type ?? "User") : "User";
      return json(200, { login, id: 1000, type });
    }

    if (!p.startsWith(repoPrefix)) return notFound();
    const rest = p.slice(repoPrefix.length);

    if (rest === "" && method === "GET") return json(200, { name: r.repo, full_name: `${r.owner}/${r.repo}`, default_branch: r.defaultBranch, private: r.visibility === "private" });

    if ((m = /^\/collaborators\/([^/]+)\/permission$/.exec(rest)) && method === "GET") {
      const login = decodeURIComponent(m[1]!);
      const level = permissionOf(r, login);
      const api = level === "maintain" ? "write" : level === "triage" ? "read" : level;
      return json(200, { permission: api, role_name: level, user: { login } });
    }

    if ((m = /^\/git\/ref\/heads\/(.+)$/.exec(rest)) && method === "GET") {
      const ref = decodeURIComponent(m[1]!);
      return ref === r.defaultBranch ? json(200, { ref: `refs/heads/${ref}`, object: { sha: BASE_SHA, type: "commit" } }) : notFound();
    }

    if ((m = /^\/issues\/(\d+)\/comments$/.exec(rest))) {
      if (method === "GET") return json(200, []);
      if (method === "POST") {
        if (!canComment(token)) return forbidden();
        const id = nextComment++;
        const c = { id, body: body?.body ?? "", user: { login: "claude[bot]", id: 41898282, type: "Bot" }, html_url: `https://github.com/${r.owner}/${r.repo}/issues/${m[1]}#issuecomment-${id}`, created_at: TRIGGER_TIME, updated_at: TRIGGER_TIME };
        comments.set(id, c);
        return json(201, c);
      }
    }
    if ((m = /^\/issues\/comments\/(\d+)$/.exec(rest))) {
      const c = comments.get(Number(m[1]));
      if (!c) return notFound();
      if (method === "GET") return json(200, c);
      if (method === "PATCH") { if (!canComment(token)) return forbidden(); Object.assign(c, { body: body?.body }); return json(200, c); }
    }
    if ((m = /^\/pulls\/(\d+)\/comments\/(\d+)\/replies$/.exec(rest)) && method === "POST") {
      if (!can(token, "pull-requests", "write")) return forbidden();
      const id = nextComment++;
      const c = { id, body: body?.body ?? "", user: { login: "claude[bot]", type: "Bot" }, created_at: TRIGGER_TIME };
      comments.set(id, c);
      return json(201, c);
    }
    if ((m = /^\/pulls\/comments\/(\d+)$/.exec(rest))) {
      const c = comments.get(Number(m[1]));
      if (!c) return notFound();
      if (method === "GET") return json(200, c);
      if (method === "PATCH") { if (!can(token, "pull-requests", "write")) return forbidden(); Object.assign(c, { body: body?.body }); return json(200, c); }
    }
    if ((m = /^\/issues\/(\d+)\/events$/.exec(rest)) && method === "GET") {
      const e = cfg.payload;
      const events = [];
      if (e.label) events.push({ event: "labeled", label: e.label, created_at: TRIGGER_TIME });
      if (e.assignee) events.push({ event: "assigned", assignee: e.assignee, created_at: TRIGGER_TIME });
      return json(200, events);
    }
    if ((m = /^\/pulls\/(\d+)$/.exec(rest)) && method === "GET" && Number(m[1]) === ISSUE_NUMBER) {
      return json(200, { number: ISSUE_NUMBER, head: { ref: r.headRef, sha: HEAD_SHA }, base: { ref: r.defaultBranch, sha: BASE_SHA }, state: "open" });
    }
    if (rest === "/actions/runs" && method === "GET") {
      return can(token, "actions", "read") && (scopesFor(token) === null || (scopesFor(token)?.actions ?? "none") !== "none")
        ? json(200, { total_count: 0, workflow_runs: [] }) : forbidden();
    }
    if (/^\/compare\//.test(rest) && method === "GET") return json(200, { total_commits: 0, ahead_by: 0, behind_by: 0, files: [], commits: [] });
    if (/^\/branches\//.test(rest) && method === "GET") return notFound();
    return notFound();
  }

  return { url: `http://127.0.0.1:${server.port}`, log, stop: () => server.stop(true) };
}
