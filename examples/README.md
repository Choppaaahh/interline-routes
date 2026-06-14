# Interline examples

"Gate + pay" in a handful of lines, using the real Interline API. Interline is a
neutral, non-custodial router for agent-to-agent payments — **one `Paywall` offers
many payment rails at once** (x402 on EVM/Solana, MPP on Tempo, ...), and the buyer's
agent settles on whichever rail it speaks. Adding a rail is one list entry; the gated
route never changes.

Repo: <https://github.com/Choppaaahh/interline-routes>

| File | Side | What it shows |
|------|------|----------------|
| [`gate_endpoint.py`](./gate_endpoint.py) | **Seller** | Gate `GET /report` behind x402 **and** MPP with one `Paywall`, in ~10 lines. Mock facilitators → runs with zero wallet/key/chain. |
| [`pay_client.py`](./pay_client.py) | **Buyer** | Pay an Interline-gated endpoint with one `pay_and_get(url)` call, including a spend-limit guard. |
| [`langchain_tool.py`](./langchain_tool.py) | **Agent** | Wrap "pay for a resource" as a LangChain `@tool` so an LLM agent can pay autonomously. LangChain optional — falls back to a plain callable. |

## Run it

```bash
# terminal 1 — start the gated seller
uvicorn examples.gate_endpoint:app --port 8402

# terminal 2 — the buyer pays + gets the work product
python examples/pay_client.py
```

By default everything uses **mock facilitators**: ephemeral keys, no funds, no chain —
the full 402 → pay → settle → receipt loop runs locally. To go live, swap in the real
facilitators (a funded testnet key for x402, a Tempo wallet + `MPP_SECRET_KEY` for MPP);
the **same code path** then settles on-chain.

> These examples use clearly-fake placeholder addresses (e.g. `0x000...0`). Replace them
> with your own receiving wallet before going live.
