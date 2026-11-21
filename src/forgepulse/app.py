"""FastAPI application backed by SQLite, matching the live Worker API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from forgepulse import __version__
from forgepulse.config import Settings
from forgepulse.crypto_util import hmac_sha256_hex
from forgepulse.github_api import enrich_from_github
from forgepulse.graph import dependency_blast_radius
from forgepulse.infer import extract_filenames, infer_ai_assisted, infer_has_tests, protected_hits
from forgepulse.models import CIJobRequest, PolicyRule, PullRequestEvent, SymbolIndexEntry
from forgepulse.runners import route_job
from forgepulse.sqlite_store import SqliteStore
from forgepulse.store import MemoryStore
from forgepulse.triage import triage_pull_request
from forgepulse.webhook import parse_json_body

ROBOTS = "User-agent: *\nAllow: /\n"
FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" fill="#f3efe4"/>'
    '<rect x="6" y="14" width="20" height="4" fill="#9d2c14"/>'
    '<rect x="14" y="6" width="4" height="20" fill="#1d1a16"/>'
    "</svg>"
)
MAX_BODY = 256_000
DISCLAIMER = "Routing decision only. ForgePulse does not launch runners."


def _playground_html() -> str:
    candidates = [
        Path.cwd() / "demo" / "playground.html",
        Path(__file__).resolve().parents[2] / "demo" / "playground.html",
        Path(__file__).resolve().parent / "static" / "index.html",
    ]
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return "<!DOCTYPE html><html><body><p>ForgePulse playground is missing.</p></body></html>"


def _origin(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _client_ip(request: Request) -> str:
    return (
        request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for") or "local"
    )


def _bearer(request: Request, x_forgepulse_token: str | None) -> str | None:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return (x_forgepulse_token or "").strip() or None


def _public_workspace(store: SqliteStore, workspace: dict[str, Any], origin: str) -> dict[str, Any]:
    return {
        "id": workspace["id"],
        "name": workspace["name"],
        "kind": workspace["kind"],
        "created_at": workspace["created_at"],
        "webhook_url": f"{origin}/w/{workspace['id']}/webhooks/github",
        "github_token_configured": bool(workspace.get("github_token_enc")),
        "storage": "sqlite",
    }


def create_app(
    *,
    settings: Settings | None = None,
    store: MemoryStore | None = None,
    sqlite: SqliteStore | None = None,
) -> FastAPI:
    config = settings or Settings.from_env()
    db = sqlite or SqliteStore(str(config.database_path), config.master_key)

    app = FastAPI(
        title="ForgePulse",
        version=__version__,
        description=(
            "Durable PR triage, CI routing records, blast radius, and portable policy."
        ),
    )
    app.state.settings = config
    app.state.sqlite = db
    app.state.store = store  # unused by routes; kept for older tests that inject MemoryStore

    @app.middleware("http")
    async def _secure_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        if int(request.headers.get("content-length") or 0) > MAX_BODY:
            return JSONResponse({"detail": "Request body is too large"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'",
        )
        return response

    def require_workspace(
        request: Request, x_forgepulse_token: str | None = None
    ) -> dict[str, Any]:
        token = _bearer(request, x_forgepulse_token)
        if not token:
            raise HTTPException(
                status_code=401, detail="Missing bearer token. Create a workspace first."
            )
        workspace = db.get_workspace_by_token(token)
        if workspace is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return workspace

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def playground() -> HTMLResponse:
        return HTMLResponse(_playground_html(), headers={"cache-control": "public, max-age=60"})

    @app.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
    def robots() -> PlainTextResponse:
        return PlainTextResponse(ROBOTS, headers={"cache-control": "public, max-age=86400"})

    @app.get("/favicon.svg", include_in_schema=False)
    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(
            FAVICON, media_type="image/svg+xml", headers={"cache-control": "public, max-age=86400"}
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        try:
            db._execute("SELECT 1")
        except Exception as exc:  # pragma: no cover
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        return {
            "status": "ok",
            "version": __version__,
            "mode": "durable",
            "storage": "sqlite",
        }

    @app.post("/workspaces", status_code=201)
    def create_user_workspace(
        request: Request, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not db.rate_limit(f"ws:{_client_ip(request)}", 10, 3600):
            raise HTTPException(
                status_code=429,
                detail="Too many workspaces created from this address. Try again later.",
            )
        name = str((body or {}).get("name") or "My workspace").strip() or "My workspace"
        return db.create_workspace(name, "user", _origin(request))

    @app.post("/workspaces/guest", status_code=201)
    def create_guest_workspace(request: Request) -> dict[str, Any]:
        if not db.rate_limit(f"guest:{_client_ip(request)}", 20, 3600):
            raise HTTPException(
                status_code=429,
                detail="Too many sample workspaces from this address. Try again later.",
            )
        return db.create_workspace("Sample org (guest)", "guest", _origin(request))

    @app.get("/workspace")
    def get_workspace(
        request: Request,
        x_forgepulse_token: str | None = Header(default=None, alias="X-ForgePulse-Token"),
    ) -> dict[str, Any]:
        workspace = require_workspace(request, x_forgepulse_token)
        return _public_workspace(db, workspace, _origin(request))

    @app.patch("/workspace")
    def patch_workspace(
        request: Request,
        body: dict[str, Any],
        x_forgepulse_token: str | None = Header(default=None, alias="X-ForgePulse-Token"),
    ) -> dict[str, Any]:
        workspace = require_workspace(request, x_forgepulse_token)
        if "github_token" in body:
            token = body.get("github_token")
            if token and (not isinstance(token, str) or not (8 <= len(token) <= 200)):
                raise HTTPException(status_code=422, detail="GitHub token looks invalid")
            db.set_github_token(
                workspace["id"], token.strip() if isinstance(token, str) and token.strip() else None
            )
            workspace = db.get_workspace(workspace["id"]) or workspace
        return _public_workspace(db, workspace, _origin(request))

    @app.post("/workspace/rotate-webhook-secret")
    def rotate_secret(
        request: Request,
        x_forgepulse_token: str | None = Header(default=None, alias="X-ForgePulse-Token"),
    ) -> dict[str, str]:
        workspace = require_workspace(request, x_forgepulse_token)
        secret = db.rotate_webhook_secret(workspace["id"])
        return {
            "webhook_secret": secret,
            "webhook_url": f"{_origin(request)}/w/{workspace['id']}/webhooks/github",
        }

    @app.get("/policy", response_model=PolicyRule)
    def get_policy(
        request: Request,
        x_forgepulse_token: str | None = Header(default=None, alias="X-ForgePulse-Token"),
    ) -> PolicyRule:
        workspace = require_workspace(request, x_forgepulse_token)
        return db.load_policy(workspace["id"])

    @app.put("/policy", response_model=PolicyRule)
    @app.post("/policy", response_model=PolicyRule)
    def put_policy(
        request: Request,
        policy: PolicyRule,
        x_forgepulse_token: str | None = Header(default=None, alias="X-ForgePulse-Token"),
    ) -> PolicyRule:
        workspace = require_workspace(request, x_forgepulse_token)
        db.save_policy(workspace["id"], policy)
        return policy

    @app.post("/triage/pr")
    def triage(
        request: Request,
        pr: PullRequestEvent,
        x_forgepulse_token: str | None = Header(default=None, alias="X-ForgePulse-Token"),
    ) -> dict[str, Any]:
        workspace = require_workspace(request, x_forgepulse_token)
        policy = db.load_policy(workspace["id"])
        decision = triage_pull_request(pr, policy)
        db.record_triage(
            workspace["id"],
            {
                "repo": pr.repo,
                "pr_number": pr.pr_number,
                "author": pr.author,
                "action": decision.action,
                "signal_score": decision.signal_score,
                "has_tests": decision.has_tests,
                "has_tests_source": decision.has_tests_source,
                "reasons": decision.reasons,
                "source": "api",
            },
        )
        return decision.model_dump()

    @app.get("/triage/history")
    def triage_history(
        request: Request,
        x_forgepulse_token: str | None = Header(default=None, alias="X-ForgePulse-Token"),
    ) -> dict[str, Any]:
        workspace = require_workspace(request, x_forgepulse_token)
        return {"events": db.list_triage(workspace["id"])}

    @app.post("/ci/enqueue")
    def enqueue(
        request: Request,
        job: CIJobRequest,
        x_forgepulse_token: str | None = Header(default=None, alias="X-ForgePulse-Token"),
    ) -> dict[str, Any]:
        workspace = require_workspace(request, x_forgepulse_token)
        routed_pool, route_reason = route_job(job)
        queued = db.enqueue_job(
            workspace["id"],
            {
                "job_id": job.job_id,
                "repo": job.repo,
                "branch": job.branch,
                "priority": job.priority,
                "routed_pool": routed_pool,
                "route_reason": route_reason,
            },
        )
        jobs = db.list_queue(workspace["id"])
        return {"queued": queued.model_dump(), "queue_depth": len(jobs), "disclaimer": DISCLAIMER}

    @app.get("/ci/queue")
    def list_ci_queue(
        request: Request,
        x_forgepulse_token: str | None = Header(default=None, alias="X-ForgePulse-Token"),
    ) -> dict[str, Any]:
        workspace = require_workspace(request, x_forgepulse_token)
        jobs = db.list_queue(workspace["id"])
        return {
            "queue_depth": len(jobs),
            "jobs": [item.model_dump() for item in jobs],
            "disclaimer": DISCLAIMER,
        }

    @app.get("/graph")
    def get_graph(
        request: Request,
        x_forgepulse_token: str | None = Header(default=None, alias="X-ForgePulse-Token"),
    ) -> dict[str, Any]:
        workspace = require_workspace(request, x_forgepulse_token)
        return {"edges": db.load_graph(workspace["id"])}

    @app.post("/graph/edges")
    def post_edge(
        request: Request,
        body: dict[str, Any],
        x_forgepulse_token: str | None = Header(default=None, alias="X-ForgePulse-Token"),
    ) -> dict[str, Any]:
        workspace = require_workspace(request, x_forgepulse_token)
        source = str(body.get("source") or "").strip()
        dest = str(body.get("dest") or "").strip()
        if not source or not dest:
            raise HTTPException(status_code=422, detail="source and dest repositories are required")
        db.add_edge(workspace["id"], source, dest)
        return {"edges": db.load_graph(workspace["id"])}

    @app.delete("/graph/edges")
    def delete_edge(
        request: Request,
        body: dict[str, Any],
        x_forgepulse_token: str | None = Header(default=None, alias="X-ForgePulse-Token"),
    ) -> dict[str, Any]:
        workspace = require_workspace(request, x_forgepulse_token)
        db.remove_edge(workspace["id"], str(body.get("source") or ""), str(body.get("dest") or ""))
        return {"edges": db.load_graph(workspace["id"])}

    @app.get("/graph/impact/{repo:path}")
    def blast_radius(
        request: Request,
        repo: str,
        x_forgepulse_token: str | None = Header(default=None, alias="X-ForgePulse-Token"),
    ) -> dict[str, Any]:
        workspace = require_workspace(request, x_forgepulse_token)
        # Temporary MemoryStore adapter so the existing BFS helper can run.
        memory = MemoryStore(
            policy=db.load_policy(workspace["id"]), graph=db.load_graph(workspace["id"])
        )
        return dependency_blast_radius(memory, repo).model_dump()

    @app.post("/index/symbol")
    def index_symbol(
        request: Request,
        entry: SymbolIndexEntry,
        x_forgepulse_token: str | None = Header(default=None, alias="X-ForgePulse-Token"),
    ) -> dict[str, Any]:
        workspace = require_workspace(request, x_forgepulse_token)
        total = db.upsert_symbol(workspace["id"], entry)
        return {"status": "indexed", "total_symbols": total}

    @app.get("/index/search")
    def search(
        request: Request,
        q: str = Query(default=""),
        x_forgepulse_token: str | None = Header(default=None, alias="X-ForgePulse-Token"),
    ) -> dict[str, Any]:
        workspace = require_workspace(request, x_forgepulse_token)
        matches = db.search_symbols(workspace["id"], q)
        return {
            "query": q,
            "count": len(matches),
            "matches": [item.model_dump() for item in matches],
        }

    @app.get("/webhooks/events")
    def webhook_events(
        request: Request,
        x_forgepulse_token: str | None = Header(default=None, alias="X-ForgePulse-Token"),
    ) -> dict[str, Any]:
        workspace = require_workspace(request, x_forgepulse_token)
        return {"events": db.list_webhooks(workspace["id"])}

    @app.post("/w/{workspace_id}/webhooks/github")
    async def github_webhook(
        workspace_id: str,
        request: Request,
        x_github_event: str | None = Header(default=None),
        x_hub_signature_256: str | None = Header(default=None),
        x_github_delivery: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if not db.rate_limit(f"hook:{workspace_id}", 120, 60):
            raise HTTPException(status_code=429, detail="Webhook rate limit exceeded")
        workspace = db.get_workspace(workspace_id)
        if workspace is None:
            raise HTTPException(status_code=404, detail="Unknown workspace")
        raw_body = await request.body()
        if len(raw_body) > MAX_BODY:
            raise HTTPException(status_code=413, detail="Request body is too large")
        secret = db.decrypt_webhook_secret(workspace)
        expected = "sha256=" + hmac_sha256_hex(secret, raw_body)
        provided = (x_hub_signature_256 or "").strip()
        if not provided.startswith("sha256=") or not _compare(expected, provided):
            db.record_webhook(
                workspace_id, {"status": "unauthorized", "detail": "Invalid webhook signature"}
            )
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
        try:
            payload = parse_json_body(raw_body)
        except ValueError as exc:
            db.record_webhook(workspace_id, {"status": "error", "detail": "Body is not valid JSON"})
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        event_name = x_github_event or "unknown"
        if event_name != "pull_request":
            db.record_webhook(
                workspace_id,
                {
                    "github_event": event_name,
                    "delivery_id": x_github_delivery,
                    "status": "ignored",
                    "detail": f"Event {event_name} is ignored",
                },
            )
            return {"event": event_name, "status": "ignored"}
        return _triage_webhook(db, workspace, payload, event_name, x_github_delivery)

    return app


def _compare(left: str, right: str) -> bool:
    import hmac

    if len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def _triage_webhook(
    db: SqliteStore,
    workspace: dict[str, Any],
    payload: dict[str, Any],
    event_name: str,
    delivery: str | None,
) -> dict[str, Any]:
    pull_request = payload.get("pull_request")
    if not isinstance(pull_request, dict):
        db.record_webhook(
            workspace["id"],
            {
                "github_event": event_name,
                "delivery_id": delivery,
                "status": "ignored",
                "detail": "No pull_request object",
            },
        )
        return {"event": event_name, "status": "ignored"}
    repository = payload.get("repository")
    repo_name = "unknown/repo"
    if isinstance(repository, dict) and isinstance(repository.get("full_name"), str):
        repo_name = repository["full_name"]
    user = pull_request.get("user")
    author = "unknown"
    if isinstance(user, dict) and isinstance(user.get("login"), str):
        author = user["login"]
    pr_number = int(pull_request.get("number") or 0)
    paths = extract_filenames(payload) or []
    merged = pull_request.get("author_merged_prs")
    merged_n = int(merged) if isinstance(merged, int) else None
    fetch_note: str | None = None
    token = db.decrypt_github_token(workspace)
    if token and "/" in repo_name:
        enrich = enrich_from_github(token, repo_name, pr_number, author)
        if enrich.get("fetched"):
            if enrich.get("paths"):
                paths = list(enrich["paths"])
            if enrich.get("author_merged_prs") is not None:
                merged_n = enrich["author_merged_prs"]
        else:
            fetch_note = str(enrich.get("error") or "GitHub fetch failed")
    elif not paths:
        fetch_note = (
            "No file list in the webhook, and no GitHub token is configured to fetch files."
        )
    has_tests, source = infer_has_tests(paths or None)
    policy = db.load_policy(workspace["id"])
    event = PullRequestEvent(
        repo=repo_name,
        pr_number=pr_number,
        author=author,
        author_merged_prs=merged_n,
        title=str(pull_request.get("title") or ""),
        body=str(pull_request.get("body") or ""),
        files_changed=int(pull_request.get("changed_files") or len(paths) or 1),
        lines_added=int(pull_request.get("additions") or 0),
        lines_deleted=int(pull_request.get("deletions") or 0),
        has_tests=has_tests,
        has_tests_source=source,
        ai_assisted=infer_ai_assisted(payload, None),
        touches_critical_paths=protected_hits(paths, policy.protected_paths),
        changed_paths=paths,
    )
    decision = triage_pull_request(event, policy)
    reasons = list(decision.reasons)
    if fetch_note:
        reasons.append(fetch_note)
        decision = decision.model_copy(update={"reasons": reasons})
    db.record_triage(
        workspace["id"],
        {
            "repo": event.repo,
            "pr_number": event.pr_number,
            "author": event.author,
            "action": decision.action,
            "signal_score": decision.signal_score,
            "has_tests": decision.has_tests,
            "has_tests_source": decision.has_tests_source,
            "reasons": decision.reasons,
            "source": "webhook",
        },
    )
    db.record_webhook(
        workspace["id"],
        {
            "github_event": event_name,
            "delivery_id": delivery,
            "status": "accepted",
            "detail": f"{decision.action} on {repo_name}#{pr_number}",
        },
    )
    return {
        "event": event_name,
        "triage": decision.model_dump(),
        "files_fetched": bool(token and paths),
    }


app = create_app()
