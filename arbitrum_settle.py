#!/usr/bin/env python3
"""
Live Arbitrum Sepolia (EVM) agent-to-agent USDC settle — Interline 4th rail.

WHY OUR OWN FACILITATOR (check-first finding): the public x402.org facilitator settles only
Base Sepolia for EVM (+ Solana/Algorand/Aptos/Stellar/Hedera) — it does NOT support Arbitrum.
So Arbitrum uses our OWN local EVM facilitator — the exact shape the Solana rail proved
(`solana_settle.py`): our facilitator key relays + pays gas, the buyer signs gaslessly.

Uses the x402 SDK's CANONICAL exact-evm pieces (ExactEvmScheme client + facilitator +
EthAccountSigner + FacilitatorWeb3Signer) — do NOT hand-roll EVM crypto (hard-won lesson).

EIP-3009 gasless flow (same as Base Sepolia, different facilitator):
  1. seller builds PaymentRequirements (scheme=exact, network=eip155:421614, USDC, amount,
     pay_to=seller, extra={name,version} = the token's EIP-712 domain, READ ON-CHAIN)
  2. buyer signs an EIP-3009 transferWithAuthorization (gasless)
  3. our facilitator verifies, submits transferWithAuthorization on-chain (pays gas), waits
     -> real Arbitrum Sepolia tx hash

The EIP-712 domain (name+version) is read FROM THE TOKEN CONTRACT at runtime (EIP-5267
eip712Domain(), name()/version() fallback) so a wrong-hardcoded domain can't silently break
the signature. The ONLY external fact is the USDC address (APV0_ARB_USDC, Circle-verified).

Reads keys from .env (NEVER echoes private keys). Arbitrum Sepolia testnet only.

    python3 arbitrum_settle.py --check          # build + sign + facilitator-verify (report only)
    python3 arbitrum_settle.py --live            # real on-chain settle (default 0.10 USDC)
    python3 arbitrum_settle.py --live --usdc 0.05
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

from eth_abi import encode as abi_encode  # noqa: E402
from eth_account import Account  # noqa: E402
from eth_utils import keccak  # noqa: E402
from web3 import Web3  # noqa: E402

from x402.schemas import PaymentPayload, PaymentRequirements  # noqa: E402
from x402.mechanisms.evm.signers import EthAccountSigner, FacilitatorWeb3Signer  # noqa: E402
from x402.mechanisms.evm.exact.client import ExactEvmScheme as ExactEvmClientScheme  # noqa: E402
from x402.mechanisms.evm.exact.facilitator import ExactEvmScheme as ExactEvmFacilitatorScheme  # noqa: E402

CHAIN_ID = 421614
NETWORK = f"eip155:{CHAIN_ID}"
RPC = os.environ.get("APV0_ARB_RPC", "https://sepolia-rollup.arbitrum.io/rpc")
USDC = os.environ.get("APV0_ARB_USDC", "")        # Circle USDC on Arbitrum Sepolia (verify-first)
USDC_DECIMALS = 6

# EIP-5267 eip712Domain() + name()/version() minimal ABI for reading the token's signing domain.
_DOMAIN_ABI = [
    {"name": "eip712Domain", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [
         {"name": "fields", "type": "bytes1"}, {"name": "name", "type": "string"},
         {"name": "version", "type": "string"}, {"name": "chainId", "type": "uint256"},
         {"name": "verifyingContract", "type": "address"}, {"name": "salt", "type": "bytes32"},
         {"name": "extensions", "type": "uint256[]"}]},
    {"name": "name", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "string"}]},
    {"name": "version", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "string"}]},
    {"name": "DOMAIN_SEPARATOR", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "bytes32"}]},
]
_EIP712_DOMAIN_TYPEHASH = keccak(
    text="EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
)


def _domain_separator(name: str, version: str, chain_id: int, contract: str) -> bytes:
    return keccak(abi_encode(
        ["bytes32", "bytes32", "bytes32", "uint256", "address"],
        [_EIP712_DOMAIN_TYPEHASH, keccak(text=name), keccak(text=version),
         chain_id, Web3.to_checksum_address(contract)],
    ))


def read_eip712_domain(w3: Web3, token_addr: str, chain_id: int) -> tuple[str, str]:
    """Resolve the token's EIP-712 (name, version) and VERIFY it against the on-chain
    DOMAIN_SEPARATOR — never trust name()/version() blindly (the EIP-712 domain name can
    differ from the ERC-20 name()). Returns the (name, version) whose reconstructed domain
    separator matches the contract's; raises if none of the candidates match."""
    addr = Web3.to_checksum_address(token_addr)
    c = w3.eth.contract(address=addr, abi=_DOMAIN_ABI)

    # gather candidate (name, version) sources
    erc20_name = c.functions.name().call()
    try:
        erc20_version = c.functions.version().call()
    except Exception:
        erc20_version = None
    eip5267 = None
    try:
        d = c.functions.eip712Domain().call()
        eip5267 = (d[1], d[2])
    except Exception:
        pass

    on_chain_ds = bytes(c.functions.DOMAIN_SEPARATOR().call())

    candidates: list[tuple[str, str]] = []
    if eip5267:
        candidates.append(eip5267)
    for nm in (erc20_name, "USDC", "USD Coin"):
        for ver in ([erc20_version] if erc20_version else []) + ["2", "1"]:
            if (nm, ver) not in candidates:
                candidates.append((nm, ver))

    for nm, ver in candidates:
        if _domain_separator(nm, ver, chain_id, addr) == on_chain_ds:
            return nm, ver
    raise ValueError(
        f"could not resolve EIP-712 domain for {addr}: no (name, version) candidate reconstructs "
        f"the on-chain DOMAIN_SEPARATOR — refusing to sign with an unverified domain."
    )


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="build + sign + facilitator-verify (report only)")
    g.add_argument("--live", action="store_true", help="real on-chain settle")
    ap.add_argument("--usdc", type=float, default=0.10, help="USDC amount to transfer")
    args = ap.parse_args()

    if not USDC:
        sys.exit("APV0_ARB_USDC unset — set the Circle USDC address on Arbitrum Sepolia in .env first "
                 "(verify-before-depend: do NOT guess the token address).")
    buyer_key = os.environ.get("APV0_BUYER_PRIVATE_KEY", "")
    fac_key = os.environ.get("APV0_EVM_FACILITATOR_PRIVATE_KEY", "")
    seller = os.environ.get("APV0_SELLER_ADDRESS", "")
    if not (buyer_key and fac_key and seller):
        sys.exit("need APV0_BUYER_PRIVATE_KEY + APV0_EVM_FACILITATOR_PRIVATE_KEY + APV0_SELLER_ADDRESS in .env "
                 "(facilitator key funds gas on Arbitrum Sepolia; gen via gen_evm_facilitator.py).")

    buyer_acct = Account.from_key(buyer_key)
    fac_acct = Account.from_key(fac_key)
    amount_atomic = str(int(round(args.usdc * (10 ** USDC_DECIMALS))))

    w3 = Web3(Web3.HTTPProvider(RPC))
    on_chain_id = w3.eth.chain_id
    if on_chain_id != CHAIN_ID:
        sys.exit(f"RPC chain_id {on_chain_id} != expected {CHAIN_ID} — wrong RPC for Arbitrum Sepolia.")
    name, version = read_eip712_domain(w3, USDC, on_chain_id)

    print(f"network:     {NETWORK}  (rpc {RPC}, chain_id {on_chain_id} ✓)")
    print(f"USDC:        {USDC}  (EIP-712 domain name='{name}' version='{version}', "
          f"DOMAIN_SEPARATOR-verified ✓)")
    print(f"buyer:       {buyer_acct.address}")
    print(f"seller:      {seller}")
    print(f"facilitator: {fac_acct.address}  (pays gas)")
    print(f"amount:      {args.usdc} USDC ({amount_atomic} atomic)\n")

    # 1. seller builds the requirements (extra carries the on-chain-read EIP-712 domain).
    reqs = PaymentRequirements(
        scheme="exact",
        network=NETWORK,
        asset=USDC,
        amount=amount_atomic,
        pay_to=Web3.to_checksum_address(seller),
        max_timeout_seconds=600,
        extra={"name": name, "version": version},
    )

    # 2. buyer signs an EIP-3009 transferWithAuthorization (gasless)
    client = ExactEvmClientScheme(EthAccountSigner(buyer_acct))
    inner = client.create_payment_payload(reqs)
    payload = PaymentPayload(x402_version=2, payload=inner, accepted=reqs, extensions=None)
    print("[1] buyer signed EIP-3009 transferWithAuthorization  ✓")

    # 3. our facilitator verifies (+ settles on --live)
    fac = ExactEvmFacilitatorScheme(FacilitatorWeb3Signer(private_key=fac_key, rpc_url=RPC))
    v = fac.verify(payload, reqs)
    ok = getattr(v, "is_valid", False)
    reason = getattr(v, "invalid_reason", None) or getattr(v, "invalid_message", None)
    print(f"[2] facilitator verify -> is_valid={ok}  {('reason=' + str(reason)) if not ok else ''}")

    if args.check:
        if not ok:
            print("\n--check: verify FAILED (expected before funding — e.g. insufficient USDC balance / "
                  "facilitator needs Arb-Sep ETH for gas). Build+sign path is proven; fund the wallets, "
                  "then --live.")
        else:
            print("\n--check: built + signed + verified, NOT submitted. Use --live to settle.")
        return

    if not ok:
        sys.exit(f"VERIFY FAILED: {reason}")

    print("[3] facilitator settling on-chain (submits transferWithAuthorization, pays gas, waits)...")
    s = fac.settle(payload, reqs)
    if not getattr(s, "success", False):
        sys.exit(f"SETTLE FAILED: {getattr(s, 'error_reason', None) or getattr(s, 'invalid_reason', '')}")

    from router.ledger import record_settlement
    tx = s.transaction
    row = record_settlement(
        tx_hash=tx, payer=getattr(s, "payer", buyer_acct.address), pay_to=seller,
        amount_atomic=amount_atomic, network=NETWORK,
        task=f"Arbitrum Sepolia USDC ${args.usdc:.2f}", resource="arbitrum://x402-evm",
        rail="x402-evm-arbitrum",
    )
    print("\n=== SETTLE SUCCESS ===")
    print(f"tx:       {tx}")
    print(f"network:  {NETWORK}")
    print(f"receipt:  logged ts={row['ts']} rail={row['rail']}")
    print(f"explorer: https://sepolia.arbiscan.io/tx/{tx}")


if __name__ == "__main__":
    main()
