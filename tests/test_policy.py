from __future__ import annotations

from pathlib import Path

import pytest

from forgepulse.policy import PolicyError, load_policy


def test_loads_default_policy() -> None:
    policy = load_policy(Path("policies/default.yaml"))
    assert policy.rule_id == "default-org-policy"
    assert "auth/" in policy.protected_paths
    assert policy.require_tests_above_lines == 150
    assert policy.allow_unverified_ai_prs is False


def test_missing_and_invalid_policy(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(PolicyError, match="not found"):
        load_policy(missing)
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(PolicyError, match="empty"):
        load_policy(empty)
    listed = tmp_path / "list.yaml"
    listed.write_text("- just a list\n", encoding="utf-8")
    with pytest.raises(PolicyError, match="mapping"):
        load_policy(listed)
    broken = tmp_path / "broken.yaml"
    broken.write_text("rule_id: [\n", encoding="utf-8")
    with pytest.raises(PolicyError, match="valid YAML"):
        load_policy(broken)
