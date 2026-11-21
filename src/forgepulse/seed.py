"""Fictional seed data for the local server and the live playground."""

from __future__ import annotations

from forgepulse.models import SymbolIndexEntry

SEED_GRAPH: dict[str, list[str]] = {
    "org/shared-types": ["org/payments-core", "org/auth-gateway", "org/web-app"],
    "org/payments-core": ["org/web-app", "org/billing-export"],
    "org/auth-gateway": ["org/web-app"],
    "org/billing-export": ["org/finance-ledger"],
}

SEED_SYMBOLS: list[SymbolIndexEntry] = [
    SymbolIndexEntry(
        repo="org/payments-core",
        symbol="process_settlement",
        file_path="src/settlement/engine.py",
        owners=["@fin-eng", "@alex-payments"],
        related_prs=[412, 489],
    ),
    SymbolIndexEntry(
        repo="org/auth-gateway",
        symbol="verify_token_chain",
        file_path="pkg/auth/chain.go",
        owners=["@sec-team"],
        related_prs=[108],
    ),
    SymbolIndexEntry(
        repo="org/shared-types",
        symbol="Money",
        file_path="src/money/Money.ts",
        owners=["@platform"],
        related_prs=[77, 81],
    ),
    SymbolIndexEntry(
        repo="org/web-app",
        symbol="CheckoutForm",
        file_path="app/checkout/CheckoutForm.tsx",
        owners=["@web-eng", "@fin-eng"],
        related_prs=[901],
    ),
]
