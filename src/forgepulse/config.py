"""Process settings. Safe defaults fail closed for webhooks."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def default_policy_path() -> Path:
    """Prefer an explicit path, then the repo copy, then the packaged default."""
    env = os.environ.get("FORGEPULSE_POLICY_PATH", "").strip()
    if env:
        return Path(env)
    repo_copy = Path.cwd() / "policies" / "default.yaml"
    if repo_copy.is_file():
        return repo_copy
    return Path(__file__).resolve().parent / "data" / "default_policy.yaml"


@dataclass(frozen=True)
class Settings:
    """Runtime settings.

    ``webhook_secret``: if set, every webhook must carry a valid
    ``X-Hub-Signature-256`` digest. The unsigned-dev bypass is never applied
    when a secret is set, including a value of ``dev-secret``.

    ``allow_unsigned``: only honored when no secret is configured. The safe
    default is false, so a misconfigured deployment rejects unsigned webhooks
    instead of accepting them.
    """

    webhook_secret: str | None
    allow_unsigned: bool
    policy_path: Path

    @classmethod
    def from_env(cls) -> Settings:
        secret = os.environ.get("FORGEPULSE_WEBHOOK_SECRET", "").strip() or None
        return cls(
            webhook_secret=secret,
            allow_unsigned=_truthy(os.environ.get("FORGEPULSE_ALLOW_UNSIGNED")),
            policy_path=default_policy_path(),
        )

    def unsigned_webhooks_allowed(self) -> bool:
        return self.webhook_secret is None and self.allow_unsigned
