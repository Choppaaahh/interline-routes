"""
Settlement receipt-ledger — every paid+settled request appends one row.

The audit trail / "what's been paid" surface. JSONL so it's append-only +
greppable. Gitignored (it's local runtime state, not source).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

LEDGER = Path(__file__).resolve().parent.parent / "logs" / "agent_payment_settlements.jsonl"


def record_settlement(
    *, tx_hash: str, payer: str | None, pay_to: str | None,
    amount_atomic: str | None, network: str, task: str | None, resource: str,
    rail: str | None = None,
) -> dict:
    """Append one settlement row; returns the row. `rail` = which rail settled (v3 aggregator)."""
    row = {
        "ts": int(time.time()),
        "rail": rail or "x402",
        "tx_hash": tx_hash,
        "payer": payer,
        "pay_to": pay_to,
        "amount_atomic": amount_atomic,
        "network": network,
        "task": (task or "")[:200],
        "resource": resource,
    }
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return row
