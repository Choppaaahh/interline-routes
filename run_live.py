#!/usr/bin/env python3
"""
LIVE runner — settles a real x402 payment on Base Sepolia testnet.

Prereqs (all in .env — see .env.example):
  APV0_NETWORK=base-sepolia
  APV0_BUYER_PRIVATE_KEY=0x...   (funded with faucet USDC)
  APV0_SELLER_ADDRESS=0x...
  APV0_FACILITATOR_URL=https://x402.org/facilitator

Run:  python3 run_live.py
This starts the seller (real facilitator), has the buyer hit /work, auto-pays the
402, and the x402.org facilitator broadcasts the EIP-3009 USDC transfer on-chain.
You get a REAL tx hash -> view it on https://sepolia.basescan.org/tx/<hash>.

Same code path as run_demo.py — only the facilitator + keys + network differ.
"""
import threading
import time

from router import buyer, config  # config loads .env at import
from router.seller import app

PORT = 8401
BASE = f"http://127.0.0.1:{PORT}"


def _serve():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


def main() -> int:
    # guard: refuse to run "live" if we're actually in mock mode or missing config
    if config.NETWORK_MODE == "mock":
        print("APV0_NETWORK=mock — this is the live runner. Set APV0_NETWORK=base-sepolia in .env.")
        print("(for the no-funds dry-run loop, use:  python3 run_demo.py)")
        return 2
    if not config.BUYER_PRIVATE_KEY or not config.SELLER_PAY_TO:
        print("missing APV0_BUYER_PRIVATE_KEY or APV0_SELLER_ADDRESS in .env")
        return 2

    t = threading.Thread(target=_serve, daemon=True)
    t.start()

    import httpx
    for _ in range(50):
        try:
            if httpx.get(f"{BASE}/health", timeout=2).status_code == 200:
                break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    else:
        print("seller did not come up")
        return 1

    u = config.usdc_cfg()
    print(f"network={config.X402_NETWORK}  facilitator={config.FACILITATOR_URL}")
    print(f"price={config.PRICE_USDC} USDC  asset={u['address']}  payTo={config.SELLER_PAY_TO}\n")
    print("hitting /work — buyer will auto-pay the 402 and the facilitator will settle on-chain...\n")

    limit = int(0.05 * 10 ** u["decimals"])  # client-side spend guard: refuse >$0.05/call
    res = buyer.pay_and_get(f"{BASE}/work", max_price_atomic=limit)

    print("RESULT:")
    print(f"  status : {res['status']}")
    print(f"  body   : {res['body']}")
    print(f"  receipt: {res['receipt']}")

    tx = (res.get("receipt") or {}).get("txHash")
    if res["status"] == 200 and tx:
        print(f"\nLIVE SETTLE OK — on-chain tx:\n  https://sepolia.basescan.org/tx/{tx}")
        return 0
    print("\nLIVE SETTLE FAILED — see body/receipt above. Common causes:")
    print("  - buyer wallet has no testnet USDC (faucet it)")
    print("  - EIP-712 domain mismatch (token name/version) — check seller._payment_requirements extra")
    print("  - facilitator doesn't support this asset/network combo")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
