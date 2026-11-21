from __future__ import annotations

from forgepulse.atlas import search_symbols, upsert_symbol
from forgepulse.models import SymbolIndexEntry
from forgepulse.store import MemoryStore


def test_search_by_symbol_path_and_owner(store: MemoryStore) -> None:
    by_symbol = search_symbols(store, "process_settlement")
    assert len(by_symbol) == 1
    assert by_symbol[0].file_path.endswith("engine.py")
    by_owner = search_symbols(store, "@sec-team")
    assert [item.symbol for item in by_owner] == ["verify_token_chain"]
    by_path = search_symbols(store, "CheckoutForm.tsx")
    assert by_path[0].symbol == "CheckoutForm"


def test_upsert_merges_on_repo_symbol_and_path(store: MemoryStore) -> None:
    entry = SymbolIndexEntry(
        repo="org/payments-core",
        symbol="process_settlement",
        file_path="src/settlement/engine.py",
        owners=["@fin-eng", "@new-owner"],
        related_prs=[489, 512],
    )
    total = upsert_symbol(store, entry)
    matches = search_symbols(store, "process_settlement")
    assert len(matches) == 1
    assert total >= 4
    assert matches[0].owners == ["@fin-eng", "@new-owner"]
    assert matches[0].related_prs == [412, 489, 512]
