from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forgepulse.app import create_app
from forgepulse.config import Settings
from forgepulse.models import PolicyRule
from forgepulse.policy import load_policy
from forgepulse.seed import SEED_GRAPH, SEED_SYMBOLS
from forgepulse.store import MemoryStore


@pytest.fixture
def policy() -> PolicyRule:
    return load_policy(Path("policies/default.yaml"))


@pytest.fixture
def store(policy: PolicyRule) -> MemoryStore:
    return MemoryStore(policy=policy, graph=SEED_GRAPH, symbols=SEED_SYMBOLS)


@pytest.fixture
def settings(tmp_path: Path, policy: PolicyRule) -> Settings:
    path = tmp_path / "policy.yaml"
    path.write_text(Path("policies/default.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    return Settings(webhook_secret="unit-test-secret", allow_unsigned=False, policy_path=path)


@pytest.fixture
def client(settings: Settings, store: MemoryStore) -> TestClient:
    return TestClient(create_app(settings=settings, store=store))


@pytest.fixture
def unsigned_client(tmp_path: Path, store: MemoryStore) -> TestClient:
    path = tmp_path / "policy.yaml"
    path.write_text(Path("policies/default.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    settings = Settings(webhook_secret=None, allow_unsigned=True, policy_path=path)
    return TestClient(create_app(settings=settings, store=store))
