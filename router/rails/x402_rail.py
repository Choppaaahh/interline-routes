"""
x402 rail — an x402 'exact' rail on ONE network family.

Adapts our facilitator (mock or real x402.org) + a requirements builder to the
uniform Rail interface. The x402 SDK already supports both EVM (eip155:*) and
Solana/SVM (solana:*) under the SAME `exact` scheme — so the rail is parametrized
by **network family**, and `matches()` routes by scheme AND family. THIS is what
lets one Paywall offer x402-on-Base AND x402-on-Solana and send each buyer's
payment to the right rail. (Resolves the v3 residual: "same scheme, different
network would collide" — now routed by family.)
"""
from __future__ import annotations

from typing import Callable


class X402Rail:
    """An x402 'exact' rail on one network family (EVM `eip155` or Solana `solana`)."""

    scheme = "exact"

    def __init__(
        self,
        facilitator,
        requirements_fn: Callable[[str], dict],
        *,
        name: str = "x402",
        network_family: str = "eip155",
    ) -> None:
        """
        facilitator: object with .verify(payment, reqs) + .settle(payment, reqs).
        requirements_fn: (resource_url) -> x402 PaymentRequirements dict (network-specific).
        name: registry key (e.g. "x402" for EVM/Base, "x402-solana" for Solana).
        network_family: CAIP-2 family this rail handles — "eip155" (EVM) or "solana" (SVM).
        """
        self.name = name
        self.network_family = network_family
        self._fac = facilitator
        self._reqs_fn = requirements_fn

    def payment_requirements(self, resource: str) -> dict:
        return self._reqs_fn(resource)

    def matches(self, payment: dict) -> bool:
        # Must be the 'exact' scheme AND on this rail's network family.
        # HARDENED — defensive guards for untrusted / malformed buyer input:
        # buyer-controlled payment dict is untrusted JSON — guard every field type.
        scheme = payment.get("scheme", "exact")
        if not isinstance(scheme, str) or scheme != self.scheme:
            return False
        # Back-compat: a payment with NO `network` key at all defaults to the EVM family
        # (legacy bare payments). This is the ONLY implicit-EVM path.
        if "network" not in payment:
            return self.network_family == "eip155"
        net = payment.get("network")
        # A PRESENT-but-malformed network → clean non-match (let the gate 402 "no rail
        # handles this payment"), NOT a silent mis-route to EVM and NOT a 500 crash.
        # Non-string (int/float/bool/dict/list) → False (no .split crash).
        if not isinstance(net, str):
            return False
        # Require a real CAIP-2 id: exactly "<family>:<reference>", both non-empty
        # ("eip155", "eip155:", "eip155:8453:extra", "" are all rejected).
        parts = net.split(":")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return False
        # Case-insensitive family match (CAIP-2 reference casing is unspecified).
        return parts[0].lower() == self.network_family.lower()

    def verify(self, payment: dict, requirements: dict):
        return self._fac.verify(payment, requirements)

    def settle(self, payment: dict, requirements: dict):
        return self._fac.settle(payment, requirements)
