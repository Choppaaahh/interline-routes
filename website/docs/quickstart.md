# Quickstart

## Run the demo — no wallet, no faucet, no risk

```bash
git clone https://github.com/Choppaaahh/interline-routes
cd interline-routes
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 run_demo.py
```

Expected:

```
DEMO PASS — agent paid agent, settlement receipt issued
```

### What just happened (the x402 "exact" loop)

```
buyer GET /work
  → seller 402 + PaymentRequirements {amount, asset=USDC, payTo, network}
  → buyer signs an EIP-3009 transferWithAuthorization (gasless USDC auth)
  → buyer retries with X-PAYMENT header (base64 signed payload)
  → facilitator VERIFIES the signature (real signer-recovery) + checks policy
  → facilitator SETTLES (mock: fake tx hash; live: on-chain USDC transfer)
  → seller 200 + work product + X-PAYMENT-RESPONSE receipt (tx hash)
```

!!! note "The verify step is real cryptography — even in mock mode"
    The facilitator recovers the EIP-712 signer and matches it to the authorization;
    only the on-chain broadcast is mocked. So a passing demo proves the buyer's signing
    is protocol-correct against real USDC.

## Gate your own endpoint (the product primitive)

```python
from router.paywall import Paywall
from router.facilitator_real import get_facilitator

paywall = Paywall(get_facilitator(), my_requirements_fn)   # mock ↔ real by env

@app.get("/my-endpoint")
def my_endpoint(request: Request):
    return paywall.gate(request, lambda: do_expensive_work())
```

That one `.gate()` call handles the whole dance — 402 challenge → verify → settle → record
receipt → deliver — and appends every settlement to the receipt-ledger. **The deployer never
touches the protocol or holds a key.**

To offer **multiple rails** at once, construct the Paywall with a rail list:

```python
from router.rails import X402Rail, MppRail
paywall = Paywall(rails=[X402Rail(fac, x402_reqs_fn), MppRail(mpp_fac, mpp_reqs_fn)])
```

The 402 now advertises both; the buyer picks one; the matching rail settles. See
[Rails & the Rail Protocol](rails.md).

## Pay an agent, get real work back (v1 dogfood)

`v0` proves the *payment*; `v1` proves the *loop* — a buyer agent pays, a seller agent does
**real inference**, returns the result + a settlement receipt:

```bash
APV0_OPENROUTER_KEY=sk-or-... python3 run_v1_dogfood.py "your task here"
```

```
V1 DOGFOOD PASS — agent paid agent for real work ✅
```

The seller's `_do_the_work(task)` is the integration point — plug in your own
model / compute / service. (Without a key it returns a stub so the loop still runs.)

→ Ready for real money? See **[Go live](go-live.md)**.
