from __future__ import annotations

from forgepulse.models import CIJobRequest
from forgepulse.runners import enqueue_ci_job, route_job
from forgepulse.store import MemoryStore


def test_failover_when_github_hosted_is_unhealthy() -> None:
    job = CIJobRequest(
        job_id="j1",
        repo="org/web-app",
        branch="main",
        preferred_pool="ephemeral-cloud",
        github_hosted_healthy=False,
    )
    pool, reason = route_job(job)
    assert pool == "fallback-alt"
    assert "degraded" in reason


def test_preferred_pool_used_when_healthy() -> None:
    job = CIJobRequest(
        job_id="j2",
        repo="org/web-app",
        branch="main",
        preferred_pool="bare-metal",
        github_hosted_healthy=False,
    )
    pool, reason = route_job(job)
    assert pool == "bare-metal"
    assert "preferred" in reason.lower()


def test_queue_orders_by_priority_then_monotonic_seq(store: MemoryStore) -> None:
    low = CIJobRequest(job_id="low", repo="org/a", branch="main", priority=10)
    high_a = CIJobRequest(job_id="high-a", repo="org/a", branch="main", priority=90)
    high_b = CIJobRequest(job_id="high-b", repo="org/a", branch="main", priority=90)
    enqueue_ci_job(store, low)
    first, _ = enqueue_ci_job(store, high_a)
    second, depth = enqueue_ci_job(store, high_b)
    assert depth == 3
    jobs = store.queue()
    assert [job.job_id for job in jobs] == ["high-a", "high-b", "low"]
    assert first.enqueue_seq < second.enqueue_seq
    assert jobs[0].enqueue_seq < jobs[1].enqueue_seq
