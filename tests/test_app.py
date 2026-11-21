from __future__ import annotations

from fastapi.testclient import TestClient


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.2.0"
    assert body["storage"] == "sqlite"
    assert body["mode"] == "durable"


def test_playground_and_static(client: TestClient) -> None:
    home = client.get("/")
    assert home.status_code == 200
    assert "text/html" in home.headers["content-type"]
    assert "ForgePulse" in home.text
    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    assert "Allow: /" in robots.text
    icon = client.get("/favicon.svg")
    assert icon.status_code == 200


def test_mutations_require_auth(client: TestClient) -> None:
    assert client.get("/workspace").status_code == 401
    assert client.get("/policy").status_code == 401
    assert client.post("/triage/pr", json=_sample_pr()).status_code == 401
    bad = client.get("/workspace", headers={"Authorization": "Bearer fp_live_deadbeef"})
    assert bad.status_code == 401


def test_header_token_alias(client: TestClient, workspace: dict[str, object]) -> None:
    token = str(workspace["token"])
    response = client.get("/workspace", headers={"X-ForgePulse-Token": token})
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == workspace["workspace"]["id"]
    assert body["github_token_configured"] is False
    assert body["storage"] == "sqlite"


def test_triage_and_history_persist(client: TestClient, auth: dict[str, str]) -> None:
    first = client.post("/triage/pr", json=_sample_pr(), headers=auth)
    assert first.status_code == 200
    decision = first.json()
    assert decision["action"] in {
        "fast-track",
        "standard-review",
        "quarantine-bot-check",
        "block-policy",
    }
    assert "signal_score" in decision
    history = client.get("/triage/history", headers=auth)
    assert history.status_code == 200
    events = history.json()["events"]
    assert len(events) == 1
    assert events[0]["repo"] == "org/payments-core"
    assert events[0]["source"] == "api"


def test_workspaces_are_isolated(client: TestClient) -> None:
    one = client.post("/workspaces", json={"name": "Alpha"}).json()
    two = client.post("/workspaces", json={"name": "Beta"}).json()
    auth_one = {"Authorization": f"Bearer {one['token']}"}
    auth_two = {"Authorization": f"Bearer {two['token']}"}
    client.post("/triage/pr", json=_sample_pr(repo="alpha/repo"), headers=auth_one)
    client.post("/triage/pr", json=_sample_pr(repo="beta/repo"), headers=auth_two)
    hist_one = client.get("/triage/history", headers=auth_one).json()["events"]
    hist_two = client.get("/triage/history", headers=auth_two).json()["events"]
    assert [item["repo"] for item in hist_one] == ["alpha/repo"]
    assert [item["repo"] for item in hist_two] == ["beta/repo"]


def test_policy_round_trip_and_validation(client: TestClient, auth: dict[str, str]) -> None:
    current = client.get("/policy", headers=auth)
    assert current.status_code == 200
    policy = current.json()
    policy["require_tests_above_lines"] = 80
    policy["protected_paths"] = ["secrets/", "auth/"]
    saved = client.put("/policy", json=policy, headers=auth)
    assert saved.status_code == 200
    assert saved.json()["require_tests_above_lines"] == 80
    again = client.get("/policy", headers=auth)
    assert again.json()["protected_paths"] == ["secrets/", "auth/"]
    invalid = client.put(
        "/policy",
        json={**policy, "require_tests_above_lines": -1},
        headers=auth,
    )
    assert invalid.status_code == 422
    empty = client.put("/policy", json={**policy, "rule_id": ""}, headers=auth)
    assert empty.status_code == 422


def test_ci_queue_is_priority_then_fifo(client: TestClient, auth: dict[str, str]) -> None:
    low = client.post(
        "/ci/enqueue",
        json={"job_id": "low", "repo": "acme/a", "branch": "main", "priority": 10},
        headers=auth,
    )
    high = client.post(
        "/ci/enqueue",
        json={"job_id": "high", "repo": "acme/a", "branch": "main", "priority": 90},
        headers=auth,
    )
    mid = client.post(
        "/ci/enqueue",
        json={"job_id": "mid", "repo": "acme/a", "branch": "main", "priority": 90},
        headers=auth,
    )
    assert low.status_code == 200
    assert "does not launch runners" in high.json()["disclaimer"]
    queue = client.get("/ci/queue", headers=auth).json()
    ids = [job["job_id"] for job in queue["jobs"]]
    assert ids == ["high", "mid", "low"]
    assert mid.json()["queued"]["enqueue_seq"] == 3


def test_graph_edges_and_blast_radius(client: TestClient, auth: dict[str, str]) -> None:
    graph = client.get("/graph", headers=auth)
    assert graph.status_code == 200
    assert "org/payments-core" in graph.json()["edges"]
    added = client.post(
        "/graph/edges",
        json={"source": "org/payments-core", "dest": "org/new-service"},
        headers=auth,
    )
    assert added.status_code == 200
    impact = client.get("/graph/impact/org/payments-core", headers=auth)
    assert impact.status_code == 200
    body = impact.json()
    assert body["source_repo"] == "org/payments-core"
    assert "org/payments-core" not in body["downstream_repos"]
    assert "org/new-service" in body["downstream_repos"]
    removed = client.request(
        "DELETE",
        "/graph/edges",
        json={"source": "org/payments-core", "dest": "org/new-service"},
        headers=auth,
    )
    assert removed.status_code == 200
    after = client.get("/graph/impact/org/payments-core", headers=auth).json()
    assert "org/new-service" not in after["downstream_repos"]
    missing = client.post("/graph/edges", json={"source": "", "dest": "x"}, headers=auth)
    assert missing.status_code == 422


def test_symbol_index_search_and_upsert(client: TestClient, auth: dict[str, str]) -> None:
    seed = client.get("/index/search", params={"q": "settlement"}, headers=auth)
    assert seed.status_code == 200
    assert seed.json()["count"] >= 1
    created = client.post(
        "/index/symbol",
        json={
            "repo": "org/payments-core",
            "symbol": "RefundService",
            "file_path": "src/refunds.py",
            "owners": ["@billing"],
            "related_prs": [99],
        },
        headers=auth,
    )
    assert created.status_code == 200
    found = client.get("/index/search", params={"q": "RefundService"}, headers=auth)
    assert found.json()["count"] == 1
    again = client.post(
        "/index/symbol",
        json={
            "repo": "org/payments-core",
            "symbol": "RefundService",
            "file_path": "src/refunds.py",
            "owners": ["@billing"],
            "related_prs": [100],
        },
        headers=auth,
    )
    assert again.status_code == 200
    merged = client.get("/index/search", params={"q": "RefundService"}, headers=auth)
    prs = merged.json()["matches"][0]["related_prs"]
    assert prs == [99, 100]


def test_github_token_and_rotate_secret(
    client: TestClient, auth: dict[str, str], workspace: dict[str, object]
) -> None:
    patched = client.patch("/workspace", json={"github_token": "ghp_testdevtoken"}, headers=auth)
    assert patched.status_code == 200
    assert patched.json()["github_token_configured"] is True
    cleared = client.patch("/workspace", json={"github_token": ""}, headers=auth)
    assert cleared.json()["github_token_configured"] is False
    invalid = client.patch("/workspace", json={"github_token": "short"}, headers=auth)
    assert invalid.status_code == 422
    rotated = client.post("/workspace/rotate-webhook-secret", headers=auth)
    assert rotated.status_code == 200
    assert rotated.json()["webhook_secret"].startswith("whsec_")
    assert rotated.json()["webhook_secret"] != workspace["webhook_secret"]


def test_guest_workspace(client: TestClient) -> None:
    response = client.post("/workspaces/guest")
    assert response.status_code == 201
    body = response.json()
    assert body["workspace"]["kind"] == "guest"
    auth = {"Authorization": f"Bearer {body['token']}"}
    policy = client.get("/policy", headers=auth)
    assert policy.status_code == 200


def _sample_pr(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "repo": "org/payments-core",
        "pr_number": 42,
        "author": "ada",
        "author_merged_prs": 20,
        "title": "Add refund path",
        "body": "Includes tests.",
        "files_changed": 3,
        "lines_added": 40,
        "lines_deleted": 4,
        "has_tests": True,
        "has_tests_source": "explicit",
        "ai_assisted": False,
        "touches_critical_paths": [],
        "changed_paths": ["src/refunds.py", "tests/test_refunds.py"],
    }
    payload.update(overrides)
    return payload
