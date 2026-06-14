"""
Rail discovery — given a paid endpoint, report which payment rails it accepts.

THIS is the differentiator vs "another x402 payment MCP": one call tells an agent
*every* way it could pay an endpoint, across whatever rails the endpoint offers —
the neutral router's rail-discovery layer. Today x402 is live; as rails land (MPP, …)
they surface in the same shape with zero caller change.

The parse step (`parse_accepts`) is split from the network fetch (`discover_rails`) so
the normalization is golden-fixture testable with no network.
"""
from __future__ import annotations

import httpx

# x402/MPP payment `scheme` → human rail name. Grows as rails land.
# Unknown schemes fall through to themselves, so discovery still SURFACES a rail we don't
# yet have a pretty name for (neutrality: report what's offered, don't hide it).
SCHEME_TO_RAIL = {"exact": "x402", "mpp": "mpp"}

# Default token decimals when not derivable from the challenge (USDC = 6).
_DEFAULT_DECIMALS = 6


def parse_accepts(body: dict) -> list[dict]:
    """Pure: normalize an x402 402-challenge body's `accepts` array into a rail list."""
    rails: list[dict] = []
    for a in (body.get("accepts") or []):
        scheme = a.get("scheme", "?")
        extra = a.get("extra") or {}
        amt = a.get("amount")
        try:
            human = f"{int(amt) / 10 ** _DEFAULT_DECIMALS:.6f} {extra.get('name', 'token')}"
        except (TypeError, ValueError):
            human = str(amt)
        rails.append({
            "rail": SCHEME_TO_RAIL.get(scheme, scheme),
            "scheme": scheme,
            "network": a.get("network"),
            "asset": a.get("asset"),
            "amount_atomic": amt,
            "price": human,
            "pay_to": a.get("payTo"),
        })
    return rails


def discover_rails(url: str, timeout: float = 15.0) -> dict:
    """GET `url`; if it returns a 402 challenge, report the rails it accepts. NO payment is made."""
    try:
        r = httpx.get(url, timeout=timeout, follow_redirects=True)
    except Exception as e:  # noqa: BLE001 — surface the failure as data, don't raise into the agent
        return {"url": url, "error": f"fetch failed: {e}", "rails": []}
    if r.status_code != 402:
        return {
            "url": url, "paid": False, "status_code": r.status_code,
            "note": "no 402 payment challenge (endpoint is free, or not an x402 resource)",
            "rails": [],
        }
    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        return {"url": url, "paid": True, "error": "402 body was not JSON", "rails": []}
    rails = parse_accepts(body)
    return {
        "url": url, "paid": True, "x402_version": body.get("x402Version"),
        "rail_count": len(rails), "rails": rails,
    }


# ── known-rails capability registry ──────────────────────────────────────────
# The neutral router is legible about the WHOLE landscape — including rails it does
# NOT settle itself. `route_mode` is the honesty knob:
#   native-settle = Interline settles this directly (x402) or via an inbound adapter
#                   that lands on x402 (AP2). We move the funds.
#   handoff       = Interline recognizes the protocol + routes an agent TO it, but does
#                   NOT settle it — the protocol settles in its own world (Virtuals' own
#                   x402 escrow; OpenAI/Stripe ACP's card-only delegated payment).
KNOWN_RAILS = [
    {
        "name": "x402",
        "kind": "settlement-rail",
        "route_mode": "native-settle",
        "networks": ["eip155 (EVM)", "solana (SVM)"],
        "settle_asset": "USDC",
        "what": "HTTP-402 micropayments. Interline settles natively across EVM + Solana behind one Paywall.",
        "docs": "https://x402.org",
    },
    {
        "name": "mpp",
        "kind": "settlement-rail",
        "route_mode": "native-settle",
        "networks": ["tempo (stablecoin)"],
        "settle_asset": "stablecoin",
        "what": "Machine Payments Protocol (Stripe + Tempo, IETF draft-ryan-httpauth-payment). HTTP-402 "
                "challenge/credential/receipt — convergent with x402, RFC-7235 framed. Interline settles it as "
                "a second PROTOCOL behind the same Paywall (the cross-protocol wedge: pay an endpoint via x402 OR "
                "mpp through one integration). Phase-1 runs the mock facilitator; live Tempo settle (official "
                "pympp SDK) is gated on a funded Tempo wallet — no Stripe account required.",
        "docs": "https://mpp.dev",
    },
    {
        "name": "ap2",
        "kind": "authorization-layer",
        "route_mode": "native-settle",
        "networks": ["eip155 (EVM)", "solana (SVM)"],
        "settle_asset": "USDC",
        "what": "Google Agent Payments Protocol — signed SD-JWT mandates. Interline's AP2 inbound adapter "
                "verifies the mandate + constraints + freshness, then settles on x402. One seam for the "
                "card/agent-commerce tier (UCP / Mastercard / Amex / PayPal all delegate to AP2).",
        "docs": "https://github.com/google-agentic-commerce/AP2",
    },
    {
        "name": "virtuals-acp",
        "kind": "commerce-ecosystem",
        "route_mode": "handoff",
        "networks": ["eip155:8453 (Base)"],
        "settle_asset": "USDC",
        "what": "Virtuals Protocol Agent Commerce Protocol — a crypto-native agent marketplace running its OWN "
                "x402 rail + on-chain escrow on Base. Interline routes an agent to it (handoff); it settles in "
                "its own ecosystem, not ours. Python SDK: virtuals-acp.",
        "docs": "https://whitepaper.virtuals.io/about-virtuals/agent-commerce-protocol-acp",
    },
    {
        "name": "openai-stripe-acp",
        "kind": "commerce-layer",
        "route_mode": "handoff",
        "networks": ["card / PSP networks"],
        "settle_asset": "fiat (card)",
        "what": "OpenAI/Stripe Agentic Commerce Protocol (ChatGPT Instant Checkout). Its delegated payment is "
                "CARD-ONLY (payment_method_type=card, settled through PSP/card networks), so Interline routes an "
                "agent to it (handoff) — our crypto rail can't be the settlement target.",
        "docs": "https://github.com/agentic-commerce-protocol/agentic-commerce-protocol",
    },
]


def known_rails_catalog() -> dict:
    """The neutral router's full rail catalog — what Interline knows about + how it relates to each.

    Separates rails Interline SETTLES natively (route_mode=native-settle: x402, AP2-via-adapter) from
    protocols it ROUTES an agent to but does not settle (route_mode=handoff: Virtuals' own x402 escrow,
    OpenAI/Stripe ACP's card-only delegated payment). Legibility over the whole landscape — reporting
    every rail, including ones we don't move funds on — IS the neutral-router thesis."""
    settles = [r["name"] for r in KNOWN_RAILS if r["route_mode"] == "native-settle"]
    handoffs = [r["name"] for r in KNOWN_RAILS if r["route_mode"] == "handoff"]
    return {
        "rail_count": len(KNOWN_RAILS),
        "native_settle": settles,
        "handoff": handoffs,
        "rails": KNOWN_RAILS,
    }
