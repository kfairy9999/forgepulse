from __future__ import annotations

import pytest

from forgepulse.config import Settings
from forgepulse.models import PolicyRule
from forgepulse.webhook import (
    WebhookAuthError,
    parse_json_body,
    pull_request_from_webhook,
    verify_github_signature,
)


def test_parse_json_body_rejects_arrays() -> None:
    with pytest.raises(ValueError):
        parse_json_body(b"[1]")
    assert parse_json_body(b'{"ok": true}')["ok"] is True


def test_verify_signature_fail_closed(tmp_path: object) -> None:
    from pathlib import Path

    settings = Settings(
        webhook_secret=None,
        allow_unsigned=False,
        policy_path=Path("policies/default.yaml"),
        database_path=Path("unused.sqlite"),
        master_key="ab" * 32,
    )
    with pytest.raises(WebhookAuthError):
        verify_github_signature(b"{}", None, settings)


def test_pull_request_from_webhook_maps_files() -> None:
    policy = PolicyRule(rule_id="default")
    event = pull_request_from_webhook(
        {
            "pull_request": {
                "number": 3,
                "title": "Fix",
                "body": "",
                "user": {"login": "ada"},
                "additions": 4,
                "deletions": 1,
                "changed_files": 2,
            },
            "repository": {"full_name": "acme/payments"},
            "commits": [{"added": ["tests/test_x.py"], "modified": ["src/x.py"], "removed": []}],
        },
        policy,
    )
    assert event is not None
    assert event.has_tests is True
    assert "tests/test_x.py" in event.changed_paths
    assert pull_request_from_webhook({"action": "ping"}, policy) is None
