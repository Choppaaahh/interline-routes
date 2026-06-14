"""
Interline — config + constants.

Scope (v0 — deliberately narrow):
  - USDC-only, single rail (x402 exact-evm scheme)
  - NO cross-currency conversion (that's the money-transmitter trigger — later, reg-gated)
  - NO ERC-4337 session-key orchestration yet
  - Base Sepolia TESTNET first; mock facilitator before that
  - keys ONLY from env, NEVER committed

This file holds the network/asset facts. Everything else reads from here so
swapping testnet->mainnet or mock->real-facilitator is a one-line change.
"""
from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv() -> None:
    """Minimal .env loader (override=False — real env + already-set vars win).

    No python-dotenv dependency. Loads KEY=VALUE lines from project-root .env
    into os.environ ONLY for keys not already set, so run_demo.py's explicit
    mock-mode setup is never clobbered, while run_live.py picks up real keys.
    """
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_dotenv()

# --- network selection -------------------------------------------------------
# "mock"        : local in-process facilitator, no chain, no keys, no risk (default)
# "base-sepolia": real testnet, real x402 facilitator, faucet USDC
# "base"        : mainnet (NOT for v0 — guarded; requires APV0_ALLOW_MAINNET=1)
NETWORK_MODE = os.environ.get("APV0_NETWORK", "mock")

# Mainnet guard: the "base" path moves REAL USDC. Refuse to arm it unless the deployer
# explicitly acknowledges via APV0_ALLOW_MAINNET=1. (This is an enforced check, not just a
# comment — a pre-launch review flagged that a docs-only "guarded" claim would be untrue.)
if NETWORK_MODE == "base" and os.environ.get("APV0_ALLOW_MAINNET") != "1":
    raise RuntimeError(
        "APV0_NETWORK=base is MAINNET (real USDC). Set APV0_ALLOW_MAINNET=1 to acknowledge, "
        "or use 'mock' / 'base-sepolia' for testnet."
    )

# Base Sepolia testnet USDC (Circle's official testnet USDC on Base Sepolia).
# 6 decimals. Mainnet Base USDC is 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913.
USDC = {
    "base-sepolia": {
        "address": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "chain_id": 84532,
        "decimals": 6,
        "name": "USDC",
    },
    "base": {
        "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "chain_id": 8453,
        "decimals": 6,
        "name": "USDC",
    },
}

# x402 V2 uses CAIP-2 network ids (eip155:<chainId>), NOT human names.
# Base Sepolia = eip155:84532, Base mainnet = eip155:8453.
_CHAIN_ID = {"mock": 84532, "base-sepolia": 84532, "base": 8453}[NETWORK_MODE]
X402_NETWORK = f"eip155:{_CHAIN_ID}"

# Facilitator endpoint. Mock = our in-process one; real = PayAI / x402.org.
# DEFAULT_FACILITATOR_URL from the SDK is https://x402.org/facilitator.
FACILITATOR_URL = os.environ.get(
    "APV0_FACILITATOR_URL",
    "http://127.0.0.1:8402" if NETWORK_MODE == "mock" else "https://x402.org/facilitator",
)

# --- wallets (env only — NEVER hardcode/commit a key) ------------------------
# Buyer signs EIP-3009 authorizations. Seller just receives.
BUYER_PRIVATE_KEY = os.environ.get("APV0_BUYER_PRIVATE_KEY", "")   # 0x... testnet key
SELLER_PAY_TO = os.environ.get("APV0_SELLER_ADDRESS", "")          # 0x... receives USDC

# --- the work (v1 dogfood) ---------------------------------------------------
# The unit of work the seller performs once paid. If an OpenRouter key is set,
# the seller does a REAL model call (the authentic dogfood: pay an agent → it
# does real inference for you). Otherwise it returns a local stub. The product's
# `_do_the_work` is the integration point — a deployer plugs in THEIR own
# model / compute / service here.
OPENROUTER_KEY = os.environ.get("APV0_OPENROUTER_KEY", "")
WORK_MODEL = os.environ.get("APV0_WORK_MODEL", "google/gemini-2.5-flash-lite")

# --- pricing -----------------------------------------------------------------
# v0 sells one unit of "agent work" (e.g. a short summary) for a sub-cent price.
PRICE_USDC = float(os.environ.get("APV0_PRICE_USDC", "0.001"))     # $0.001 per call

def usdc_cfg() -> dict:
    net = "base-sepolia" if NETWORK_MODE in ("mock", "base-sepolia") else "base"
    return USDC[net]

def price_atomic() -> int:
    """Price in USDC atomic units (6 decimals)."""
    return int(round(PRICE_USDC * 10 ** usdc_cfg()["decimals"]))

def is_live() -> bool:
    return NETWORK_MODE != "mock"
