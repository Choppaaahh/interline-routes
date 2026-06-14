# Interline — agent-to-agent payment router

An **agent-to-agent payment router**: one agent does work, another agent pays for
it, no human keys a card. **Interline** is a neutral, non-custodial router over the
fragmented agent-payment-rail stack — *"OpenRouter for agent payments."* Built on
[x402](https://x402.org) (HTTP-402 micropayments, USDC), aggregator-shaped so adding
a rail is one adapter, not a rewrite.

Today it routes **USDC over x402 across two networks (EVM + Solana)** behind one
`Paywall.gate()` call, ships an **MCP router** so agents can discover + pay endpoints,
and includes a **Google-AP2 inbound adapter** — a signed AP2 mandate (verified, constraint-checked + freshness-gated) settles non-custodially on the rails.


## Run the demo (no wallet, no faucet, no risk)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 run_demo.py
```

Expected: `DEMO PASS — agent paid agent, settlement receipt issued`.

What just happened (the x402 "exact-evm" loop):

```
buyer GET /work
  -> seller 402 + PaymentRequirements {amount, asset=USDC, payTo, network}
  -> buyer signs an EIP-3009 transferWithAuthorization (gasless USDC auth)
  -> buyer retries with X-PAYMENT header (base64 signed payload)
  -> facilitator VERIFIES the signature (real signer-recovery) + checks policy
  -> facilitator SETTLES (mock: fake tx hash; live: on-chain USDC transfer)
  -> seller 200 + work product + X-PAYMENT-RESPONSE receipt (tx hash)
```

The facilitator's verify step is **real cryptography** even in mock mode — it
recovers the EIP-712 signer and matches it to the authorization. Only the
on-chain broadcast is mocked. So a passing demo proves the buyer's signing is
protocol-correct against real USDC.

## Files

| file | role |
|---|---|
| `router/paywall.py` | the reusable `Paywall` primitive — gate any endpoint behind x402 in one `.gate()` call |
| `router/ledger.py` | settlement receipt-ledger → `logs/agent_payment_settlements.jsonl` (audit trail) |
| `router/seller.py` | FastAPI agent that sells work — defines the requirements + work, wires one `Paywall.gate()` |
| `router/buyer.py` | agent that auto-pays 402s (`pay_and_get`) with a client-side spend limit |
| `router/facilitator_mock.py` | in-process verify+settle (real sig check via the x402 SDK's EIP-3009 typed-data, mock chain) — the dry-run rail |
| `router/facilitator_real.py` | live x402 facilitator over HTTP via the x402 SDK's own client; `get_facilitator()` picks mock↔real by config |
| `router/config.py` | network/asset facts + `.env` loader; mock↔testnet↔mainnet is a one-line env change |
| `run_demo.py` | end-to-end smoke (ephemeral keys, mock facilitator) |
| `run_live.py` | live runner — settles a real x402 payment on Base Sepolia |

## Go live (Base Sepolia testnet)

1. **Two testnet wallets** (buyer signs, seller receives):
   ```bash
   python3 -c "from eth_account import Account; a=Account.create(); print('addr', a.address); print('key', a.key.hex())"
   ```
   Do this twice. Keep the keys out of git (use `.env`).
2. **Fund the buyer** with Base Sepolia testnet USDC (Circle faucet) + a little
   testnet ETH for any gas the facilitator relays.
3. **`.env`** (copy `.env.example` → `.env`, it's gitignored + auto-loaded):
   ```
   APV0_NETWORK=base-sepolia
   APV0_BUYER_PRIVATE_KEY=0x...        # buyer testnet key (signs)
   APV0_SELLER_ADDRESS=0x...           # seller address (receives)
   APV0_FACILITATOR_URL=https://x402.org/facilitator
   ```
4. **Run it live:**
   ```bash
   python3 run_live.py
   ```
   The seller now uses `RealFacilitator` (the x402 SDK's own facilitator client),
   the buyer auto-pays the 402, and x402.org broadcasts the EIP-3009 USDC transfer
   on-chain. You get a real tx hash → `https://sepolia.basescan.org/tx/<hash>`.

   > Gasless: with EIP-3009 the **facilitator relays gas**, so the buyer only needs
   > testnet **USDC**, not ETH.

## Gate your own endpoint (the product primitive)

```python
from router.paywall import Paywall
from router.facilitator_real import get_facilitator

paywall = Paywall(get_facilitator(), my_requirements_fn)   # mock ↔ real by env

@app.get("/my-endpoint")
def my_endpoint(request: Request):
    return paywall.gate(request, lambda: do_expensive_work())
```

That one `.gate()` call handles the whole x402 V2 dance — 402 challenge → verify →
settle → record receipt → deliver — and appends every settlement to the
receipt-ledger. The deployer never touches the protocol or holds a key.

## v1 dogfood — pay an agent, get REAL work back

v0 proved the *payment*. v1 proves the *loop*: a buyer agent pays → a seller agent
does **real inference** → returns the result + a settlement receipt. The seller's
`_do_the_work(task)` is the product's integration point (plug in your own
model/compute/service).

```bash
APV0_OPENROUTER_KEY=sk-or-... python3 run_v1_dogfood.py "your task here"
```
→ `V1 DOGFOOD PASS — agent paid agent for real work ✅` (mock payment + real model
call by default; set `APV0_NETWORK=base-sepolia` for a real on-chain settle under
the same loop). Without a key it returns a stub so the product still runs.

This is the authentic loop: an agent doing real work, metered + paid-for over
x402 — the same primitive whether the work is a model call, a compute job, or
any other billable agent task.

