#!/usr/bin/env node
/**
 * v2 wallet-worker — non-custodial scoped agent wallet (ERC-4337 Kernel + session keys, Base).
 *
 * The TS half of the polyglot v2 stack. The Python x402 orchestrator subprocess-calls
 * this CLI; JSON in/out. Keys come from argv/env and are NEVER logged.
 *
 * Trust model (on-chain-provable non-custodial):
 *   - OWNER key (operator-held)  → sudo validator. Can revoke. We never hold it.
 *   - SESSION key (agent-held)   → scoped validator (CallPolicy + RateLimit + expiry).
 *   - ROUTER                     → holds NEITHER. Cannot move funds. Verifiable on-chain.
 *
 * Commands (JSON to stdout):
 *   address  --owner-key 0x..                          → {smartAccountAddress}  (counterfactual, no deploy)
 *   grant    --owner-key 0x.. --usdc 0x.. --pay-to 0x.. --max-atomic N --rate-count K --rate-secs S --expires-secs E
 *                                                       → {serialized, sessionAddress, smartAccountAddress}
 *   pay      --serialized <b64> --usdc 0x.. --pay-to 0x.. --amount-atomic N
 *                                                       → {userOpHash, txHash}   (scoped UserOp; limit enforced on-chain)
 *
 * Needs: APV0_BUNDLER_RPC (ZeroDev/Pimlico project bundler) + APV0_PAYMASTER_RPC for live use.
 * Without them: `address`/`grant` (counterfactual + serialize) still work offline-ish; `pay` needs the bundler.
 */
import {
  createPublicClient,
  http,
  encodeFunctionData,
  erc20Abi,
  type Address,
  type Hex,
} from "viem";
import { baseSepolia } from "viem/chains";
import { privateKeyToAccount, generatePrivateKey } from "viem/accounts";
import {
  createKernelAccount,
  createKernelAccountClient,
  createZeroDevPaymasterClient,
} from "@zerodev/sdk";
import { getEntryPoint, KERNEL_V3_1 } from "@zerodev/sdk/constants";
import { signerToEcdsaValidator } from "@zerodev/ecdsa-validator";
import {
  toPermissionValidator,
  serializePermissionAccount,
  deserializePermissionAccount,
} from "@zerodev/permissions";
import { toECDSASigner } from "@zerodev/permissions/signers";
import {
  toCallPolicy,
  toRateLimitPolicy,
  toTimestampPolicy,
  CallPolicyVersion,
  ParamCondition,
} from "@zerodev/permissions/policies";

const entryPoint = getEntryPoint("0.7");
const kernelVersion = KERNEL_V3_1;
const chain = baseSepolia;
const RPC = process.env.APV0_BASE_SEPOLIA_RPC ?? "https://sepolia.base.org";
const BUNDLER = process.env.APV0_BUNDLER_RPC ?? "";
const PAYMASTER = process.env.APV0_PAYMASTER_RPC ?? "";

const publicClient = createPublicClient({ chain, transport: http(RPC) });

function argMap(argv: string[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (let i = 0; i < argv.length; i += 2) {
    const k = argv[i];
    if (k?.startsWith("--")) out[k.slice(2)] = argv[i + 1] ?? "";
  }
  return out;
}
function out(obj: unknown): void {
  process.stdout.write(JSON.stringify(obj) + "\n");
}
function fail(msg: string): never {
  process.stdout.write(JSON.stringify({ error: msg }) + "\n");
  process.exit(1);
}

/** Owner-key Kernel account (sudo validator only) — the base smart account. */
async function ownerKernelAccount(ownerKey: Hex) {
  const owner = privateKeyToAccount(ownerKey);
  const sudoValidator = await signerToEcdsaValidator(publicClient, {
    signer: owner,
    entryPoint,
    kernelVersion,
  });
  return createKernelAccount(publicClient, {
    entryPoint,
    kernelVersion,
    plugins: { sudo: sudoValidator },
  });
}

/** A CallPolicy that allows ONLY USDC.transfer to `payTo` with amount <= maxAtomic. */
function scopedCallPolicy(usdc: Address, payTo: Address, maxAtomic: bigint) {
  return toCallPolicy({
    policyVersion: CallPolicyVersion.V0_0_4,
    permissions: [
      {
        target: usdc,
        valueLimit: 0n,
        abi: erc20Abi,
        functionName: "transfer",
        args: [
          { condition: ParamCondition.EQUAL, value: payTo },
          { condition: ParamCondition.LESS_THAN_OR_EQUAL, value: maxAtomic },
        ],
      },
    ],
  });
}

async function cmdAddress(a: Record<string, string>) {
  const acct = await ownerKernelAccount(a["owner-key"] as Hex);
  out({ smartAccountAddress: acct.address });
}

async function cmdGrant(a: Record<string, string>) {
  const ownerKey = a["owner-key"] as Hex;
  const usdc = a["usdc"] as Address;
  const payTo = a["pay-to"] as Address;
  const maxAtomic = BigInt(a["max-atomic"]);
  const rateCount = Number(a["rate-count"] ?? "1");
  const rateSecs = Number(a["rate-secs"] ?? "86400"); // daily
  const expiresSecs = Number(a["expires-secs"] ?? "604800"); // 7d

  const owner = privateKeyToAccount(ownerKey);
  const sudoValidator = await signerToEcdsaValidator(publicClient, {
    signer: owner,
    entryPoint,
    kernelVersion,
  });

  // fresh session key — the agent will hold this; operator never sees it after grant
  const sessionPrivateKey = generatePrivateKey();
  const sessionAccount = privateKeyToAccount(sessionPrivateKey);
  const sessionSigner = await toECDSASigner({ signer: sessionAccount });

  const permissionValidator = await toPermissionValidator(publicClient, {
    entryPoint,
    kernelVersion,
    signer: sessionSigner,
    policies: [
      scopedCallPolicy(usdc, payTo, maxAtomic),
      toRateLimitPolicy({ count: rateCount, interval: rateSecs }),
      toTimestampPolicy({ validUntil: Math.floor(Date.now() / 1000) + expiresSecs }),
    ],
  });

  const account = await createKernelAccount(publicClient, {
    entryPoint,
    kernelVersion,
    plugins: { sudo: sudoValidator, regular: permissionValidator },
  });

  const serialized = await serializePermissionAccount(account, sessionPrivateKey);
  out({ serialized, sessionAddress: sessionAccount.address, smartAccountAddress: account.address });
}

async function cmdPay(a: Record<string, string>) {
  if (!BUNDLER) fail("APV0_BUNDLER_RPC required for pay (ZeroDev/Pimlico project bundler)");
  const usdc = a["usdc"] as Address;
  const payTo = a["pay-to"] as Address;
  const amountAtomic = BigInt(a["amount-atomic"]);

  const account = await deserializePermissionAccount(
    publicClient,
    entryPoint,
    kernelVersion,
    a["serialized"],
  );

  const paymaster = PAYMASTER
    ? createZeroDevPaymasterClient({ chain, transport: http(PAYMASTER) })
    : undefined;

  const kernelClient = createKernelAccountClient({
    account,
    chain,
    bundlerTransport: http(BUNDLER),
    ...(paymaster
      ? { paymaster: { getPaymasterData: (uo) => paymaster.sponsorUserOperation({ userOperation: uo }) } }
      : {}),
  });

  const callData = await account.encodeCalls([
    {
      to: usdc,
      value: 0n,
      data: encodeFunctionData({ abi: erc20Abi, functionName: "transfer", args: [payTo, amountAtomic] }),
    },
  ]);

  const userOpHash = await kernelClient.sendUserOperation({ callData });
  const receipt = await kernelClient.waitForUserOperationReceipt({ hash: userOpHash });
  out({ userOpHash, txHash: receipt.receipt.transactionHash });
}

async function main() {
  const [cmd, ...rest] = process.argv.slice(2);
  const a = argMap(rest);
  try {
    if (cmd === "address") await cmdAddress(a);
    else if (cmd === "grant") await cmdGrant(a);
    else if (cmd === "pay") await cmdPay(a);
    else fail(`unknown command: ${cmd ?? "(none)"} — use address|grant|pay`);
  } catch (e) {
    fail(`${(e as Error).message}`);
  }
}

main();
