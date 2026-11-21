"""Shared fixtures: isolated SQLite workspaces and authenticated clients."""

from __future__ import annotations

import secrets
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from forgepulse.app import create_app
from forgepulse.config import Settings
from forgepulse.models import PolicyRule
from forgepulse.policy import load_policy
from forgepulse.seed import SEED_GRAPH, SEED_SYMBOLS
from forgepulse.store import MemoryStore


@pytest.fixture()
def master_key() -> str:
    return secrets.token_hex(32)


@pytest.fixture()
def settings(tmp_path: Path, master_key: str) -> Settings:
    policy = Path.cwd() / "policies" / "default.yaml"
    if not policy.is_file():
        policy = Path(__file__).resolve().parents[1] / "policies" / "default.yaml"
    return Settings(
        webhook_secret=None,
        allow_unsigned=False,
        policy_path=policy,
        database_path=tmp_path / "forgepulse.sqlite",
        master_key=master_key,
    )


@pytest.fixture()
def client(settings: Settings) -> Generator[TestClient, None, None]:
    app = create_app(settings=settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def workspace(client: TestClient) -> dict[str, object]:
    response = client.post("/workspaces", json={"name": "Test workspace"})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture()
def auth(workspace: dict[str, object]) -> dict[str, str]:
    return {"Authorization": f"Bearer {workspace['token']}"}


@pytest.fixture()
def policy() -> PolicyRule:
    path = Path.cwd() / "policies" / "default.yaml"
    if not path.is_file():
        path = Path(__file__).resolve().parents[1] / "policies" / "default.yaml"
    return load_policy(path)


@pytest.fixture()
def default_policy(policy: PolicyRule) -> PolicyRule:
    return policy


@pytest.fixture()
def store(policy: PolicyRule) -> MemoryStore:
    return MemoryStore(policy=policy, graph=SEED_GRAPH, symbols=SEED_SYMBOLS)
