# MCP server

Interline ships a [Model Context Protocol](https://modelcontextprotocol.io) server so an
agent can **discover** which rails an endpoint accepts, **pay** it, and read its **payment
history** — and see a neutral catalog of the whole rail landscape.

## Run it

```bash
uvx interline
```

Then point your MCP client (Claude, an agent runtime, etc.) at the server.

## Tools

### `discover_payment_rails(url)`

GET the URL; if it returns a 402 challenge, report every rail it accepts (scheme, network,
asset, price, payTo) — **no payment is made.** This is the differentiator vs "another x402
payment tool": one call tells an agent *every* way it could pay an endpoint.

### `pay_for_resource(url, task="", max_price_usdc=0.01)`

Pay a 402-gated endpoint and return its work product + the settlement receipt. The
`max_price_usdc` is a client-side spend cap — the agent will not pay above it.

### `list_known_rails()`

The neutral router's full rail catalog — what Interline knows about **and how it relates to
each**. It separates rails Interline **settles natively** (`native-settle`: x402, MPP,
AP2-via-adapter) from protocols it **routes an agent to but does not settle**
(`handoff`). Reporting the whole landscape — including rails it doesn't move funds on — is
the neutral-router posture.

### `payment_history(limit=20)`

The recent settlement receipts from the local ledger (an audit trail of what this agent has
paid).

## Why discovery matters

An agent that finds a paid endpoint shouldn't have to guess the protocol. `discover_payment_rails`
turns "this returned a 402" into "here are the N ways to pay it," and `pay_for_resource`
acts on it — across whatever rails the endpoint offers, with one integration.
