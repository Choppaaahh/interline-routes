# Go live

The demo runs on a mock facilitator (real signature verification, mocked on-chain
broadcast). To settle real value, point the same code at a testnet — the only change is
config + keys.

!!! warning "Keys"
    Generate fresh keys, keep them in `.env` (gitignored), and **never commit them**.
    Use testnet keys for testnet. Interline never holds your key — the payer signs locally.

## x402 on Base Sepolia (EVM testnet)

1. **Two testnet wallets** (buyer signs, seller receives):
   ```bash
   python3 -c "from eth_account import Account; a=Account.create(); print('addr', a.address); print('key', a.key.hex())"
   ```
   Run it twice.
2. **Fund the buyer** with Base Sepolia testnet USDC ([Circle faucet](https://faucet.circle.com)).
3. **`.env`** (copy `.env.example` → `.env`):
   ```
   APV0_NETWORK=base-sepolia
   APV0_BUYER_PRIVATE_KEY=0x...        # buyer testnet key (signs)
   APV0_SELLER_ADDRESS=0x...           # seller address (receives)
   APV0_FACILITATOR_URL=https://x402.org/facilitator
   ```
4. **Run it live:**
   ```bash
   python3 run_live.py
   ```
   The buyer auto-pays the 402 and x402.org broadcasts the EIP-3009 USDC transfer on-chain.
   You get a real tx hash → `https://sepolia.basescan.org/tx/<hash>`.

!!! note "Gasless"
    With EIP-3009 the **facilitator relays gas**, so the buyer only needs testnet **USDC**,
    not ETH.

## x402 on Solana (devnet)

Interline settles the same `exact` scheme on Solana devnet (SVM). Fund a devnet keypair with
devnet USDC and run the Solana settle path — the rail routes by CAIP-2 network family, so the
same `Paywall` handles EVM and Solana payments side by side.

## MPP on Tempo (testnet)

The MPP rail settles stablecoin payments on [Tempo](https://tempo.xyz) via the official
`pympp` SDK. Tempo ships a **public testnet** (Moderato) with a faucet for test stablecoins,
and the SDK has the testnet RPC built in — fund a Tempo wallet from the faucet, set
`MPP_SECRET_KEY`, and the same multi-rail `Paywall` settles MPP payments alongside x402.

## Mainnet

Mainnet is a config change away once you've validated on testnet. **Validate on testnet
first** — minimum-size, mock → testnet → mainnet, the same discipline at each step.
