"""
MPP rail — a Machine Payments Protocol rail behind the Interline aggregator.

MPP (Stripe + Tempo, IETF `draft-ryan-httpauth-payment`, spec at mpp.dev / the
"Payment" HTTP authentication scheme) is HTTP-402 **challenge → credential →
receipt** — *convergent* with x402, but natively framed with RFC-7235 auth
headers (`WWW-Authenticate: Payment` / `Authorization: Payment` /
`Payment-Receipt`) rather than x402's `X-PAYMENT` envelope. Its official Python
SDK is `pympp` (`pip install "pympp[tempo]"`, PyPI, author Tempo,
github.com/tempoxyz/pympp), and it folds verify+settle into one async
`Mpp.charge(authorization, amount)` that returns a `Challenge` (needs payment)
OR a `(credential, receipt)` tuple (paid).

Here MPP is adapted to the uniform `Rail` interface as a **settlement backend
keyed by `scheme="mpp"`**: the Paywall offers it in its 402 `accepts`, the buyer
picks it, and verify/settle route to the MPP/Tempo facilitator. THIS is what
makes the cross-protocol wedge *provable* — one Paywall offering x402 AND mpp,
each settling natively on its own backend, no caller change. The same
N-rails-behind-1-interface collapse the x402 EVM/SVM rails already prove,
extended ACROSS protocols (the durable wedge: neutral × non-custodial ×
cross-PROTOCOL).

Honest scope (Phase-1):
  - This rail settles MPP-method payments behind Interline's uniform envelope.
    Native MPP wire-format INGRESS (a gate that speaks `WWW-Authenticate:
    Payment`) is a separate, later build — not needed to prove the wedge.
  - `verify()` is a STRUCTURAL pre-check (well-formed credential, method match,
    amount bound, replay) — MPP folds the cryptographic verify INTO `charge()`,
    so a true verify-without-settle isn't natively exposed; the facilitator's
    `settle()` is where the real money moves.
  - The default `MppMockFacilitator` proves the whole loop with ZERO wallet, key,
    or chain (same minimum-size-first / dry-run-first discipline as the x402
    mock facilitator). The LIVE Tempo settle (`MppTempoFacilitator`) uses the
    official `pympp` SDK + a funded Tempo wallet + `MPP_SECRET_KEY`, and is GATED
    exactly like x402's real-facilitator path was gated on funded testnet keys.

A new rail = implement this + register it. No Paywall change.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Callable


# --- result shapes (duck-type the x402 facilitator's VerifyResult/SettleResult) ---
@dataclass
class MppVerifyResult:
    is_valid: bool
    reason: str = ""


@dataclass
class MppSettleResult:
    success: bool
    tx_hash: str = ""
    network: str = ""
    reason: str = ""


def default_mpp_requirements_fn(
    *,
    currency: str,
    recipient: str,
    amount: str,
    network: str = "tempo-testnet",
    method: str = "tempo",
    realm: str = "interline",
) -> "Callable[[str], dict]":
    """Build a requirements_fn that returns a standard MPP `tempo`-method offer.

    Field names track the real `pympp` `tempo(currency=, recipient=)` +
    `charge(amount=)` shape; `scheme`/`method`/`payTo`/`network`/`resource` keep
    the offer compatible with Interline's uniform `accepts`/ledger envelope
    (payTo mirrors the MPP `recipient` so the aggregator's ledger + receipt code
    reads it uniformly across rails). `amount` is a DECIMAL string ("0.50"),
    NOT x402's atomic integer — faithful to `Mpp.charge(amount="0.50")`.
    """
    def _fn(resource: str) -> dict:
        return {
            "scheme": "mpp",
            "method": method,
            "network": network,
            "currency": currency,   # MPP token address
            "payTo": recipient,     # MPP `recipient` (aliased for uniform ledger)
            "amount": amount,       # MPP decimal charge amount, e.g. "0.50"
            "realm": realm,
            "resource": resource,
        }
    return _fn


class MppMockFacilitator:
    """In-process stand-in for the live MPP/Tempo settle — NO wallet, NO chain.

    Does honest STRUCTURAL validation of an MPP-method credential (method match,
    payer present, amount within bound, replay protection by nonce) and returns a
    deterministic mock receipt. A mock PASS proves the buyer's payment is
    well-formed + policy-correct for this rail; it does NOT move funds. Swap in
    `MppTempoFacilitator` (funded Tempo wallet + MPP_SECRET_KEY) for live settle.
    """

    def __init__(self) -> None:
        self._spent_nonces: "set[str]" = set()  # replay protection

    def verify(self, payment: dict, requirements: dict) -> MppVerifyResult:
        try:
            payload = payment.get("payload")
            if not isinstance(payload, dict):
                return MppVerifyResult(False, "missing payload")
            auth = payload.get("authorization")
            if not isinstance(auth, dict):
                return MppVerifyResult(False, "missing authorization credential")

            # method match (mpp routes by scheme; the method scopes the settle backend)
            req_method = requirements.get("method", "tempo")
            if auth.get("method", req_method) != req_method:
                return MppVerifyResult(False, f"method mismatch: {auth.get('method')} != {req_method}")

            payer = auth.get("from")
            if not isinstance(payer, str) or not payer:
                return MppVerifyResult(False, "credential missing payer (from)")

            # amount bound — MPP amounts are DECIMAL strings ("0.50"), not atomic ints
            try:
                if Decimal(str(auth.get("amount", "0"))) < Decimal(str(requirements.get("amount", "0"))):
                    return MppVerifyResult(False, "amount below required")
            except (InvalidOperation, ValueError):
                return MppVerifyResult(False, "amount not a valid decimal")

            # replay: a credential nonce can only settle once (Tempo enforces this on-chain)
            nonce = payload.get("nonce")
            if nonce is not None and nonce in self._spent_nonces:
                return MppVerifyResult(False, "nonce already spent (replay)")

            return MppVerifyResult(True)
        except Exception as e:  # noqa: BLE001 — surface any shape error as invalid, never crash the gate
            return MppVerifyResult(False, f"verify error: {e}")

    def settle(self, payment: dict, requirements: dict) -> MppSettleResult:
        v = self.verify(payment, requirements)
        if not v.is_valid:
            return MppSettleResult(False, reason=v.reason)
        payload = payment["payload"]
        auth = payload["authorization"]
        nonce = payload.get("nonce")
        if nonce is not None:
            self._spent_nonces.add(nonce)
        # deterministic mock receipt (a real Tempo settle returns a chain tx ref)
        seed = f"{nonce}{auth.get('from')}{auth.get('amount')}{requirements.get('payTo')}"
        fake_tx = "tempo:" + hashlib.sha256(seed.encode()).hexdigest()
        return MppSettleResult(True, tx_hash=fake_tx, network=str(requirements.get("network", "")))


def _run_coro(coro):
    """Run an async coroutine from sync code. The Rail.settle path is called from
    the SYNC Paywall.gate (a sync FastAPI route), so there's no running loop —
    asyncio.run is correct. The thread fallback covers the rare case of an already-
    running loop (e.g. an async caller), so settle() never raises on that."""
    try:
        return asyncio.run(coro)
    except RuntimeError:
        # already-running loop -> run the coroutine on a fresh loop in a worker thread
        import threading
        box: dict = {}

        def _worker():
            loop = asyncio.new_event_loop()
            try:
                box["r"] = loop.run_until_complete(coro)
            except Exception as e:  # noqa: BLE001
                box["e"] = e
            finally:
                loop.close()

        t = threading.Thread(target=_worker)
        t.start()
        t.join()
        if "e" in box:
            raise box["e"]
        return box["r"]


class MppTempoFacilitator:
    """LIVE MPP settle via the official `pympp` SDK on Tempo. Real-wired; GATED on a wallet.

    Requires at go-live: `pip install "pympp[tempo]"` (installed), a funded Tempo
    wallet, and `MPP_SECRET_KEY`. `MppMockFacilitator` stays the DEFAULT — the rail,
    routing, and wedge proof all work without any of this (mock first, funded settle
    second — same discipline as x402's mock→funded-testnet graduation).

    Real flow (verified against the installed pympp v0.8.x API + mpp.dev/sdk/python):
        from mpp.server import Mpp
        from mpp.methods.tempo import tempo, ChargeIntent
        mpp = Mpp.create(
            method=tempo(currency=.., recipient=.., chain_id=TESTNET_CHAIN_ID,
                         rpc_url=.., intents={"charge": ChargeIntent()}),
            realm=.., secret_key=os.environ["MPP_SECRET_KEY"])
        result = await mpp.charge(authorization=<Authorization: Payment header>, amount="0.50")
        # result is a Challenge (credential absent/invalid) OR (Credential, Receipt) (paid).
    `charge()` is async + folds verify+settle, so `verify()` here is the same
    structural pre-check the mock does; `settle()` runs the real charge.

    The buyer's signed credential rides the Interline envelope at
    `payment["payload"]["mpp_authorization"]` (the raw `Authorization: Payment`
    header). Absent/invalid -> charge() returns a Challenge -> SettleResult(False).
    No funded wallet -> an honest gated-error, never a fake success.
    """

    def __init__(
        self,
        *,
        recipient: str,
        currency: "str | None" = None,
        secret_key: "str | None" = None,
        chain_id: "int | None" = None,
        rpc_url: "str | None" = None,
        realm: str = "interline",
        network_label: str = "tempo-testnet",
    ) -> None:
        self._recipient = recipient
        self._currency = currency
        self._secret_key = secret_key or os.environ.get("MPP_SECRET_KEY")
        self._chain_id = chain_id
        self._rpc_url = rpc_url
        self._realm = realm
        self._network_label = network_label
        self._mock = MppMockFacilitator()  # reuse the structural verify

    def verify(self, payment: dict, requirements: dict) -> MppVerifyResult:
        # structural pre-check identical to the mock (MPP's crypto verify lives in charge()).
        return self._mock.verify(payment, requirements)

    def _build_mpp(self):
        """Construct the server-side MPP handler (sync). Lazy-imports pympp."""
        from mpp.server import Mpp  # type: ignore
        from mpp.methods.tempo import (  # type: ignore
            tempo, ChargeIntent, TESTNET_CHAIN_ID, default_currency_for_chain,
        )
        chain_id = self._chain_id or TESTNET_CHAIN_ID
        currency = self._currency or default_currency_for_chain(chain_id)
        method = tempo(
            currency=currency,
            recipient=self._recipient,
            chain_id=chain_id,
            rpc_url=self._rpc_url,
            intents={"charge": ChargeIntent()},
        )
        return Mpp.create(method=method, realm=self._realm, secret_key=self._secret_key)

    def _map_charge_result(self, result) -> MppSettleResult:
        """Map pympp charge()'s return -> the uniform SettleResult.
        (Credential, Receipt) tuple = settled; anything else (a Challenge) = the
        credential was absent/invalid/insufficient so no settle happened."""
        if isinstance(result, tuple) and len(result) == 2:
            _cred, receipt = result
            ok = bool(getattr(receipt, "success", True))
            tx = getattr(receipt, "reference", None) or getattr(receipt, "external_id", "") or ""
            return MppSettleResult(
                ok,
                tx_hash=str(tx),
                network=self._network_label,
                reason="" if ok else f"receipt not successful (status={getattr(receipt, 'status', '?')})",
            )
        return MppSettleResult(
            False,
            reason="charge returned a Challenge — credential absent/invalid/insufficient (no settle)",
        )

    def settle(self, payment: dict, requirements: dict) -> MppSettleResult:
        if not self._secret_key:
            return MppSettleResult(False, reason="MPP_SECRET_KEY not set — live Tempo settle is gated")
        try:
            from mpp.server import Mpp  # type: ignore  # noqa: F401 — import-guard only
        except ImportError:
            return MppSettleResult(False, reason='pympp not installed — pip install "pympp[tempo]" for live Tempo settle')
        # the buyer's signed `Authorization: Payment` header rides the envelope
        auth_header = (payment.get("payload") or {}).get("mpp_authorization")
        amount = str(requirements.get("amount", "0"))
        try:
            mpp = self._build_mpp()
            result = _run_coro(mpp.charge(authorization=auth_header, amount=amount))
        except Exception as e:  # noqa: BLE001 — surface any wiring/network error as a clean failure
            return MppSettleResult(False, reason=f"charge error: {e}")
        return self._map_charge_result(result)


class MppRail:
    """An MPP 'tempo'-method rail. Adapts an MPP facilitator + requirements builder
    to the uniform `Rail` interface so the Paywall can offer x402 AND mpp behind
    one integration."""

    scheme = "mpp"

    def __init__(
        self,
        facilitator,
        requirements_fn: "Callable[[str], dict]",
        *,
        name: str = "mpp",
        method: str = "tempo",
    ) -> None:
        """
        facilitator: object with .verify(payment, reqs) + .settle(payment, reqs)
                     returning the MppVerifyResult/MppSettleResult shape.
        requirements_fn: (resource_url) -> MPP PaymentRequirements offer dict.
        name: registry key (default "mpp").
        method: MPP payment method this rail settles (default "tempo").
        """
        self.name = name
        self.method = method
        self._fac = facilitator
        self._reqs_fn = requirements_fn

    def payment_requirements(self, resource: str) -> dict:
        return self._reqs_fn(resource)

    def matches(self, payment: dict) -> bool:
        # Untrusted buyer JSON — guard the scheme field type before comparing
        # (same defensive posture as X402Rail.matches per the adversarial validation
        # Workflow: a non-string/absent scheme is a clean non-match, never a crash).
        scheme = payment.get("scheme")
        return isinstance(scheme, str) and scheme == self.scheme

    def verify(self, payment: dict, requirements: dict):
        return self._fac.verify(payment, requirements)

    def settle(self, payment: dict, requirements: dict):
        return self._fac.settle(payment, requirements)
