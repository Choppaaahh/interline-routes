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

# x402 payment `scheme` → human rail name. Grows as rails land (e.g. an MPP scheme → "mpp").
# Unknown schemes fall through to themselves, so discovery still SURFACES a rail we don't
# yet have a pretty name for (neutrality: report what's offered, don't hide it).
SCHEME_TO_RAIL = {"exact": "x402"}

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
