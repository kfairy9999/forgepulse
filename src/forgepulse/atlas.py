"""CodeAtlas: symbol, owner, and related-PR lookup."""

from __future__ import annotations

from forgepulse.models import SymbolIndexEntry
from forgepulse.store import MemoryStore


def upsert_symbol(store: MemoryStore, entry: SymbolIndexEntry) -> int:
    return store.upsert_symbol(entry)


def search_symbols(store: MemoryStore, query: str) -> list[SymbolIndexEntry]:
    return store.search_symbols(query)
