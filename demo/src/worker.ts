import {
  DEFAULT_POLICY,
  MemoryStore,
  SEED_GRAPH,
  SEED_SYMBOLS,
  blastRadius,
  routeJob,
  searchSymbols,
  triagePullRequest,
  upsertSymbol,
  type CIJobRequest,
  type PullRequestEvent,
  type SymbolIndexEntry,
} from "./logic";
import { PLAYGROUND_HTML } from "./page";

const store = new MemoryStore(DEFAULT_POLICY, SEED_GRAPH, SEED_SYMBOLS);

const SECURITY: Record<string, string> = {
  "content-security-policy":
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; connect-src 'self'; base-uri 'none'; form-action 'none'",
  "x-content-type-options": "nosniff",
  "referrer-policy": "no-referrer",
  "x-frame-options": "DENY",
};

const ROBOTS = "User-agent: *\nAllow: /\n";
const FAVICON =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" fill="#f3efe4"/><rect x="6" y="14" width="20" height="4" fill="#9d2c14"/><rect x="14" y="6" width="4" height="20" fill="#1d1a16"/></svg>';

function send(request: Request, body: string | null, init: ResponseInit): Response {
  const headers = { ...SECURITY, ...(init.headers as Record<string, string> | undefined) };
  const payload = request.method === "HEAD" ? null : body;
  return new Response(payload, { ...init, headers });
}

function json(request: Request, data: unknown, status = 200): Response {
  return send(request, JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

async function readJson(request: Request): Promise<unknown> {
  const text = await request.text();
  if (!text) return {};
  return JSON.parse(text) as unknown;
}

export default {
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;
    const method = request.method;

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
        return json(request, { status: "ok", version: "0.1.0", mode: "prototype", storage: "in-memory" });
      }
      if (path === "/policy") {
        return json(request, store.policy);
      }
      if (path === "/ci/queue") {
        return json(request, { queue_depth: store.queue.length, jobs: store.queue });
      }
      if (path === "/index/search") {
        const q = url.searchParams.get("q") || "";
        const matches = searchSymbols(store.symbols, q);
        return json(request, { query: q, count: matches.length, matches });
      }
      if (path.startsWith("/graph/impact/")) {
        const repo = decodeURIComponent(path.slice("/graph/impact/".length));
        return json(request, blastRadius(store.graph, repo));
      }
      return send(request, "Not found", {
        status: 404,
        headers: { "content-type": "text/plain; charset=utf-8" },
      });
    }

    if (method === "POST") {
      try {
        if (path === "/triage/pr") {
          const body = (await readJson(request)) as PullRequestEvent;
          if (!body || !body.repo || body.pr_number == null || !body.title) {
            return json(request, { detail: "repo, pr_number, author, and title are required" }, 422);
          }
          return json(request, triagePullRequest(body, store.policy));
        }
        if (path === "/ci/enqueue") {
          const job = (await readJson(request)) as CIJobRequest;
          if (!job?.job_id || !job.repo || !job.branch) {
            return json(request, { detail: "job_id, repo, and branch are required" }, 422);
          }
          const [routed_pool, route_reason] = routeJob(job);
          const queued = {
            job_id: job.job_id,
            repo: job.repo,
            branch: job.branch,
            priority: clampPriority(job.priority),
            routed_pool,
            route_reason,
            enqueue_seq: store.nextSeq(),
            enqueued_at: new Date().toISOString(),
          };
          const [, depth] = store.enqueue(queued);
          return json(request, { queued, queue_depth: depth });
        }
        if (path === "/index/symbol") {
          const entry = (await readJson(request)) as SymbolIndexEntry;
          if (!entry?.repo || !entry.symbol || !entry.file_path) {
            return json(request, { detail: "repo, symbol, and file_path are required" }, 422);
          }
          const total = upsertSymbol(store.symbols, {
            ...entry,
            owners: entry.owners || [],
            related_prs: entry.related_prs || [],
          });
          return json(request, { status: "indexed", total_symbols: total });
        }
        return send(request, "Not found", {
          status: 404,
          headers: { "content-type": "text/plain; charset=utf-8" },
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : "Invalid request";
        return json(request, { detail: message }, 400);
      }
    }

    return send(request, "Method not allowed", {
      status: 405,
      headers: { "content-type": "text/plain; charset=utf-8", allow: "GET, HEAD, POST" },
    });
  },
};

function clampPriority(value: number | undefined): number {
  const priority = Number.isFinite(value) ? Math.trunc(value as number) : 50;
  return Math.min(100, Math.max(1, priority || 50));
}
