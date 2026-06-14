#!/usr/bin/env python3
"""
Generate a dedicated EVM facilitator keypair for the Arbitrum rail.

Base Sepolia used the public x402.org facilitator, so we never needed our own EVM
facilitator key. Arbitrum's local facilitator (x402.org doesn't settle Arbitrum) needs one,
funded with Arbitrum Sepolia ETH for gas (EIP-3009 gasless-buyer: the facilitator relays).

Writes APV0_EVM_FACILITATOR_PRIVATE_KEY into .env (gitignored, runtime-only) iff not already
set. Prints ONLY the ADDRESS — the private key never touches the console or any channel.

    python3 gen_evm_facilitator.py
"""
from __future__ import annotations

from pathlib import Path

from eth_account import Account

KEY = "APV0_EVM_FACILITATOR_PRIVATE_KEY"
ENV = Path(__file__).resolve().parent / ".env"


def main():
    existing = {}
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                existing[k.strip()] = v.strip()

    if existing.get(KEY):
        addr = Account.from_key(existing[KEY]).address
        print(f"{KEY} already set in .env.")
        print(f"facilitator address: {addr}")
        print("→ fund this address with Arbitrum Sepolia ETH (gas) before --live.")
        return

    acct = Account.create()
    with ENV.open("a") as f:
        f.write(f"\n# EVM facilitator (Arbitrum rail) — gas payer; gitignored, runtime-only\n")
        f.write(f"{KEY}={acct.key.hex()}\n")
    print(f"generated EVM facilitator key -> .env ({KEY})")
    print(f"facilitator address: {acct.address}")
    print("→ fund this address with Arbitrum Sepolia ETH (gas) before --live.")
    print("  (the private key is in .env only — gitignored, never echoed/committed.)")


if __name__ == "__main__":
    main()
