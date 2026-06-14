# wallet-worker — v2 non-custodial scoped agent wallet (ERC-4337 + session keys)

The **TS half** of the polyglot v2 stack (ERC-4337 session-key tooling is TS-first; no
mature pure-Python lib in 2026). Python (`router/wallet.py`) subprocess-calls this CLI.

**Trust model (on-chain-provable non-custodial):** operator holds the OWNER key
(revoke-only), the agent holds the SESSION key (scoped by CallPolicy + rate-limit +
expiry), the router holds **neither**. "We cannot move your funds" is verifiable on-chain.

## Setup
```bash
cd wallet && npm install --legacy-peer-deps
```

## Commands (JSON out; keys via args, never logged)
```bash
# counterfactual smart-account address (reads chain, no deploy)
tsx src/walletWorker.ts address --owner-key 0x..

# operator grants a SCOPED session key (only USDC.transfer to payTo, ≤max, ≤rate, expires)
tsx src/walletWorker.ts grant --owner-key 0x.. --usdc 0x.. --pay-to 0x.. \
    --max-atomic 1000 --rate-count 10 --rate-secs 86400 --expires-secs 604800

# agent pays via the scoped session key (needs APV0_BUNDLER_RPC)
tsx src/walletWorker.ts pay --serialized <b64> --usdc 0x.. --pay-to 0x.. --amount-atomic 1000
```

## Env (live `pay` only)
- `APV0_BUNDLER_RPC` — ZeroDev/Pimlico project bundler URL (Base Sepolia)
- `APV0_PAYMASTER_RPC` — paymaster URL for gas sponsorship (agent holds no ETH)
- `APV0_BASE_SEPOLIA_RPC` — optional RPC override (default: sepolia.base.org)

## Status
- `address` + `grant` = **executes** against Base Sepolia (smart-account computed,
  scoped session key created + serialized). typecheck PASS.
- `pay` = needs a bundler/paymaster API key (operator-provided, like v0's faucet wallets).
- Stack: ZeroDev Kernel v3.1 + `@zerodev/permissions` (CallPolicy + RateLimit + Timestamp)
  + ecdsa-validator + viem on Base Sepolia.
