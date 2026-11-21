from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forgepulse.app import create_app
from forgepulse.config import Settings
from forgepulse.models import PolicyRule
from forgepulse.seed import SEED_GRAPH, SEED_SYMBOLS
from forgepulse.store import MemoryStore
from forgepulse.webhook import (
    WebhookAuthError,
    pull_request_from_webhook,
    verify_github_signature,
)

SECRET = "unit-test-secret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _settings(secret: str | None, allow: bool, tmp_path: Path) -> Settings:
    path = tmp_path / "policy.yaml"
    path.write_text(Path("policies/default.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    return Settings(webhook_secret=secret, allow_unsigned=allow, policy_path=path)


def test_valid_signature_accepted() -> None:
    settings = Settings(
        webhook_secret=SECRET, allow_unsigned=False, policy_path=Path("policies/default.yaml")
    )
    body = b'{"ok":true}'
    verify_github_signature(body, _sign(body), settings)


def test_invalid_and_missing_signatures_rejected() -> None:
    settings = Settings(
        webhook_secret=SECRET, allow_unsigned=False, policy_path=Path("policies/default.yaml")
    )
    body = b'{"ok":true}'
    with pytest.raises(WebhookAuthError, match="Invalid"):
        verify_github_signature(body, _sign(body, "wrong-secret"), settings)
    with pytest.raises(WebhookAuthError, match="malformed"):
        verify_github_signature(body, None, settings)
    with pytest.raises(WebhookAuthError, match="malformed"):
        verify_github_signature(body, "md5=abcd", settings)


def test_dev_secret_does_not_bypass_when_set() -> None:
    settings = Settings(
        webhook_secret="dev-secret",
        allow_unsigned=True,
        policy_path=Path("policies/default.yaml"),
    )
    with pytest.raises(WebhookAuthError, match="malformed"):
        verify_github_signature(b"{}", None, settings)


def test_unsigned_allowed_only_without_a_secret() -> None:
    open_settings = Settings(
        webhook_secret=None, allow_unsigned=True, policy_path=Path("policies/default.yaml")
    )
    verify_github_signature(b"{}", None, open_settings)
    closed = Settings(
        webhook_secret=None, allow_unsigned=False, policy_path=Path("policies/default.yaml")
    )
    with pytest.raises(WebhookAuthError, match="not configured"):
        verify_github_signature(b"{}", None, closed)


def test_allow_unsigned_ignored_when_secret_is_set() -> None:
    settings = Settings(
        webhook_secret=SECRET, allow_unsigned=True, policy_path=Path("policies/default.yaml")
    )
    with pytest.raises(WebhookAuthError, match="malformed"):
        verify_github_signature(b"{}", None, settings)


def test_mapper_infers_tests_and_flags_unknown(policy: PolicyRule) -> None:
    with_files = pull_request_from_webhook(
        {
            "repository": {"full_name": "org/payments-core"},
            "pull_request": {
                "number": 9,
                "title": "Tighten session cookie flags",
                "body": "",
                "user": {"login": "ada"},
                "additions": 12,
                "deletions": 2,
                "changed_files": 1,
                "files": [{"filename": "auth/session.py"}],
            },
        },
        policy,
    )
    assert with_files is not None
    assert with_files.has_tests is False
    assert with_files.has_tests_source == "inferred"
    assert with_files.touches_critical_paths == ["auth/session.py"]

    unknown = pull_request_from_webhook(
        {
            "repository": {"full_name": "org/payments-core"},
            "pull_request": {
                "number": 10,
                "title": "Tighten session cookie flags",
                "user": {"login": "ada"},
                "additions": 12,
                "deletions": 2,
                "changed_files": 4,
            },
        },
        policy,
    )
    assert unknown is not None
    assert unknown.has_tests is None
    assert unknown.has_tests_source == "unknown"


def test_webhook_http_signature_and_inference(
    client: TestClient, unsigned_client: TestClient
) -> None:
    payload = {
        "repository": {"full_name": "org/auth-gateway"},
        "pull_request": {
            "number": 44,
            "title": "Add token chain regression tests",
            "body": "Generated with help from Claude for the scaffolding.",
            "user": {"login": "new-hire"},
            "author_merged_prs": 0,
            "additions": 40,
            "deletions": 2,
            "changed_files": 2,
            "files": [
                {"filename": "pkg/auth/chain.go"},
                {"filename": "pkg/auth/chain_test.go"},
            ],
        },
    }
    body = json.dumps(payload).encode()
    denied = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "Content-Type": "application/json"},
    )
    assert denied.status_code == 401

    ok = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(body),
        },
    )
    assert ok.status_code == 200
    triage = ok.json()["triage"]
    assert triage["has_tests"] is True
    assert triage["has_tests_source"] == "inferred"
    # First-time AI-assisted still quarantines even with tests.
    assert triage["action"] == "quarantine-bot-check"

    ignored = client.post(
        "/webhooks/github",
        content=json.dumps({"zen": "ok"}).encode(),
        headers={
            "X-GitHub-Event": "ping",
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(json.dumps({"zen": "ok"}).encode()),
        },
    )
    assert ignored.json()["status"] == "ignored"

    unsigned = unsigned_client.post(
        "/webhooks/github",
        json={"zen": "ok"},
        headers={"X-GitHub-Event": "ping"},
    )
    assert unsigned.status_code == 200


def test_closed_app_rejects_unsigned_without_secret(tmp_path: Path) -> None:
    settings = _settings(None, False, tmp_path)
    store = MemoryStore(policy=store_policy(settings), graph=SEED_GRAPH, symbols=SEED_SYMBOLS)
    client = TestClient(create_app(settings=settings, store=store))
    response = client.post(
        "/webhooks/github", json={"ok": True}, headers={"X-GitHub-Event": "ping"}
    )
    assert response.status_code == 401
    assert "not configured" in response.json()["detail"]


def store_policy(settings: Settings):
    from forgepulse.policy import load_policy

    return load_policy(settings.policy_path)
