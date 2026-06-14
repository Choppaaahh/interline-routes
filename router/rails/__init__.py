"""
Rail registry — the aggregator's rail table.

`register(rail)` adds a rail to the offer set; the Paywall offers every registered
rail in its 402 `accepts` and dispatches by `match_rail(payment)`. This module is
the "all rails plug in here" surface that makes the product an *aggregator* rather
than a single-rail integration.

Rails are NOT auto-registered on import — the caller constructs a rail with its own
facilitator/config and registers it (keeps config explicit + test-isolated).
"""
from __future__ import annotations

from .base import Rail
from .x402_rail import X402Rail

__all__ = ["Rail", "X402Rail", "register", "get_rail", "all_rails", "match_rail", "clear"]

_REGISTRY: "dict[str, Rail]" = {}


def register(rail: "Rail") -> "Rail":
    """Add (or replace) a rail by its `.name`. Returns the rail for chaining."""
    _REGISTRY[rail.name] = rail
    return rail


def get_rail(name: str):
    """Look up a registered rail by name (None if absent)."""
    return _REGISTRY.get(name)


def all_rails() -> list:
    """Every registered rail, in registration order."""
    return list(_REGISTRY.values())


def match_rail(payment: dict):
    """Pick the first registered rail that handles this buyer payment (None if none)."""
    for r in _REGISTRY.values():
        try:
            if r.matches(payment):
                return r
        except Exception:  # noqa: BLE001 — a misbehaving rail must not break routing
            continue
    return None


def clear() -> None:
    """Reset the registry (test helper)."""
    _REGISTRY.clear()
