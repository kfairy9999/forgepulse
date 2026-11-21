import {
  decryptString,
  encryptString,
  hashToken,
  randomId,
  randomToken,
} from "./crypto";
import type { PolicyRule, QueuedJob, SymbolIndexEntry } from "./logic";
import { DEFAULT_POLICY, SEED_GRAPH, SEED_SYMBOLS } from "./logic";

export interface Workspace {
  id: string;
  name: string;
  kind: "guest" | "user";
  token_hash: string;
  webhook_secret_enc: string;
  github_token_enc: string | null;
  created_at: string;
  last_seen_at: string;
}

export interface CreatedWorkspace {
  workspace: { id: string; name: string; kind: "guest" | "user"; created_at: string };
  token: string;
  webhook_secret: string;
  webhook_url: string;
}

function nowIso(): string {
  return new Date().toISOString();
}

export async function rateLimit(
  db: D1Database,
  key: string,
  limit: number,
  windowSec: number,
): Promise<boolean> {
  const now = Math.floor(Date.now() / 1000);
  const row = await db
    .prepare("SELECT window_start, count FROM rate_limits WHERE key = ?")
    .bind(key)
    .first<{ window_start: number; count: number }>();
  if (!row || now - Number(row.window_start) >= windowSec) {
    await db
      .prepare("INSERT OR REPLACE INTO rate_limits (key, window_start, count) VALUES (?, ?, 1)")
      .bind(key, now)
      .run();
    return true;
  }
  if (Number(row.count) >= limit) return false;
  await db.prepare("UPDATE rate_limits SET count = count + 1 WHERE key = ?").bind(key).run();
  return true;
}

export async function createWorkspace(
  db: D1Database,
  master: string,
  opts: { name: string; kind: "guest" | "user"; origin: string },
): Promise<CreatedWorkspace> {
  const id = randomId("ws_");
  const token = randomToken("fp_live_");
  const webhookSecret = randomToken("whsec_");
  const tokenHash = await hashToken(master, token);
  const secretEnc = await encryptString(master, webhookSecret);
  const created = nowIso();
  await db
    .prepare(
      "INSERT INTO workspaces (id, name, kind, token_hash, webhook_secret_enc, github_token_enc, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
    )
    .bind(id, opts.name.slice(0, 80), opts.kind, tokenHash, secretEnc, created, created)
    .run();
  await seedWorkspace(db, id, created);
  return {
    workspace: { id, name: opts.name.slice(0, 80), kind: opts.kind, created_at: created },
    token,
    webhook_secret: webhookSecret,
    webhook_url: `${opts.origin}/w/${id}/webhooks/github`,
  };
}

export async function seedWorkspace(db: D1Database, workspaceId: string, at: string): Promise<void> {
  const policy = DEFAULT_POLICY;
  await db
    .prepare(
      "INSERT OR REPLACE INTO policies (workspace_id, rule_id, require_tests_above_lines, protected_paths, allow_unverified_ai_prs, default_reviewers, protected_reviewers, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
    )
    .bind(
      workspaceId,
      policy.rule_id,
      policy.require_tests_above_lines,
      JSON.stringify(policy.protected_paths),
      policy.allow_unverified_ai_prs ? 1 : 0,
      JSON.stringify(policy.default_reviewers),
      JSON.stringify(policy.protected_reviewers),
      at,
    )
    .run();
  await db.prepare("INSERT OR REPLACE INTO ci_seq (workspace_id, next_seq) VALUES (?, 0)").bind(workspaceId).run();
  const edgeStmts = [];
  for (const [source, dests] of Object.entries(SEED_GRAPH)) {
    for (const dest of dests) {
      edgeStmts.push(
        db
          .prepare("INSERT OR IGNORE INTO graph_edges (workspace_id, source_repo, dest_repo) VALUES (?, ?, ?)")
          .bind(workspaceId, source, dest),
      );
    }
  }
  const symbolStmts = SEED_SYMBOLS.map((item) =>
    db
      .prepare(
        "INSERT OR IGNORE INTO symbols (workspace_id, repo, symbol, file_path, owners, related_prs) VALUES (?, ?, ?, ?, ?, ?)",
      )
      .bind(
        workspaceId,
        item.repo,
        item.symbol,
        item.file_path,
        JSON.stringify(item.owners),
        JSON.stringify(item.related_prs),
      ),
  );
  if (edgeStmts.length + symbolStmts.length) await db.batch([...edgeStmts, ...symbolStmts]);
}

export async function getWorkspaceById(db: D1Database, id: string): Promise<Workspace | null> {
  const row = await db.prepare("SELECT * FROM workspaces WHERE id = ?").bind(id).first<Workspace>();
  return row ?? null;
}

export async function getWorkspaceByToken(
  db: D1Database,
  master: string,
  token: string,
): Promise<Workspace | null> {
  const hash = await hashToken(master, token);
  const row = await db.prepare("SELECT * FROM workspaces WHERE token_hash = ?").bind(hash).first<Workspace>();
  if (row) {
    await db.prepare("UPDATE workspaces SET last_seen_at = ? WHERE id = ?").bind(nowIso(), row.id).run();
  }
  return row ?? null;
}

export async function rotateWebhookSecret(
  db: D1Database,
  master: string,
  workspaceId: string,
): Promise<string> {
  const secret = randomToken("whsec_");
  const enc = await encryptString(master, secret);
  await db.prepare("UPDATE workspaces SET webhook_secret_enc = ? WHERE id = ?").bind(enc, workspaceId).run();
  return secret;
}

export async function setGithubToken(
  db: D1Database,
  master: string,
  workspaceId: string,
  token: string | null,
): Promise<void> {
  const enc = token ? await encryptString(master, token) : null;
  await db.prepare("UPDATE workspaces SET github_token_enc = ? WHERE id = ?").bind(enc, workspaceId).run();
}

export async function decryptGithubToken(master: string, workspace: Workspace): Promise<string | null> {
  if (!workspace.github_token_enc) return null;
  return decryptString(master, workspace.github_token_enc);
}

export async function decryptWebhookSecret(master: string, workspace: Workspace): Promise<string> {
  return decryptString(master, workspace.webhook_secret_enc);
}

export async function loadPolicy(db: D1Database, workspaceId: string): Promise<PolicyRule> {
  const row = await db
    .prepare("SELECT * FROM policies WHERE workspace_id = ?")
    .bind(workspaceId)
    .first<{
      rule_id: string;
      require_tests_above_lines: number;
      protected_paths: string;
      allow_unverified_ai_prs: number;
      default_reviewers: string;
      protected_reviewers: string;
    }>();
  if (!row) return { ...DEFAULT_POLICY, protected_paths: [...DEFAULT_POLICY.protected_paths] };
  return {
    rule_id: row.rule_id,
    require_tests_above_lines: Number(row.require_tests_above_lines),
    protected_paths: JSON.parse(row.protected_paths) as string[],
    allow_unverified_ai_prs: Boolean(row.allow_unverified_ai_prs),
    default_reviewers: JSON.parse(row.default_reviewers) as string[],
    protected_reviewers: JSON.parse(row.protected_reviewers) as string[],
  };
}

export async function savePolicy(db: D1Database, workspaceId: string, policy: PolicyRule): Promise<void> {
  await db
    .prepare(
      "INSERT OR REPLACE INTO policies (workspace_id, rule_id, require_tests_above_lines, protected_paths, allow_unverified_ai_prs, default_reviewers, protected_reviewers, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
    )
    .bind(
      workspaceId,
      policy.rule_id.slice(0, 80),
      policy.require_tests_above_lines,
      JSON.stringify(policy.protected_paths),
      policy.allow_unverified_ai_prs ? 1 : 0,
      JSON.stringify(policy.default_reviewers),
      JSON.stringify(policy.protected_reviewers),
      nowIso(),
    )
    .run();
}

export async function loadGraph(db: D1Database, workspaceId: string): Promise<Record<string, string[]>> {
  const { results } = await db
    .prepare("SELECT source_repo, dest_repo FROM graph_edges WHERE workspace_id = ?")
    .bind(workspaceId)
    .all<{ source_repo: string; dest_repo: string }>();
  const graph: Record<string, string[]> = {};
  for (const row of results || []) {
    graph[row.source_repo] = graph[row.source_repo] || [];
    graph[row.source_repo].push(row.dest_repo);
  }
  return graph;
}

export async function addEdge(db: D1Database, workspaceId: string, source: string, dest: string): Promise<void> {
  await db
    .prepare("INSERT OR IGNORE INTO graph_edges (workspace_id, source_repo, dest_repo) VALUES (?, ?, ?)")
    .bind(workspaceId, source.slice(0, 200), dest.slice(0, 200))
    .run();
}

export async function removeEdge(db: D1Database, workspaceId: string, source: string, dest: string): Promise<void> {
  await db
    .prepare("DELETE FROM graph_edges WHERE workspace_id = ? AND source_repo = ? AND dest_repo = ?")
    .bind(workspaceId, source, dest)
    .run();
}

export async function searchSymbols(
  db: D1Database,
  workspaceId: string,
  query: string,
): Promise<SymbolIndexEntry[]> {
  const { results } = await db
    .prepare("SELECT repo, symbol, file_path, owners, related_prs FROM symbols WHERE workspace_id = ?")
    .bind(workspaceId)
    .all<{ repo: string; symbol: string; file_path: string; owners: string; related_prs: string }>();
  const needle = query.toLowerCase().trim();
  const items: SymbolIndexEntry[] = (results || []).map((row) => ({
    repo: row.repo,
    symbol: row.symbol,
    file_path: row.file_path,
    owners: JSON.parse(row.owners) as string[],
    related_prs: JSON.parse(row.related_prs) as number[],
  }));
  if (!needle) return items;
  return items.filter(
    (item) =>
      item.symbol.toLowerCase().includes(needle) ||
      item.file_path.toLowerCase().includes(needle) ||
      item.owners.some((owner) => owner.toLowerCase().includes(needle)) ||
      item.repo.toLowerCase().includes(needle),
  );
}

export async function upsertSymbol(db: D1Database, workspaceId: string, entry: SymbolIndexEntry): Promise<number> {
  const existing = await db
    .prepare(
      "SELECT owners, related_prs FROM symbols WHERE workspace_id = ? AND repo = ? AND symbol = ? AND file_path = ?",
    )
    .bind(workspaceId, entry.repo, entry.symbol, entry.file_path)
    .first<{ owners: string; related_prs: string }>();
  let related = entry.related_prs || [];
  if (existing) {
    const prior = JSON.parse(existing.related_prs) as number[];
    related = [...new Set([...prior, ...related])];
  }
  await db
    .prepare(
      "INSERT OR REPLACE INTO symbols (workspace_id, repo, symbol, file_path, owners, related_prs) VALUES (?, ?, ?, ?, ?, ?)",
    )
    .bind(
      workspaceId,
      entry.repo.slice(0, 200),
      entry.symbol.slice(0, 200),
      entry.file_path.slice(0, 400),
      JSON.stringify((entry.owners || []).slice(0, 20)),
      JSON.stringify(related.slice(0, 50)),
    )
    .run();
  const count = await db
    .prepare("SELECT COUNT(*) AS n FROM symbols WHERE workspace_id = ?")
    .bind(workspaceId)
    .first<{ n: number }>();
  return Number(count?.n || 0);
}

export async function enqueueJob(db: D1Database, workspaceId: string, job: Omit<QueuedJob, "enqueue_seq" | "enqueued_at">): Promise<QueuedJob> {
  await db
    .prepare("INSERT OR IGNORE INTO ci_seq (workspace_id, next_seq) VALUES (?, 0)")
    .bind(workspaceId)
    .run();
  await db.prepare("UPDATE ci_seq SET next_seq = next_seq + 1 WHERE workspace_id = ?").bind(workspaceId).run();
  const seqRow = await db
    .prepare("SELECT next_seq FROM ci_seq WHERE workspace_id = ?")
    .bind(workspaceId)
    .first<{ next_seq: number }>();
  const seq = Number(seqRow?.next_seq || 1);
  const enqueued_at = nowIso();
  await db
    .prepare(
      "INSERT INTO ci_jobs (workspace_id, enqueue_seq, job_id, repo, branch, priority, routed_pool, route_reason, enqueued_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
    )
    .bind(
      workspaceId,
      seq,
      job.job_id.slice(0, 120),
      job.repo.slice(0, 200),
      job.branch.slice(0, 200),
      job.priority,
      job.routed_pool,
      job.route_reason.slice(0, 300),
      enqueued_at,
    )
    .run();
  return { ...job, enqueue_seq: seq, enqueued_at };
}

export async function listQueue(db: D1Database, workspaceId: string): Promise<QueuedJob[]> {
  const { results } = await db
    .prepare(
      "SELECT job_id, repo, branch, priority, routed_pool, route_reason, enqueue_seq, enqueued_at FROM ci_jobs WHERE workspace_id = ? ORDER BY priority DESC, enqueue_seq ASC LIMIT 200",
    )
    .bind(workspaceId)
    .all<QueuedJob>();
  return results || [];
}

export async function recordTriage(
  db: D1Database,
  workspaceId: string,
  event: {
    repo: string;
    pr_number: number;
    author?: string;
    action: string;
    signal_score: number;
    has_tests: boolean | null;
    has_tests_source: string;
    reasons: string[];
    source: "api" | "webhook";
  },
): Promise<void> {
  await db
    .prepare(
      "INSERT INTO triage_events (id, workspace_id, created_at, repo, pr_number, author, action, signal_score, has_tests, has_tests_source, reasons, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    )
    .bind(
      randomId("tr_"),
      workspaceId,
      nowIso(),
      event.repo,
      event.pr_number,
      event.author || null,
      event.action,
      event.signal_score,
      event.has_tests == null ? null : event.has_tests ? 1 : 0,
      event.has_tests_source,
      JSON.stringify(event.reasons),
      event.source,
    )
    .run();
}

export async function listTriage(db: D1Database, workspaceId: string): Promise<Record<string, unknown>[]> {
  const { results } = await db
    .prepare(
      "SELECT id, created_at, repo, pr_number, author, action, signal_score, has_tests, has_tests_source, reasons, source FROM triage_events WHERE workspace_id = ? ORDER BY created_at DESC LIMIT 50",
    )
    .bind(workspaceId)
    .all<{
      id: string;
      created_at: string;
      repo: string;
      pr_number: number;
      author: string | null;
      action: string;
      signal_score: number;
      has_tests: number | null;
      has_tests_source: string;
      reasons: string;
      source: string;
    }>();
  return (results || []).map((row) => ({
    ...row,
    has_tests: row.has_tests == null ? null : Boolean(row.has_tests),
    reasons: JSON.parse(row.reasons) as string[],
  }));
}

export async function recordWebhook(
  db: D1Database,
  workspaceId: string,
  event: { github_event?: string; delivery_id?: string; status: string; detail?: string },
): Promise<void> {
  await db
    .prepare(
      "INSERT INTO webhook_events (id, workspace_id, created_at, github_event, delivery_id, status, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
    )
    .bind(
      randomId("wh_"),
      workspaceId,
      nowIso(),
      event.github_event || null,
      event.delivery_id || null,
      event.status,
      event.detail || null,
    )
    .run();
}

export async function listWebhooks(db: D1Database, workspaceId: string): Promise<Record<string, unknown>[]> {
  const { results } = await db
    .prepare(
      "SELECT id, created_at, github_event, delivery_id, status, detail FROM webhook_events WHERE workspace_id = ? ORDER BY created_at DESC LIMIT 50",
    )
    .bind(workspaceId)
    .all();
  return (results || []) as Record<string, unknown>[];
}

export function publicWorkspace(ws: Workspace, origin: string, githubConfigured: boolean) {
  return {
    id: ws.id,
    name: ws.name,
    kind: ws.kind,
    created_at: ws.created_at,
    webhook_url: `${origin}/w/${ws.id}/webhooks/github`,
    github_token_configured: githubConfigured,
    storage: "d1",
  };
}
