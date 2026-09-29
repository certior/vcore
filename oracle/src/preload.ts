/**
 * Loaded with `bun --preload` into the action's process. It does not touch the action's
 * code: it only routes network access. Requests to api.anthropic.com (the GitHub App
 * token exchange in src/github/token.ts) go to the mock; any other non-local fetch is
 * refused and logged, so a scenario can never reach the internet.
 */
const mock = process.env.ORACLE_MOCK_URL!;
const realFetch = globalThis.fetch;

function urlOf(input: unknown): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return (input as Request).url;
}

const patched = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = urlOf(input);
  if (url.startsWith("https://api.anthropic.com/")) {
    const target = `${mock}/_anthropic/${url.slice("https://api.anthropic.com/".length)}`;
    if (input instanceof Request) return realFetch(new Request(target, input), init);
    return realFetch(target, init);
  }
  if (url.startsWith(mock) || /^https?:\/\/(127\.0\.0\.1|localhost)[:/]/.test(url)) return realFetch(input as RequestInfo, init);
  console.error(`[oracle] blocked network request: ${url}`);
  throw new TypeError(`oracle: network access blocked (${url})`);
}) as typeof fetch;
globalThis.fetch = Object.assign(patched, realFetch);
