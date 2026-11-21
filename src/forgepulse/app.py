"""FastAPI application. In-memory prototype, not a production service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from forgepulse import __version__
from forgepulse.atlas import search_symbols, upsert_symbol
from forgepulse.config import Settings
from forgepulse.graph import dependency_blast_radius
from forgepulse.models import (
    BlastRadius,
    CIJobRequest,
    PolicyRule,
    PullRequestEvent,
    SymbolIndexEntry,
    TriageDecision,
)
from forgepulse.policy import PolicyError, load_policy
from forgepulse.runners import enqueue_ci_job
from forgepulse.seed import SEED_GRAPH, SEED_SYMBOLS
from forgepulse.store import MemoryStore
from forgepulse.triage import triage_pull_request
from forgepulse.webhook import (
    WebhookAuthError,
    parse_json_body,
    pull_request_from_webhook,
    verify_github_signature,
)

ROBOTS = "User-agent: *\nAllow: /\n"

FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" fill="#f3efe4"/>'
    '<rect x="6" y="14" width="20" height="4" fill="#9d2c14"/>'
    '<rect x="14" y="6" width="4" height="20" fill="#1d1a16"/>'
    "</svg>"
)


def _playground_html() -> str:
    candidates = [
        Path.cwd() / "demo" / "playground.html",
        Path(__file__).resolve().parents[2] / "demo" / "playground.html",
        Path(__file__).resolve().parent / "static" / "index.html",
    ]
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>ForgePulse</title></head><body>"
        "<p>ForgePulse playground file is missing. See the README.</p>"
        "</body></html>"
    )


def create_app(
    *,
    settings: Settings | None = None,
    store: MemoryStore | None = None,
) -> FastAPI:
    config = settings or Settings.from_env()
    try:
        policy = load_policy(config.policy_path)
    except PolicyError as exc:
        raise RuntimeError(str(exc)) from exc
    memory = store or MemoryStore(policy=policy, graph=SEED_GRAPH, symbols=SEED_SYMBOLS)

    app = FastAPI(
        title="ForgePulse",
        version=__version__,
        description=(
            "Prototype. In-memory state. Not production. "
            "TriageGate, RunnerMesh, GraphSync, CodeAtlas, and PolicyVault."
        ),
    )
    app.state.settings = config
    app.state.store = memory

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def playground() -> HTMLResponse:
        return HTMLResponse(
            _playground_html(),
            headers={"cache-control": "public, max-age=60"},
        )

    @app.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
    def robots() -> PlainTextResponse:
        return PlainTextResponse(ROBOTS, headers={"cache-control": "public, max-age=86400"})

    @app.get("/favicon.svg", include_in_schema=False)
    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(
            FAVICON,
            media_type="image/svg+xml",
            headers={"cache-control": "public, max-age=86400"},
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {
            "status": "ok",
            "version": __version__,
            "mode": "prototype",
            "storage": "in-memory",
        }

    @app.get("/policy", response_model=PolicyRule)
    def get_policy() -> PolicyRule:
        return memory.policy()

    @app.post("/triage/pr", response_model=TriageDecision)
    def triage(pr: PullRequestEvent) -> TriageDecision:
        return triage_pull_request(pr, memory.policy())

    @app.post("/ci/enqueue")
    def enqueue(job: CIJobRequest) -> dict[str, Any]:
        queued, depth = enqueue_ci_job(memory, job)
        return {"queued": queued.model_dump(), "queue_depth": depth}

    @app.get("/ci/queue")
    def list_queue() -> dict[str, Any]:
        jobs = memory.queue()
        return {"queue_depth": len(jobs), "jobs": [item.model_dump() for item in jobs]}

    @app.get("/graph/impact/{repo:path}", response_model=BlastRadius)
    def blast_radius(repo: str) -> BlastRadius:
        return dependency_blast_radius(memory, repo)

    @app.post("/index/symbol")
    def index_symbol(entry: SymbolIndexEntry) -> dict[str, Any]:
        total = upsert_symbol(memory, entry)
        return {"status": "indexed", "total_symbols": total}

    @app.get("/index/search")
    def search(q: str = Query(default="", min_length=0)) -> dict[str, Any]:
        matches = search_symbols(memory, q)
        return {
            "query": q,
            "count": len(matches),
            "matches": [item.model_dump() for item in matches],
        }

    @app.post("/webhooks/github")
    async def github_webhook(
        request: Request,
        x_github_event: str | None = Header(default=None),
        x_hub_signature_256: str | None = Header(default=None),
    ) -> dict[str, Any]:
        raw_body = await request.body()
        try:
            verify_github_signature(raw_body, x_hub_signature_256, config)
        except WebhookAuthError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        try:
            payload = parse_json_body(raw_body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if x_github_event == "pull_request":
            event = pull_request_from_webhook(payload, memory.policy())
            if event is None:
                return {"event": x_github_event, "status": "ignored"}
            decision = triage_pull_request(event, memory.policy())
            return {"event": x_github_event, "triage": decision.model_dump()}
        return {"event": x_github_event or "unknown", "status": "ignored"}

    return app


app = create_app()
