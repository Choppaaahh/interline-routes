#!/usr/bin/env python3
"""
MPP-rail self-test — the cross-PROTOCOL wedge, made provable (NO wallet, NO key, NO chain).

Proves:
  1. default_mpp_requirements_fn builds a faithful MPP 'tempo' offer (golden fields).
  2. MppRail.matches() routes by scheme="mpp" AND is hardened against untrusted JSON
     (non-string / absent scheme -> clean non-match, never a crash).
  3. MppMockFacilitator.verify(): valid credential PASS; under-amount / wrong-method /
     malformed-credential / replay all FAIL with the right reason (no crash).
  4. MppMockFacilitator.settle(): deterministic mock receipt (tempo: + sha256), network
     passthrough, replay-blocked on a second settle of the same nonce.
  5. THE WEDGE: one Paywall(rails=[X402Rail, MppRail]) offers BOTH protocols, and a buyer
     payment routes to the rail that matches it — x402 payment -> x402 rail (receipt
     rail=x402), mpp payment -> mpp rail (receipt rail=mpp). One integration, two PROTOCOLS.

Golden values are hand-computed and asserted verbatim.

Run from repo root: python3 selftest_mpp_rail.py
"""
from __future__ import annotations

import base64
import json
import sys
from dataclasses import dataclass

from router.paywall import Paywall, X_PAYMENT, X_PAYMENT_RESPONSE
from router import ledger
from router.rails.x402_rail import X402Rail
from router.rails.mpp_rail import (
    MppRail,
    MppMockFacilitator,
    MppTempoFacilitator,
    default_mpp_requirements_fn,
)


FAILS = []


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        FAILS.append(name)


# --- x402 side: a trivial valid facilitator (no eth signing needed for routing proof) ---
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


class FakeX402Fac:
    def verify(self, p, rq):
        return V(True)

    def settle(self, p, rq):
        return S(True, tx_hash="0xX402TX", network="eip155:84532")


class FakeHeaders(dict):
    def get(self, k, default=None):
        return super().get(k.lower(), default)


class FakeRequest:
    def __init__(self, url, headers=None, qp=None):
        self.url = url
        self.headers = FakeHeaders({k.lower(): v for k, v in (headers or {}).items()})
        self.query_params = dict(qp or {})


def _x_payment(payload: dict) -> str:
    return base64.b64encode(json.dumps(payload).encode()).decode()


def _body(resp):
    return json.loads(resp.body)


# --- golden fixtures (hand-computed) ---
MPP_REQS_FN = default_mpp_requirements_fn(
    currency="0xCURR", recipient="0xRECIP", amount="0.50", network="tempo-testnet",
)
VALID_MPP_PAYMENT = {
    "scheme": "mpp", "method": "tempo",
    "payload": {"authorization": {"from": "0xPAYER", "amount": "0.50", "method": "tempo"}, "nonce": "n1"},
}


def main():
    # 1. requirements offer — golden fields
    reqs = MPP_REQS_FN("http://x/work")
    check("offer scheme=mpp", reqs["scheme"] == "mpp")
    check("offer method=tempo", reqs["method"] == "tempo")
    check("offer network=tempo-testnet (golden)", reqs["network"] == "tempo-testnet")
    check("offer currency passthrough", reqs["currency"] == "0xCURR")
    check("offer payTo aliases recipient (uniform ledger)", reqs["payTo"] == "0xRECIP")
    check("offer amount is decimal '0.50' (NOT atomic int)", reqs["amount"] == "0.50")
    check("offer resource passthrough", reqs["resource"] == "http://x/work")

    # 2. matches() — scheme routing + untrusted-JSON hardening
    rail = MppRail(MppMockFacilitator(), MPP_REQS_FN)
    check("matches mpp scheme", rail.matches({"scheme": "mpp"}) is True)
    check("matches NOT x402 scheme", rail.matches({"scheme": "exact"}) is False)
    check("matches non-string scheme -> False (no crash)", rail.matches({"scheme": 123}) is False)
    check("matches absent scheme -> False (no implicit mpp)", rail.matches({}) is False)

    # 3. verify() — valid PASS, the failure modes FAIL with the right reason (no crash)
    fac = MppMockFacilitator()
    check("verify valid credential -> is_valid", fac.verify(VALID_MPP_PAYMENT, reqs).is_valid is True)

    under = {"scheme": "mpp", "payload": {"authorization": {"from": "0xP", "amount": "0.10", "method": "tempo"}, "nonce": "u1"}}
    rv = fac.verify(under, reqs)
    check("verify under-amount -> invalid", rv.is_valid is False and "amount below required" in rv.reason)

    wrong_method = {"scheme": "mpp", "payload": {"authorization": {"from": "0xP", "amount": "0.50", "method": "stripe"}, "nonce": "w1"}}
    rv = fac.verify(wrong_method, reqs)
    check("verify wrong-method -> invalid", rv.is_valid is False and "method mismatch" in rv.reason)

    malformed = {"scheme": "mpp", "payload": {"authorization": "not-a-dict"}}
    rv = fac.verify(malformed, reqs)
    check("verify malformed credential -> invalid (no crash)", rv.is_valid is False and "credential" in rv.reason)

    no_payload = {"scheme": "mpp"}
    check("verify missing payload -> invalid (no crash)", fac.verify(no_payload, reqs).is_valid is False)

    # 4. settle() — deterministic mock receipt + network + replay block
    s1 = fac.settle(VALID_MPP_PAYMENT, reqs)
    check("settle valid -> success", s1.success is True)
    check("settle tx_hash prefixed tempo:", s1.tx_hash.startswith("tempo:"))
    check("settle tx_hash is sha256 (tempo: + 64 hex)", len(s1.tx_hash) == len("tempo:") + 64)
    check("settle network passthrough", s1.network == "tempo-testnet")
    # determinism: a FRESH facilitator on the same payment yields the SAME tx_hash
    s_fresh = MppMockFacilitator().settle(VALID_MPP_PAYMENT, reqs)
    check("settle is deterministic (same input -> same tx)", s_fresh.tx_hash == s1.tx_hash)
    # replay: settling n1 again on the SAME facilitator is blocked (nonce spent)
    s2 = fac.settle(VALID_MPP_PAYMENT, reqs)
    check("settle replay (same nonce, same fac) -> blocked", s2.success is False and "replay" in s2.reason)

    # 5. THE WEDGE — one Paywall offers x402 AND mpp; each payment routes to its rail
    captured = []
    orig = ledger.record_settlement
    ledger.record_settlement = lambda **kw: captured.append(kw) or kw
    try:
        x402_rail = X402Rail(
            FakeX402Fac(),
            lambda res: {"scheme": "exact", "network": "eip155:84532", "payTo": "0xX", "amount": "1000"},
            name="x402", network_family="eip155",
        )
        mpp_rail = MppRail(MppMockFacilitator(), MPP_REQS_FN, name="mpp")
        pw = Paywall(rails=[x402_rail, mpp_rail])

        # 402 offers BOTH protocols
        r = pw.gate(FakeRequest("http://x/work"), lambda: {"work": "done"})
        b = _body(r)
        check("402 offers 2 rails", r.status_code == 402 and len(b.get("accepts", [])) == 2)
        check("402 accepts both protocols {exact, mpp}", {a["scheme"] for a in b["accepts"]} == {"exact", "mpp"})

        # x402 payment routes to the x402 rail
        x402_pay = {"scheme": "exact", "network": "eip155:84532",
                    "payload": {"authorization": {"from": "0xBUYER"}, "signature": "0xSIG"}}
        r = pw.gate(FakeRequest("http://x/work", {X_PAYMENT: _x_payment(x402_pay)}, {"task": "t"}), lambda: {"w": "x402"})
        check("x402 payment -> 200", r.status_code == 200)
        check("x402 receipt tags rail=x402",
              json.loads(base64.b64decode(r.headers[X_PAYMENT_RESPONSE]))["rail"] == "x402")

        # mpp payment routes to the mpp rail (THE WEDGE: a second protocol, same Paywall)
        r = pw.gate(FakeRequest("http://x/work", {X_PAYMENT: _x_payment(VALID_MPP_PAYMENT)}, {"task": "t2"}), lambda: {"w": "mpp"})
        check("mpp payment -> 200 (cross-protocol settle)", r.status_code == 200)
        rcpt = json.loads(base64.b64decode(r.headers[X_PAYMENT_RESPONSE]))
        check("mpp receipt tags rail=mpp", rcpt["rail"] == "mpp")
        check("mpp receipt tx is a tempo: ref", str(rcpt["txHash"]).startswith("tempo:"))
        check("mpp ledger row tagged rail=mpp + payer", bool(captured) and captured[-1].get("rail") == "mpp" and captured[-1].get("payer") == "0xPAYER")
    finally:
        ledger.record_settlement = orig

    # 6. LIVE Tempo facilitator — the real charge() bridge result-mapping (deterministic, NO network/wallet)
    class FakeReceipt:
        def __init__(self, success, reference="", external_id="", status="ok"):
            self.success = success
            self.reference = reference
            self.external_id = external_id
            self.status = status

    live = MppTempoFacilitator(recipient="0xRECIP", secret_key=None)
    # no secret_key -> honest gated-error (NOT a fake success — no silent paper-trading)
    g = live.settle(VALID_MPP_PAYMENT, reqs)
    check("live settle without MPP_SECRET_KEY -> gated-error (not success)", g.success is False and "gated" in g.reason)
    # (Credential, Receipt) tuple with success -> SettleResult success, tx from receipt.reference
    settled = live._map_charge_result(("CRED", FakeReceipt(True, reference="tempo-tx-0xABC")))
    check("live map (Cred,Receipt) success -> tx from reference", settled.success is True and settled.tx_hash == "tempo-tx-0xABC")
    check("live map success -> network label tempo-testnet", settled.network == "tempo-testnet")
    # reference empty -> falls back to external_id
    settled2 = live._map_charge_result(("CRED", FakeReceipt(True, reference="", external_id="ext-99")))
    check("live map success -> tx falls back to external_id", settled2.tx_hash == "ext-99")
    # receipt.success False -> SettleResult False
    failed = live._map_charge_result(("CRED", FakeReceipt(False, status="insufficient")))
    check("live map receipt.success=False -> SettleResult False", failed.success is False and "insufficient" in failed.reason)
    # a Challenge (non-tuple return) -> credential absent/invalid -> SettleResult False (no settle)
    chal = live._map_charge_result(object())
    check("live map Challenge (non-tuple) -> SettleResult False", chal.success is False and "Challenge" in chal.reason)

    print()
    if FAILS:
        print(f"SELF-TEST FAIL: {len(FAILS)} -> {FAILS}")
        sys.exit(1)
    print("SELF-TEST PASS — MPP rail (cross-protocol wedge): offer + scheme-routing + verify/settle "
          "+ x402⊕mpp behind one Paywall + LIVE charge()-bridge result-mapping, golden-fixture verified.")


if __name__ == "__main__":
    main()
