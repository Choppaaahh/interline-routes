"""
Paywall — the reusable gate. THIS is the product primitive.

Gate any unit of work behind a payment in one call. Backward-compatible single-rail
(v0/v1/v2) AND multi-rail aggregator (v3+):

    # single-rail (x402) — unchanged from v0
    paywall = Paywall(get_facilitator(), my_requirements_fn)

    # multi-rail aggregator (v3+) — offer N rails, route by what the buyer chose
    paywall = Paywall(rails=[X402Rail(fac, reqs_fn), StripeRail(...), L402Rail(...)])

    @app.get("/thing")
    def thing(request: Request):
        return paywall.gate(request, lambda: do_expensive_work())

The gate handles the whole dance — 402 challenge listing EVERY rail's offer →
buyer picks one → dispatch verify/settle to the rail that `matches` → record
receipt → deliver work — so a deployer never touches a protocol. Adding a rail is
a `rails=[...]` entry; no gate change. That N-rails-behind-1-call collapse IS the
aggregator (see `router/rails/`).
"""
from __future__ import annotations

import base64
import json
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse

from . import ledger

X_PAYMENT = "x-payment"
X_PAYMENT_RESPONSE = "x-payment-response"


class Paywall:
    def __init__(self, facilitator=None, requirements_fn: "Callable[[str], dict] | None" = None,
                 *, rails: "list | None" = None) -> None:
        """
        Two construction modes:
          - single-rail (back-compat): Paywall(facilitator, requirements_fn) — wraps an
            x402 rail internally.
          - multi-rail (aggregator):   Paywall(rails=[rail1, rail2, ...]) — each rail
            implements router.rails.base.Rail.
        """
        if rails is not None:
            self._rails = list(rails)
        elif facilitator is not None and requirements_fn is not None:
            from .rails.x402_rail import X402Rail  # local import avoids a cycle at module load
            self._rails = [X402Rail(facilitator, requirements_fn)]
        else:
            raise ValueError("Paywall needs either rails=[...] or (facilitator, requirements_fn)")

    def gate(self, request: Request, work_fn: Callable[[], dict]) -> JSONResponse:
        resource = str(request.url)
        # 402 challenge offers EVERY rail's PaymentRequirements (the buyer picks one).
        accepts = [r.payment_requirements(resource) for r in self._rails]

        raw = request.headers.get(X_PAYMENT)
        if not raw:
            return JSONResponse(
                status_code=402,
                content={"x402Version": 2, "accepts": accepts, "error": "payment required"},
            )

        try:
            payment = json.loads(base64.b64decode(raw))
        except Exception:  # noqa: BLE001
            return JSONResponse(status_code=400, content={"error": "malformed X-PAYMENT header"})

        # route to the rail that handles the buyer's chosen payment.
        # HARDENED — guard each matches() call so a misbehaving
        # rail on attacker-controlled `payment` can never 500 the money endpoint — same
        # defensive posture as the registry's match_rail() (router/rails/__init__.py).
        def _safe_match(r) -> bool:
            try:
                return r.matches(payment)
            except Exception:  # noqa: BLE001
                return False
        rail = next((r for r in self._rails if _safe_match(r)), None)
        if rail is None:
            return JSONResponse(
                status_code=402,
                content={"error": "no rail handles this payment", "accepts": accepts},
            )
        reqs = rail.payment_requirements(resource)

        v = rail.verify(payment, reqs)
        if not v.is_valid:
            return JSONResponse(status_code=402, content={"error": f"payment invalid: {v.reason}", "accepts": accepts})

        s = rail.settle(payment, reqs)
        if not s.success:
            return JSONResponse(status_code=402, content={"error": f"settlement failed: {s.reason}", "accepts": accepts})

        # settled — record the receipt (tagged with the rail), then deliver the work.
        auth = (payment.get("payload") or {}).get("authorization") or {}
        ledger.record_settlement(
            tx_hash=s.tx_hash, payer=auth.get("from"), pay_to=reqs.get("payTo"),
            amount_atomic=reqs.get("amount"), network=s.network,
            task=request.query_params.get("task"), resource=resource,
            rail=getattr(rail, "name", "x402"),
        )
        receipt = base64.b64encode(
            json.dumps({"txHash": s.tx_hash, "network": s.network, "rail": getattr(rail, "name", "x402")}).encode()
        ).decode()
        return JSONResponse(status_code=200, content=work_fn(), headers={X_PAYMENT_RESPONSE: receipt})
