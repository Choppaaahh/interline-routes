#!/usr/bin/env python3
"""
Solana devnet setup/inspect helper for the SVM rail.

Reads buyer + facilitator keypairs from .env (NEVER echoes private keys — prints
PUBLIC keys + balances only). Reports devnet SOL balance + USDC-devnet ATA balance,
and (with --airdrop) requests devnet SOL for tx fees. The USDC mint is Circle's
official Solana devnet USDC (4zMMC9...) — test USDC comes from faucet.circle.com.

Usage:
    python3 setup_solana.py            # inspect pubkeys + balances
    python3 setup_solana.py --airdrop  # also request 1 devnet SOL to each wallet
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# --- load .env (KEY=VAL lines) WITHOUT echoing secrets ---
ENV = Path(__file__).resolve().parent / ".env"
if ENV.exists():
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

try:
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey
    from solana.rpc.api import Client
    from solana.rpc.commitment import Confirmed
except ImportError as e:
    sys.exit(f"missing solana libs: {e}  (pip install 'x402[svm]')")

RPC = os.environ.get("APV0_SOLANA_RPC", "https://api.devnet.solana.com")
USDC_MINT = Pubkey.from_string(
    os.environ.get("APV0_SOLANA_USDC_MINT", "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU")
)
TOKEN_PROGRAM = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")


def derive_ata(owner: Pubkey, mint: Pubkey) -> Pubkey:
    """Associated token account address (standard SPL derivation)."""
    ata, _ = Pubkey.find_program_address(
        [bytes(owner), bytes(TOKEN_PROGRAM), bytes(mint)], ATA_PROGRAM
    )
    return ata


def usdc_balance(client: Client, owner: Pubkey) -> str:
    ata = derive_ata(owner, USDC_MINT)
    try:
        resp = client.get_token_account_balance(ata, commitment=Confirmed)
        ui = resp.value.ui_amount_string
        return f"{ui} USDC (ata {ata})"
    except Exception:
        return f"NO ATA / 0 USDC (ata {ata} not initialized)"


def load(env_key: str) -> Keypair:
    raw = os.environ.get(env_key, "")
    if not raw:
        sys.exit(f"{env_key} not set in .env")
    return Keypair.from_base58_string(raw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--airdrop", action="store_true", help="request 1 devnet SOL to each wallet")
    args = ap.parse_args()

    client = Client(RPC)
    buyer = load("APV0_SOLANA_BUYER_KEYPAIR")
    fac = load("APV0_SOLANA_FACILITATOR_KEYPAIR")

    wallets = [("BUYER", buyer), ("FACILITATOR+SELLER", fac)]
    print(f"RPC: {RPC}")
    print(f"USDC mint: {USDC_MINT}\n")

    for label, kp in wallets:
        pk = kp.pubkey()
        if args.airdrop:
            try:
                sig = client.request_airdrop(pk, 1_000_000_000)  # 1 SOL
                print(f"  airdrop requested for {label}: {sig.value}")
                time.sleep(2)
            except Exception as e:
                print(f"  airdrop FAILED for {label}: {e}")

    if args.airdrop:
        print("  (waiting 12s for airdrops to confirm...)")
        time.sleep(12)
        print()

    for label, kp in wallets:
        pk = kp.pubkey()
        sol = client.get_balance(pk, commitment=Confirmed).value / 1e9
        print(f"{label}")
        print(f"  pubkey: {pk}")
        print(f"  SOL:    {sol:.4f}")
        print(f"  USDC:   {usdc_balance(client, pk)}")
        print()


if __name__ == "__main__":
    main()
