#!/usr/bin/env python3
"""
v1 DOGFOOD — pay an agent, get REAL work back.

The v0 milestone proved the *payment* (agent → agent USDC settle). v1 proves the
*loop*: a buyer agent needs work done → pays → a seller agent does REAL inference
→ returns the result + a settlement receipt. This is the authentic origin story:
an agent's own real work, metered + paid-for over x402.

Default = mock payment (no testnet needed) + real work (set APV0_OPENROUTER_KEY).
Set APV0_NETWORK=base-sepolia for a real on-chain settle under the same loop.

Run:  APV0_OPENROUTER_KEY=sk-... python3 run_v1_dogfood.py "your task here"
"""
import os
import sys
import threading
import time
import urllib.parse

from eth_account import Account

# mock-payment by default; ephemeral keys so the demo runs with zero setup.
_buyer = Account.create()
_seller = Account.create()
os.environ.setdefault("APV0_NETWORK", "mock")
os.environ["APV0_BUYER_PRIVATE_KEY"] = _buyer.key.hex()
os.environ["APV0_SELLER_ADDRESS"] = _seller.address

import uvicorn  # noqa: E402

from router import buyer, config  # noqa: E402
from router.seller import app  # noqa: E402

PORT = 8401
BASE = f"http://127.0.0.1:{PORT}"


def _serve():
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


def main() -> int:
    task = " ".join(sys.argv[1:]).strip() or "In 2 sentences: why will autonomous agents pay each other?"

    threading.Thread(target=_serve, daemon=True).start()
    import httpx
    for _ in range(50):
        try:
            if httpx.get(f"{BASE}/health", timeout=2).status_code == 200:
                break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    else:
        print("seller did not come up"); return 1

    real = "REAL model call" if config.OPENROUTER_KEY else "stub (set APV0_OPENROUTER_KEY for real)"
    print(f"payment={config.NETWORK_MODE}  work={real} ({config.WORK_MODEL})  price={config.PRICE_USDC} USDC")
    print(f"buyer={_buyer.address}  seller={_seller.address}")
    print(f"\nTASK: {task}\n")
    print("buyer hits /work → 402 → pays → seller does the work → returns result + receipt...\n")

    url = f"{BASE}/work?task=" + urllib.parse.quote(task)
    limit = int(0.05 * 10 ** config.usdc_cfg()["decimals"])
    res = buyer.pay_and_get(url, max_price_atomic=limit)

    body = res.get("body") or {}
    print("=" * 60)
    print(f"PAID: status {res['status']}  |  receipt txHash: {(res.get('receipt') or {}).get('txHash','—')}")
    print("WORK PRODUCT:")
    print(f"  {body.get('result') or body.get('error') or body}")
    print("=" * 60)

    paid = res["status"] == 200 and (res.get("receipt") or {}).get("txHash")
    work_ok = bool(body.get("result")) and not body.get("error")
    ok = paid and work_ok
    if ok:
        print("\nV1 DOGFOOD PASS — agent paid agent for real work ✅")
    elif paid and not work_ok:
        print(f"\nV1 PARTIAL — payment settled but work failed: {body.get('error')}")
    else:
        print("\nV1 FAIL — payment did not settle")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
