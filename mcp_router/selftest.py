#!/usr/bin/env python3
"""
MCP-router self-test — NO network, NO keys. Golden-fixture verified.

Proves:
  1. parse_accepts normalizes a single-rail x402 402 body (golden: 1000 atomic -> "0.001000 USDC").
  2. parse_accepts is NEUTRAL — surfaces a SECOND, unknown-scheme rail (proves discovery isn't x402-only).
  3. parse_accepts on empty/no-accepts -> 0 rails (no crash).
  4. discover_rails routes: 402 -> rails parsed; 200 -> paid=False; fetch-error -> error surfaced.
  5. payment_history reads the ledger shape.

Run from repo root: python -m mcp_router.selftest
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_router import discovery  # noqa: E402

FAILS = []


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


# --- GOLDEN FIXTURES (hand-computed, asserted verbatim — model must not recompute) ---
# single x402 rail: amount 1000 atomic / 1e6 = 0.001 USDC
GOLDEN_SINGLE = {
    "x402Version": 2,
    "accepts": [
        {"scheme": "exact", "network": "eip155:84532", "asset": "0xUSDC",
         "amount": "1000", "payTo": "0xSELLER", "extra": {"name": "USDC"}},
    ],
}
# multi-rail: x402 (1000 -> 0.001) + an unknown "mpp" scheme (5000 -> 0.005)
GOLDEN_MULTI = {
    "x402Version": 2,
    "accepts": [
        {"scheme": "exact", "network": "eip155:84532", "amount": "1000", "payTo": "0xA", "extra": {"name": "USDC"}},
        {"scheme": "mpp", "network": "eip155:8453", "amount": "5000", "payTo": "0xB", "extra": {"name": "USDC"}},
    ],
}


class FakeResp:
    def __init__(self, status, payload=None, raise_json=False):
        self.status_code = status
        self._payload = payload
        self._raise = raise_json

    def json(self):
        if self._raise:
            raise ValueError("not json")
        return self._payload


def main():
    # 1. single-rail golden
    rails = discovery.parse_accepts(GOLDEN_SINGLE)
    check("single-rail -> 1 rail", len(rails) == 1)
    check("single-rail scheme exact -> rail x402", rails[0]["rail"] == "x402")
    check("single-rail price = 0.001000 USDC (golden)", rails[0]["price"] == "0.001000 USDC")
    check("single-rail pay_to passthrough", rails[0]["pay_to"] == "0xSELLER")
    check("single-rail amount_atomic passthrough", rails[0]["amount_atomic"] == "1000")

    # 2. multi-rail neutrality — unknown scheme surfaced as its own rail
    rails = discovery.parse_accepts(GOLDEN_MULTI)
    check("multi-rail -> 2 rails", len(rails) == 2)
    check("multi-rail surfaces unknown 'mpp' scheme as rail", rails[1]["rail"] == "mpp")
    check("multi-rail mpp price = 0.005000 USDC (golden)", rails[1]["price"] == "0.005000 USDC")
    check("multi-rail networks distinct", rails[0]["network"] != rails[1]["network"])

    # 3. empty / malformed
    check("no-accepts -> 0 rails", discovery.parse_accepts({}) == [])
    check("null-accepts -> 0 rails", discovery.parse_accepts({"accepts": None}) == [])
    bad_amt = discovery.parse_accepts({"accepts": [{"scheme": "exact", "amount": None}]})
    check("None amount -> price is string, no crash", bad_amt[0]["price"] == "None")

    # 4. discover_rails routing (monkeypatch httpx.get)
    orig = discovery.httpx.get
    try:
        discovery.httpx.get = lambda url, **kw: FakeResp(402, GOLDEN_SINGLE)
        d = discovery.discover_rails("http://x/work")
        check("402 -> paid True + 1 rail", d.get("paid") is True and d.get("rail_count") == 1)

        discovery.httpx.get = lambda url, **kw: FakeResp(200, {"work": "free"})
        d = discovery.discover_rails("http://x/free")
        check("200 -> paid False", d.get("paid") is False and d.get("rails") == [])

        def _raise(url, **kw):
            raise RuntimeError("conn refused")
        discovery.httpx.get = _raise
        d = discovery.discover_rails("http://x/down")
        check("fetch error -> error surfaced as data", "error" in d and d["rails"] == [])

        discovery.httpx.get = lambda url, **kw: FakeResp(402, None, raise_json=True)
        d = discovery.discover_rails("http://x/badjson")
        check("402 non-JSON -> error surfaced", "error" in d)
    finally:
        discovery.httpx.get = orig

    # 5. payment_history shape (import server lazily; ledger may be empty)
    from mcp_router import server  # noqa: F401
    hist = server.payment_history(limit=5)
    check("payment_history returns count+settlements", "count" in hist and "settlements" in hist)

    # 6. known-rails catalog (GOLDEN — neutral landscape, settle-vs-handoff honesty)
    cat = discovery.known_rails_catalog()
    check("catalog has 5 known rails", cat["rail_count"] == 5)
    check("native_settle = [x402, mpp, ap2] (golden)", cat["native_settle"] == ["x402", "mpp", "ap2"])
    check("handoff = [virtuals-acp, openai-stripe-acp] (golden)",
          cat["handoff"] == ["virtuals-acp", "openai-stripe-acp"])
    by = {r["name"]: r for r in cat["rails"]}
    check("x402 is native-settle", by["x402"]["route_mode"] == "native-settle")
    check("mpp is native-settle (the cross-protocol wedge)", by["mpp"]["route_mode"] == "native-settle")
    check("mpp is a settlement-rail (a 2nd protocol, not an authz layer)", by["mpp"]["kind"] == "settlement-rail")
    check("ap2 is native-settle", by["ap2"]["route_mode"] == "native-settle")
    check("virtuals-acp is handoff (its own x402 escrow)", by["virtuals-acp"]["route_mode"] == "handoff")
    # honesty assertion: ACP is card-only -> handoff, NOT a crypto rail we settle
    check("openai-stripe-acp is handoff", by["openai-stripe-acp"]["route_mode"] == "handoff")
    check("openai-stripe-acp settle_asset is fiat/card (NOT crypto — honesty)",
          "card" in by["openai-stripe-acp"]["settle_asset"].lower() or "fiat" in by["openai-stripe-acp"]["settle_asset"].lower())
    required = {"name", "kind", "route_mode", "networks", "settle_asset", "what", "docs"}
    check("every rail has full metadata", all(required <= set(r) for r in cat["rails"]))
    check("every route_mode is native-settle or handoff",
          all(r["route_mode"] in ("native-settle", "handoff") for r in cat["rails"]))
    # the MCP tool returns the same catalog
    check("server.list_known_rails tool == catalog", server.list_known_rails() == cat)

    print()
    if FAILS:
        print(f"SELF-TEST FAIL: {len(FAILS)} -> {FAILS}")
        sys.exit(1)
    print("SELF-TEST PASS — MCP router discovery (neutral multi-rail) + history wired, golden-fixture verified.")


if __name__ == "__main__":
    main()
