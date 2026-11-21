"""Process-local, thread-safe in-memory state.

This is intentionally not durable. Multiple processes, Workers isolates, or
replicas do not share it. Swap for Postgres + Redis before any production use.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence

from forgepulse.models import PolicyRule, QueuedJob, SymbolIndexEntry


class MemoryStore:
    """In-memory queue, symbol index, graph, and active policy."""

    def __init__(
        self,
        *,
        policy: PolicyRule,
        graph: Mapping[str, Sequence[str]] | None = None,
        symbols: Sequence[SymbolIndexEntry] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._policy = policy
        self._graph: dict[str, list[str]] = {
            key: list(values) for key, values in (graph or {}).items()
        }
        self._symbols: list[SymbolIndexEntry] = [item.model_copy() for item in symbols or []]
        self._queue: list[QueuedJob] = []
        self._seq = 0

    def policy(self) -> PolicyRule:
        with self._lock:
            return self._policy.model_copy(deep=True)

    def set_policy(self, policy: PolicyRule) -> None:
        with self._lock:
            self._policy = policy

    def graph(self) -> dict[str, list[str]]:
        with self._lock:
            return {key: list(values) for key, values in self._graph.items()}

    def next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def enqueue(self, job: QueuedJob) -> tuple[QueuedJob, int]:
        with self._lock:
            self._queue.append(job)
            self._queue.sort(key=lambda item: (-item.priority, item.enqueue_seq))
            return job, len(self._queue)

    def queue(self) -> list[QueuedJob]:
        with self._lock:
            return [item.model_copy() for item in self._queue]

    def queue_depth(self) -> int:
        with self._lock:
            return len(self._queue)

    def upsert_symbol(self, entry: SymbolIndexEntry) -> int:
        incoming = entry.model_copy()
        with self._lock:
            for index, existing in enumerate(self._symbols):
                if (
                    existing.repo == incoming.repo
                    and existing.symbol == incoming.symbol
                    and existing.file_path == incoming.file_path
                ):
                    merged_prs = list(dict.fromkeys([*existing.related_prs, *incoming.related_prs]))
                    self._symbols[index] = incoming.model_copy(
                        update={
                            "owners": list(incoming.owners),
                            "related_prs": merged_prs,
                        }
                    )
                    return len(self._symbols)
            self._symbols.append(incoming)
            return len(self._symbols)

    def search_symbols(self, query: str) -> list[SymbolIndexEntry]:
        needle = query.lower().strip()
        with self._lock:
            if not needle:
                return [item.model_copy() for item in self._symbols]
            matches = [
                item
                for item in self._symbols
                if needle in item.symbol.lower()
                or needle in item.file_path.lower()
                or any(needle in owner.lower() for owner in item.owners)
            ]
            return [item.model_copy() for item in matches]
