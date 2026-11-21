from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forgepulse.config import Settings, default_policy_path
from forgepulse.models import PolicyRule
from forgepulse.store import MemoryStore
from forgepulse.webhook import parse_json_body, pull_request_from_webhook


def test_settings_from_env_and_unsigned_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy = tmp_path / "policy.yaml"
    policy.write_text(Path("policies/default.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("FORGEPULSE_POLICY_PATH", str(policy))
    monkeypatch.setenv("FORGEPULSE_WEBHOOK_SECRET", "from-env")
    monkeypatch.setenv("FORGEPULSE_ALLOW_UNSIGNED", "true")
    settings = Settings.from_env()
    assert settings.webhook_secret == "from-env"
    assert settings.allow_unsigned is True
    assert settings.unsigned_webhooks_allowed() is False
    monkeypatch.delenv("FORGEPULSE_WEBHOOK_SECRET")
    open_settings = Settings.from_env()
    assert open_settings.unsigned_webhooks_allowed() is True


def test_default_policy_path_prefers_cwd() -> None:
    path = default_policy_path()
    assert path.name in {"default.yaml", "default_policy.yaml"}


def test_store_set_policy_and_queue_depth(store: MemoryStore) -> None:
    store.set_policy(PolicyRule(rule_id="swapped"))
    assert store.policy().rule_id == "swapped"
    assert store.queue_depth() == 0


def test_parse_json_body_and_ignored_pull_request(policy: PolicyRule) -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_json_body(b"not-json")
    with pytest.raises(ValueError, match="JSON object"):
        parse_json_body(b"[1]")
    assert pull_request_from_webhook({"ok": True}, policy) is None


def test_webhook_pull_request_without_pr_is_ignored(client: TestClient) -> None:
    import hashlib
    import hmac
    import json

    body = json.dumps({"action": "opened"}).encode()
    signature = "sha256=" + hmac.new(b"unit-test-secret", body, hashlib.sha256).hexdigest()
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "Content-Type": "application/json",
            "X-Hub-Signature-256": signature,
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
