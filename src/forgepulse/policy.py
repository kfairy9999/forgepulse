"""PolicyVault: load portable rules from YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from forgepulse.models import PolicyRule


class PolicyError(ValueError):
    """The policy file is missing or invalid."""


def load_policy(path: Path) -> PolicyRule:
    if not path.is_file():
        raise PolicyError(f"Policy file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyError(f"Policy file is not valid YAML: {path}") from exc
    if raw is None:
        raise PolicyError(f"Policy file is empty: {path}")
    if not isinstance(raw, dict):
        raise PolicyError(f"Policy file must be a mapping: {path}")
    return PolicyRule.model_validate(_stringify_keys(raw))


def _stringify_keys(raw: dict[object, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in raw.items()}
