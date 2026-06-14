# Interline

**An agent-to-agent payment router.** One agent does work, another agent pays for it —
no human keys a card. Interline is a **neutral, non-custodial router over the fragmented
agent-payment-rail stack**: *"OpenRouter for agent payments."*

One integration → pay across every rail, no caller change. Adding a rail is one adapter,
not a rewrite.

## What it does today

- **Settles USDC over [x402](https://x402.org) across two networks** — **EVM (Base Sepolia)**
  and **Solana (devnet)** — behind one `Paywall.gate()` call.
- **Speaks a second protocol: [MPP](https://mpp.dev)** (Machine Payments Protocol,
  Stripe + Tempo) — the same `Paywall` routes an x402 payment **or** an MPP payment to the
  right rail. (One integration, two protocols.)
- **Ships an MCP server** (`uvx interline`) so agents can **discover** which rails an
  endpoint accepts, **pay** it, and read **payment history** — plus a neutral
  `list_known_rails` catalog of the whole landscape.
- **Has a Google-AP2 inbound adapter** — a signed AP2 mandate (verified, constraint-checked,
  freshness-gated) settles non-custodially on the rails.

## The shape

```
                       ┌─────────────────────────────┐
   your agent ───────► │   Paywall.gate()            │
   (one integration)   │   (the product primitive)   │
                       └──────────────┬──────────────┘
                                      │ routes by the payment the buyer chose
              ┌───────────────┬───────┴───────┬───────────────┐
              ▼               ▼               ▼               ▼
         x402 (EVM)      x402 (Solana)      MPP (Tempo)    + the next rail
                                                            (one adapter)
```

Every rail implements the same small [`Rail` Protocol](rails.md)
(`payment_requirements / matches / verify / settle`). The Paywall offers all of them in
its 402 challenge, the buyer picks one, and the matching rail settles. **N rails behind one
interface** — that collapse *is* the product.

## Non-custodial by construction

Interline never holds funds. The payer signs with their own key; the server verifies and
routes. See the [trust model](trust-model.md).

## Next

- **[Quickstart](quickstart.md)** — run the demo with no wallet, no faucet, no risk, then
  gate your own endpoint in one line.
- **[Rails & the Rail Protocol](rails.md)** — the aggregator seam + how to add a rail.
- **[MCP server](mcp.md)** — let an agent discover + pay endpoints.
- **[Go live](go-live.md)** — testnet → mainnet.
