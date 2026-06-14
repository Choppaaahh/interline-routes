"""
Buyer — an agent that auto-pays x402 paywalls (x402 V2, SDK-canonical signing).

This is the client half of the dogfood: an agent that needs work done, hits a
402, and pays. The signing goes through the x402 SDK's own exact-evm scheme
(EthAccountSigner under the hood) so the payment is facilitator-accepted by
construction — no hand-rolled EIP-712 to drift out of spec.

The router's whole value prop lives here: the agent calls `pay_and_get` and never
thinks about chains, nonces, EIP-3009, or signature serialization.

v0: single rail (x402 exact-evm). v3: this is where multi-rail best-execution
routing slots in (pick the rail the counterparty accepts).
"""
from __future__ import annotations

import base64
import json

import httpx
from eth_account import Account
from x402 import x402ClientSync
from x402.mechanisms.evm.exact import register_exact_evm_client
from x402.schemas import parse_payment_required

from . import config

X_PAYMENT = "X-PAYMENT"
X_PAYMENT_RESPONSE = "x-payment-response"


def _client_for(private_key: str) -> x402ClientSync:
    """An x402 client with the exact-evm scheme registered for this key."""
    client = x402ClientSync()
    register_exact_evm_client(client, Account.from_key(private_key))  # auto-wraps LocalAccount
    return client


def pay_and_get(url: str, private_key: str | None = None, *, max_price_atomic: int | None = None) -> dict:
    """
    Hit `url`. If 402, auto-pay (up to max_price_atomic guard) and retry.
    Returns {status, body, receipt}. The guard is the v0 spend-limit stand-in
    (a real ERC-4337 session-key would enforce this on-chain; v0 enforces client-side).
    """
    private_key = private_key or config.BUYER_PRIVATE_KEY
    if not private_key:
        raise RuntimeError("no buyer private key (set APV0_BUYER_PRIVATE_KEY)")
    client = _client_for(private_key)

    with httpx.Client(timeout=30) as c:
        r = c.get(url)
        if r.status_code != 402:
            return {"status": r.status_code, "body": r.json(), "receipt": None}

        payment_required = parse_payment_required(r.json())     # V2 PaymentRequired envelope
        asked = int(payment_required.accepts[0].amount)         # V2 requirements use `amount`
        if max_price_atomic is not None and asked > max_price_atomic:
            raise RuntimeError(f"price {asked} exceeds spend limit {max_price_atomic} — refusing to pay")

        # SDK builds + signs the payment (facilitator-accepted by construction)
        payload = client.create_payment_payload(payment_required)
        header = base64.b64encode(
            json.dumps(payload.model_dump(by_alias=True, exclude_none=True)).encode()
        ).decode()

        r2 = c.get(url, headers={X_PAYMENT: header})
        receipt = None
        if X_PAYMENT_RESPONSE in r2.headers:
            receipt = json.loads(base64.b64decode(r2.headers[X_PAYMENT_RESPONSE]))
        return {"status": r2.status_code, "body": r2.json() if r2.content else None, "receipt": receipt}
