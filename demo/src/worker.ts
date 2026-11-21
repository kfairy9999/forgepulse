import {
  addEdge,
  createWorkspace,
  decryptGithubToken,
  decryptWebhookSecret,
  getWorkspaceById,
  getWorkspaceByToken,
  listQueue,
  listTriage,
  listWebhooks,
  loadGraph,
  loadPolicy,
  enqueueJob,
  publicWorkspace,
  rateLimit,
  recordTriage,
  recordWebhook,
  removeEdge,
  rotateWebhookSecret,
  savePolicy,
  searchSymbols,
  setGithubToken,
  upsertSymbol,
  type Workspace,
} from "./db";
import { enrichFromGitHub } from "./github";
import {
  DEFAULT_POLICY,
  blastRadius,
  extractFilenames,
  inferAiAssisted,
  inferHasTests,
  protectedHits,
  routeJob,
  triagePullRequest,
  type CIJobRequest,
  type PolicyRule,
  type PullRequestEvent,
  type SymbolIndexEntry,
} from "./logic";
import { hmacSha256Hex, timingSafeEqualHex } from "./crypto";
import { PLAYGROUND_HTML } from "./page";

export interface Env {
  DB: D1Database;
  FORGEPULSE_MASTER_KEY: string;
}

const MAX_BODY = 256_000;
const VERSION = "0.2.0";

const SECURITY: Record<string, string> = {
  "content-security-policy":
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; connect-src 'self'; base-uri 'none'; form-action 'none'",
  "x-content-type-options": "nosniff",
  "referrer-policy": "no-referrer",
  "x-frame-options": "DENY",
  "permissions-policy": "camera=(), microphone=(), geolocation=()",
};

const ROBOTS = "User-agent: *\nAllow: /\n";
const FAVICON =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" fill="#f3efe4"/><rect x="6" y="14" width="20" height="4" fill="#9d2c14"/><rect x="14" y="6" width="4" height="20" fill="#1d1a16"/></svg>';

function originOf(request: Request): string {
  const url = new URL(request.url);
  return `${url.protocol}//${url.host}`;
}

function clientIp(request: Request): string {
  return request.headers.get("cf-connecting-ip") || request.headers.get("x-forwarded-for") || "unknown";
}

function corsHeaders(request: Request): Record<string, string> {
  const origin = request.headers.get("origin") || "";
  const url = new URL(request.url);
  const allowed =
    origin === `${url.protocol}//${url.host}` ||
    /^https:\/\/[a-z0-9-]+\.kfairley9999\.workers\.dev$/.test(origin) ||
    origin.startsWith("http://127.0.0.1:") ||
    origin.startsWith("http://localhost:");
  if (!allowed) return {};
  return {
    "access-control-allow-origin": origin,
    "access-control-allow-methods": "GET, HEAD, POST, PUT, DELETE, OPTIONS",
    "access-control-allow-headers": "authorization, content-type, x-forgepulse-token, x-github-event, x-hub-signature-256, x-github-delivery",
    "access-control-max-age": "600",
    vary: "origin",
  };
}

function send(request: Request, body: string | null, init: ResponseInit): Response {
  const headers = { ...SECURITY, ...corsHeaders(request), ...(init.headers as Record<string, string> | undefined) };
  return new Response(request.method === "HEAD" ? null : body, { ...init, headers });
}

function json(request: Request, data: unknown, status = 200): Response {
  return send(request, JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

function error(request: Request, status: number, detail: string): Response {
  return json(request, { detail }, status);
}

async function readBody(request: Request): Promise<{ bytes: Uint8Array; text: string }> {
  const length = Number(request.headers.get("content-length") || "0");
  if (length > MAX_BODY) throw new HttpError(413, "Request body is too large");
  const buf = new Uint8Array(await request.arrayBuffer());
  if (buf.byteLength > MAX_BODY) throw new HttpError(413, "Request body is too large");
  return { bytes: buf, text: new TextDecoder().decode(buf) };
}

async function readJson(request: Request): Promise<unknown> {
  const { text } = await readBody(request);
  if (!text) return {};
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new HttpError(400, "Request body is not valid JSON");
  }
}

class HttpError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function requireMaster(env: Env): string {
  const key = (env.FORGEPULSE_MASTER_KEY || "").trim();
  if (!/^[0-9a-fA-F]{64}$/.test(key)) {
    throw new HttpError(500, "Server is missing FORGEPULSE_MASTER_KEY");
  }
  return key;
}

function bearerToken(request: Request): string | null {
  const header = request.headers.get("authorization") || "";
  if (header.toLowerCase().startsWith("bearer ")) return header.slice(7).trim() || null;
  return request.headers.get("x-forgepulse-token")?.trim() || null;
}

async function requireWorkspace(env: Env, request: Request): Promise<Workspace> {
  const token = bearerToken(request);
  if (!token) throw new HttpError(401, "Missing bearer token. Create a workspace first.");
  const ws = await getWorkspaceByToken(env.DB, requireMaster(env), token);
  if (!ws) throw new HttpError(401, "Invalid or expired token");
  return ws;
}

function asPolicy(raw: unknown): PolicyRule {
  if (!raw || typeof raw !== "object") throw new HttpError(422, "Policy must be an object");
  const body = raw as Record<string, unknown>;
  const rule_id = String(body.rule_id || "").trim();
  if (rule_id.length < 2 || rule_id.length > 80) throw new HttpError(422, "rule_id is required");
  const lines = Number(body.require_tests_above_lines);
  if (!Number.isInteger(lines) || lines < 1 || lines > 100000) {
    throw new HttpError(422, "require_tests_above_lines must be an integer from 1 to 100000");
  }
  const paths = Array.isArray(body.protected_paths) ? body.protected_paths : [];
  if (!paths.every((p) => typeof p === "string" && p.trim() && p.length < 200)) {
    throw new HttpError(422, "protected_paths must be an array of short strings");
  }
  const default_reviewers = Array.isArray(body.default_reviewers) ? body.default_reviewers : DEFAULT_POLICY.default_reviewers;
  const protected_reviewers = Array.isArray(body.protected_reviewers)
    ? body.protected_reviewers
    : DEFAULT_POLICY.protected_reviewers;
  if (![...default_reviewers, ...protected_reviewers].every((p) => typeof p === "string" && p.length < 80)) {
    throw new HttpError(422, "reviewers must be short strings");
  }
  return {
    rule_id,
    require_tests_above_lines: lines,
    protected_paths: (paths as string[]).map((p) => p.trim()).slice(0, 50),
    allow_unverified_ai_prs: Boolean(body.allow_unverified_ai_prs),
    default_reviewers: (default_reviewers as string[]).slice(0, 20),
    protected_reviewers: (protected_reviewers as string[]).slice(0, 20),
  };
}

function asPullRequest(raw: unknown): PullRequestEvent {
  if (!raw || typeof raw !== "object") throw new HttpError(422, "Pull request body is required");
  const body = raw as Record<string, unknown>;
  const repo = String(body.repo || "").trim();
  const title = String(body.title || "").trim();
  const author = String(body.author || "").trim();
  const pr_number = Number(body.pr_number);
  if (!repo || !title || !author || !Number.isInteger(pr_number) || pr_number < 1) {
    throw new HttpError(422, "repo, pr_number, author, and title are required");
  }
  return {
    repo: repo.slice(0, 200),
    pr_number,
    author: author.slice(0, 100),
    author_merged_prs: body.author_merged_prs == null ? null : Number(body.author_merged_prs),
    title: title.slice(0, 300),
    body: String(body.body || "").slice(0, 4000),
    files_changed: body.files_changed == null ? 1 : Number(body.files_changed) || 1,
    lines_added: Number(body.lines_added) || 0,
    lines_deleted: Number(body.lines_deleted) || 0,
    has_tests: typeof body.has_tests === "boolean" ? body.has_tests : null,
    has_tests_source: body.has_tests_source === "explicit" || body.has_tests_source === "inferred" ? body.has_tests_source : "unknown",
    ai_assisted: typeof body.ai_assisted === "boolean" ? body.ai_assisted : null,
    touches_critical_paths: Array.isArray(body.touches_critical_paths)
      ? (body.touches_critical_paths as unknown[]).filter((p): p is string => typeof p === "string").slice(0, 50)
      : [],
    changed_paths: Array.isArray(body.changed_paths)
      ? (body.changed_paths as unknown[]).filter((p): p is string => typeof p === "string").slice(0, 200)
      : [],
  };
}

// Re-export helpers that the previous logic.ts may not have if we need extractFilenames there.
// They live in infer on the Python side; the Worker logic.ts may need these exports. Worker imports them from logic.

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    try {
      return await handle(request, env);
    } catch (err) {
      if (err instanceof HttpError) return error(request, err.status, err.message);
      const message = err instanceof Error ? err.message : "Internal error";
      return error(request, 500, message);
    }
  },
};

async function handle(request: Request, env: Env): Promise<Response> {
  if (!env.DB) return error(request, 500, "D1 database binding is missing");
  const url = new URL(request.url);
  const path = url.pathname;
  const method = request.method;

  if (method === "OPTIONS") {
    return send(request, null, { status: 204 });
  }

  if (method === "GET" || method === "HEAD") {
    if (path === "/robots.txt") {
      return send(request, ROBOTS, {
        headers: { "content-type": "text/plain; charset=utf-8", "cache-control": "public, max-age=86400" },
      });
    }
    if (path === "/favicon.svg" || path === "/favicon.ico") {
      return send(request, FAVICON, {
        headers: { "content-type": "image/svg+xml", "cache-control": "public, max-age=86400" },
      });
    }
    if (path === "/" || path === "") {
      return send(request, PLAYGROUND_HTML, {
        headers: { "content-type": "text/html; charset=utf-8", "cache-control": "public, max-age=60" },
      });
    }
    if (path === "/healthz") {
      let storage = "d1";
      try {
        await env.DB.prepare("SELECT 1 AS ok").first();
      } catch {
        storage = "d1-unreachable";
      }
      return json(request, {
        status: storage === "d1" ? "ok" : "degraded",
        version: VERSION,
        mode: "durable",
        storage,
      });
    }
  }

  if (path === "/workspaces" && method === "POST") {
    if (!(await rateLimit(env.DB, `ws:${clientIp(request)}`, 10, 3600))) {
      return error(request, 429, "Too many workspaces created from this address. Try again later.");
    }
    const body = (await readJson(request)) as { name?: string };
    const name = String(body.name || "My workspace").trim() || "My workspace";
    const created = await createWorkspace(env.DB, requireMaster(env), {
      name,
      kind: "user",
      origin: originOf(request),
    });
    return json(request, created, 201);
  }

  if (path === "/workspaces/guest" && method === "POST") {
    if (!(await rateLimit(env.DB, `guest:${clientIp(request)}`, 20, 3600))) {
      return error(request, 429, "Too many sample workspaces from this address. Try again later.");
    }
    const created = await createWorkspace(env.DB, requireMaster(env), {
      name: "Sample org (guest)",
      kind: "guest",
      origin: originOf(request),
    });
    return json(request, created, 201);
  }

  if (path.startsWith("/w/") && path.endsWith("/webhooks/github") && method === "POST") {
    return handleWebhook(request, env, path.split("/")[2] || "");
  }

  // Authenticated API
  const mutating = method !== "GET" && method !== "HEAD";
  if (mutating && !(await rateLimit(env.DB, `mut:${clientIp(request)}`, 60, 60))) {
    return error(request, 429, "Rate limit exceeded. Please wait a minute.");
  }

  const authNeeded = [
    "/workspace",
    "/policy",
    "/triage/pr",
    "/triage/history",
    "/ci/enqueue",
    "/ci/queue",
    "/graph",
    "/index/symbol",
    "/index/search",
    "/webhooks/events",
  ];
  const needsAuth =
    authNeeded.some((p) => path === p || path.startsWith(p + "/") || path.startsWith("/graph/"));
  if (needsAuth) {
    const ws = await requireWorkspace(env, request);
    return handleAuthed(request, env, ws, path, method, url);
  }

  return send(request, "Not found", {
    status: 404,
    headers: { "content-type": "text/plain; charset=utf-8" },
  });
}

async function handleAuthed(
  request: Request,
  env: Env,
  ws: Workspace,
  path: string,
  method: string,
  url: URL,
): Promise<Response> {
  const origin = originOf(request);
  const master = requireMaster(env);

  if (path === "/workspace" && method === "GET") {
    return json(request, publicWorkspace(ws, origin, Boolean(ws.github_token_enc)));
  }
  if (path === "/workspace" && method === "PATCH") {
    const body = (await readJson(request)) as { github_token?: string | null };
    if ("github_token" in body) {
      const token = body.github_token;
      if (token && (typeof token !== "string" || token.length < 8 || token.length > 200)) {
        return error(request, 422, "GitHub token looks invalid");
      }
      await setGithubToken(env.DB, master, ws.id, token ? token.trim() : null);
    }
    const fresh = await getWorkspaceById(env.DB, ws.id);
    return json(request, publicWorkspace(fresh || ws, origin, Boolean(fresh?.github_token_enc)));
  }
  if (path === "/workspace/rotate-webhook-secret" && method === "POST") {
    const secret = await rotateWebhookSecret(env.DB, master, ws.id);
    return json(request, { webhook_secret: secret, webhook_url: `${origin}/w/${ws.id}/webhooks/github` });
  }

  if (path === "/policy" && method === "GET") {
    return json(request, await loadPolicy(env.DB, ws.id));
  }
  if (path === "/policy" && (method === "PUT" || method === "POST")) {
    const policy = asPolicy(await readJson(request));
    await savePolicy(env.DB, ws.id, policy);
    return json(request, policy);
  }

  if (path === "/triage/pr" && method === "POST") {
    const pr = asPullRequest(await readJson(request));
    const policy = await loadPolicy(env.DB, ws.id);
    const decision = triagePullRequest(pr, policy);
    await recordTriage(env.DB, ws.id, {
      repo: pr.repo,
      pr_number: pr.pr_number,
      author: pr.author,
      action: decision.action,
      signal_score: decision.signal_score,
      has_tests: decision.has_tests,
      has_tests_source: decision.has_tests_source,
      reasons: decision.reasons,
      source: "api",
    });
    return json(request, decision);
  }
  if (path === "/triage/history" && method === "GET") {
    return json(request, { events: await listTriage(env.DB, ws.id) });
  }

  if (path === "/ci/enqueue" && method === "POST") {
    const body = (await readJson(request)) as CIJobRequest;
    if (!body?.job_id || !body.repo || !body.branch) {
      return error(request, 422, "job_id, repo, and branch are required");
    }
    const [routed_pool, route_reason] = routeJob(body);
    const queued = await enqueueJob(env.DB, ws.id, {
      job_id: String(body.job_id),
      repo: String(body.repo),
      branch: String(body.branch),
      priority: clampPriority(body.priority),
      routed_pool,
      route_reason,
    });
    const jobs = await listQueue(env.DB, ws.id);
    return json(request, { queued, queue_depth: jobs.length, disclaimer: "Routing decision only. ForgePulse does not launch runners." });
  }
  if (path === "/ci/queue" && method === "GET") {
    const jobs = await listQueue(env.DB, ws.id);
    return json(request, {
      queue_depth: jobs.length,
      jobs,
      disclaimer: "Routing decision only. ForgePulse does not launch runners.",
    });
  }

  if (path === "/graph" && method === "GET") {
    return json(request, { edges: await loadGraph(env.DB, ws.id) });
  }
  if (path === "/graph/edges" && method === "POST") {
    const body = (await readJson(request)) as { source?: string; dest?: string };
    const source = String(body.source || "").trim();
    const dest = String(body.dest || "").trim();
    if (!source || !dest) return error(request, 422, "source and dest repositories are required");
    await addEdge(env.DB, ws.id, source, dest);
    return json(request, { edges: await loadGraph(env.DB, ws.id) });
  }
  if (path === "/graph/edges" && method === "DELETE") {
    const body = (await readJson(request)) as { source?: string; dest?: string };
    await removeEdge(env.DB, ws.id, String(body.source || ""), String(body.dest || ""));
    return json(request, { edges: await loadGraph(env.DB, ws.id) });
  }
  if (path.startsWith("/graph/impact/") && method === "GET") {
    const repo = decodeURIComponent(path.slice("/graph/impact/".length));
    const graph = await loadGraph(env.DB, ws.id);
    return json(request, blastRadius(graph, repo));
  }

  if (path === "/index/symbol" && method === "POST") {
    const entry = (await readJson(request)) as SymbolIndexEntry;
    if (!entry?.repo || !entry.symbol || !entry.file_path) {
      return error(request, 422, "repo, symbol, and file_path are required");
    }
    const total = await upsertSymbol(env.DB, ws.id, {
      ...entry,
      owners: entry.owners || [],
      related_prs: entry.related_prs || [],
    });
    return json(request, { status: "indexed", total_symbols: total });
  }
  if (path === "/index/search" && method === "GET") {
    const q = url.searchParams.get("q") || "";
    const matches = await searchSymbols(env.DB, ws.id, q);
    return json(request, { query: q, count: matches.length, matches });
  }

  if (path === "/webhooks/events" && method === "GET") {
    return json(request, { events: await listWebhooks(env.DB, ws.id) });
  }

  return send(request, "Not found", {
    status: 404,
    headers: { "content-type": "text/plain; charset=utf-8" },
  });
}

async function handleWebhook(request: Request, env: Env, workspaceId: string): Promise<Response> {
  if (!(await rateLimit(env.DB, `hook:${workspaceId}`, 120, 60))) {
    return error(request, 429, "Webhook rate limit exceeded");
  }
  const ws = await getWorkspaceById(env.DB, workspaceId);
  if (!ws) return error(request, 404, "Unknown workspace");
  const master = requireMaster(env);
  const { bytes } = await readBody(request);
  const signature = request.headers.get("x-hub-signature-256") || "";
  const secret = await decryptWebhookSecret(master, ws);
  const expected = "sha256=" + (await hmacSha256Hex(secret, bytes));
  if (!signature.startsWith("sha256=") || !timingSafeEqualHex(expected, signature.trim())) {
    await recordWebhook(env.DB, ws.id, { status: "unauthorized", detail: "Invalid webhook signature" });
    return error(request, 401, "Invalid webhook signature");
  }
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(new TextDecoder().decode(bytes)) as Record<string, unknown>;
  } catch {
    await recordWebhook(env.DB, ws.id, { status: "error", detail: "Body is not valid JSON" });
    return error(request, 400, "Request body is not valid JSON");
  }
  const githubEvent = request.headers.get("x-github-event") || "unknown";
  const delivery = request.headers.get("x-github-delivery") || undefined;
  if (githubEvent !== "pull_request") {
    await recordWebhook(env.DB, ws.id, {
      github_event: githubEvent,
      delivery_id: delivery,
      status: "ignored",
      detail: `Event ${githubEvent} is ignored`,
    });
    return json(request, { event: githubEvent, status: "ignored" });
  }
  const prObj = payload.pull_request;
  if (!prObj || typeof prObj !== "object") {
    await recordWebhook(env.DB, ws.id, { github_event: githubEvent, delivery_id: delivery, status: "ignored", detail: "No pull_request object" });
    return json(request, { event: githubEvent, status: "ignored" });
  }
  const pr = prObj as Record<string, unknown>;
  const repoName =
    typeof (payload.repository as { full_name?: string } | undefined)?.full_name === "string"
      ? (payload.repository as { full_name: string }).full_name
      : "unknown/repo";
  const user = pr.user as { login?: string } | undefined;
  const author = typeof user?.login === "string" ? user.login : "unknown";
  const prNumber = Number(pr.number) || 0;
  let paths = extractFilenames(payload) || [];
  let merged =
    typeof pr.author_merged_prs === "number"
      ? (pr.author_merged_prs as number)
      : null;
  let fetchNote: string | undefined;
  const ghToken = await decryptGithubToken(master, ws);
  if (ghToken && repoName.includes("/")) {
    const enrich = await enrichFromGitHub(ghToken, repoName, prNumber, author);
    if (enrich.fetched) {
      if (enrich.paths.length) paths = enrich.paths;
      if (enrich.author_merged_prs != null) merged = enrich.author_merged_prs;
    } else {
      fetchNote = enrich.error;
    }
  } else if (!paths.length) {
    fetchNote = "No file list in the webhook, and no GitHub token is configured to fetch files.";
  }
  const [has_tests, source] = inferHasTests(paths.length ? paths : null);
  const policy = await loadPolicy(env.DB, ws.id);
  const event: PullRequestEvent = {
    repo: repoName,
    pr_number: prNumber,
    author,
    author_merged_prs: merged,
    title: String(pr.title || ""),
    body: String(pr.body || ""),
    files_changed: Number(pr.changed_files) || paths.length || 1,
    lines_added: Number(pr.additions) || 0,
    lines_deleted: Number(pr.deletions) || 0,
    has_tests,
    has_tests_source: source,
    ai_assisted: inferAiAssisted(payload, null),
    touches_critical_paths: protectedHits(paths, policy.protected_paths),
    changed_paths: paths,
  };
  const decision = triagePullRequest(event, policy);
  if (fetchNote) decision.reasons = [...decision.reasons, fetchNote];
  await recordTriage(env.DB, ws.id, {
    repo: event.repo,
    pr_number: event.pr_number,
    author: event.author,
    action: decision.action,
    signal_score: decision.signal_score,
    has_tests: decision.has_tests,
    has_tests_source: decision.has_tests_source,
    reasons: decision.reasons,
    source: "webhook",
  });
  await recordWebhook(env.DB, ws.id, {
    github_event: githubEvent,
    delivery_id: delivery,
    status: "accepted",
    detail: `${decision.action} on ${repoName}#${prNumber}`,
  });
  return json(request, { event: githubEvent, triage: decision, files_fetched: Boolean(ghToken && paths.length) });
}

function clampPriority(value: number | undefined): number {
  const priority = Number.isFinite(value) ? Math.trunc(value as number) : 50;
  return Math.min(100, Math.max(1, priority || 50));
}
