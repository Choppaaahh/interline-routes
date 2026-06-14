#!/usr/bin/env python3
"""
Golden self-test for the AP2 inbound adapter (Interline keystone) — Phase 2.

Exercises the CANONICAL constraint surface: builds `OpenPaymentMandate` (the buyer's
authority grant w/ AmountRange / AllowedPayees / Budget) + a closed generated
`PaymentMandate`, and asserts the SDK's real `check_payment_constraints` engine
ACCEPTS in-range payments and REJECTS over-max / under-min / wrong-payee / over-budget.

Golden values are lifted from the AP2 SDK's own constraints_tests.py (ground truth):
  AmountRange(min=1000,max=5000,USD) + amount=500  -> below minimum
  AmountRange(min=1000,max=5000,USD) + amount=6000 -> exceeds maximum
  Budget(max=15.0 USD)=1500c + ctx total=1500 + amount=100 -> cumulative 1600 > 1500
  AllowedPayees([Shop/s-1]) + closed payee Shop/s-1 -> PASS ; Other/x-9 -> REJECT
Units bridge golden: $0.10 USD = 10 minor-units -> USDC 6-dec atomic = 100000.

No network. Run: python3 selftest_ap2_adapter.py   (exit 0 = all golden pass)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jwcrypto.jwk import JWK  # noqa: E402

from ap2.sdk import jwt_helper  # noqa: E402
from ap2.sdk.constraints import MandateContext  # noqa: E402
from ap2.sdk.generated.open_payment_mandate import (  # noqa: E402
    AllowedPayees,
    AmountRange,
    Budget,
    OpenPaymentMandate,
)
from ap2.sdk.generated.payment_mandate import PaymentMandate  # noqa: E402
from ap2.sdk.generated.types.amount import Amount  # noqa: E402
from ap2.sdk.generated.types.merchant import Merchant  # noqa: E402
from ap2.sdk.generated.types.payment_instrument import PaymentInstrument  # noqa: E402

from router.inbound.ap2_adapter import AP2InboundAdapter  # noqa: E402

# Merchant rail identity (the settlement destination — comes from MERCHANT config, never
# from the buyer's mandate; this separation is the non-custody guarantee).
MERCHANT_PAYEE = Merchant(name="Interline Test Merchant", id="merch-1")
PAY_TO = "EtWjFakeMerchantSolanaAddr1111111111111111"
FEE_PAYER = "FacFakeFeePayerSolanaAddr22222222222222222"

# A minimal but valid jwcrypto JWK keypair for the SD-JWT roundtrip.
_PRIV = JWK.generate(kty="EC", crv="P-256")
_PUB = JWK.from_json(_PRIV.export_public())


def _adapter() -> AP2InboundAdapter:
    return AP2InboundAdapter(
        network="solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1",
        asset="4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU",
        asset_decimals=6,
        pay_to=PAY_TO,
        fee_payer=FEE_PAYER,
        currency_minor_decimals=2,
    )


def _open(constraints):
    # cnf (key-binding claim) is required by the schema; a dummy is fine for constraint tests.
    return OpenPaymentMandate(constraints=constraints, cnf={"jwk": {}})


def _closed(*, amount=1000, currency="USD", payee=MERCHANT_PAYEE, exp="fresh", iat="fresh"):
    now = int(time.time())
    return PaymentMandate(
        transaction_id="tx-test-1",
        payee=payee,
        payment_amount=Amount(amount=amount, currency=currency),
        payment_instrument=PaymentInstrument(id="pi-1", type="x402"),
        iat=(now if iat == "fresh" else iat),
        exp=(now + 300 if exp == "fresh" else exp),
    )


def main() -> int:
    a = _adapter()
    passed = 0
    failed = []

    def check(name, cond):
        nonlocal passed
        if cond:
            passed += 1
        else:
            failed.append(name)

    # ── units bridge (golden) ───────────────────────────────────────────────
    check("units $0.10 (10c) -> 100000 atomic", a._minor_to_atomic(10) == "100000")
    check("units $1.00 (100c) -> 1000000 atomic", a._minor_to_atomic(100) == "1000000")
    check("units $0.01 (1c) -> 10000 atomic", a._minor_to_atomic(1) == "10000")
    check("units 0 -> 0 atomic", a._minor_to_atomic(0) == "0")

    # ── empty constraints -> always passes ──────────────────────────────────
    v = a.verify(_open([]), _closed(amount=999999))
    check("empty-constraints PASSES", v.ok and v.violations == [])

    # ── AmountRange (golden from SDK tests) ─────────────────────────────────
    rng = [AmountRange(min=1000, max=5000, currency="USD")]
    check("in-range (1000) PASSES", a.verify(_open(rng), _closed(amount=1000)).ok)
    check("in-range (5000 max) PASSES", a.verify(_open(rng), _closed(amount=5000)).ok)
    vmin = a.verify(_open(rng), _closed(amount=500))
    check("below-min (500) REJECTS", not vmin.ok and any("below minimum" in x for x in vmin.violations))
    vmax = a.verify(_open(rng), _closed(amount=6000))
    check("above-max (6000) REJECTS", not vmax.ok and any("exceeds maximum" in x for x in vmax.violations))
    vcur = a.verify(_open([AmountRange(max=50000, currency="USD")]), _closed(amount=1000, currency="EUR"))
    check("currency-mismatch REJECTS", not vcur.ok and any("Currency mismatch" in x for x in vcur.violations))

    # ── AllowedPayees (non-custody / payee-binding) ─────────────────────────
    allowed = [AllowedPayees(allowed=[MERCHANT_PAYEE])]
    check("allowed-payee PASSES", a.verify(_open(allowed), _closed(payee=MERCHANT_PAYEE)).ok)
    wrong = a.verify(_open(allowed), _closed(payee=Merchant(name="Attacker", id="evil-9")))
    check("wrong-payee REJECTS", not wrong.ok and any("not in allowed list" in x for x in wrong.violations))

    # ── Budget (cumulative, needs MandateContext) ───────────────────────────
    budget = [Budget(max=15.0, currency="USD")]  # 1500 cents
    under = a.verify(_open(budget), _closed(amount=1000), mandate_context=MandateContext(total_amount=0))
    check("budget-under PASSES", under.ok)
    over = a.verify(
        _open(budget), _closed(amount=100), mandate_context=MandateContext(total_amount=1500)
    )
    check("budget-cumulative-over REJECTS", not over.ok and any("exceeds budget limit" in x for x in over.violations))
    noctx = a.verify(_open(budget), _closed(amount=100), mandate_context=None)
    check("budget-missing-context REJECTS (fail-closed)", not noctx.ok)

    # ── to_payment_requirements mapping + non-custody ───────────────────────
    reqs = a.to_payment_requirements(_closed(amount=10))  # $0.10
    check("map amount 10c -> 100000 atomic", reqs["amount"] == "100000")
    check("map payTo == MERCHANT config (non-custody)", reqs["payTo"] == PAY_TO)
    check("map asset == merchant asset", reqs["asset"] == a.asset)
    check("map feePayer == merchant feePayer", reqs["extra"]["feePayer"] == FEE_PAYER)
    check("map carries AP2 provenance", reqs["extra"]["ap2"]["payee_id"] == "merch-1")
    check("map provenance minor_units", reqs["extra"]["ap2"]["minor_units"] == 10)

    # ── SD-JWT signature roundtrip + tamper-reject ──────────────────────────
    token = jwt_helper.create_jwt({"alg": "ES256"}, {"vct": "mandate.payment.1", "amt": 10}, _PRIV)
    decoded = a.verify_signature(token, _PUB)
    check("SD-JWT roundtrip decodes", decoded.get("amt") == 10)
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    try:
        a.verify_signature(tampered, _PUB)
        check("tampered SD-JWT REJECTS", False)
    except Exception:
        check("tampered SD-JWT REJECTS", True)

    # ── fail-CLOSED on a verify error (money path) ──────────────────────────
    bad = a.verify("not-a-mandate", "also-not-a-mandate")  # type: ignore[arg-type]
    check("verify fails CLOSED on garbage input", not bad.ok)

    # ── mandate freshness (VULN fix: expiry / replay) ─────────────────────────
    now = int(time.time())
    check("expired mandate REJECTS", not a.verify(_open([]), _closed(exp=now - 3600)).ok)
    check("future-dated (iat) mandate REJECTS", not a.verify(_open([]), _closed(iat=now + 3600)).ok)
    check("missing-exp mandate REJECTS (default-deny)", not a.verify(_open([]), _closed(exp=None)).ok)
    a_lax = AP2InboundAdapter(network=a.network, asset=a.asset, asset_decimals=6,
                              pay_to=PAY_TO, fee_payer=FEE_PAYER, require_expiry=False)
    check("missing-exp PASSES when require_expiry=False", a_lax.verify(_open([]), _closed(exp=None)).ok)
    check("fresh mandate PASSES freshness gate", a.verify(_open([]), _closed(exp=now + 300, iat=now)).ok)

    # ── non-custody: a mandate's payee can NEVER set the on-chain pay_to ─────
    attacker_reqs = a.to_payment_requirements(_closed(payee=Merchant(name="Attacker", id="evil-9")))
    check("non-custody: attacker-payee mandate still maps to MERCHANT pay_to", attacker_reqs["payTo"] == PAY_TO)

    # ── report ──────────────────────────────────────────────────────────────
    total = passed + len(failed)
    print(f"AP2 adapter golden self-test: {passed}/{total} PASS")
    if failed:
        for f in failed:
            print(f"  FAIL: {f}")
        return 1
    print("ALL GOLDEN PASS ✅  (real constraint engine accepts in-range, rejects over/under/wrong/over-budget)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
