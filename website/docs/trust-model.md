# Trust model

Interline is **non-custodial by construction**. It routes payments; it never holds them.

## What Interline does and doesn't touch

| | |
|---|---|
| **Never holds funds** | The payer signs an authorization with their **own key**, locally. Interline (and its facilitator) verify + route — they never take custody of the money. |
| **Never holds your key** | Your private key stays in your environment. The server receives a *signed authorization*, not a key. |
| **Verifies, doesn't trust** | Even in mock mode the facilitator does **real signer-recovery** (EIP-712 / the rail's native crypto) and matches it to the authorization. A payment that doesn't verify doesn't settle. |
| **Records every settlement** | Each settle appends to a local receipt-ledger (tx hash, payer, payTo, amount, network, rail) — an audit trail you own. |
| **Client-side spend caps** | The buyer/MCP client carries a `max_price` limit — it won't pay above what you set. |

## The authorization layers (AP2)

The Google-AP2 inbound adapter accepts a **signed mandate** rather than a raw payment. Before
anything settles, the adapter:

1. **Verifies** the mandate signature,
2. **Checks the constraints** carried in the mandate (the payer's stated limits),
3. **Gates on freshness** (rejects stale mandates),

and only then routes to a rail. The funds still move non-custodially on the underlying rail —
AP2 is the *authorization* layer in front, not a custodian.

## Why this matters

Custody is the line that turns a router into a money-transmitter. Interline stays on the
right side of it: it's **software that routes signed payments**, not a holder of funds. That's
the whole posture — neutral, non-custodial, and auditable.
