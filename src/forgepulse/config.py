"""Process settings. Safe defaults fail closed for unsigned webhooks."""

from __future__ import annotations

import contextlib
import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def default_policy_path() -> Path:
    env = os.environ.get("FORGEPULSE_POLICY_PATH", "").strip()
    if env:
        return Path(env)
    repo_copy = Path.cwd() / "policies" / "default.yaml"
    if repo_copy.is_file():
        return repo_copy
    return Path(__file__).resolve().parent / "data" / "default_policy.yaml"


def default_database_path() -> Path:
    env = os.environ.get("FORGEPULSE_DATABASE", "").strip()
    if env:
        return Path(env)
    data = Path.cwd() / ".data"
    data.mkdir(exist_ok=True)
    return data / "forgepulse.sqlite"


def resolve_master_key() -> str:
    env = os.environ.get("FORGEPULSE_MASTER_KEY", "").strip()
    if env:
        return env
    key_file = Path.cwd() / ".data" / "master.key"
    if key_file.is_file():
        return key_file.read_text(encoding="utf-8").strip()
    key_file.parent.mkdir(exist_ok=True)
    key = secrets.token_hex(32)
    key_file.write_text(key, encoding="utf-8")
    with contextlib.suppress(OSError):
        key_file.chmod(0o600)
    return key


@dataclass(frozen=True)
class Settings:
    webhook_secret: str | None
    allow_unsigned: bool
    policy_path: Path
    database_path: Path
    master_key: str

    @classmethod
    def from_env(cls) -> Settings:
        secret = os.environ.get("FORGEPULSE_WEBHOOK_SECRET", "").strip() or None
        return cls(
            webhook_secret=secret,
            allow_unsigned=_truthy(os.environ.get("FORGEPULSE_ALLOW_UNSIGNED")),
            policy_path=default_policy_path(),
            database_path=default_database_path(),
            master_key=resolve_master_key(),
        )

    def unsigned_webhooks_allowed(self) -> bool:
        return self.webhook_secret is None and self.allow_unsigned
