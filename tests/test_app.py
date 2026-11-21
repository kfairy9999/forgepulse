from __future__ import annotations

import threading

from fastapi.testclient import TestClient

from forgepulse.models import CIJobRequest
from forgepulse.runners import enqueue_ci_job
from forgepulse.store import MemoryStore


def test_health_and_policy_and_playground(client: TestClient) -> None:
    health = client.get("/healthz").json()
    assert health["status"] == "ok"
    assert health["storage"] == "in-memory"
    policy = client.get("/policy").json()
    assert policy["rule_id"] == "default-org-policy"
    home = client.get("/")
    assert home.status_code == 200
    assert "text/html" in home.headers["content-type"]
    assert "ForgePulse" in home.text
    favicon = client.get("/favicon.svg")
    assert favicon.status_code == 200
    assert client.get("/robots.txt").text.startswith("User-agent:")


def test_triage_and_graph_and_search_endpoints(client: TestClient) -> None:
    triage = client.post(
        "/triage/pr",
        json={
            "repo": "org/payments-core",
            "pr_number": 7,
            "author": "ada",
            "author_merged_prs": 8,
            "title": "Fix settlement rounding for weekend batches",
            "has_tests": True,
            "lines_added": 20,
            "lines_deleted": 3,
        },
    )
    assert triage.status_code == 200
    assert triage.json()["action"] == "fast-track"

    radius = client.get("/graph/impact/org/shared-types")
    assert radius.status_code == 200
    body = radius.json()
    assert body["total_impacted"] == 5
    assert "org/finance-ledger" in body["downstream_repos"]

    search = client.get("/index/search", params={"q": "verify_token_chain"})
    assert search.json()["count"] == 1
    indexed = client.post(
        "/index/symbol",
        json={
            "repo": "org/web-app",
            "symbol": "RefundBanner",
            "file_path": "app/refund/RefundBanner.tsx",
            "owners": ["@web-eng"],
            "related_prs": [910],
        },
    )
    assert indexed.json()["status"] == "indexed"
    assert client.get("/index/search", params={"q": "RefundBanner"}).json()["count"] == 1


def test_enqueue_and_list_queue(client: TestClient) -> None:
    first = client.post(
        "/ci/enqueue",
        json={
            "job_id": "build-1",
            "repo": "org/web-app",
            "branch": "main",
            "priority": 40,
        },
    )
    second = client.post(
        "/ci/enqueue",
        json={
            "job_id": "build-2",
            "repo": "org/web-app",
            "branch": "main",
            "priority": 80,
            "preferred_pool": "ephemeral-cloud",
            "github_hosted_healthy": False,
        },
    )
    assert first.status_code == 200
    assert second.json()["queued"]["routed_pool"] == "fallback-alt"
    listed = client.get("/ci/queue").json()
    assert listed["queue_depth"] == 2
    assert listed["jobs"][0]["job_id"] == "build-2"
    assert listed["jobs"][0]["enqueue_seq"] == 2


def test_webhook_rejects_invalid_json(client: TestClient) -> None:
    import hashlib
    import hmac

    body = b"not-json"
    signature = "sha256=" + hmac.new(b"unit-test-secret", body, hashlib.sha256).hexdigest()
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 400


def test_store_is_thread_safe(store: MemoryStore) -> None:
    errors: list[BaseException] = []

    def worker(prefix: str) -> None:
        try:
            for index in range(40):
                enqueue_ci_job(
                    store,
                    CIJobRequest(
                        job_id=f"{prefix}-{index}",
                        repo="org/web-app",
                        branch="main",
                        priority=50,
                    ),
                )
        except BaseException as exc:  # pragma: no cover - surfaces a failure
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("a", "b", "c", "d")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    jobs = store.queue()
    assert len(jobs) == 160
    seqs = [job.enqueue_seq for job in jobs]
    assert len(set(seqs)) == 160
    assert sorted(seqs) == list(range(1, 161))
