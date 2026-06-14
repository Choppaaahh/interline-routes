#!/usr/bin/env python3
"""
End-to-end smoke for Interline — proves the full x402 loop with ZERO
testnet setup, ZERO faucet, ZERO key-at-risk (mock facilitator, ephemeral keys).

  402 (payment required)  ->  buyer signs EIP-3009 USDC auth  ->  retry
  ->  facilitator verifies the signature (REAL crypto)  ->  settles (mock)
  ->  200 + work product + receipt (tx hash)

Run:  python3 run_demo.py
Expect: "DEMO PASS" + a settlement receipt with a tx hash.

This is the dogfood proof. To go live: set APV0_NETWORK=base-sepolia +
APV0_BUYER_PRIVATE_KEY + APV0_SELLER_ADDRESS + APV0_FACILITATOR_URL, fund the
buyer with faucet USDC, and the SAME code path settles on a real chain.
"""
import os
import threading
import time

# Ephemeral keys for the smoke — generated fresh, never persisted, mock-mode only.
from eth_account import Account

_buyer = Account.create()
_seller = Account.create()
os.environ.setdefault("APV0_NETWORK", "mock")
os.environ["APV0_BUYER_PRIVATE_KEY"] = _buyer.key.hex()
os.environ["APV0_SELLER_ADDRESS"] = _seller.address
os.environ.setdefault("APV0_PRICE_USDC", "0.001")

import uvicorn  # noqa: E402

from router import buyer, config  # noqa: E402
from router.seller import app  # noqa: E402

PORT = 8401
BASE = f"http://127.0.0.1:{PORT}"


def _serve():
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


def main() -> int:
    t = threading.Thread(target=_serve, daemon=True)
    t.start()

    # wait for liveness
    import httpx
    for _ in range(50):
        try:
            if httpx.get(f"{BASE}/health", timeout=2).status_code == 200:
                break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    else:
        print("DEMO FAIL — seller did not come up")
        return 1

    print(f"network_mode={config.NETWORK_MODE}  price={config.PRICE_USDC} USDC "
          f"({config.price_atomic()} atomic)  asset={config.usdc_cfg()['address']}")
    print(f"buyer={_buyer.address}\nseller={_seller.address}\n")

    # spend-limit guard: refuse anything over $0.01 (the v0 client-side session-limit stand-in)
    limit = int(0.01 * 10 ** config.usdc_cfg()["decimals"])
    res = buyer.pay_and_get(f"{BASE}/work", max_price_atomic=limit)

    print("RESULT:")
    print(f"  status : {res['status']}")
    print(f"  body   : {res['body']}")
    print(f"  receipt: {res['receipt']}")

    ok = res["status"] == 200 and res["receipt"] and res["receipt"].get("txHash")
    print("\n" + ("DEMO PASS — agent paid agent, settlement receipt issued" if ok else "DEMO FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
