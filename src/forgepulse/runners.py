"""RunnerMesh: priority queue and multi-pool failover."""

from __future__ import annotations

from datetime import datetime, timezone

from forgepulse.models import CIJobRequest, QueuedJob, RunnerPool
from forgepulse.store import MemoryStore


def route_job(job: CIJobRequest) -> tuple[RunnerPool, str]:
    if not job.github_hosted_healthy and job.preferred_pool == "ephemeral-cloud":
        return (
            "fallback-alt",
            "GitHub-hosted pool marked degraded; routed to fallback runner pool",
        )
    return job.preferred_pool, "Routed to preferred runner pool"


def enqueue_ci_job(store: MemoryStore, job: CIJobRequest) -> tuple[QueuedJob, int]:
    routed_pool, route_reason = route_job(job)
    queued = QueuedJob(
        job_id=job.job_id,
        repo=job.repo,
        branch=job.branch,
        priority=job.priority,
        routed_pool=routed_pool,
        route_reason=route_reason,
        enqueue_seq=store.next_seq(),
        enqueued_at=datetime.now(timezone.utc).isoformat(),
    )
    return store.enqueue(queued)
