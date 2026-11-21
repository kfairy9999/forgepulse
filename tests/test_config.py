from __future__ import annotations

from pathlib import Path

from forgepulse.config import (
    Settings,
    default_database_path,
    default_policy_path,
    resolve_master_key,
)


def test_unsigned_allowed_only_without_secret(monkeypatch: object) -> None:
    monkeypatch.setenv("FORGEPULSE_WEBHOOK_SECRET", "")  # type: ignore[attr-defined]
    monkeypatch.setenv("FORGEPULSE_ALLOW_UNSIGNED", "true")  # type: ignore[attr-defined]
    monkeypatch.setenv("FORGEPULSE_MASTER_KEY", "ab" * 32)  # type: ignore[attr-defined]
    settings = Settings.from_env()
    assert settings.unsigned_webhooks_allowed() is True

    monkeypatch.setenv("FORGEPULSE_WEBHOOK_SECRET", "super-secret")  # type: ignore[attr-defined]
    settings = Settings.from_env()
    assert settings.webhook_secret == "super-secret"
    assert settings.unsigned_webhooks_allowed() is False


def test_master_key_round_trip(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[attr-defined]
    monkeypatch.delenv("FORGEPULSE_MASTER_KEY", raising=False)  # type: ignore[attr-defined]
    first = resolve_master_key()
    assert len(first) == 64
    second = resolve_master_key()
    assert first == second
    monkeypatch.setenv("FORGEPULSE_MASTER_KEY", "cd" * 32)  # type: ignore[attr-defined]
    assert resolve_master_key() == "cd" * 32


def test_database_and_policy_paths(tmp_path: Path, monkeypatch: object) -> None:
    custom_db = tmp_path / "custom.sqlite"
    custom_policy = tmp_path / "policy.yaml"
    custom_policy.write_text("rule_id: x\n", encoding="utf-8")
    monkeypatch.setenv("FORGEPULSE_DATABASE", str(custom_db))  # type: ignore[attr-defined]
    monkeypatch.setenv("FORGEPULSE_POLICY_PATH", str(custom_policy))  # type: ignore[attr-defined]
    assert default_database_path() == custom_db
    assert default_policy_path() == custom_policy
