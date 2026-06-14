# Rails & the Rail Protocol

Interline is an **aggregator**. Each payment rail plugs in behind a uniform interface, the
`Paywall` offers every registered rail, and the buyer's chosen payment routes to the rail
that handles it. Adding a rail is one adapter — no `Paywall` change.

## The `Rail` Protocol

A rail bundles ONE payment rail's full lifecycle (`router/rails/base.py`):

```python
class Rail(Protocol):
    name: str  # registry key, e.g. "x402"

    def payment_requirements(self, resource: str) -> dict:
        """This rail's 402-challenge offer (one entry in the `accepts` array)."""

    def matches(self, payment: dict) -> bool:
        """Does this rail handle the buyer's chosen payment? (by scheme / network)"""

    def verify(self, payment: dict, requirements: dict):
        """Validate without moving funds. Returns an object with .is_valid + .reason."""

    def settle(self, payment: dict, requirements: dict):
        """Move the funds. Returns .success + .tx_hash + .network + .reason."""
```

The `Paywall` offers every rail's `payment_requirements()` in its 402 `accepts`, the buyer
picks one, and the Paywall dispatches `verify` / `settle` to the rail that `matches`.

## Rails today

| rail | networks | settles | notes |
|---|---|---|---|
| **x402** | EVM (`eip155`) + Solana (`solana`) | USDC | HTTP-402 micropayments; one rail per network family, routed by CAIP-2 |
| **MPP** | Tempo (stablecoin) | stablecoin | Machine Payments Protocol (Stripe + Tempo) — a second *protocol* behind the same Paywall |
| **AP2** (inbound adapter) | settles on x402 | USDC | a signed Google-AP2 mandate → verified + constraint-checked + freshness-gated → settles on a rail |

x402 EVM and x402 Solana are the same `exact` scheme on different network families, so
`matches()` routes by **scheme AND family** — one Paywall offers x402-on-Base *and*
x402-on-Solana and sends each buyer's payment to the right rail.

### The cross-protocol wedge

MPP is convergent with x402 (both are HTTP-402 challenge → credential → receipt) but a
distinct protocol. Interline settles **both behind one `Paywall`**:

```python
from router.rails import X402Rail, MppRail
paywall = Paywall(rails=[X402Rail(fac, x402_reqs_fn), MppRail(mpp_fac, mpp_reqs_fn)])
# an x402 payment routes to the x402 rail; an mpp payment routes to the mpp rail.
```

That's "pay this endpoint via x402 **or** MPP through one integration" — the durable wedge:
**neutral × non-custodial × cross-protocol.**

## Add a rail

1. Implement the four `Rail` methods (wrap your rail's facilitator + a requirements builder).
2. Guard untrusted input in `matches()` — the buyer's `payment` dict is untrusted JSON; a
   non-string / absent field must be a clean non-match, never a crash.
3. Register it in the `Paywall`'s `rails=[...]` list. No gate change.
4. Add golden self-tests (`selftest_v3_rails.py` / `selftest_mpp_rail.py` are the templates).

The same N-rails-behind-one-interface collapse the x402 and MPP rails already prove —
extended to whatever rail you add next.
