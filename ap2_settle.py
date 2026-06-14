#!/usr/bin/env python3
"""
AP2 → live-settle bridge — Interline keystone Phase 2.

The full AP2 loop, end to end:
  1. merchant grants authority   -> OpenPaymentMandate (AmountRange + AllowedPayees)
  2. a specific payment is built -> closed PaymentMandate (payee=merchant, amount=$0.10)
  3. the mandate is SIGNED       -> JWS/ES256 (verify_signature, real crypto)
  4. Interline VERIFIES          -> signature + check_payment_constraints (settlement ⊆ mandate)
  5. Interline MAPS              -> to_payment_requirements (AP2 minor-units -> USDC atomic)
  6. the PROVEN Solana rail SETTLES -> ExactSvm buyer-sign + facilitator co-sign, on-chain
  7. AP2 audit receipt appended  -> logs/agent_payment_settlements.jsonl (mandate provenance + tx)

This proves the keystone: one signed AP2 mandate produces a REAL on-chain devnet USDC
settle on an EXISTING rail — the card/agent-commerce tier (UCP/Mastercard/Amex/PayPal all
delegate to AP2) reaching our proven rails through ONE inbound seam.

Reads Solana keypairs from .env (NEVER echoes private keys). Devnet only.

    python3 ap2_settle.py --check          # verify mandate + build, DO NOT submit
    python3 ap2_settle.py --live            # verify mandate + REAL on-chain settle
    python3 ap2_settle.py --live --usd 0.25 # different amount (must stay within mandate range)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ENV = Path(__file__).resolve().parent / ".env"
if ENV.exists():
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from jwcrypto.jwk import JWK  # noqa: E402
from solders.keypair import Keypair  # noqa: E402

from x402.schemas import PaymentPayload, PaymentRequirements  # noqa: E402
from x402.mechanisms.svm.signers import (  # noqa: E402
    FacilitatorKeypairSigner,
    KeypairSigner,
)
from x402.mechanisms.svm.exact import (  # noqa: E402
    ExactSvmClientScheme,
    ExactSvmFacilitatorScheme,
)

from ap2.sdk import jwt_helper  # noqa: E402
from ap2.sdk.constraints import MandateContext  # noqa: E402
from ap2.sdk.generated.open_payment_mandate import (  # noqa: E402
    AllowedPayees,
    AmountRange,
    OpenPaymentMandate,
)
from ap2.sdk.generated.payment_mandate import PaymentMandate  # noqa: E402
from ap2.sdk.generated.types.amount import Amount  # noqa: E402
from ap2.sdk.generated.types.merchant import Merchant  # noqa: E402
from ap2.sdk.generated.types.payment_instrument import PaymentInstrument  # noqa: E402

from router.inbound.ap2_adapter import AP2InboundAdapter  # noqa: E402
from router.ledger import record_settlement  # noqa: E402

RPC = os.environ.get("APV0_SOLANA_RPC", "https://api.devnet.solana.com")
# Canonical Solana devnet CAIP-2 (genesis-hash form) the x402 SDK requires.
NETWORK = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
USDC_MINT = os.environ.get("APV0_SOLANA_USDC_MINT", "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU")
USDC_DECIMALS = 6

# The merchant's AP2 identity (binds to the on-chain pay_to via the adapter config below).
MERCHANT = Merchant(name="Interline Demo Seller", id="interline-seller-1")
# Mandate spend ceiling (AP2 minor-units = USD cents): authorize up to $1.00 to this merchant.
MANDATE_MAX_CENTS = 100

# Replay ledger: a settled mandate's transaction_id is recorded so a captured mandate cannot
# be re-settled (the replay sibling of the AP2 expiry VULN).
REPLAY_LEDGER = Path(__file__).resolve().parent / "logs" / "ap2_settled_tx_ids.jsonl"


def _already_settled(tx_id: str) -> bool:
    if not REPLAY_LEDGER.exists():
        return False
    for line in REPLAY_LEDGER.read_text().splitlines():
        if line.strip() and json.loads(line).get("transaction_id") == tx_id:
            return True
    return False


def _record_settled(tx_id: str, tx_hash: str) -> None:
    REPLAY_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with REPLAY_LEDGER.open("a") as f:
        f.write(json.dumps({"transaction_id": tx_id, "tx_hash": tx_hash, "ts": int(time.time())}) + "\n")


def _reason(resp) -> str:
    """Defensive reason extractor — x402 Verify/Settle responses use different field names
    (invalid_reason / invalid_message / error_reason); `.reason` does NOT exist on them
    ( bug: `.reason` crashed every real rail-failure diagnostic path)."""
    for fld in ("invalid_reason", "invalid_message", "error_reason", "error_message", "reason"):
        val = getattr(resp, fld, None)
        if val:
            return str(val)
    return "unknown"


def build_signed_mandate(usd: float, issuer_priv: JWK):
    """Construct the AP2 mandate pair for a `usd` payment to MERCHANT, sign the closed one."""
    cents = int(round(usd * 100))
    now = int(time.time())
    open_mandate = OpenPaymentMandate(
        constraints=[
            AmountRange(min=1, max=MANDATE_MAX_CENTS, currency="USD"),
            AllowedPayees(allowed=[MERCHANT]),
        ],
        cnf={"jwk": issuer_priv.export_public(as_dict=True)},
    )
    closed = PaymentMandate(
        transaction_id=f"interline-ap2-demo-{now}",
        payee=MERCHANT,
        payment_amount=Amount(amount=cents, currency="USD"),
        payment_instrument=PaymentInstrument(id="usdc-solana", type="x402"),
        iat=now,
        exp=now + 300,  # fresh 5-min mandate — exercised by the adapter's freshness gate
    )
    # Sign the closed mandate as a JWS (ES256) — the trusted-surface signature.
    token = jwt_helper.create_jwt(
        {"alg": "ES256", "typ": "mandate.payment.1"},
        closed.model_dump(mode="json"),
        issuer_priv,
    )
    return open_mandate, closed, token


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="verify mandate + build, do NOT submit")
    g.add_argument("--live", action="store_true", help="verify mandate + REAL on-chain settle")
    ap.add_argument("--usd", type=float, default=0.10, help="USD amount (must stay within mandate range)")
    args = ap.parse_args()

    buyer_kp = Keypair.from_base58_string(os.environ["APV0_SOLANA_BUYER_KEYPAIR"])
    fac_kp = Keypair.from_base58_string(os.environ["APV0_SOLANA_FACILITATOR_KEYPAIR"])
    seller_owner = str(fac_kp.pubkey())   # facilitator wallet = merchant/recipient on-chain
    fee_payer = str(fac_kp.pubkey())      # facilitator wallet sponsors gas (gasless buyer)

    # ── AP2 layer: build + sign + VERIFY the mandate ─────────────────────────
    issuer_priv = JWK.generate(kty="EC", crv="P-256")
    issuer_pub = JWK.from_json(issuer_priv.export_public())
    open_mandate, closed, token = build_signed_mandate(args.usd, issuer_priv)

    adapter = AP2InboundAdapter(
        network=NETWORK,
        asset=USDC_MINT,
        asset_decimals=USDC_DECIMALS,
        pay_to=seller_owner,   # merchant's on-chain address (from MERCHANT config, not the mandate)
        fee_payer=fee_payer,
        currency_minor_decimals=2,
    )

    print(f"AP2 mandate:  authorize merchant '{MERCHANT.id}' up to ${MANDATE_MAX_CENTS/100:.2f}")
    print(f"payment:      ${args.usd:.2f}  (payee={MERCHANT.id})")

    # (a) signature
    payload_decoded = adapter.verify_signature(token, issuer_pub)
    print(f"[AP2-1] signature verify        ✓  (vct={payload_decoded.get('vct')})")

    # (b) constraints (settlement ⊆ mandate authority)
    verdict = adapter.verify(open_mandate, closed, mandate_context=MandateContext(total_amount=0))
    if not verdict.ok:
        sys.exit(f"[AP2-2] MANDATE REJECTED: {verdict.violations}")
    print("[AP2-2] constraint verify       ✓  (payment within mandate authority)")

    # (c) map -> internal PaymentRequirements (AP2 minor-units -> USDC atomic)
    reqs_dict = adapter.to_payment_requirements(closed)
    amount_atomic = reqs_dict["amount"]
    print(f"[AP2-3] mapped -> {reqs_dict['amount']} atomic USDC  (payTo={reqs_dict['payTo'][:8]}…, "
          f"feePayer={reqs_dict['extra']['feePayer'][:8]}…)\n")

    # ── rail layer: settle on the PROVEN Solana rail ─────────────────────────
    reqs = PaymentRequirements(
        scheme="exact",
        network=NETWORK,
        asset=USDC_MINT,
        amount=amount_atomic,
        pay_to=seller_owner,
        max_timeout_seconds=120,
        extra={"feePayer": fee_payer},
    )
    client = ExactSvmClientScheme(KeypairSigner(buyer_kp), rpc_url=RPC)
    inner = client.create_payment_payload(reqs)
    payload = PaymentPayload(x402_version=2, payload=inner, accepted=reqs, extensions=None)
    print("[RAIL-1] buyer signed SPL TransferChecked  ✓")

    fac = ExactSvmFacilitatorScheme(FacilitatorKeypairSigner(fac_kp, rpc_url=RPC))
    v = fac.verify(payload, reqs)
    print(f"[RAIL-2] facilitator verify -> is_valid={v.is_valid}"
          f"{(' reason=' + _reason(v)) if not v.is_valid else ''}")
    if not v.is_valid:
        sys.exit(f"RAIL VERIFY FAILED: {_reason(v)}")

    if args.check:
        print("\n--check: AP2 mandate verified + rail-payload built + verified, NOT submitted. Use --live.")
        return

    # replay guard: a captured mandate cannot be re-settled (replay sibling of the expiry VULN)
    if _already_settled(closed.transaction_id):
        sys.exit(f"REPLAY BLOCKED: mandate {closed.transaction_id} already settled")

    print("[RAIL-3] facilitator settling on-chain (signs as fee payer, submits, waits)...")
    s = fac.settle(payload, reqs)
    if not s.success:
        sys.exit(f"SETTLE FAILED: {_reason(s)}")
    _record_settled(closed.transaction_id, s.transaction)

    # ── AP2 audit receipt ────────────────────────────────────────────────────
    row = record_settlement(
        tx_hash=s.transaction,
        payer=s.payer,
        pay_to=seller_owner,
        amount_atomic=amount_atomic,
        network=s.network,
        task=f"AP2 mandate {closed.transaction_id} payee={MERCHANT.id} ${args.usd:.2f}",
        resource="ap2://inbound-adapter",
        rail="x402-svm+ap2",
    )
    print("\n=== AP2 SETTLE SUCCESS ===")
    print(f"tx:        {s.transaction}")
    print(f"payer:     {s.payer}")
    print(f"network:   {s.network}")
    print(f"mandate:   {closed.transaction_id}  (payee {MERCHANT.id}, ${args.usd:.2f} within ${MANDATE_MAX_CENTS/100:.2f})")
    print(f"receipt:   logged ts={row['ts']} rail={row['rail']}")
    print(f"explorer:  https://explorer.solana.com/tx/{s.transaction}?cluster=devnet")


if __name__ == "__main__":
    main()
