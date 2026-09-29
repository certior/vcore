/** Scenario and Observation, as in bench/schema/{scenario,observation}.schema.json. */

export type Role = "maintainer" | "outsider";
export type Permission = "admin" | "maintain" | "write" | "triage" | "read" | "none";

export type Scenario = {
  id: string;
  description?: string;
  workflow: string;
  job?: string;
  step?: number;
  event: {
    name: string;
    action?: string | null;
    mention?: boolean;
    label?: string;
    assignee?: string;
    inputs?: Record<string, string | boolean | number>;
    client_payload?: Record<string, unknown>;
  };
  actor: { login: string; permission: Permission; type?: "User" | "Bot" };
  content?: {
    target?: "issue" | "pr";
    issue_author?: Role;
    comment_authors?: Role[];
    pr_author?: Role;
    pr_head?: "same-repo" | "fork";
    review_authors?: Role[];
  };
  repo?: {
    owner?: string;
    name?: string;
    visibility?: "public" | "private";
    default_branch?: string;
    default_token_permissions?: "restricted" | "permissive";
  };
  secrets?: string[];
  vars?: Record<string, string>;
};

export type Observation = {
  id: string;
  action_commit: string;
  runs: boolean;
  stop_reason: string | null;
  mode: "tag" | "agent" | null;
  allowed_tools: string[];
  disallowed_tools: string[];
  permission_mode: string | null;
  mcp_servers: string[];
  credentials: { token_in_git_config: boolean; credential_helper: boolean; claude_env: string[] };
  env_scrub: boolean;
  prompt_canaries: string[];
  extra?: Record<string, unknown>;
};

export const ACTION_COMMIT = "bf38e86e58df9ebf3420326d019f955bb3be64dd";

/** Placeholder credential values. Every credential the oracle hands out matches CREDENTIAL. */
export const TOKENS = {
  workflow: "ghs_ORACLEWORKFLOWTOKEN000000000000000",
  app: "ghs_ORACLEAPPTOKEN0000000000000000000000",
  oidc: "eyJORACLE.OIDC.JWT",
  oidcRequest: "ORACLEOIDCREQUESTTOKEN",
};
/** Secrets named like private keys get a PEM frame, since the action checks the format of ssh_signing_key. */
export const secretValue = (name: string) => {
  const v = `ORACLESECRET_${name.toUpperCase()}_0000`;
  return /SSH|PRIVATE_KEY|SIGNING_KEY/i.test(name) ? `-----BEGIN OPENSSH PRIVATE KEY-----\n${v}\n-----END OPENSSH PRIVATE KEY-----\n` : v;
};
export const CREDENTIAL = /ghs_ORACLE|ORACLESECRET_|ORACLE\.OIDC|ORACLEOIDCREQUESTTOKEN/;
