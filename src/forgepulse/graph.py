"""GraphSync: downstream blast radius over a repository dependency graph."""

from __future__ import annotations

from collections import deque

from forgepulse.models import BlastRadius
from forgepulse.store import MemoryStore


def dependency_blast_radius(store: MemoryStore, repo: str) -> BlastRadius:
    """Breadth-first walk of downstream dependents.

    The prototype used a list named ``stack`` and ``pop(0)``, which is a queue.
    This implementation uses ``deque`` and names it accordingly. Cycles are
    ignored via a ``seen`` set. The source repo is not included in the result.
    """
    graph = store.graph()
    queue: deque[str] = deque(neighbor for neighbor in graph.get(repo, []) if neighbor != repo)
    seen: set[str] = {repo, *queue}
    visited: list[str] = []
    while queue:
        current = queue.popleft()
        visited.append(current)
        for downstream in graph.get(current, []):
            if downstream not in seen:
                seen.add(downstream)
                queue.append(downstream)
    return BlastRadius(
        source_repo=repo,
        downstream_repos=visited,
        total_impacted=len(visited),
    )
