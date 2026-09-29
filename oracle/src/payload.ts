/**
 * Synthesized GitHub data for a scenario: the webhook payload and the issue/PR data the
 * mock API serves. Every free-text field carries a canary naming the field and its author,
 * so the oracle can tell which authors' text reaches Claude Code.
 */
import type { Role, Scenario } from "./types";

export const LOGIN: Record<Role, string> = { maintainer: "maintainer", outsider: "outsider" };
const USER_ID: Record<string, number> = { maintainer: 1001, outsider: 1002 };

export type Author = Role | "actor";

export function canary(field: string, author: Author): string {
  return `CANARY-${field.replace(/\./g, "__")}-${author}`;
}
export const CANARY_RE = /CANARY-([a-z_]+)-(maintainer|outsider|actor)/g;
export function decodeCanary(field: string, author: string): string {
  return `${field.replace(/__/g, ".")}@${author}`;
}

const T0 = Date.parse("2026-01-01T00:00:00Z");
const at = (minutes: number) => new Date(T0 + minutes * 60_000).toISOString();
export const TRIGGER_TIME = at(600);

export const ISSUE_NUMBER = 7;
export const TRIGGER_COMMENT_ID = 7700;
export const HEAD_SHA = "1111111111111111111111111111111111111111";
export const BASE_SHA = "2222222222222222222222222222222222222222";

export type Resolved = {
  s: Required<Pick<Scenario, "id" | "workflow" | "event" | "actor">> & Scenario;
  owner: string;
  repo: string;
  defaultBranch: string;
  visibility: "public" | "private";
  target: "issue" | "pr";
  issueAuthor: Role;
  commentAuthors: Role[];
  prAuthor: Role;
  fork: boolean;
  reviewAuthors: Role[];
  headRef: string;
};

export function resolve(s: Scenario): Resolved {
  const c = s.content ?? {};
  const prEvent = ["pull_request", "pull_request_target", "pull_request_review", "pull_request_review_comment"].includes(s.event.name);
  const prAuthor = c.pr_author ?? "outsider";
  return {
    s: s as Resolved["s"],
    owner: s.repo?.owner ?? "acme",
    repo: s.repo?.name ?? "widget",
    defaultBranch: s.repo?.default_branch ?? "main",
    visibility: s.repo?.visibility ?? "public",
    target: prEvent ? "pr" : (c.target ?? "issue"),
    issueAuthor: c.issue_author ?? "outsider",
    commentAuthors: c.comment_authors ?? [],
    prAuthor,
    fork: (c.pr_head ?? "fork") === "fork",
    reviewAuthors: c.review_authors ?? [],
    headRef: `feature/${canary("pr.head_ref", prAuthor)}`,
  };
}

/** Login -> repository permission, as the collaborator permission API reports it. */
export function permissionOf(r: Resolved, login: string): string {
  if (login === r.s.actor.login) return r.s.actor.permission;
  if (login === LOGIN.maintainer) return "write";
  if (login === LOGIN.outsider) return "read";
  return "none";
}

function user(login: string, type = "User") {
  return { login, id: USER_ID[login] ?? 1003, type, html_url: `https://github.com/${login}` };
}

function actorUser(r: Resolved) {
  return user(r.s.actor.login, r.s.actor.type ?? "User");
}

function repository(r: Resolved) {
  return {
    id: 42, name: r.repo, full_name: `${r.owner}/${r.repo}`, owner: user(r.owner, "Organization"),
    private: r.visibility === "private", default_branch: r.defaultBranch, fork: false,
    html_url: `https://github.com/${r.owner}/${r.repo}`,
  };
}

function mention(r: Resolved, triggerPhrase: string): string {
  return r.s.event.mention === false ? "" : `${triggerPhrase} `;
}

function issueObject(r: Resolved, triggerPhrase: string, withMention: boolean) {
  const title = `Issue title ${canary("issue.title", r.issueAuthor)}`;
  const body = `${withMention ? mention(r, triggerPhrase) : ""}Issue body ${canary("issue.body", r.issueAuthor)}`;
  return {
    number: ISSUE_NUMBER, title, body, user: user(LOGIN[r.issueAuthor]), state: "open",
    labels: [] as { name: string }[], assignees: [] as unknown[], created_at: at(0),
    updated_at: TRIGGER_TIME, html_url: `https://github.com/${r.owner}/${r.repo}/issues/${ISSUE_NUMBER}`,
    ...(r.target === "pr" ? { pull_request: { url: `https://api.github.com/repos/${r.owner}/${r.repo}/pulls/${ISSUE_NUMBER}` } } : {}),
  };
}

function prObject(r: Resolved, triggerPhrase: string, withMention: boolean) {
  const headRepo = r.fork
    ? { ...repository(r), full_name: `${LOGIN[r.prAuthor]}/${r.repo}`, owner: user(LOGIN[r.prAuthor]), fork: true }
    : repository(r);
  return {
    number: ISSUE_NUMBER, state: "open", draft: false, merged: false,
    title: `PR title ${canary("pr.title", r.prAuthor)}`,
    body: `${withMention ? mention(r, triggerPhrase) : ""}PR body ${canary("pr.body", r.prAuthor)}`,
    user: user(LOGIN[r.prAuthor]), created_at: at(0), updated_at: TRIGGER_TIME,
    head: { ref: r.headRef, sha: HEAD_SHA, label: `${headRepo.owner.login}:${r.headRef}`, repo: headRepo, user: headRepo.owner },
    base: { ref: r.defaultBranch, sha: BASE_SHA, label: `${r.owner}:${r.defaultBranch}`, repo: repository(r), user: user(r.owner, "Organization") },
    html_url: `https://github.com/${r.owner}/${r.repo}/pull/${ISSUE_NUMBER}`,
    author_association: r.prAuthor === "maintainer" ? "MEMBER" : "CONTRIBUTOR",
  };
}

export function eventPayload(r: Resolved, inputs: { triggerPhrase: string; labelTrigger: string; assigneeTrigger: string }): Record<string, unknown> {
  const e = r.s.event;
  const base = { repository: repository(r), sender: actorUser(r) };
  const triggerBody = `${mention(r, inputs.triggerPhrase)}Please help ${canary("trigger.body", "actor")}`;
  switch (e.name) {
    case "issues": {
      const action = e.action ?? "opened";
      const opened = action === "opened";
      const issue = issueObject(r, inputs.triggerPhrase, opened);
      if (opened) { issue.created_at = TRIGGER_TIME; issue.user = user(LOGIN[r.issueAuthor]); }
      const out: Record<string, unknown> = { action, issue, ...base };
      if (action === "labeled") {
        const label = { name: e.label ?? inputs.labelTrigger };
        issue.labels = [label];
        out.label = label;
      }
      if (action === "assigned") out.assignee = user(e.assignee ?? inputs.assigneeTrigger.replace(/^@/, ""));
      return out;
    }
    case "issue_comment":
      return {
        action: e.action ?? "created", issue: issueObject(r, inputs.triggerPhrase, false),
        comment: { id: TRIGGER_COMMENT_ID, body: triggerBody, user: actorUser(r), created_at: TRIGGER_TIME, updated_at: TRIGGER_TIME,
          author_association: associationOf(r), html_url: `https://github.com/${r.owner}/${r.repo}/issues/${ISSUE_NUMBER}#issuecomment-${TRIGGER_COMMENT_ID}` },
        ...base,
      };
    case "pull_request":
    case "pull_request_target":
      return { action: e.action ?? "opened", number: ISSUE_NUMBER, pull_request: prObject(r, inputs.triggerPhrase, true), ...base };
    case "pull_request_review":
      return {
        action: e.action ?? "submitted", pull_request: prObject(r, inputs.triggerPhrase, false),
        review: { id: 8800, body: triggerBody, user: actorUser(r), state: "commented", submitted_at: TRIGGER_TIME, author_association: associationOf(r) },
        ...base,
      };
    case "pull_request_review_comment":
      return {
        action: e.action ?? "created", pull_request: prObject(r, inputs.triggerPhrase, false),
        comment: { id: TRIGGER_COMMENT_ID, body: triggerBody, user: actorUser(r), created_at: TRIGGER_TIME, updated_at: TRIGGER_TIME,
          path: "CLAUDE.md", diff_hunk: "@@ -1 +1 @@", author_association: associationOf(r) },
        ...base,
      };
    case "workflow_dispatch":
      return { inputs: e.inputs ?? {}, ref: `refs/heads/${r.defaultBranch}`, workflow: ".github/workflows/claude.yml", ...base };
    case "repository_dispatch":
      return { action: e.action ?? "custom", client_payload: e.client_payload ?? { text: canary("dispatch.client_payload", "actor") }, ...base };
    case "schedule":
      return { schedule: "0 0 * * *", repository: repository(r) };
    case "workflow_run":
      return {
        action: e.action ?? "completed", ...base,
        workflow_run: {
          id: 99, name: "CI", event: "pull_request", status: "completed", conclusion: "failure",
          head_branch: r.headRef, head_sha: HEAD_SHA, display_title: `PR title ${canary("pr.title", r.prAuthor)}`,
          actor: user(LOGIN[r.prAuthor]), triggering_actor: user(LOGIN[r.prAuthor]),
          head_repository: r.fork ? { full_name: `${LOGIN[r.prAuthor]}/${r.repo}` } : repository(r),
          pull_requests: r.fork ? [] : [{ number: ISSUE_NUMBER, head: { ref: r.headRef, sha: HEAD_SHA }, base: { ref: r.defaultBranch, sha: BASE_SHA } }],
        },
      };
    case "push":
      return { ref: `refs/heads/${r.defaultBranch}`, after: BASE_SHA, before: BASE_SHA, commits: [], head_commit: null, pusher: { name: r.s.actor.login }, ...base };
    default:
      throw new Error(`unsupported event ${e.name}`);
  }
}

function associationOf(r: Resolved): string {
  const p = r.s.actor.permission;
  return p === "admin" ? "OWNER" : ["maintain", "write"].includes(p) ? "MEMBER" : "NONE";
}

/** Comments that existed before the trigger, plus the trigger comment itself (which the action filters by time). */
function commentNodes(r: Resolved, includeTrigger: boolean) {
  const nodes = r.commentAuthors.map((role, i) => ({
    id: `IC_${i}`, databaseId: 5000 + i, body: `Earlier comment ${canary("comment.body", role)}`,
    author: { __typename: "User", login: LOGIN[role] }, createdAt: at(10 + i), updatedAt: at(10 + i), lastEditedAt: null, isMinimized: false,
  }));
  if (includeTrigger && r.s.event.name === "issue_comment") {
    nodes.push({
      id: "IC_trigger", databaseId: TRIGGER_COMMENT_ID, body: "trigger comment (served by the API)",
      author: { __typename: "User", login: r.s.actor.login }, createdAt: TRIGGER_TIME, updatedAt: TRIGGER_TIME, lastEditedAt: null, isMinimized: false,
    });
  }
  return nodes;
}

export function graphqlIssue(r: Resolved, payloadIssue: { title: string; body: string }) {
  return {
    title: payloadIssue.title, body: payloadIssue.body, author: { __typename: "User", login: LOGIN[r.issueAuthor] },
    createdAt: at(0), updatedAt: TRIGGER_TIME, lastEditedAt: null, state: "OPEN",
    labels: { nodes: [] }, comments: { nodes: commentNodes(r, true) },
  };
}

export function graphqlPullRequest(r: Resolved) {
  const pr = prObject(r, "@claude", false);
  return {
    title: pr.title, body: pr.body, author: { __typename: "User", login: LOGIN[r.prAuthor] },
    baseRefName: r.defaultBranch, headRefName: r.headRef, headRefOid: HEAD_SHA, isCrossRepository: r.fork,
    headRepository: { owner: { login: r.fork ? LOGIN[r.prAuthor] : r.owner }, name: r.repo },
    createdAt: at(0), updatedAt: TRIGGER_TIME, lastEditedAt: null, additions: 1, deletions: 1, state: "OPEN",
    labels: { nodes: [] },
    commits: { totalCount: 1, nodes: [{ commit: { oid: HEAD_SHA, message: `Commit ${canary("commit.message", r.prAuthor)}`, author: { name: LOGIN[r.prAuthor], email: `${LOGIN[r.prAuthor]}@example.invalid` } } }] },
    files: { nodes: [{ path: "CLAUDE.md", additions: 1, deletions: 1, changeType: "MODIFIED" }] },
    comments: { nodes: commentNodes(r, true) },
    reviews: {
      nodes: r.reviewAuthors.map((role, i) => ({
        id: `R_${i}`, databaseId: 6000 + i, author: { __typename: "User", login: LOGIN[role] },
        body: `Review ${canary("review.body", role)}`, state: "COMMENTED", submittedAt: at(20 + i), updatedAt: at(20 + i), lastEditedAt: null,
        comments: { nodes: [{
          id: `RC_${i}`, databaseId: 6100 + i, body: `Inline ${canary("review_comment.body", role)}`, path: "CLAUDE.md", line: 1,
          diffHunk: "@@ -1 +1 @@", author: { __typename: "User", login: LOGIN[role] }, createdAt: at(20 + i), updatedAt: at(20 + i), lastEditedAt: null, isMinimized: false,
        }] },
      })),
    },
  };
}
