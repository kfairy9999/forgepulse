/** Core ForgePulse logic, ported from the Python package for the Worker demo. */

export type TriageAction =
  | "fast-track"
  | "standard-review"
  | "quarantine-bot-check"
  | "block-policy";

export type TestEvidence = "explicit" | "inferred" | "unknown";
export type RunnerPool = "ephemeral-cloud" | "bare-metal" | "fallback-alt";

export interface PolicyRule {
  rule_id: string;
  require_tests_above_lines: number;
  protected_paths: string[];
  allow_unverified_ai_prs: boolean;
  default_reviewers: string[];
  protected_reviewers: string[];
}

export interface PullRequestEvent {
  repo: string;
  pr_number: number;
  author: string;
  author_merged_prs?: number | null;
  title: string;
  body?: string;
  files_changed?: number;
  lines_added?: number;
  lines_deleted?: number;
  has_tests?: boolean | null;
  has_tests_source?: TestEvidence;
  ai_assisted?: boolean | null;
  touches_critical_paths?: string[];
  changed_paths?: string[];
}

export interface TriageDecision {
  pr_number: number;
  repo: string;
  signal_score: number;
  action: TriageAction;
  reasons: string[];
  suggested_reviewers: string[];
  has_tests: boolean | null;
  has_tests_source: TestEvidence;
}

export interface CIJobRequest {
  job_id: string;
  repo: string;
  branch: string;
  priority?: number;
  estimated_seconds?: number;
  preferred_pool?: RunnerPool;
  github_hosted_healthy?: boolean;
}

export interface QueuedJob {
  job_id: string;
  repo: string;
  branch: string;
  priority: number;
  routed_pool: RunnerPool;
  route_reason: string;
  enqueue_seq: number;
  enqueued_at: string;
}

export interface SymbolIndexEntry {
  repo: string;
  symbol: string;
  file_path: string;
  owners: string[];
  related_prs: number[];
}

export const DEFAULT_POLICY: PolicyRule = {
  rule_id: "default-org-policy",
  require_tests_above_lines: 150,
  protected_paths: ["infra/", "auth/", "billing/"],
  allow_unverified_ai_prs: false,
  default_reviewers: ["@maintainers"],
  protected_reviewers: ["@sec-team", "@staff-eng"],
};

export const SEED_GRAPH: Record<string, string[]> = {
  "org/shared-types": ["org/payments-core", "org/auth-gateway", "org/web-app"],
  "org/payments-core": ["org/web-app", "org/billing-export"],
  "org/auth-gateway": ["org/web-app"],
  "org/billing-export": ["org/finance-ledger"],
};

export const SEED_SYMBOLS: SymbolIndexEntry[] = [
  {
    repo: "org/payments-core",
    symbol: "process_settlement",
    file_path: "src/settlement/engine.py",
    owners: ["@fin-eng", "@alex-payments"],
    related_prs: [412, 489],
  },
  {
    repo: "org/auth-gateway",
    symbol: "verify_token_chain",
    file_path: "pkg/auth/chain.go",
    owners: ["@sec-team"],
    related_prs: [108],
  },
  {
    repo: "org/shared-types",
    symbol: "Money",
    file_path: "src/money/Money.ts",
    owners: ["@platform"],
    related_prs: [77, 81],
  },
  {
    repo: "org/web-app",
    symbol: "CheckoutForm",
    file_path: "app/checkout/CheckoutForm.tsx",
    owners: ["@web-eng", "@fin-eng"],
    related_prs: [901],
  },
];

const TEST_PATH =
  /(?:^|\/)(?:tests?|spec|__tests__|e2e|cypress|testdata)(?:\/|$)|(?:^|\/)test_[^/]+$|[^/]+_test\.[^/]+$|[^/]+\.(?:tests?|spec)\.[^/]+$/i;

export function isTestPath(path: string): boolean {
  const normalized = path.replace(/\\/g, "/").trim();
  return Boolean(normalized && TEST_PATH.test(normalized));
}

export function inferHasTests(paths: string[] | null | undefined): [boolean | null, TestEvidence] {
  if (!paths || paths.length === 0) return [null, "unknown"];
  return [paths.some(isTestPath), "inferred"];
}

export function protectedHits(paths: string[], prefixes: string[]): string[] {
  const hits: string[] = [];
  const seen = new Set<string>();
  for (const path of paths) {
    const normalized = path.replace(/\\/g, "/").replace(/^\.\//, "");
    for (const prefix of prefixes) {
      if (normalized.startsWith(prefix) && !seen.has(path)) {
        hits.push(path);
        seen.add(path);
        break;
      }
    }
  }
  return hits;
}

export function enrichPullRequest(pr: PullRequestEvent, policy: PolicyRule): PullRequestEvent {
  let hasTests = pr.has_tests ?? null;
  let source: TestEvidence = pr.has_tests_source ?? "unknown";
  if (hasTests == null || source === "unknown") {
    const [inferred, inferredSource] = inferHasTests(pr.changed_paths);
    if (inferredSource === "inferred") {
      hasTests = inferred;
      source = "inferred";
    } else if (hasTests == null) {
      source = "unknown";
    } else {
      source = "explicit";
    }
  } else if (source !== "inferred") {
    source = "explicit";
  }
  let paths = pr.touches_critical_paths ? [...pr.touches_critical_paths] : [];
  if (paths.length === 0 && pr.changed_paths?.length) {
    paths = protectedHits(pr.changed_paths, policy.protected_paths);
  }
  const filesChanged = pr.changed_paths?.length
    ? Math.max(pr.files_changed ?? 1, pr.changed_paths.length)
    : (pr.files_changed ?? 1);
  return {
    ...pr,
    has_tests: hasTests,
    has_tests_source: source,
    touches_critical_paths: paths,
    files_changed: filesChanged,
  };
}

export function triagePullRequest(pr: PullRequestEvent, policy: PolicyRule): TriageDecision {
  const event = enrichPullRequest(pr, policy);
  let score = 0.5;
  const reasons: string[] = [];
  const churn = (event.lines_added ?? 0) + (event.lines_deleted ?? 0);
  const merged = event.author_merged_prs;

  if (merged == null) {
    reasons.push("Contributor history unknown");
  } else if (merged >= 5) {
    score += 0.2;
    reasons.push(`Trusted contributor (${merged} merged PRs)`);
  } else if (merged === 0) {
    score -= 0.15;
    reasons.push("First-time contributor");
  }

  if (event.has_tests === true) {
    score += 0.15;
    reasons.push("Includes test coverage");
  } else if (event.has_tests === false && churn > 80) {
    score -= 0.15;
    reasons.push("Large change without tests");
  } else if (event.has_tests == null) {
    reasons.push("Test presence not inferred from payload (no file list)");
  }

  if (event.has_tests === false && churn >= policy.require_tests_above_lines) {
    reasons.push(`Policy requires tests above ${policy.require_tests_above_lines} changed lines`);
  }

  if (event.ai_assisted === true && (merged ?? 0) === 0 && merged != null && churn > 300) {
    score -= 0.25;
    reasons.push("Large unreviewed AI-assisted PR from new author");
  }

  const title = (event.title || "").trim();
  if (title.length < 12 || title.toLowerCase().startsWith("fix issue") || title.toLowerCase().startsWith("update code")) {
    score -= 0.15;
    reasons.push("Low-specificity PR title");
  }

  score = Math.max(0, Math.min(1, Math.round(score * 100) / 100));
  let hits = protectedHits(event.touches_critical_paths || [], policy.protected_paths);
  if (hits.length === 0 && event.changed_paths?.length) {
    hits = protectedHits(event.changed_paths, policy.protected_paths);
  }

  if (hits.length && event.has_tests === false) {
    return {
      pr_number: event.pr_number,
      repo: event.repo,
      signal_score: score,
      action: "block-policy",
      reasons: [...reasons, `Protected paths lack tests: ${hits.join(", ")}`],
      suggested_reviewers: [...policy.protected_reviewers],
      has_tests: event.has_tests ?? null,
      has_tests_source: event.has_tests_source ?? "unknown",
    };
  }

  if (hits.length && event.has_tests == null) {
    reasons.push(`Protected paths touched; test presence unknown: ${hits.join(", ")}`);
  }

  const firstTime = merged === 0;
  let action: TriageAction;
  if (event.ai_assisted === true && !policy.allow_unverified_ai_prs && firstTime) {
    action = "quarantine-bot-check";
  } else if (score >= 0.75) {
    action = "fast-track";
  } else if (score < 0.35) {
    action = "quarantine-bot-check";
  } else {
    action = "standard-review";
  }

  return {
    pr_number: event.pr_number,
    repo: event.repo,
    signal_score: score,
    action,
    reasons: reasons.length ? reasons : ["Standard baseline signals"],
    suggested_reviewers: hits.length ? [...policy.protected_reviewers] : [...policy.default_reviewers],
    has_tests: event.has_tests ?? null,
    has_tests_source: event.has_tests_source ?? "unknown",
  };
}

export function routeJob(job: CIJobRequest): [RunnerPool, string] {
  const preferred = job.preferred_pool ?? "ephemeral-cloud";
  if (job.github_hosted_healthy === false && preferred === "ephemeral-cloud") {
    return ["fallback-alt", "GitHub-hosted pool marked degraded; routed to fallback runner pool"];
  }
  return [preferred, "Routed to preferred runner pool"];
}

export function blastRadius(graph: Record<string, string[]>, repo: string) {
  const queue = [...(graph[repo] || [])].filter((item) => item !== repo);
  const seen = new Set([repo, ...queue]);
  const visited: string[] = [];
  while (queue.length) {
    const current = queue.shift() as string;
    visited.push(current);
    for (const downstream of graph[current] || []) {
      if (!seen.has(downstream)) {
        seen.add(downstream);
        queue.push(downstream);
      }
    }
  }
  return { source_repo: repo, downstream_repos: visited, total_impacted: visited.length };
}

export function searchSymbols(index: SymbolIndexEntry[], query: string): SymbolIndexEntry[] {
  const needle = query.toLowerCase().trim();
  if (!needle) return index.map((item) => ({ ...item, owners: [...item.owners], related_prs: [...item.related_prs] }));
  return index.filter(
    (item) =>
      item.symbol.toLowerCase().includes(needle) ||
      item.file_path.toLowerCase().includes(needle) ||
      item.owners.some((owner) => owner.toLowerCase().includes(needle)),
  );
}

export function upsertSymbol(index: SymbolIndexEntry[], entry: SymbolIndexEntry): number {
  const existing = index.findIndex(
    (item) => item.repo === entry.repo && item.symbol === entry.symbol && item.file_path === entry.file_path,
  );
  if (existing >= 0) {
    const prior = index[existing];
    const related = [...new Set([...prior.related_prs, ...entry.related_prs])];
    index[existing] = { ...entry, owners: [...entry.owners], related_prs: related };
  } else {
    index.push({ ...entry, owners: [...entry.owners], related_prs: [...entry.related_prs] });
  }
  return index.length;
}

export class MemoryStore {
  policy: PolicyRule;
  graph: Record<string, string[]>;
  symbols: SymbolIndexEntry[];
  queue: QueuedJob[] = [];
  seq = 0;

  constructor(policy: PolicyRule, graph: Record<string, string[]>, symbols: SymbolIndexEntry[]) {
    this.policy = policy;
    this.graph = Object.fromEntries(Object.entries(graph).map(([key, values]) => [key, [...values]]));
    this.symbols = symbols.map((item) => ({ ...item, owners: [...item.owners], related_prs: [...item.related_prs] }));
  }

  nextSeq(): number {
    this.seq += 1;
    return this.seq;
  }

  enqueue(job: QueuedJob): [QueuedJob, number] {
    this.queue.push(job);
    this.queue.sort((a, b) => b.priority - a.priority || a.enqueue_seq - b.enqueue_seq);
    return [job, this.queue.length];
  }
}

const AI_HINT =
  /\b(?:copilot|chatgpt|openai|claude|gemini|generated by|ai-assisted|cursor agent|aider|codex)\b/i;
const AI_LABELS = new Set(["ai", "ai-generated", "ai-assisted", "copilot", "generated"]);

export function extractFilenames(payload: Record<string, unknown>): string[] | null {
  const names = fromFileList(payload.files);
  const pullRequest = payload.pull_request;
  if (!names.length && pullRequest && typeof pullRequest === "object") {
    names.push(...fromFileList((pullRequest as { files?: unknown }).files));
  }
  if (!names.length && Array.isArray(payload.commits)) {
    for (const commit of payload.commits) {
      if (!commit || typeof commit !== "object") continue;
      const c = commit as Record<string, unknown>;
      for (const key of ["added", "modified", "removed"]) {
        if (Array.isArray(c[key])) {
          names.push(...(c[key] as unknown[]).filter((n): n is string => typeof n === "string" && Boolean(n.trim())));
        }
      }
    }
  }
  return names.length ? names : null;
}

function fromFileList(files: unknown): string[] {
  if (!Array.isArray(files)) return [];
  const names: string[] = [];
  for (const item of files) {
    if (typeof item === "string" && item.trim()) names.push(item.trim());
    else if (item && typeof item === "object") {
      const rec = item as Record<string, unknown>;
      for (const key of ["filename", "path", "name"]) {
        if (typeof rec[key] === "string" && (rec[key] as string).trim()) {
          names.push((rec[key] as string).trim());
          break;
        }
      }
    }
  }
  return names;
}

export function inferAiAssisted(payload: Record<string, unknown>, explicit: boolean | null): boolean | null {
  if (explicit != null) return explicit;
  const pullRequest =
    payload.pull_request && typeof payload.pull_request === "object"
      ? (payload.pull_request as Record<string, unknown>)
      : payload;
  const title = String(pullRequest.title || "");
  const body = String(pullRequest.body || "");
  if (AI_HINT.test(title) || AI_HINT.test(body)) return true;
  const labels = pullRequest.labels;
  if (Array.isArray(labels)) {
    for (const label of labels) {
      const name = typeof label === "string" ? label : (label as { name?: string })?.name || "";
      if (AI_LABELS.has(name.toLowerCase()) || AI_HINT.test(name)) return true;
    }
  }
  const user = pullRequest.user as { login?: string; type?: string } | undefined;
  if (user?.login?.endsWith("[bot]") || user?.type === "Bot") return true;
  return null;
}
