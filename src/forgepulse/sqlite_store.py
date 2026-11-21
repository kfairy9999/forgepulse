"""SQLite store with the same schema as the Cloudflare D1 database."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forgepulse.crypto_util import (
    decrypt_string,
    encrypt_string,
    hash_token,
    random_id,
    random_token,
)
from forgepulse.models import PolicyRule, QueuedJob, SymbolIndexEntry
from forgepulse.seed import SEED_GRAPH, SEED_SYMBOLS

SCHEMA_PATHS = [
    Path.cwd() / "migrations" / "0001_init.sql",
    Path(__file__).resolve().parents[2] / "migrations" / "0001_init.sql",
    Path(__file__).resolve().parents[2] / "demo" / "migrations" / "0001_init.sql",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqliteStore:
    def __init__(self, path: str, master_key: str) -> None:
        self._path = path
        self._master = master_key
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._apply_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _apply_schema(self) -> None:
        sql_path = next((p for p in SCHEMA_PATHS if p.is_file()), None)
        if sql_path is None:
            raise RuntimeError("migrations/0001_init.sql is missing")
        self._conn.executescript(sql_path.read_text(encoding="utf-8"))
        self._conn.commit()

    def _execute(self, sql: str, params: Iterable[object] = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, tuple(params))

    def rate_limit(self, key: str, limit: int, window_sec: int) -> bool:
        now = int(time.time())
        with self._lock:
            row = self._execute(
                "SELECT window_start, count FROM rate_limits WHERE key = ?", (key,)
            ).fetchone()
            if row is None or now - int(row["window_start"]) >= window_sec:
                self._execute(
                    "INSERT OR REPLACE INTO rate_limits (key, window_start, count) VALUES (?, ?, 1)",
                    (key, now),
                )
                self._conn.commit()
                return True
            if int(row["count"]) >= limit:
                return False
            self._execute("UPDATE rate_limits SET count = count + 1 WHERE key = ?", (key,))
            self._conn.commit()
            return True

    def create_workspace(self, name: str, kind: str, origin: str) -> dict[str, Any]:
        workspace_id = random_id("ws_")
        token = random_token("fp_live_")
        secret = random_token("whsec_")
        created = _now()
        with self._lock:
            self._execute(
                "INSERT INTO workspaces (id, name, kind, token_hash, webhook_secret_enc, github_token_enc, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    workspace_id,
                    name[:80],
                    kind,
                    hash_token(self._master, token),
                    encrypt_string(self._master, secret),
                    created,
                    created,
                ),
            )
            self._seed(workspace_id, created)
            self._conn.commit()
        return {
            "workspace": {
                "id": workspace_id,
                "name": name[:80],
                "kind": kind,
                "created_at": created,
            },
            "token": token,
            "webhook_secret": secret,
            "webhook_url": f"{origin}/w/{workspace_id}/webhooks/github",
        }

    def _seed(self, workspace_id: str, at: str) -> None:
        policy = PolicyRule(rule_id="default-org-policy")
        self._save_policy(workspace_id, policy, at)
        self._execute(
            "INSERT OR REPLACE INTO ci_seq (workspace_id, next_seq) VALUES (?, 0)", (workspace_id,)
        )
        for source, dests in SEED_GRAPH.items():
            for dest in dests:
                self._execute(
                    "INSERT OR IGNORE INTO graph_edges (workspace_id, source_repo, dest_repo) VALUES (?, ?, ?)",
                    (workspace_id, source, dest),
                )
        for item in SEED_SYMBOLS:
            self._execute(
                "INSERT OR IGNORE INTO symbols (workspace_id, repo, symbol, file_path, owners, related_prs) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    workspace_id,
                    item.repo,
                    item.symbol,
                    item.file_path,
                    json.dumps(item.owners),
                    json.dumps(item.related_prs),
                ),
            )

    def get_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        return dict(row) if row else None

    def get_workspace_by_token(self, token: str) -> dict[str, Any] | None:
        digest = hash_token(self._master, token)
        with self._lock:
            row = self._execute(
                "SELECT * FROM workspaces WHERE token_hash = ?", (digest,)
            ).fetchone()
            if row:
                self._execute(
                    "UPDATE workspaces SET last_seen_at = ? WHERE id = ?", (_now(), row["id"])
                )
                self._conn.commit()
                return dict(row)
        return None

    def rotate_webhook_secret(self, workspace_id: str) -> str:
        secret = random_token("whsec_")
        with self._lock:
            self._execute(
                "UPDATE workspaces SET webhook_secret_enc = ? WHERE id = ?",
                (encrypt_string(self._master, secret), workspace_id),
            )
            self._conn.commit()
        return secret

    def set_github_token(self, workspace_id: str, token: str | None) -> None:
        enc = encrypt_string(self._master, token) if token else None
        with self._lock:
            self._execute(
                "UPDATE workspaces SET github_token_enc = ? WHERE id = ?", (enc, workspace_id)
            )
            self._conn.commit()

    def decrypt_github_token(self, workspace: dict[str, Any]) -> str | None:
        blob = workspace.get("github_token_enc")
        return decrypt_string(self._master, blob) if blob else None

    def decrypt_webhook_secret(self, workspace: dict[str, Any]) -> str:
        return decrypt_string(self._master, workspace["webhook_secret_enc"])

    def load_policy(self, workspace_id: str) -> PolicyRule:
        with self._lock:
            row = self._execute(
                "SELECT * FROM policies WHERE workspace_id = ?", (workspace_id,)
            ).fetchone()
        if not row:
            return PolicyRule(rule_id="default-org-policy")
        return PolicyRule(
            rule_id=row["rule_id"],
            require_tests_above_lines=int(row["require_tests_above_lines"]),
            protected_paths=json.loads(row["protected_paths"]),
            allow_unverified_ai_prs=bool(row["allow_unverified_ai_prs"]),
            default_reviewers=json.loads(row["default_reviewers"]),
            protected_reviewers=json.loads(row["protected_reviewers"]),
        )

    def save_policy(self, workspace_id: str, policy: PolicyRule) -> None:
        with self._lock:
            self._save_policy(workspace_id, policy, _now())
            self._conn.commit()

    def _save_policy(self, workspace_id: str, policy: PolicyRule, at: str) -> None:
        self._execute(
            "INSERT OR REPLACE INTO policies (workspace_id, rule_id, require_tests_above_lines, protected_paths, allow_unverified_ai_prs, default_reviewers, protected_reviewers, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                workspace_id,
                policy.rule_id,
                policy.require_tests_above_lines,
                json.dumps(policy.protected_paths),
                int(policy.allow_unverified_ai_prs),
                json.dumps(policy.default_reviewers),
                json.dumps(policy.protected_reviewers),
                at,
            ),
        )

    def load_graph(self, workspace_id: str) -> dict[str, list[str]]:
        with self._lock:
            rows = self._execute(
                "SELECT source_repo, dest_repo FROM graph_edges WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchall()
        graph: dict[str, list[str]] = {}
        for row in rows:
            graph.setdefault(row["source_repo"], []).append(row["dest_repo"])
        return graph

    def add_edge(self, workspace_id: str, source: str, dest: str) -> None:
        with self._lock:
            self._execute(
                "INSERT OR IGNORE INTO graph_edges (workspace_id, source_repo, dest_repo) VALUES (?, ?, ?)",
                (workspace_id, source[:200], dest[:200]),
            )
            self._conn.commit()

    def remove_edge(self, workspace_id: str, source: str, dest: str) -> None:
        with self._lock:
            self._execute(
                "DELETE FROM graph_edges WHERE workspace_id = ? AND source_repo = ? AND dest_repo = ?",
                (workspace_id, source, dest),
            )
            self._conn.commit()

    def search_symbols(self, workspace_id: str, query: str) -> list[SymbolIndexEntry]:
        with self._lock:
            rows = self._execute(
                "SELECT repo, symbol, file_path, owners, related_prs FROM symbols WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchall()
        items = [
            SymbolIndexEntry(
                repo=row["repo"],
                symbol=row["symbol"],
                file_path=row["file_path"],
                owners=json.loads(row["owners"]),
                related_prs=json.loads(row["related_prs"]),
            )
            for row in rows
        ]
        needle = query.lower().strip()
        if not needle:
            return items
        return [
            item
            for item in items
            if needle in item.symbol.lower()
            or needle in item.file_path.lower()
            or needle in item.repo.lower()
            or any(needle in owner.lower() for owner in item.owners)
        ]

    def upsert_symbol(self, workspace_id: str, entry: SymbolIndexEntry) -> int:
        with self._lock:
            existing = self._execute(
                "SELECT related_prs FROM symbols WHERE workspace_id = ? AND repo = ? AND symbol = ? AND file_path = ?",
                (workspace_id, entry.repo, entry.symbol, entry.file_path),
            ).fetchone()
            related = list(entry.related_prs)
            if existing:
                related = list(dict.fromkeys([*json.loads(existing["related_prs"]), *related]))
            self._execute(
                "INSERT OR REPLACE INTO symbols (workspace_id, repo, symbol, file_path, owners, related_prs) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    workspace_id,
                    entry.repo,
                    entry.symbol,
                    entry.file_path,
                    json.dumps(list(entry.owners)),
                    json.dumps(related),
                ),
            )
            count = self._execute(
                "SELECT COUNT(*) AS n FROM symbols WHERE workspace_id = ?", (workspace_id,)
            ).fetchone()
            self._conn.commit()
            return int(count["n"] if count else 0)

    def enqueue_job(self, workspace_id: str, job: dict[str, Any]) -> QueuedJob:
        with self._lock:
            self._execute(
                "INSERT OR IGNORE INTO ci_seq (workspace_id, next_seq) VALUES (?, 0)",
                (workspace_id,),
            )
            self._execute(
                "UPDATE ci_seq SET next_seq = next_seq + 1 WHERE workspace_id = ?", (workspace_id,)
            )
            seq_row = self._execute(
                "SELECT next_seq FROM ci_seq WHERE workspace_id = ?", (workspace_id,)
            ).fetchone()
            seq = int(seq_row["next_seq"] if seq_row else 1)
            enqueued_at = _now()
            self._execute(
                "INSERT INTO ci_jobs (workspace_id, enqueue_seq, job_id, repo, branch, priority, routed_pool, route_reason, enqueued_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    workspace_id,
                    seq,
                    job["job_id"],
                    job["repo"],
                    job["branch"],
                    job["priority"],
                    job["routed_pool"],
                    job["route_reason"],
                    enqueued_at,
                ),
            )
            self._conn.commit()
        return QueuedJob(
            job_id=job["job_id"],
            repo=job["repo"],
            branch=job["branch"],
            priority=job["priority"],
            routed_pool=job["routed_pool"],
            route_reason=job["route_reason"],
            enqueue_seq=seq,
            enqueued_at=enqueued_at,
        )

    def list_queue(self, workspace_id: str) -> list[QueuedJob]:
        with self._lock:
            rows = self._execute(
                "SELECT job_id, repo, branch, priority, routed_pool, route_reason, enqueue_seq, enqueued_at FROM ci_jobs WHERE workspace_id = ? ORDER BY priority DESC, enqueue_seq ASC LIMIT 200",
                (workspace_id,),
            ).fetchall()
        return [QueuedJob.model_validate(dict(row)) for row in rows]

    def record_triage(self, workspace_id: str, event: dict[str, Any]) -> None:
        has_tests = event.get("has_tests")
        with self._lock:
            self._execute(
                "INSERT INTO triage_events (id, workspace_id, created_at, repo, pr_number, author, action, signal_score, has_tests, has_tests_source, reasons, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    random_id("tr_"),
                    workspace_id,
                    _now(),
                    event["repo"],
                    event["pr_number"],
                    event.get("author"),
                    event["action"],
                    event["signal_score"],
                    None if has_tests is None else int(bool(has_tests)),
                    event.get("has_tests_source"),
                    json.dumps(event["reasons"]),
                    event["source"],
                ),
            )
            self._conn.commit()

    def list_triage(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._execute(
                "SELECT id, created_at, repo, pr_number, author, action, signal_score, has_tests, has_tests_source, reasons, source FROM triage_events WHERE workspace_id = ? ORDER BY created_at DESC LIMIT 50",
                (workspace_id,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["has_tests"] = None if item["has_tests"] is None else bool(item["has_tests"])
            item["reasons"] = json.loads(item["reasons"])
            out.append(item)
        return out

    def record_webhook(self, workspace_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            self._execute(
                "INSERT INTO webhook_events (id, workspace_id, created_at, github_event, delivery_id, status, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    random_id("wh_"),
                    workspace_id,
                    _now(),
                    event.get("github_event"),
                    event.get("delivery_id"),
                    event["status"],
                    event.get("detail"),
                ),
            )
            self._conn.commit()

    def list_webhooks(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._execute(
                "SELECT id, created_at, github_event, delivery_id, status, detail FROM webhook_events WHERE workspace_id = ? ORDER BY created_at DESC LIMIT 50",
                (workspace_id,),
            ).fetchall()
        return [dict(row) for row in rows]
