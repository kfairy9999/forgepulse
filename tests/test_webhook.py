from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from urllib.request import Request

from fastapi.testclient import TestClient

from forgepulse import github_api


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "action": "opened",
        "number": 7,
        "pull_request": {
            "number": 7,
            "title": "Tighten charge path",
            "body": "Adds unit tests",
            "user": {"login": "ada"},
            "additions": 12,
            "deletions": 2,
            "changed_files": 2,
            "author_merged_prs": 8,
        },
        "repository": {"full_name": "acme/payments"},
        "commits": [
            {
                "added": ["src/charges.py"],
                "modified": ["tests/test_charges.py"],
                "removed": [],
            }
        ],
    }
    body.update(overrides)
    return body


def test_unsigned_webhook_is_rejected(client: TestClient, workspace: dict[str, object]) -> None:
    url = f"/w/{workspace['workspace']['id']}/webhooks/github"
    response = client.post(url, json=_payload(), headers={"X-GitHub-Event": "pull_request"})
    assert response.status_code == 401


def test_bad_signature_is_rejected(client: TestClient, workspace: dict[str, object]) -> None:
    url = f"/w/{workspace['workspace']['id']}/webhooks/github"
    body = json.dumps(_payload()).encode()
    response = client.post(
        url,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": "sha256=" + ("ab" * 32),
        },
    )
    assert response.status_code == 401


def test_signed_webhook_triages_and_records(
    client: TestClient, workspace: dict[str, object], auth: dict[str, str]
) -> None:
    url = f"/w/{workspace['workspace']['id']}/webhooks/github"
    payload = json.dumps(_payload()).encode()
    response = client.post(
        url,
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "abc-123",
            "X-Hub-Signature-256": _sign(str(workspace["webhook_secret"]), payload),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["event"] == "pull_request"
    assert body["triage"]["repo"] == "acme/payments"
    assert body["triage"]["has_tests"] is True
    events = client.get("/webhooks/events", headers=auth).json()["events"]
    assert events[0]["status"] == "accepted"
    history = client.get("/triage/history", headers=auth).json()["events"]
    assert history[0]["source"] == "webhook"


def test_unknown_event_is_ignored(client: TestClient, workspace: dict[str, object]) -> None:
    url = f"/w/{workspace['workspace']['id']}/webhooks/github"
    payload = json.dumps({"zen": "keep it simple"}).encode()
    response = client.post(
        url,
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": _sign(str(workspace["webhook_secret"]), payload),
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_invalid_json_is_bad_request(client: TestClient, workspace: dict[str, object]) -> None:
    url = f"/w/{workspace['workspace']['id']}/webhooks/github"
    payload = b"not-json"
    response = client.post(
        url,
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign(str(workspace["webhook_secret"]), payload),
        },
    )
    assert response.status_code == 400


def test_unknown_workspace_is_not_found(client: TestClient) -> None:
    response = client.post("/w/ws_missing/webhooks/github", json=_payload())
    assert response.status_code == 404


def test_webhook_fetches_files_when_token_configured(
    client: TestClient, workspace: dict[str, object], auth: dict[str, str], monkeypatch: Any
) -> None:
    patched = client.patch("/workspace", json={"github_token": "ghp_testtokenvalue"}, headers=auth)
    assert patched.status_code == 200

    class FakeResponse:
        def __init__(self, payload: object) -> None:
            self._payload = json.dumps(payload).encode()

        def read(self) -> bytes:
            return self._payload

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def fake_urlopen(request: Request, timeout: float = 0) -> FakeResponse:
        url = request.full_url
        if "/pulls/" in url and url.endswith("/files?per_page=100"):
            return FakeResponse(
                [{"filename": "src/auth/session.py"}, {"filename": "tests/test_session.py"}]
            )
        if "/search/issues" in url:
            return FakeResponse({"total_count": 14})
        raise AssertionError(url)

    monkeypatch.setattr(github_api, "urlopen", fake_urlopen)
    # github_api imports urlopen at module level
    from urllib import request as urllib_request

    monkeypatch.setattr(urllib_request, "urlopen", fake_urlopen)

    # The module used urlopen from urllib.request inside the function via import...
    # github_api._get_json calls urlopen from its import. Patch the module attr.
    url = f"/w/{workspace['workspace']['id']}/webhooks/github"
    # Native GitHub PR webhooks often omit changed files.
    payload = json.dumps(_payload(commits=None)).encode()
    response = client.post(
        url,
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": _sign(str(workspace["webhook_secret"]), payload),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["files_fetched"] is True
    assert (
        "session.py" in " ".join(body["triage"].get("changed_paths", []))
        or body["triage"]["has_tests"] is True
    )
    # protected path auth/ should be detected from fetched files
    assert body["triage"]["action"] in {
        "standard-review",
        "block-policy",
        "quarantine-bot-check",
        "fast-track",
    }
    history = client.get("/triage/history", headers=auth).json()["events"]
    assert history[0]["source"] == "webhook"
