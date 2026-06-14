"""
Real facilitator — talks to a live x402 facilitator (x402.org / self-hosted PayAI)
over HTTP, using the x402 SDK's own client so the verify/settle contract is exactly
right (not hand-rolled).

Our hand-rolled seller/buyer dicts are already the x402 **V1** shape
(PaymentRequirementsV1 / PaymentPayloadV1), so we parse them into the SDK models
and hand them to HTTPFacilitatorClientSync. We expose the SAME verify()/settle()
interface as MockFacilitator (returning our VerifyResult/SettleResult) so seller.py
can swap mock<->real on one config flag.

On a real chain the facilitator does the on-chain EIP-3009 verification against the
actual USDC contract — so the EIP-712 domain (token name/version) in our
requirements `extra` MUST match the deployed USDC. For Base USDC that's
name="USDC", version="2" (set in seller._payment_requirements). If a live verify
ever fails with signer-mismatch, the domain name/version is the first thing to check.
"""
from __future__ import annotations

from x402.http import FacilitatorConfig, HTTPFacilitatorClientSync
from x402.schemas import parse_payment_payload, parse_payment_requirements

from . import config
from .facilitator_mock import SettleResult, VerifyResult


class RealFacilitator:
    """Live x402 facilitator over HTTP via the x402 SDK client."""

    def __init__(self, url: str | None = None, timeout: float = 30.0) -> None:
        self._client = HTTPFacilitatorClientSync(
            FacilitatorConfig(url=url or config.FACILITATOR_URL, timeout=timeout)
        )

    def _models(self, payment: dict, requirements: dict):
        reqs = parse_payment_requirements(2, requirements)   # our dicts are V2 shape (amount/payTo, CAIP-2)
        payload = parse_payment_payload(payment)
        return payload, reqs

    def verify(self, payment: dict, requirements: dict) -> VerifyResult:
        try:
            payload, reqs = self._models(payment, requirements)
            r = self._client.verify(payload, reqs)
            return VerifyResult(bool(r.is_valid), r.invalid_reason or r.invalid_message or "")
        except Exception as e:  # noqa: BLE001
            return VerifyResult(False, f"facilitator verify error: {e}")

    def settle(self, payment: dict, requirements: dict) -> SettleResult:
        try:
            payload, reqs = self._models(payment, requirements)
            r = self._client.settle(payload, reqs)
            return SettleResult(
                bool(r.success),
                tx_hash=getattr(r, "transaction", "") or "",
                network=getattr(r, "network", "") or "",
                reason=(r.error_reason or r.error_message or "") if not r.success else "",
            )
        except Exception as e:  # noqa: BLE001
            return SettleResult(False, reason=f"facilitator settle error: {e}")


def get_facilitator():
    """Factory: real facilitator when live, mock otherwise. seller.py calls this."""
    if config.is_live():
        return RealFacilitator()
    from .facilitator_mock import MockFacilitator
    return MockFacilitator()
