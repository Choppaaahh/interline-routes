"""
Mock facilitator — verify + settle, in-process, NO chain, NO broadcast.

This is the dry-run rail. It does REAL cryptographic verification of the x402 V2
exact-evm payload (rebuilds the EIP-3009 typed-data via the SDK's own eip712
helper and recovers the signer), but does NOT broadcast on-chain — settle()
returns a deterministic fake tx hash.

Because it uses the SDK's `build_typed_data_for_signing`, the mock and the live
x402.org facilitator verify against IDENTICAL typed-data — so a mock PASS means
the buyer's signing is genuinely protocol-correct, not just self-consistent.

Why a mock first: proves the entire x402 loop (402 -> sign -> retry -> verify ->
settle -> 200) end-to-end with zero testnet setup, zero faucet, zero key-at-risk.
Same discipline as standard minimum-size-first / dry-run-first.
Swap APV0_NETWORK=base-sepolia to route verify/settle to the real facilitator.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from eth_account import Account
from x402.mechanisms.evm.eip712 import hash_eip3009_authorization
from x402.mechanisms.evm.types import ExactEIP3009Authorization


@dataclass
class VerifyResult:
    is_valid: bool
    reason: str = ""


@dataclass
class SettleResult:
    success: bool
    tx_hash: str = ""
    network: str = ""
    reason: str = ""


class MockFacilitator:
    """In-process stand-in for the real x402 facilitator (PayAI / x402.org)."""

    def __init__(self) -> None:
        self._spent_nonces: set[str] = set()  # replay protection (a real chain does this via the nonce)

    def verify(self, payment: dict, requirements: dict) -> VerifyResult:
        """Real signer-recovery against SDK-built typed-data; no chain read."""
        try:
            inner = payment["payload"]
            auth = inner["authorization"]
            sig = inner["signature"]
            extra = requirements.get("extra", {})

            authz = ExactEIP3009Authorization(
                from_address=auth["from"],
                to=auth["to"],
                value=str(auth["value"]),
                valid_after=str(auth["validAfter"]),
                valid_before=str(auth["validBefore"]),
                nonce=auth["nonce"],
            )
            # SDK builds the exact EIP-712 hash; we recover the signer from it.
            msg_hash = hash_eip3009_authorization(
                authz, int(extra["chainId"]), requirements["asset"],
                extra.get("name", "USDC"), extra.get("version", "2"),
            )
            recovered = Account._recover_hash(
                msg_hash, signature=bytes.fromhex(sig.removeprefix("0x"))
            )
            if recovered.lower() != auth["from"].lower():
                return VerifyResult(False, f"signer mismatch: recovered {recovered} != from {auth['from']}")

            # policy checks (the same ones a real facilitator enforces on-chain)
            if auth["to"].lower() != requirements["payTo"].lower():
                return VerifyResult(False, "payTo mismatch")
            if int(auth["value"]) < int(requirements["amount"]):
                return VerifyResult(False, "amount below required")
            now = int(time.time())
            if int(auth["validAfter"]) > now or now > int(auth["validBefore"]):
                return VerifyResult(False, "authorization not currently valid (expiry window)")
            if auth["nonce"] in self._spent_nonces:
                return VerifyResult(False, "nonce already spent (replay)")
            return VerifyResult(True)
        except Exception as e:  # noqa: BLE001 — mock surfaces any shape error as invalid
            return VerifyResult(False, f"verify error: {e}")

    def settle(self, payment: dict, requirements: dict) -> SettleResult:
        """Mock settle: mark nonce spent, return deterministic fake tx hash."""
        v = self.verify(payment, requirements)
        if not v.is_valid:
            return SettleResult(False, reason=v.reason)
        auth = payment["payload"]["authorization"]
        self._spent_nonces.add(auth["nonce"])
        fake_tx = "0x" + hashlib.sha256(
            (auth["nonce"] + auth["from"] + auth["to"] + str(auth["value"])).encode()
        ).hexdigest()
        return SettleResult(True, tx_hash=fake_tx, network=str(requirements.get("network", "")))
