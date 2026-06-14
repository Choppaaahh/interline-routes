# Interline (MCP server)

**Rail-discovery + future-proof payments for AI agents — as an MCP server.**

Two things your agent gets, **today**:
1. **Discover** which payment rail(s) any paid endpoint accepts — *before* paying. (The discovery layer single-rail clients don't have.)
2. **Pay** through it — non-custodial, your own key, capped.

And the part that pays off across **every rail**: **integrate once.** Three rails are live today — [x402](https://x402.org) (HTTP 402 + USDC) on EVM (Base Sepolia) and on Solana (devnet), plus **MPP** on Tempo (Moderato testnet) — all behind the *same tool*. Real on-chain agent-to-agent settles are confirmed on all three. Every future rail drops in with **zero code change**. **Never re-integrate agent payments again** — when the next rail ships, your agent already speaks it.

This is **not "another x402 MCP."** Single-rail clients make you wire up one rail (and re-wire for the next). This is the **discovery + routing layer above them** — same shape OpenRouter has for models. Non-custodial by design: payments use **your own wallet key**; this server never holds, sees, or routes funds through itself.

## Tools

| Tool | What it does |
|---|---|
| `discover_payment_rails(url)` | Probe a paid endpoint; report **which rails + prices** it accepts. **No payment.** The discovery layer. |
| `pay_for_resource(url, task, max_price_usdc)` | Pay + fetch through the accepted rail; return content **+ a settlement receipt**. Never exceeds `max_price_usdc`. |
| `payment_history(limit)` | The **unified cross-rail receipt ledger** — every settlement, every rail, one view. |

## Install

```bash
pip install interline        # (or: uvx interline)
```

### Add to Claude Desktop / Cursor / any MCP client

```jsonc
{
  "mcpServers": {
    "interline": {
      "command": "uvx",
      "args": ["interline"],
      "env": {
        "APV0_BUYER_PRIVATE_KEY": "0x...",   // YOUR wallet key — stays in your env, never leaves your machine
        "APV0_NETWORK": "base-sepolia"        // or "base" for mainnet
      }
    }
  }
}
```

Local dev (from a clone):

```jsonc
{ "mcpServers": { "interline": {
    "command": "python", "args": ["-m", "mcp_router.server"],
    "cwd": "/path/to/interline-routes",
    "env": { "APV0_BUYER_PRIVATE_KEY": "0x...", "APV0_NETWORK": "base-sepolia" }
}}}
```

`discover_payment_rails` needs no key (it only reads the 402 challenge). `pay_for_resource` needs `APV0_BUYER_PRIVATE_KEY` (your funded wallet).

## Non-custodial guarantee

- The router **never holds funds.** `discover` only reads a public 402 challenge; `pay` signs with **your** key, locally, bounded by `max_price_usdc`.
- The fee model (when one exists) is a **software/routing fee billed to you, the developer — never a cut of the funds flow.** That's the line between software (this) and money transmission (not this).

## Status

Three rails live — x402 USDC on EVM (Base Sepolia) and on Solana (devnet), plus MPP on Tempo (Moderato testnet). Real on-chain agent-to-agent settles confirmed on all three. Neutral by construction: `discover_payment_rails` surfaces *any* rail an endpoint offers, and each additional rail is a drop-in adapter with no caller change. Built on the [interline-routes](https://github.com/Choppaaahh/interline-routes) rail-agnostic core.
