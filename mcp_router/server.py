#!/usr/bin/env python3
"""
Interline — an MCP server that lets ANY MCP-using agent pay across payment rails.

Three tools:
  discover_payment_rails(url)               — probe a paid endpoint; report which rails + prices it accepts (NO payment)
  pay_for_resource(url, task, max_usd)      — pay + fetch through the accepted rail; return content + receipt
  payment_history(limit)                    — the unified cross-rail receipt ledger

NEUTRAL by design: discovery reports EVERY rail an endpoint offers (today x402; MPP/others drop
into the same shape). NON-CUSTODIAL: payment uses the CALLER'S OWN wallet key (env), never ours.

Run as a stdio MCP server:  python -m mcp_router.server   (or the `interline` entry point)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Make `router` importable whether run from the repo root or as an installed entry point.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp_router.discovery import discover_rails, known_rails_catalog  # noqa: E402

mcp = FastMCP("interline")


@mcp.tool()
def discover_payment_rails(url: str) -> dict:
    """Probe a paid endpoint and report which payment rails it accepts + the price for each — WITHOUT paying.

    The neutral rail-discovery layer: one call tells your agent every way it could pay this endpoint,
    across whatever rails the endpoint offers. Use this before pay_for_resource to see the price/rails."""
    return discover_rails(url)


@mcp.tool()
def pay_for_resource(url: str, task: str = "", max_price_usdc: float = 0.01) -> dict:
    """Pay for and fetch a paywalled resource, returning its content + a settlement receipt.

    Routes the payment through the rail the endpoint accepts; never pays more than max_price_usdc.
    Uses the caller's OWN wallet key from APV0_BUYER_PRIVATE_KEY (non-custodial — this server never
    holds or sees funds). `task` is appended as a query param for endpoints that take one."""
    from router import buyer  # local import: only needed on the pay path

    max_atomic = int(round(max_price_usdc * 10 ** 6))
    full_url = url if not task else f"{url}{'&' if '?' in url else '?'}task={task}"
    try:
        result = buyer.pay_and_get(full_url, max_price_atomic=max_atomic)
        return {"ok": True, **result}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


@mcp.tool()
def list_known_rails() -> dict:
    """List EVERY agent-payment rail/protocol Interline knows about + how it relates to each — the neutral catalog.

    Separates rails Interline SETTLES natively (route_mode=native-settle: x402, AP2-via-adapter) from protocols
    it ROUTES you TO but does not settle (route_mode=handoff: Virtuals ACP's own on-chain escrow, OpenAI/Stripe
    ACP's card-only delegated payment). One call = the whole agent-payment landscape, including the rails we
    don't move funds on. Pair with discover_payment_rails (what a specific endpoint accepts)."""
    return known_rails_catalog()


@mcp.tool()
def payment_history(limit: int = 20) -> dict:
    """Return the most recent cross-rail settlement receipts — the unified ledger across every rail paid through."""
    from router import ledger  # local import

    p = ledger.LEDGER
    if not p.exists():
        return {"count": 0, "settlements": []}
    rows = [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]
    return {"count": len(rows), "settlements": rows[-limit:]}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
