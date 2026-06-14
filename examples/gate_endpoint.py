"""
SELLER side — gate an HTTP endpoint behind a payment with Interline, in ~10 lines.

Interline is "OpenRouter for agent payments": one `Paywall` offers N payment rails
at once. Here we gate `GET /report` behind BOTH x402 (USDC, EVM) AND MPP (Tempo) —
the buyer's agent picks whichever rail it speaks, and the same `.gate()` call routes
verify + settle to the right one. Adding a rail is one list entry; the route never
changes.

This uses the REAL Paywall + rail constructors from `router/`. By default it wires
mock facilitators so it runs with ZERO wallet / key / chain (dry-run first). To go
live, swap in the real facilitators (see the comments) — the SAME code path settles
on-chain.

Repo: https://github.com/Choppaaahh/interline-routes

    uvicorn examples.gate_endpoint:app --port 8402
"""
from __future__ import annotations

from fastapi import FastAPI, Request

from router.facilitator_real import get_facilitator          # x402 facilitator (mock until live)
from router.paywall import Paywall
from router.rails import (
    X402Rail,
    MppRail,
    MppMockFacilitator,
    default_mpp_requirements_fn,
)

app = FastAPI(title="example: Interline-gated endpoint")

# A fake-but-clearly-labeled recipient address. Replace with YOUR receiving wallet.
SELLER_PAY_TO = "0x0000000000000000000000000000000000000000"  # <- your wallet here


def _x402_requirements(resource: str) -> dict:
    """The x402 (USDC on Base Sepolia) offer for the 402 challenge."""
    return {
        "scheme": "exact",
        "network": "eip155:84532",                 # Base Sepolia (CAIP-2)
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",  # test USDC
        "amount": "1000",                          # atomic (USDC has 6 decimals -> 0.001 USDC)
        "payTo": SELLER_PAY_TO,
        "maxTimeoutSeconds": 120,
        "extra": {"name": "USDC", "version": "2", "chainId": 84532},
    }


# --- the whole gate: two rails behind one Paywall -------------------------------
paywall = Paywall(rails=[
    # x402 rail (EVM). get_facilitator() is a mock until you set live env vars.
    X402Rail(get_facilitator(), _x402_requirements, name="x402", network_family="eip155"),
    # MPP rail (Tempo). MppMockFacilitator proves the loop with no wallet; swap in
    # MppTempoFacilitator(recipient=..., secret_key=...) for a live Tempo settle.
    MppRail(
        MppMockFacilitator(),
        default_mpp_requirements_fn(
            currency="USDC", recipient=SELLER_PAY_TO, amount="0.001",
        ),
    ),
])


@app.get("/report")
def report(request: Request):
    # ONE line gates the work behind payment. Interline handles the 402 dance,
    # the rail selection, settlement, and the receipt header.
    return paywall.gate(request, lambda: {"report": "the paid work product goes here"})


@app.get("/health")
def health() -> dict:
    return {"ok": True}
