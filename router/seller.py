"""
Seller — an agent that sells a unit of work behind an x402 paywall.

v0 "unit of work" = a stand-in for real agent work (a model call / summary /
compute job). This is the dogfood target: the agent that does work gets paid for
it, agent-to-agent, no human keying a card.

Flow (x402 "exact-evm" scheme):
  GET  /work            -> if no payment: 402 + PaymentRequirements JSON + header
                           if X-PAYMENT present: verify+settle -> 200 + work + receipt
  GET  /health          -> liveness

The x402 gate lives in `router/paywall.py` (the reusable `Paywall` primitive) —
this file just defines the requirements + the work, and wires one `.gate()` call.
"""
from __future__ import annotations

import time

import httpx
from fastapi import FastAPI, Request

from . import config
from .facilitator_real import get_facilitator
from .paywall import Paywall

app = FastAPI(title="Interline seller")


def _payment_requirements(resource: str) -> dict:
    """Build the x402 V2 PaymentRequirements (the 402 body).

    V2 shape: `amount` + `payTo` (not maxAmountRequired/resource), CAIP-2 network.
    """
    u = config.usdc_cfg()
    return {
        "scheme": "exact",
        "network": config.X402_NETWORK,                 # eip155:<chainId>
        "asset": u["address"],
        "amount": str(config.price_atomic()),
        "payTo": config.SELLER_PAY_TO or "0xSELLER_UNSET",
        "maxTimeoutSeconds": 120,
        "extra": {"name": u["name"], "version": "2", "chainId": u["chain_id"]},
    }


def _do_the_work(task: str | None = None) -> dict:
    """The actual paid service — v1 dogfood: a REAL model call when a key is set.

    This is the product's integration point. A deployer plugs in their own
    model/compute/service here. With APV0_OPENROUTER_KEY set, the seller does a
    real inference call = the authentic "pay an agent → it does real work for you".
    """
    task = (task or "Write one crisp sentence on why agents will pay each other.").strip()
    if config.OPENROUTER_KEY:
        try:
            r = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {config.OPENROUTER_KEY}"},
                json={"model": config.WORK_MODEL,
                      "messages": [{"role": "user", "content": task}],
                      "max_tokens": 400},
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
            return {
                "task": task,
                "result": data["choices"][0]["message"]["content"],
                "model": config.WORK_MODEL,
                "produced_at": int(time.time()),
            }
        except Exception as e:  # noqa: BLE001 — surface the failure, don't fake work
            return {"task": task, "error": f"work failed: {e}", "produced_at": int(time.time())}
    # no key → honest local stub (product still runs; deployer wires real work)
    return {
        "task": task,
        "result": "[stub] set APV0_OPENROUTER_KEY for a real model answer here",
        "produced_at": int(time.time()),
    }


# the reusable x402 gate — mock facilitator for dry-run, real x402.org when live.
_paywall = Paywall(get_facilitator(), _payment_requirements)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "network_mode": config.NETWORK_MODE, "price_usdc": config.PRICE_USDC}


@app.get("/work")
def work(request: Request):
    # one line gates the work behind payment — the Paywall handles the x402 dance.
    return _paywall.gate(request, lambda: _do_the_work(request.query_params.get("task")))
