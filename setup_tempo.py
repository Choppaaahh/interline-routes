#!/usr/bin/env python3
"""
Tempo (Moderato testnet) setup/inspect helper for the MPP rail.

Generates buyer + fee-payer/seller Tempo keypairs (EVM-style) + a server HMAC secret,
writes them to .env (NEVER echoes private keys — prints PUBLIC addresses + balances only),
faucet-funds both addresses with testnet pathUSD, and reports on-chain pathUSD balances.

Tempo Moderato testnet: chain 42431, RPC https://rpc.moderato.tempo.xyz, pathUSD (TIP-20,
ERC-20-compatible) at 0x20c0…0000. Faucet is API-claimable (no auth): POST .../api/faucet.

    python3 setup_tempo.py            # gen-if-absent + inspect pubkeys + balances
    python3 setup_tempo.py --faucet   # also request testnet pathUSD to each wallet

TESTNET ONLY. Private keys live in .env (gitignored); never committed, never echoed.
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
import time
from pathlib import Path

ENV = Path(__file__).resolve().parent / ".env"


def _load_env() -> dict:
    vals = {}
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip()
            os.environ.setdefault(k.strip(), v.strip())
    return vals


def _append_env(key: str, value: str) -> None:
    """Append KEY=VALUE to .env (creates the file if absent). Never prints the value."""
    with ENV.open("a") as f:
        f.write(f"{key}={value}\n")
    os.environ[key] = value


# Moderato testnet facts
RPC = os.environ.get("APV0_TEMPO_RPC", "https://rpc.moderato.tempo.xyz")
CHAIN_ID = 42431
PATH_USD = "0x20c0000000000000000000000000000000000000"
FAUCET_URL = "https://docs.tempo.xyz/api/faucet"
ERC20_BALANCEOF_ABI = [{
    "constant": True, "inputs": [{"name": "owner", "type": "address"}],
    "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
    "stateMutability": "view", "type": "function",
}]


def _gen_if_absent(env: dict) -> None:
    """Generate the 2 keypairs + server secret IF not already in .env (idempotent)."""
    from eth_account import Account

    if "APV0_MPP_BUYER_KEY" not in env:
        a = Account.create()
        _append_env("APV0_MPP_BUYER_KEY", a.key.hex())
        _append_env("APV0_MPP_BUYER_ADDR", a.address)
        print(f"  generated BUYER     {a.address}")
    if "APV0_MPP_SELLER_KEY" not in env:
        a = Account.create()
        _append_env("APV0_MPP_SELLER_KEY", a.key.hex())   # also the fee-payer (co-signs + recipient)
        _append_env("APV0_MPP_SELLER_ADDR", a.address)
        print(f"  generated SELLER/FEE {a.address}")
    if "MPP_SECRET_KEY" not in env:
        _append_env("MPP_SECRET_KEY", secrets.token_hex(32))  # server HMAC challenge secret (NOT a wallet key)
        print("  generated MPP_SECRET_KEY (server HMAC secret)")


def _path_usd_balance(w3, addr: str) -> float:
    c = w3.eth.contract(address=w3.to_checksum_address(PATH_USD), abi=ERC20_BALANCEOF_ABI)
    raw = c.functions.balanceOf(w3.to_checksum_address(addr)).call()
    return raw / (10 ** 6)  # pathUSD = 6 decimals


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--faucet", action="store_true", help="request testnet pathUSD to each wallet")
    args = ap.parse_args()

    env = _load_env()
    print("=== Tempo Moderato testnet setup (keys in .env, gitignored) ===")
    _gen_if_absent(env)
    env = _load_env()  # reload after any gen

    buyer = env["APV0_MPP_BUYER_ADDR"]
    seller = env["APV0_MPP_SELLER_ADDR"]
    print(f"\n  RPC:    {RPC}  (chain {CHAIN_ID})")
    print(f"  buyer:  {buyer}")
    print(f"  seller: {seller}  (also fee-payer + recipient)\n")

    if args.faucet:
        import httpx
        for label, addr in (("buyer", buyer), ("seller", seller)):
            try:
                r = httpx.post(FAUCET_URL, json={"address": addr}, timeout=30)
                print(f"  faucet {label} {addr[:10]}… -> HTTP {r.status_code} {str(r.text)[:120]}")
            except Exception as e:  # noqa: BLE001
                print(f"  faucet {label} FAILED: {e}")
        print("  (waiting 8s for faucet txs to land...)")
        time.sleep(8)

    # on-chain balance read (the truth — not the faucet's HTTP response)
    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(RPC))
        print(f"  chain reachable: {w3.is_connected()}  (block {w3.eth.block_number})")
        print(f"  buyer  pathUSD: {_path_usd_balance(w3, buyer):.4f}")
        print(f"  seller pathUSD: {_path_usd_balance(w3, seller):.4f}")
    except Exception as e:  # noqa: BLE001
        print(f"  balance read failed: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
