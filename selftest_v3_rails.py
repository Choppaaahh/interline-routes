#!/usr/bin/env python3
"""
v3 self-test — the rail-agnostic aggregator seam (NO network, NO keys, NO chain).

Proves:
  1. Paywall(rails=[A, B]) offers BOTH rails in the 402 `accepts`.
  2. A buyer payment routes to the rail that `matches` it (A->A, B->B).
  3. An unknown payment -> 402 "no rail handles".
  4. The settled receipt + ledger row are tagged with the winning rail.
  5. Back-compat: Paywall(facilitator, reqs_fn) still works (single x402 rail, rail=x402).
  6. Registry: register / get_rail / match_rail / all_rails / clear.

Run from repo root: python3 selftest_v3_rails.py
"""
from __future__ import annotations

import base64
import json
import sys
from dataclasses import dataclass

from router.paywall import Paywall, X_PAYMENT, X_PAYMENT_RESPONSE
from router import rails as rail_registry
from router import ledger


# --- tiny result shapes (duck-type the facilitator's VerifyResult/SettleResult) ---
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


class FakeRail:
    """A stand-in rail — proves any rail implementing the interface plugs in."""

    def __init__(self, name, scheme):
        self.name = name
        self.scheme = scheme

    def payment_requirements(self, resource):
        return {"scheme": self.scheme, "network": "eip155:84532", "payTo": f"0x{self.name}", "amount": "1000"}

    def matches(self, payment):
        return payment.get("scheme") == self.scheme

    def verify(self, payment, requirements):
        return V(True)

    def settle(self, payment, requirements):
        return S(True, tx_hash=f"0xTX_{self.name}", network="eip155:84532")


class FakeHeaders(dict):
    def get(self, k, default=None):
        return super().get(k.lower(), default)


class FakeRequest:
    def __init__(self, url, headers=None, qp=None):
        self.url = url
        self.headers = FakeHeaders({k.lower(): v for k, v in (headers or {}).items()})
        self.query_params = dict(qp or {})


def _x_payment(scheme):
    payload = {"scheme": scheme, "network": "eip155:84532",
               "payload": {"authorization": {"from": "0xBUYER"}, "signature": "0xSIG"}}
    return base64.b64encode(json.dumps(payload).encode()).decode()


def _body(resp):
    return json.loads(resp.body)


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
        railA = FakeRail("railA", "exact")
        railB = FakeRail("railB", "stripe")
        pw = Paywall(rails=[railA, railB])

        # 1. no payment -> 402 offering BOTH rails
        r = pw.gate(FakeRequest("http://x/work"), lambda: {"work": "done"})
        b = _body(r)
        check("no-payment -> 402", r.status_code == 402)
        check("402 offers 2 rails in accepts", len(b.get("accepts", [])) == 2)
        check("accepts lists both schemes", {a["scheme"] for a in b["accepts"]} == {"exact", "stripe"})

        # 2a. payment for railA routes to railA
        captured.clear()
        r = pw.gate(FakeRequest("http://x/work", {X_PAYMENT: _x_payment("exact")}, {"task": "t"}),
                    lambda: {"work": "A"})
        check("railA payment -> 200", r.status_code == 200)
        check("railA receipt tags rail=railA",
              json.loads(base64.b64decode(r.headers[X_PAYMENT_RESPONSE]))["rail"] == "railA")
        check("ledger row tagged rail=railA", bool(captured) and captured[-1].get("rail") == "railA")

        # 2b. payment for railB routes to railB
        r = pw.gate(FakeRequest("http://x/work", {X_PAYMENT: _x_payment("stripe")}), lambda: {"work": "B"})
        check("railB payment -> 200", r.status_code == 200)
        check("railB receipt tags rail=railB",
              json.loads(base64.b64decode(r.headers[X_PAYMENT_RESPONSE]))["rail"] == "railB")

        # 3. unknown scheme -> 402 no rail handles
        r = pw.gate(FakeRequest("http://x/work", {X_PAYMENT: _x_payment("dogecoin")}), lambda: {"work": "?"})
        check("unknown scheme -> 402", r.status_code == 402)
        check("unknown scheme error names no-rail", "no rail" in _body(r).get("error", ""))

        # 4. back-compat single-rail via facilitator + reqs_fn
        class FakeFac:
            def verify(self, p, rq):
                return V(True)

            def settle(self, p, rq):
                return S(True, tx_hash="0xLEGACY", network="eip155:84532")

        legacy = Paywall(FakeFac(), lambda res: {"scheme": "exact", "network": "eip155:84532", "payTo": "0xX", "amount": "1"})
        r = legacy.gate(FakeRequest("http://x/work", {X_PAYMENT: _x_payment("exact")}), lambda: {"ok": 1})
        check("back-compat single-rail -> 200", r.status_code == 200)
        check("back-compat receipt rail defaults x402",
              json.loads(base64.b64decode(r.headers[X_PAYMENT_RESPONSE]))["rail"] == "x402")

        # 5. registry
        rail_registry.clear()
        rail_registry.register(railA)
        rail_registry.register(railB)
        check("registry get_rail", rail_registry.get_rail("railA") is railA)
        check("registry all_rails count", len(rail_registry.all_rails()) == 2)
        check("registry match_rail by scheme", rail_registry.match_rail({"scheme": "stripe"}) is railB)
        check("registry match_rail miss -> None", rail_registry.match_rail({"scheme": "nope"}) is None)
        rail_registry.clear()
        check("registry clear empties", rail_registry.all_rails() == [])
    finally:
        ledger.record_settlement = orig

    print()
    if FAILS:
        print(f"SELF-TEST FAIL: {len(FAILS)} -> {FAILS}")
        sys.exit(1)
    print("SELF-TEST PASS — v3 rail-agnostic aggregator seam works (multi-rail routing + back-compat).")


if __name__ == "__main__":
    main()
