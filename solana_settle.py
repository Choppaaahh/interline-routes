#!/usr/bin/env python3
"""
Live Solana (SVM) agent-to-agent USDC settle — Interline 2nd rail.

Uses the x402 SDK's CANONICAL exact-svm pieces (KeypairSigner / FacilitatorKeypairSigner
/ ExactSvm{Client,Facilitator}Scheme) — do NOT hand-roll Solana crypto (hard-won lesson).

Flow (one direct settle, no HTTP server needed):
  1. seller builds PaymentRequirements (scheme=exact, network=solana:devnet, USDC, amount,
     pay_to=seller owner, extra.feePayer=facilitator)
  2. buyer wallet signs an SPL TransferChecked via the client scheme
  3. facilitator wallet verifies, co-signs as fee-payer, submits on-chain, waits
     -> real devnet tx signature

Reads keypairs from .env (NEVER echoes private keys). Devnet only.

    python3 solana_settle.py --check          # build + sign, DO NOT submit (no funds moved)
    python3 solana_settle.py --live            # real on-chain settle (default 0.10 USDC)
    python3 solana_settle.py --live --usdc 0.25
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ENV = Path(__file__).resolve().parent / ".env"
if ENV.exists():
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from solders.keypair import Keypair  # noqa: E402
from x402.schemas import PaymentPayload, PaymentRequirements  # noqa: E402
from x402.mechanisms.svm.signers import KeypairSigner, FacilitatorKeypairSigner  # noqa: E402
from x402.mechanisms.svm.exact import (  # noqa: E402
    ExactSvmClientScheme,
    ExactSvmFacilitatorScheme,
)

RPC = os.environ.get("APV0_SOLANA_RPC", "https://api.devnet.solana.com")
# Canonical Solana devnet CAIP-2 (genesis-hash form) the x402 SDK requires; still
# family "solana" so the Interline rail's family-based matches() routes it correctly.
NETWORK = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
USDC_MINT = os.environ.get("APV0_SOLANA_USDC_MINT", "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU")
USDC_DECIMALS = 6


def _reason(resp) -> str:
    """Defensive reason extractor — x402 Verify/Settle responses use invalid_reason /
    invalid_message / error_reason, NOT `.reason` (`.reason` crashed every
    real rail-failure diagnostic path)."""
    for fld in ("invalid_reason", "invalid_message", "error_reason", "error_message", "reason"):
        val = getattr(resp, fld, None)
        if val:
            return str(val)
    return "unknown"


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="build + sign, do NOT submit")
    g.add_argument("--live", action="store_true", help="real on-chain settle")
    ap.add_argument("--usdc", type=float, default=0.10, help="USDC amount to transfer")
    args = ap.parse_args()

    buyer_kp = Keypair.from_base58_string(os.environ["APV0_SOLANA_BUYER_KEYPAIR"])
    fac_kp = Keypair.from_base58_string(os.environ["APV0_SOLANA_FACILITATOR_KEYPAIR"])
    seller_owner = str(fac_kp.pubkey())   # facilitator wallet is also the merchant/recipient
    fee_payer = str(fac_kp.pubkey())      # facilitator wallet sponsors gas (canonical x402 gasless-buyer)
    amount_atomic = str(int(round(args.usdc * (10 ** USDC_DECIMALS))))

    print(f"network:     {NETWORK}  (rpc {RPC})")
    print(f"buyer:       {buyer_kp.pubkey()}")
    print(f"seller:      {seller_owner}")
    print(f"fee payer:   {fee_payer}")
    print(f"amount:      {args.usdc} USDC ({amount_atomic} atomic)\n")

    # 1. seller builds the requirements
    reqs = PaymentRequirements(
        scheme="exact",
        network=NETWORK,
        asset=USDC_MINT,
        amount=amount_atomic,
        pay_to=seller_owner,
        max_timeout_seconds=120,
        extra={"feePayer": fee_payer},
    )

    # 2. buyer signs an SPL TransferChecked
    client = ExactSvmClientScheme(KeypairSigner(buyer_kp), rpc_url=RPC)
    inner = client.create_payment_payload(reqs)
    payload = PaymentPayload(
        x402_version=2,
        payload=inner,
        accepted=reqs,
        extensions=None,
    )
    print("[1] buyer signed SPL TransferChecked payload  ✓")

    # 3. facilitator verifies (+ settles on --live)
    fac = ExactSvmFacilitatorScheme(FacilitatorKeypairSigner(fac_kp, rpc_url=RPC))
    v = fac.verify(payload, reqs)
    print(f"[2] facilitator verify -> is_valid={v.is_valid}  {('reason='+_reason(v)) if not v.is_valid else ''}")
    if not v.is_valid:
        sys.exit(f"VERIFY FAILED: {_reason(v)}")

    if args.check:
        print("\n--check: built + verified, NOT submitted (no funds moved). Use --live to settle.")
        return

    print("[3] facilitator settling on-chain (signs as fee payer, submits, waits)...")
    s = fac.settle(payload, reqs)
    if not s.success:
        sys.exit(f"SETTLE FAILED: {_reason(s)}")
    print("\n=== SETTLE SUCCESS ===")
    print(f"tx:       {s.transaction}")
    print(f"payer:    {s.payer}")
    print(f"network:  {s.network}")
    print(f"explorer: https://explorer.solana.com/tx/{s.transaction}?cluster=devnet")


if __name__ == "__main__":
    main()
