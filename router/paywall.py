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
import sys
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
        # HARDENED: guard per-rail so ONE broken rail can't crash the whole 402
        # (which would block ALL rails, including working ones).
        accepts = []
        for r in self._rails:
            try:
                accepts.append(r.payment_requirements(resource))
            except Exception as e:  # noqa: BLE001
                print(f"[paywall] WARN rail {getattr(r, 'name', '?')} payment_requirements() raised "
                      f"(omitted from 402 offer): {e}", file=sys.stderr)

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
        # HARDENED: a decoded payload that isn't a JSON object (list/int/str) would
        # AttributeError on payment.get() downstream — reject cleanly as malformed.
        if not isinstance(payment, dict):
            return JSONResponse(status_code=400, content={"error": "X-PAYMENT must decode to a JSON object"})

        # route to the rail that handles the buyer's chosen payment.
        # HARDENED: guard each matches() call so a misbehaving
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

        # HARDENED: a 3rd-party rail returning a non-conforming verify()/settle()
        # result must not AttributeError-500 the gate — duck-type guard (aggregator
        # extensibility defense; we accept any rail implementing the Rail Protocol).
        v = rail.verify(payment, reqs)
        if not (hasattr(v, "is_valid") and hasattr(v, "reason")):
            print(f"[paywall] ERROR rail {getattr(rail, 'name', '?')} verify() returned bad shape "
                  f"({type(v).__name__}) — no settle", file=sys.stderr)
            return JSONResponse(status_code=502, content={"error": "rail returned an invalid verify result"})
        if not v.is_valid:
            return JSONResponse(status_code=402, content={"error": f"payment invalid: {v.reason}", "accepts": accepts})

        s = rail.settle(payment, reqs)
        if not all(hasattr(s, a) for a in ("success", "tx_hash", "network", "reason")):
            print(f"[paywall] ERROR rail {getattr(rail, 'name', '?')} settle() returned bad shape "
                  f"({type(s).__name__})", file=sys.stderr)
            return JSONResponse(status_code=502, content={"error": "rail returned an invalid settle result"})
        if not s.success:
            return JSONResponse(status_code=402, content={"error": f"settlement failed: {s.reason}", "accepts": accepts})

        # settled ON-CHAIN — from here the buyer has PAID; every downstream step is
        # best-effort and must NOT cost them their payment (security review:
        # post-settle robustness — money-received/work-not-delivered hardening).
        auth = (payment.get("payload") or {}).get("authorization") or {}
        rail_name = getattr(rail, "name", "x402")
        try:
            ledger.record_settlement(
                tx_hash=s.tx_hash, payer=auth.get("from"), pay_to=reqs.get("payTo"),
                amount_atomic=reqs.get("amount"), network=s.network,
                task=request.query_params.get("task"), resource=resource,
                rail=rail_name,
            )
        except Exception as e:  # noqa: BLE001 — settle already happened on-chain; a ledger-write
            # failure must NOT 500 a buyer who already paid. Log loudly for reconciliation, continue.
            print(f"[paywall] WARN settled tx={s.tx_hash} but ledger write FAILED: {e} "
                  f"(payer={auth.get('from')} rail={rail_name}) — RECONCILE", file=sys.stderr)
        receipt = base64.b64encode(
            json.dumps({"txHash": s.tx_hash, "network": s.network, "rail": rail_name}).encode()
        ).decode()
        # deliver the work; if work_fn raises, still return the RECEIPT (payment proof for an
        # idempotent retry / reconciliation) rather than a bare 500 that hides the paid settlement.
        try:
            result = work_fn()
        except Exception as e:  # noqa: BLE001
            print(f"[paywall] WARN settled tx={s.tx_hash} but work_fn FAILED: {e} — buyer holds receipt", file=sys.stderr)
            return JSONResponse(
                status_code=502,
                content={"error": "paid+settled but work failed; retry with the same payment",
                         "txHash": s.tx_hash, "rail": rail_name},
                headers={X_PAYMENT_RESPONSE: receipt},
            )
        return JSONResponse(status_code=200, content=result, headers={X_PAYMENT_RESPONSE: receipt})
