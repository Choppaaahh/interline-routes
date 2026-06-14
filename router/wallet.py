"""
wallet.py — Python↔TS boundary for the v2 non-custodial scoped wallet.

ERC-4337 session-key tooling is TS-first (no mature pure-Python lib in 2026), so the
smart-account + session-key work lives in `wallet/` (ZeroDev/TS) and Python orchestrates
it by subprocess-calling the CLI. This thin wrapper is that boundary.

Trust model (on-chain-provable non-custodial): operator holds the OWNER key (revoke-only),
the agent holds the SESSION key (scoped by CallPolicy + rate-limit + expiry), the router
holds NEITHER. Keys are passed as args to the worker and never logged here.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

WORKER = Path(__file__).resolve().parent.parent / "wallet"
_TSX = WORKER / "node_modules" / ".bin" / "tsx"
_ENTRY = WORKER / "src" / "walletWorker.ts"


def _call(cmd: str, **kwargs) -> dict:
    """Run a wallet-worker command; return its parsed JSON. Raises on error."""
    args = [str(_TSX), str(_ENTRY), cmd]
    for k, v in kwargs.items():
        if v is not None:
            args += [f"--{k.replace('_', '-')}", str(v)]
    proc = subprocess.run(args, capture_output=True, text=True, cwd=str(WORKER), timeout=120)
    line = (proc.stdout or proc.stderr).strip().splitlines()[-1] if (proc.stdout or proc.stderr).strip() else "{}"
    data = json.loads(line)
    if "error" in data:
        raise RuntimeError(f"wallet-worker {cmd}: {data['error']}")
    return data


def smart_account_address(owner_key: str) -> str:
    """Counterfactual smart-account address for an owner key (no deploy, reads chain)."""
    return _call("address", owner_key=owner_key)["smartAccountAddress"]


def grant_session(
    owner_key: str, usdc: str, pay_to: str, *,
    max_atomic: int, rate_count: int = 10, rate_secs: int = 86400, expires_secs: int = 604800,
) -> dict:
    """
    Operator grants a SCOPED session key: only USDC.transfer to `pay_to`, amount ≤ max_atomic,
    ≤ rate_count tx per rate_secs, expiring in expires_secs. Returns the serialized session
    account (hand to the agent) + addresses. The operator never sees the session key again.
    """
    return _call(
        "grant", owner_key=owner_key, usdc=usdc, pay_to=pay_to,
        max_atomic=max_atomic, rate_count=rate_count, rate_secs=rate_secs, expires_secs=expires_secs,
    )


def pay(serialized: str, usdc: str, pay_to: str, amount_atomic: int) -> dict:
    """
    Agent pays via the scoped session key (a UserOp; the on-chain CallPolicy + rate-limit
    enforce the cap). Needs APV0_BUNDLER_RPC set. Returns {userOpHash, txHash}.
    """
    return _call("pay", serialized=serialized, usdc=usdc, pay_to=pay_to, amount_atomic=amount_atomic)
