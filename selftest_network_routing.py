#!/usr/bin/env python3
"""
v3.1 self-test — network-aware multi-rail routing (NO network, golden fixtures).

The 2nd-rail unlock: x402-on-EVM and x402-on-Solana BOTH use scheme="exact", so the
Paywall must route by scheme AND network FAMILY. Proves a Paywall offering both x402
rails (a) shows BOTH in discovery, (b) routes an eip155 payment to the EVM rail and a
solana payment to the Solana rail, (c) back-compat: a bare (no-network) payment → EVM.

Run from repo root: python3 selftest_network_routing.py
"""
from __future__ import annotations

import base64
import json
import sys
from dataclasses import dataclass

from router.paywall import Paywall, X_PAYMENT, X_PAYMENT_RESPONSE
from router.rails.x402_rail import X402Rail
from router import ledger


@dataclass
class V:
    is_valid: bool
    reason: str = ""


@dataclass
class S:
    success: bool
    tx_hash: str = ""
    network: str = ""
    reason: str = ""


class FakeFac:
    """Stand-in facilitator that stamps which rail settled (via the network in reqs)."""

    def verify(self, payment, reqs):
        return V(True)

    def settle(self, payment, reqs):
        return S(True, tx_hash="0xTX", network=reqs.get("network", "?"))


def evm_reqs(resource):
    return {"scheme": "exact", "network": "eip155:84532", "asset": "0xUSDC",
            "amount": "1000", "payTo": "0xEVMSELLER", "extra": {"name": "USDC"}}


def sol_reqs(resource):
    return {"scheme": "exact", "network": "solana:devnet",
            "asset": "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU",
            "amount": "1000", "payTo": "SoLSeLLeRpubkey1111111111111111111111111111",
            "extra": {"name": "USDC", "feePayer": "FeePayer1111111111111111111111111111111111"}}


class FakeHeaders(dict):
    def get(self, k, default=None):
        return super().get(k.lower(), default)


class FakeRequest:
    def __init__(self, url, headers=None, qp=None):
        self.url = url
        self.headers = FakeHeaders({k.lower(): v for k, v in (headers or {}).items()})
        self.query_params = dict(qp or {})


def _xp(scheme=None, network=None):
    p = {"payload": {"authorization": {"from": "0xBUYER"}, "signature": "0xSIG"}}
    if scheme is not None:
        p["scheme"] = scheme
    if network is not None:
        p["network"] = network
    return base64.b64encode(json.dumps(p).encode()).decode()


def _body(r):
    return json.loads(r.body)


FAILS = []


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


def main():
    captured = []
    orig = ledger.record_settlement
    ledger.record_settlement = lambda **kw: captured.append(kw) or kw
    try:
        evm = X402Rail(FakeFac(), evm_reqs, name="x402", network_family="eip155")
        sol = X402Rail(FakeFac(), sol_reqs, name="x402-solana", network_family="solana")

        # unit: matches() is network-aware (the v3 residual fix)
        check("EVM rail matches eip155 payment", evm.matches({"scheme": "exact", "network": "eip155:8453"}))
        check("EVM rail REJECTS solana payment", not evm.matches({"scheme": "exact", "network": "solana:devnet"}))
        check("Solana rail matches solana payment", sol.matches({"scheme": "exact", "network": "solana:devnet"}))
        check("Solana rail REJECTS eip155 payment", not sol.matches({"scheme": "exact", "network": "eip155:8453"}))
        check("EVM rail matches bare (no-network) payment [back-compat]", evm.matches({"scheme": "exact"}))

        pw = Paywall(rails=[evm, sol])

        # 1. discovery shows BOTH rails (the visible aggregator)
        r = pw.gate(FakeRequest("http://x/work"), lambda: {"work": "done"})
        b = _body(r)
        nets = {a["network"] for a in b.get("accepts", [])}
        check("402 offers 2 rails", len(b.get("accepts", [])) == 2)
        check("discovery shows BOTH eip155 + solana", nets == {"eip155:84532", "solana:devnet"})

        # 2a. eip155 payment routes to EVM rail
        captured.clear()
        r = pw.gate(FakeRequest("http://x/work", {X_PAYMENT: _xp("exact", "eip155:84532")}), lambda: {"w": 1})
        check("eip155 payment -> 200", r.status_code == 200)
        rcpt = json.loads(base64.b64decode(r.headers[X_PAYMENT_RESPONSE]))
        check("eip155 routed to rail=x402", rcpt["rail"] == "x402")
        check("eip155 settled on eip155 network", rcpt["network"] == "eip155:84532")

        # 2b. solana payment routes to Solana rail
        r = pw.gate(FakeRequest("http://x/work", {X_PAYMENT: _xp("exact", "solana:devnet")}), lambda: {"w": 1})
        check("solana payment -> 200", r.status_code == 200)
        rcpt = json.loads(base64.b64decode(r.headers[X_PAYMENT_RESPONSE]))
        check("solana routed to rail=x402-solana", rcpt["rail"] == "x402-solana")
        check("solana settled on solana network", rcpt["network"] == "solana:devnet")

        # 3. back-compat: Paywall(facilitator, reqs_fn) single EVM rail still works
        legacy = Paywall(FakeFac(), evm_reqs)
        r = legacy.gate(FakeRequest("http://x/work", {X_PAYMENT: _xp("exact", "eip155:84532")}), lambda: {"ok": 1})
        check("back-compat single-rail -> 200", r.status_code == 200)

        # 4. ADVERSARIAL — malformed / hostile input must not crash or mis-route
        #    buyer-controlled `network` must CLEANLY 402 ("no rail"), never 500-crash and
        #    never silently mis-route to EVM. Unit-level on matches():
        check("non-string network (int) -> no match, no crash",
              not evm.matches({"scheme": "exact", "network": 123}) and not sol.matches({"scheme": "exact", "network": 123}))
        check("non-string network (bool/float/dict/list) -> no match",
              not any(evm.matches({"scheme": "exact", "network": n}) for n in (True, 1.5, {}, [])))
        check("present-but-empty network -> no match (NOT mis-route to EVM)",
              not evm.matches({"scheme": "exact", "network": ""}))
        check("malformed 'eip155' (no colon) -> no match", not evm.matches({"scheme": "exact", "network": "eip155"}))
        check("malformed 'eip155:' (empty ref) -> no match", not evm.matches({"scheme": "exact", "network": "eip155:"}))
        check("malformed 'eip155:8453:extra' (3-part) -> no match", not evm.matches({"scheme": "exact", "network": "eip155:8453:extra"}))
        check("case-insensitive family 'EIP155:8453' -> EVM match", evm.matches({"scheme": "exact", "network": "EIP155:8453"}))
        check("non-string scheme -> no match", not evm.matches({"scheme": 123, "network": "eip155:8453"}))
        # End-to-end: a malformed network in a well-formed base64 body -> clean 402, not 500.
        pw2 = Paywall(rails=[evm, sol])
        r = pw2.gate(FakeRequest("http://x/work", {X_PAYMENT: _xp("exact", 123)}), lambda: {"w": 1})
        check("e2e malformed-network body -> 402 (no 500 crash)", r.status_code == 402)
    finally:
        ledger.record_settlement = orig

    print()
    if FAILS:
        print(f"SELF-TEST FAIL: {len(FAILS)} -> {FAILS}")
        sys.exit(1)
    print("SELF-TEST PASS — network-aware multi-rail routing works (x402-EVM ⊕ x402-Solana, golden-fixture).")


if __name__ == "__main__":
    main()
