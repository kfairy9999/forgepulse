from __future__ import annotations

from forgepulse.graph import dependency_blast_radius
from forgepulse.models import PolicyRule
from forgepulse.store import MemoryStore


def test_shared_types_blast_radius_is_breadth_first(store: MemoryStore) -> None:
    result = dependency_blast_radius(store, "org/shared-types")
    assert result.total_impacted == 5
    # Immediate dependents come first, then their dependents, in queue order.
    assert result.downstream_repos[:3] == [
        "org/payments-core",
        "org/auth-gateway",
        "org/web-app",
    ]
    assert "org/billing-export" in result.downstream_repos
    assert "org/finance-ledger" in result.downstream_repos
    assert result.source_repo not in result.downstream_repos


def test_unknown_repo_has_zero_impact(store: MemoryStore) -> None:
    result = dependency_blast_radius(store, "org/does-not-exist")
    assert result.downstream_repos == []
    assert result.total_impacted == 0


def test_cycles_do_not_loop() -> None:
    store = MemoryStore(
        policy=PolicyRule(rule_id="cycle"),
        graph={"a": ["b"], "b": ["c", "a"], "c": ["a"]},
    )
    result = dependency_blast_radius(store, "a")
    assert result.downstream_repos == ["b", "c"]
    assert result.total_impacted == 2
