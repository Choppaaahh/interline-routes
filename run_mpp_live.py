#!/usr/bin/env python3
"""
Live MPP / Tempo (Moderato testnet) agent-to-agent settle — Interline 3rd rail.

Uses the official `pympp` SDK's CANONICAL pieces (Mpp server + mpp.client.get + the tempo
method) — do NOT hand-roll Tempo crypto. HTTP-mediated (the run_live.py shape, NOT the
direct solana_settle.py shape) because the MPP buyer credential is minted INTERNALLY by the
pympp HTTP Client during the 402 -> sign -> retry flow.

Flow:
  1. seller stands up an MPP-gated endpoint: Mpp.create(method=tempo(fee_payer=seller,
     recipient=seller, currency=pathUSD, chain_id=42431, intents={charge})). A GET route
     calls `await mpp.charge(authorization, amount)`.
  2. buyer hits it via `mpp.client.get(url, methods=[tempo(account=buyer, ...)])` — the
     Client gets the 402 challenge, builds + signs a Tempo transfer (awaiting fee payer),
     retries with Authorization: Payment.
  3. server's charge() verifies, co-signs as fee-payer, broadcasts on Moderato -> real tx;
     returns (credential, receipt). Funds move buyer -> seller (pathUSD).

Verify is by ON-CHAIN pathUSD BALANCE DELTA (read before/after), NOT the script print
Keys from.env (run setup_tempo.py --faucet first). TESTNET ONLY.

    python3 run_mpp_live.py --check    # server boots + issues a 402 challenge (no payment)
    python3 run_mpp_live.py --live     # real on-chain settle (default 0.50 pathUSD)
    python3 run_mpp_live.py --live --amount 1.00
"""
# NOTE: deliberately NO `from __future__ import annotations` — FastAPI/pydantic must
# resolve the route's `Optional[str]` Header annotation to a real type at def-time;
# stringized annotations become a ForwardRef the function-local import can't resolve.

import argparse
import asyncio
import os
import sys
import threading
import time
from pathlib import Path

ENV = Path(__file__).resolve().parent / ".env"
if ENV.exists():
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

RPC = os.environ.get("APV0_TEMPO_RPC", "https://rpc.moderato.tempo.xyz")
CHAIN_ID = 42431
PATH_USD = "0x20c0000000000000000000000000000000000000"
PORT = 8411
BASE = f"http://127.0.0.1:{PORT}"
_ERC20 = [{"constant": True, "inputs": [{"name": "o", "type": "address"}], "name": "balanceOf",
           "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"}]


def _path_usd(addr: str) -> float:
    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider(RPC))
    c = w3.eth.contract(address=w3.to_checksum_address(PATH_USD), abi=_ERC20)
    return c.functions.balanceOf(w3.to_checksum_address(addr)).call() / (10 ** 6)


def _build_server(amount: str):
    """FastAPI app gated by an MPP Tempo charge. Returns (app, seller_addr)."""
    from typing import Optional
    from fastapi import FastAPI, Header
    from fastapi.responses import JSONResponse
    from mpp import Challenge
    from mpp.server import Mpp
    from mpp.methods.tempo import tempo, ChargeIntent, TempoAccount

    seller = TempoAccount.from_key(os.environ["APV0_MPP_SELLER_KEY"])
    mpp = Mpp.create(
        method=tempo(
            fee_payer=seller,            # server co-signs (sponsors gas) + is the recipient
            recipient=seller.address,
            currency=PATH_USD,
            chain_id=CHAIN_ID,
            intents={"charge": ChargeIntent()},
        ),
        realm="interline",
        secret_key=os.environ["MPP_SECRET_KEY"],
    )

    app = FastAPI()

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/work")
    async def work(authorization: Optional[str] = Header(default=None)):
        result = await mpp.charge(authorization=authorization, amount=amount)
        if isinstance(result, Challenge):
            return JSONResponse(status_code=402, content={"error": "payment required"},
                                headers={"WWW-Authenticate": result.to_www_authenticate(mpp.realm)})
        credential, receipt = result
        return {"data": "interline: agent paid agent on Tempo (MPP rail)",
                "payer": credential.source,
                "receipt": receipt.to_payment_receipt()}

    return app, seller.address


def _serve(app):
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


def _wait_up():
    import httpx
    for _ in range(60):
        try:
            if httpx.get(f"{BASE}/health", timeout=2).status_code == 200:
                return True
        except Exception:  # noqa: BLE001
            time.sleep(0.15)
    return False


async def _buyer_pay(amount: str) -> dict:
    """Buyer hits /work via the pympp Client — auto 402 -> sign -> retry -> settle."""
    from mpp.client import get
    from mpp.methods.tempo import tempo, ChargeIntent, TempoAccount
    buyer = TempoAccount.from_key(os.environ["APV0_MPP_BUYER_KEY"])
    resp = await get(f"{BASE}/work",
                     methods=[tempo(account=buyer, chain_id=CHAIN_ID, intents={"charge": ChargeIntent()})])
    return {"status": resp.status_code, "body": resp.text}


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="server boots + issues a 402 (no payment)")
    g.add_argument("--live", action="store_true", help="real on-chain settle")
    ap.add_argument("--amount", default="0.50", help="pathUSD amount")
    args = ap.parse_args()

    for k in ("APV0_MPP_BUYER_KEY", "APV0_MPP_SELLER_KEY", "MPP_SECRET_KEY"):
        if not os.environ.get(k):
            return print(f"missing {k} in .env — run: python3 setup_tempo.py --faucet") or 2

    app, seller_addr = _build_server(args.amount)
    buyer_addr = os.environ["APV0_MPP_BUYER_ADDR"]
    threading.Thread(target=_serve, args=(app,), daemon=True).start()
    if not _wait_up():
        return print("seller did not come up") or 1
    print(f"network: Tempo Moderato (chain {CHAIN_ID}, {RPC})")
    print(f"buyer:   {buyer_addr}\nseller:  {seller_addr}\namount:  {args.amount} pathUSD\n")

    if args.check:
        import httpx
        r = httpx.get(f"{BASE}/work", timeout=10)
        ok = r.status_code == 402 and "Payment" in r.headers.get("WWW-Authenticate", "")
        print(f"[check] GET /work -> {r.status_code}  WWW-Authenticate={r.headers.get('WWW-Authenticate','')[:60]!r}")
        print("[check] PASS — server issues an MPP 402 Payment challenge." if ok else "[check] FAIL")
        return 0 if ok else 1

    # --live: balance delta is the truth
    b0, s0 = _path_usd(buyer_addr), _path_usd(seller_addr)
    print(f"[before] buyer {b0:.4f}  seller {s0:.4f} pathUSD")
    print("[pay] buyer paying via pympp Client (402 -> sign -> retry -> on-chain settle)...")
    try:
        res = asyncio.run(_buyer_pay(args.amount))
    except Exception as e:  # noqa: BLE001
        print(f"PAY ERROR: {e}")
        return 1
    print(f"[pay] server responded {res['status']}: {str(res['body'])[:200]}")
    time.sleep(6)  # let the settle tx finalize
    b1, s1 = _path_usd(buyer_addr), _path_usd(seller_addr)
    print(f"[after]  buyer {b1:.4f}  seller {s1:.4f} pathUSD")
    print(f"\n=== ON-CHAIN DELTA === buyer {b1-b0:+.4f}  seller {s1-s0:+.4f} pathUSD")
    settled = res["status"] == 200 and (s1 - s0) > 0
    print("=== SETTLE SUCCESS — agent paid agent on Tempo, on-chain confirmed ===" if settled
          else "=== NOT CONFIRMED — check response + delta above ===")
    return 0 if settled else 1


if __name__ == "__main__":
    sys.exit(main())
