from __future__ import annotations

import secrets
from pathlib import Path

from forgepulse.models import PolicyRule, SymbolIndexEntry
from forgepulse.sqlite_store import SqliteStore


def _store(path: Path) -> SqliteStore:
    return SqliteStore(str(path), secrets.token_hex(32))


def test_state_survives_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "fp.sqlite"
    key = secrets.token_hex(32)
    first = SqliteStore(str(db_path), key)
    created = first.create_workspace("Durable", "user", "http://test")
    workspace_id = created["workspace"]["id"]
    first.record_triage(
        workspace_id,
        {
            "repo": "acme/payments",
            "pr_number": 1,
            "author": "ada",
            "action": "standard-review",
            "signal_score": 0.4,
            "has_tests": True,
            "has_tests_source": "inferred",
            "reasons": ["tests present"],
            "source": "api",
        },
    )
    first.save_policy(workspace_id, PolicyRule(rule_id="custom", require_tests_above_lines=40))
    first.close()

    second = SqliteStore(str(db_path), key)
    policy = second.load_policy(workspace_id)
    assert policy.rule_id == "custom"
    assert policy.require_tests_above_lines == 40
    events = second.list_triage(workspace_id)
    assert len(events) == 1
    assert events[0]["repo"] == "acme/payments"
    token = created["token"]
    loaded = second.get_workspace_by_token(token)
    assert loaded is not None
    assert loaded["id"] == workspace_id
    second.close()


def test_workspaces_cannot_see_each_other(tmp_path: Path) -> None:
    store = _store(tmp_path / "fp.sqlite")
    one = store.create_workspace("One", "user", "http://test")
    two = store.create_workspace("Two", "user", "http://test")
    store.add_edge(one["workspace"]["id"], "a/repo", "b/repo")
    assert "a/repo" in store.load_graph(one["workspace"]["id"])
    assert "a/repo" not in store.load_graph(two["workspace"]["id"])
    store.upsert_symbol(
        one["workspace"]["id"],
        SymbolIndexEntry(repo="a/repo", symbol="Foo", file_path="foo.py", owners=["@a"]),
    )
    assert store.search_symbols(two["workspace"]["id"], "Foo") == []
    store.close()


def test_rate_limit_blocks_after_budget(tmp_path: Path) -> None:
    store = _store(tmp_path / "fp.sqlite")
    assert store.rate_limit("ip:test", limit=2, window_sec=60) is True
    assert store.rate_limit("ip:test", limit=2, window_sec=60) is True
    assert store.rate_limit("ip:test", limit=2, window_sec=60) is False
    store.close()
