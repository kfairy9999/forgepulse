CREATE TABLE IF NOT EXISTS workspaces (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('guest', 'user')),
  token_hash TEXT NOT NULL UNIQUE,
  webhook_secret_enc TEXT NOT NULL,
  github_token_enc TEXT,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policies (
  workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
  rule_id TEXT NOT NULL,
  require_tests_above_lines INTEGER NOT NULL,
  protected_paths TEXT NOT NULL,
  allow_unverified_ai_prs INTEGER NOT NULL,
  default_reviewers TEXT NOT NULL,
  protected_reviewers TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS graph_edges (
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  source_repo TEXT NOT NULL,
  dest_repo TEXT NOT NULL,
  PRIMARY KEY (workspace_id, source_repo, dest_repo)
);

CREATE TABLE IF NOT EXISTS symbols (
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  repo TEXT NOT NULL,
  symbol TEXT NOT NULL,
  file_path TEXT NOT NULL,
  owners TEXT NOT NULL,
  related_prs TEXT NOT NULL,
  PRIMARY KEY (workspace_id, repo, symbol, file_path)
);

CREATE TABLE IF NOT EXISTS ci_jobs (
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  enqueue_seq INTEGER NOT NULL,
  job_id TEXT NOT NULL,
  repo TEXT NOT NULL,
  branch TEXT NOT NULL,
  priority INTEGER NOT NULL,
  routed_pool TEXT NOT NULL,
  route_reason TEXT NOT NULL,
  enqueued_at TEXT NOT NULL,
  PRIMARY KEY (workspace_id, enqueue_seq)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ci_jobs_job_id ON ci_jobs (workspace_id, job_id);

CREATE TABLE IF NOT EXISTS ci_seq (
  workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
  next_seq INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS triage_events (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  repo TEXT NOT NULL,
  pr_number INTEGER NOT NULL,
  author TEXT,
  action TEXT NOT NULL,
  signal_score REAL NOT NULL,
  has_tests INTEGER,
  has_tests_source TEXT,
  reasons TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('api', 'webhook'))
);

CREATE INDEX IF NOT EXISTS idx_triage_ws_time ON triage_events (workspace_id, created_at DESC);

CREATE TABLE IF NOT EXISTS webhook_events (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  github_event TEXT,
  delivery_id TEXT,
  status TEXT NOT NULL,
  detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_webhook_ws_time ON webhook_events (workspace_id, created_at DESC);

CREATE TABLE IF NOT EXISTS rate_limits (
  key TEXT PRIMARY KEY,
  window_start INTEGER NOT NULL,
  count INTEGER NOT NULL
);
