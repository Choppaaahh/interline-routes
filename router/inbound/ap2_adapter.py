"""
AP2 inbound adapter (Interline keystone) Phase 2.

AP2 (Google Agent Payments Protocol) is an AUTHORIZATION LAYER, not a settlement rail.
This adapter sits IN FRONT of the rail registry: it takes a signed AP2 mandate, verifies
the signature + that the requested settlement is within the mandate's constraints, and
converts it into the internal `PaymentRequirements` shape our PROVEN x402 rails settle.

CANONICAL SURFACE (Phase-2 correction). The load-bearing security check —
`ap2.sdk.constraints.check_payment_constraints` — only validates the SDK's **generated**
chain types (`OpenPaymentMandate` + the closed `PaymentMandate` from
`ap2.sdk.generated.*`). Phase 1 mapped from `ap2.models.mandate.PaymentMandate` (an early
guess at the A2A-wire shape); that object is NOT what the constraint engine accepts, so the
Phase-1 verify() never actually exercised constraints. Phase 2 unifies the whole verify→map
path onto the generated/constraint surface so ONE mandate object flows through
verify-signature → verify-constraints → map → settle.

Wired to the OFFICIAL Google SDK (`ap2`, pip git+google-agentic-commerce/AP2):
  - signature verify  -> ap2.sdk.jwt_helper.verify_jwt            (mandates are JWS/ES256)
  - constraint verify  -> ap2.sdk.constraints.check_payment_constraints
                          ([] when the closed payment is within the open mandate;
                           non-empty = violations -> REJECT, fail CLOSED)
  - amount extract     -> PaymentMandate.payment_amount  (Amount: {amount:int, currency})
                          amount is ISO-4217 *minor units* (USD cents) -> we bridge to the
                          on-chain asset's atomic units (USDC 6-dec) before settlement.

NON-CUSTODY (preserved + load-bearing). The on-chain recipient (`pay_to`) and rail identity
(`network`/`asset`/`fee_payer`) are MERCHANT config the adapter is constructed with. The
MANDATE supplies only the *amount + authority*; it can NEVER set the payee/rail/destination.
A buyer who signs "pay merchant M up to $X" cannot redirect funds — the chain address comes
from the merchant's Interline config, not from anything the buyer controls.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

# Official AP2 SDK — the GENERATED / constraint surface (Phase-2 canonical).
from ap2.sdk import jwt_helper
from ap2.sdk.constraints import MandateContext, check_payment_constraints
from ap2.sdk.generated.open_payment_mandate import OpenPaymentMandate
from ap2.sdk.generated.payment_mandate import PaymentMandate


@dataclass
class MandateVerdict:
    """Result of verifying an AP2 mandate before routing to settlement."""

    ok: bool
    violations: list[str] = field(default_factory=list)  # empty iff ok
    reason: str = ""


class AP2InboundAdapter:
    """Verify an AP2 payment mandate, then convert it to internal PaymentRequirements.

    Stateless + non-custodial: holds no funds, only authorizes + shapes the request.
    """

    def __init__(
        self,
        *,
        network: str,
        asset: str,
        asset_decimals: int,
        pay_to: str,
        fee_payer: str,
        scheme: str = "exact",
        currency_minor_decimals: int = 2,
        clock_skew_seconds: int = 60,
        require_expiry: bool = True,
    ) -> None:
        """
        Args:
          network/asset/pay_to/fee_payer: MERCHANT rail identity (the settlement destination).
          asset_decimals: on-chain atomic decimals of `asset` (USDC = 6).
          currency_minor_decimals: ISO-4217 minor-unit decimals of the MANDATE's fiat
            currency (USD = 2). The mandate amount is in these minor units; we bridge to
            `asset_decimals` atomic units. (USDC is a USD stablecoin, so a USD-denominated
            mandate maps 1:1 in value; only the decimal scale changes: cents -> 6-dec atomic.)
        """
        self.network = network
        self.asset = asset
        self.asset_decimals = asset_decimals
        self.pay_to = pay_to
        self.fee_payer = fee_payer
        self.scheme = scheme
        self.currency_minor_decimals = currency_minor_decimals
        self.clock_skew_seconds = clock_skew_seconds
        self.require_expiry = require_expiry

    # --- 1. signature verification (JWS/ES256, via official SDK) ---
    def verify_signature(self, token: str, issuer_public_key) -> dict[str, Any]:
        """Verify a JWS-signed mandate token. Returns the decoded payload, or raises.

        `issuer_public_key` is a jwcrypto JWK (the mandate issuer's public key). Delegates
        to the SDK's `verify_jwt` — we do NOT hand-roll the crypto (hard-won lesson).
        """
        return jwt_helper.verify_jwt(token, issuer_public_key)

    # --- 2. constraint verification (settlement ⊆ mandate authority) ---
    def check_constraints(
        self,
        open_mandate: OpenPaymentMandate,
        payment_mandate: PaymentMandate,
        *,
        mandate_context: MandateContext | None = None,
        open_checkout_hash: str | None = None,
    ) -> list[str]:
        """Return constraint violations ([] = the payment is within the authorized mandate).

        Delegates to the SDK's `check_payment_constraints` (amount range / allowed payees /
        budget / execution date / preset-claim binding). This is the load-bearing
        'settlement-matches-mandate' check — the security surface of the whole adapter.
        `mandate_context` carries cumulative spend/uses (required for Budget + recurrence).
        """
        return check_payment_constraints(
            open_mandate,
            payment_mandate,
            open_checkout_hash=open_checkout_hash,
            mandate_context=mandate_context,
        )

    def verify(
        self,
        open_mandate: OpenPaymentMandate,
        payment_mandate: PaymentMandate,
        *,
        mandate_context: MandateContext | None = None,
        open_checkout_hash: str | None = None,
    ) -> MandateVerdict:
        """Full pre-settlement constraint gate. Fails CLOSED on any error (money path).

        (Signature verification is a separate call — it needs the issuer key; callers run
        verify_signature() on the raw token first, then verify() on the parsed mandates.)
        """
        try:
            violations = self.check_constraints(
                open_mandate,
                payment_mandate,
                mandate_context=mandate_context,
                open_checkout_hash=open_checkout_hash,
            )
        except Exception as e:  # noqa: BLE001 — fail CLOSED on any verify error
            return MandateVerdict(
                ok=False,
                violations=[f"constraint-check-error: {e}"],
                reason="verify raised",
            )
        if violations:
            return MandateVerdict(
                ok=False, violations=violations, reason="payment exceeds mandate authority"
            )
        # VULN-fix (surfaced in adversarial review): the upstream SDK verifies the SIGNATURE only —
        # it does NOT enforce mandate freshness. Without this, an expired (even 13h-dead) or
        # future-dated mandate settles. Freshness is OUR responsibility on the money path.
        temporal = self._temporal_violations(payment_mandate)
        if temporal:
            return MandateVerdict(ok=False, violations=temporal,
                                  reason="mandate not temporally valid")
        return MandateVerdict(ok=True, violations=[])

    def _temporal_violations(self, payment_mandate: PaymentMandate) -> list[str]:
        """Reject expired / future-dated mandates (default-deny on missing exp). ±skew tolerance.

        `exp` / `iat` are Unix-epoch seconds on the generated PaymentMandate. The SDK does not
        check them — this is the freshness gate that adversarial review flagged as a VULN.
        """
        now = int(time.time())
        skew = self.clock_skew_seconds
        out: list[str] = []
        exp = getattr(payment_mandate, "exp", None)
        iat = getattr(payment_mandate, "iat", None)
        if exp is None:
            if self.require_expiry:
                out.append("mandate has no exp (expiry) claim — default-deny")
        elif exp < now - skew:
            out.append(f"mandate expired: exp={exp} < now={now} (skew {skew}s)")
        if iat is not None and iat > now + skew:
            out.append(f"mandate not yet valid: iat={iat} > now={now} (skew {skew}s)")
        return out

    # --- 3. map a verified mandate -> internal PaymentRequirements (for our rails) ---
    def to_payment_requirements(self, payment_mandate: PaymentMandate) -> dict[str, Any]:
        """Convert a VERIFIED AP2 PaymentMandate into the PaymentRequirements dict the
        existing rail registry routes + settles. Caller MUST have run verify() first.

        Amount comes from the mandate (`payment_amount`, ISO-4217 minor units); destination
        + rail come from the merchant config this adapter was constructed with.
        """
        amt = payment_mandate.payment_amount  # Amount {amount:int (minor units), currency}
        atomic = self._minor_to_atomic(amt.amount)
        return {
            "scheme": self.scheme,
            "network": self.network,
            "asset": self.asset,
            "amount": atomic,
            "payTo": self.pay_to,
            "maxTimeoutSeconds": 120,
            "extra": {
                "feePayer": self.fee_payer,
                # provenance: which AP2 mandate authorized this settlement (audit trail)
                "ap2": {
                    "transaction_id": payment_mandate.transaction_id,
                    "currency": amt.currency,
                    "minor_units": amt.amount,
                    "payee_id": payment_mandate.payee.id,
                    "payee_name": payment_mandate.payee.name,
                },
            },
        }

    def _minor_to_atomic(self, minor_units: int) -> str:
        """ISO-4217 minor units (e.g. USD cents) -> on-chain asset atomic units (string).

        scale = asset_decimals - currency_minor_decimals.
          USD cents (2) -> USDC atomic (6): scale=+4, so 10 cents ($0.10) -> 100000 atomic.
        Uses Decimal; rejects a conversion that would lose precision (scale<0 + non-divisible).
        """
        scale = self.asset_decimals - self.currency_minor_decimals
        d = Decimal(int(minor_units))
        if scale >= 0:
            atomic = d * (Decimal(10) ** scale)
        else:
            factor = Decimal(10) ** (-scale)
            if d % factor != 0:
                raise ValueError(
                    f"amount {minor_units} minor-units not representable at "
                    f"{self.asset_decimals} asset-decimals without precision loss"
                )
            atomic = d / factor
        return str(int(atomic))
