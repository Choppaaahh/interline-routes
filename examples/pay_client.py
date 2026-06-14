"""
BUYER side — pay an Interline-gated endpoint, in a few lines.

The buyer agent just calls `pay_and_get(url)`. It never thinks about chains, nonces,
EIP-3009, or signature serialization — the x402 SDK builds + signs the payment, and
Interline's buyer client handles the 402 -> pay -> retry loop. A spend-limit guard
(`max_price_atomic`) refuses anything over your budget before signing.

This is the same client path the project's own end-to-end demos use. By default it
talks to a mock-settle seller, so it runs with an ephemeral key and ZERO funds.

Repo: https://github.com/Choppaaahh/interline-routes

    # in one terminal:  uvicorn examples.gate_endpoint:app --port 8402
    # in another:        python examples/pay_client.py
"""
from __future__ import annotations

from eth_account import Account

from router import buyer

# An ephemeral buyer key — generated fresh, never persisted. For a real buyer agent,
# load YOUR funded key from the environment instead of creating one here.
BUYER_KEY = Account.create().key.hex()

ENDPOINT = "http://127.0.0.1:8402/report"

# spend-limit guard: USDC has 6 decimals, so 0.01 USDC == 10_000 atomic units.
MAX_PRICE_ATOMIC = int(0.01 * 10 ** 6)


def main() -> int:
    # one call: hit the endpoint, auto-pay if it returns 402 (within the guard), retry.
    res = buyer.pay_and_get(ENDPOINT, private_key=BUYER_KEY, max_price_atomic=MAX_PRICE_ATOMIC)

    print(f"status : {res['status']}")
    print(f"body   : {res['body']}")
    print(f"receipt: {res['receipt']}")   # {'txHash': ..., 'network': ..., 'rail': ...} when paid

    paid = res["status"] == 200 and (res.get("receipt") or {}).get("txHash")
    print("\nPAID + WORK RECEIVED" if paid else "\nnot paid")
    return 0 if paid else 1


if __name__ == "__main__":
    raise SystemExit(main())
