"""
Rail — the aggregator seam. THIS is what makes us "OpenRouter for payments".

A Rail bundles ONE payment rail's full lifecycle behind a uniform interface:

  - payment_requirements(resource) -> this rail's 402-challenge offer
  - matches(payment)               -> does this rail handle the buyer's chosen payment?
  - verify(payment, requirements)  -> is the payment valid? (no settle)
  - settle(payment, requirements)  -> move the funds; return a receipt

The Paywall offers EVERY registered rail in its 402 `accepts` array, the buyer
picks one, and the Paywall dispatches verify/settle to the rail that `matches`.

Adding a rail (v3+: Stripe ACP, AP2, Skyfire, L402, ...) = implement this Protocol
+ register it. No Paywall change. That **N-rails-behind-1-interface collapse** is
the product — the same shape OpenRouter has for models and Equinix has for networks.

verify() returns any object with `.is_valid` + `.reason`; settle() returns any
object with `.success` + `.tx_hash` + `.network` + `.reason` (the x402 facilitator's
VerifyResult/SettleResult already match — a new rail just returns the same shape).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Rail(Protocol):
    name: str  # registry key, e.g. "x402"

    def payment_requirements(self, resource: str) -> dict:
        """The PaymentRequirements offer for THIS rail (one entry in the 402 `accepts`)."""
        ...

    def matches(self, payment: dict) -> bool:
        """True if this rail handles the buyer's chosen payment (by scheme/network)."""
        ...

    def verify(self, payment: dict, requirements: dict):
        """Validate without moving funds. Returns an object with .is_valid + .reason."""
        ...

    def settle(self, payment: dict, requirements: dict):
        """Move the funds. Returns an object with .success + .tx_hash + .network + .reason."""
        ...
