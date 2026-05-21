---
name: Ocl
description: Use when building Web3 applications that need smart accounts, gasless transactions, passkey authentication, session keys, transaction batching, or chain abstraction. Reach for this skill when implementing account abstraction (ERC-4337 or EIP-7702), configuring gas sponsorship, setting up authentication providers, or managing bundler and paymaster infrastructure.
metadata:
    mintlify-proj: ocl
    version: "1.0"
---

# ZeroDev Skill

## Product summary

ZeroDev is a smart account SDK that abstracts away gas fees, seed phrases, and complex transaction flows for Web3 applications. It provides the Kernel smart account (ERC-4337 and EIP-7702 compatible), bundler and paymaster infrastructure, and integrations with auth providers like Privy, Dynamic, and Magic. Key files and commands: install `@zerodev/sdk` and `@zerodev/ecdsa-validator`; get your RPC URL from the [ZeroDev dashboard](https://dashboard.zerodev.app); use `createKernelAccount` and `createKernelAccountClient` to set up accounts; configure gas policies via the dashboard or Admin API at `https://api.zerodev.app`. The primary docs site is https://ocl-de73b1a4.mintlify.app.

## When to use

Use ZeroDev when you need to:
- **Create smart accounts** — Set up Kernel accounts with ECDSA, passkey, or multisig validators for users who don't have ETH.
- **Sponsor gas** — Cover transaction fees for users so they never need to hold native tokens.
- **Batch transactions** — Combine multiple calls (e.g., approve + transfer) into a single atomic UserOp.
- **Authenticate users** — Integrate passkeys, social login (via Privy/Dynamic), or other auth providers as account signers.
- **Automate transactions** — Use session keys to delegate specific transaction permissions to agents or third parties.
- **Configure infrastructure** — Set up bundlers, paymasters, and gas policies to control sponsorship rules and spending limits.
- **Support multiple chains** — Use a single ZeroDev project across 50+ networks with chain-specific RPC URLs.

Trigger conditions: when a user needs to transact without holding ETH, when you want to improve UX by removing seed phrase management, when you need fine-grained access control, or when you're building on ERC-4337 or EIP-7702.

## Quick reference

### Installation

```bash
npm i @zerodev/sdk @zerodev/ecdsa-validator
```

For passkeys: `npm i @zerodev/passkey-validator`  
For permissions/session keys: `npm i @zerodev/permissions`

### Core setup pattern

```typescript
import { createKernelAccount, createKernelAccountClient, createZeroDevPaymasterClient } from "@zerodev/sdk"
import { signerToEcdsaValidator } from "@zerodev/ecdsa-validator"
import { KERNEL_V3_1, getEntryPoint } from "@zerodev/sdk/constants"
import { createPublicClient, http } from "viem"

const entryPoint = getEntryPoint("0.7")  // Use 0.7 for new projects
const publicClient = createPublicClient({ transport: http(RPC_URL), chain })
const ecdsaValidator = await signerToEcdsaValidator(publicClient, { signer, entryPoint, kernelVersion: KERNEL_V3_1 })
const account = await createKernelAccount(publicClient, { plugins: { sudo: ecdsaValidator }, entryPoint, kernelVersion: KERNEL_V3_1 })
const kernelClient = createKernelAccountClient({ account, chain, bundlerTransport: http(ZERODEV_RPC), client: publicClient })
```

### RPC URL format

```
https://rpc.zerodev.app/api/v3/{PROJECT_ID}/chain/{CHAIN_ID}
```

Append `?provider=ULTRA_RELAY` for optimized sponsored transactions (30% less gas, 20% lower latency).

### Common operations

| Task | Method | Notes |
|------|--------|-------|
| Send single transaction | `kernelClient.sendTransaction({ to, value, data })` | Returns tx hash; already mined |
| Batch transactions | `kernelClient.sendTransaction({ calls: [...] })` | Atomic; reverts if any call fails |
| Send raw UserOp | `kernelClient.sendUserOperation({ callData })` | Fine-grained control; use `encodeCalls` to build callData |
| Sponsor gas | Attach `paymaster` to client config | Requires gas policy on dashboard |
| Wait for UserOp | `kernelClient.waitForUserOperationReceipt({ hash })` | Polls until on-chain |
| Get account address | `account.address` | Computed counterfactually; free until first UserOp |

### EntryPoint and Kernel versions

| Scenario | EntryPoint | Kernel version | SDK version |
|----------|-----------|-----------------|-------------|
| New project | 0.7 | v3.1 | v5.3+ |
| Existing production (SDK v4 or lower) | 0.6 | v2.4 | v4 or lower |
| Existing production (SDK v5) | 0.7 | v3.0 | v5 |

Always specify `kernelVersion` explicitly to avoid silent compatibility issues on SDK upgrades.

### Gas policy types

| Type | Scope | Use case |
|------|-------|----------|
| Project | All transactions in project | Cap total spend per hour/day/month |
| Contract | Specific contract address | Sponsor only calls to your contract |
| Wallet | Specific sender address | Sponsor only transactions from a user |
| Custom | Webhook-driven | Apply arbitrary sponsorship logic |

### Rate limit types

- **Amount** — Total gas (in native token) per time window
- **Request** — Number of UserOps per time window
- **Gas price** — Skip sponsorship if network gas exceeds threshold
- **Amount per transaction** — Max gas per single UserOp

## Decision guidance

### When to use Viem API vs UserOp API

| Scenario | Use Viem API | Use UserOp API |
|----------|-------------|-----------------|
| Standard transaction or batch | ✓ | — |
| Contract interaction via Viem | ✓ | — |
| Fine-grained gas control | — | ✓ |
| Override nonce or gas limits | — | ✓ |
| Separate signing from sending | — | ✓ |
| Custom gas estimation | — | ✓ |

### When to use passkeys vs ECDSA vs multisig

| Signer type | Pros | Cons | Use when |
|-------------|------|------|----------|
| ECDSA | Simple, cheap, standard | Requires private key management | Building with existing auth providers or EOAs |
| Passkeys | Hardware-backed, no seed phrases, synced by platform | Expensive on chains without ERC-7212 (300–400k gas) | Users want device-native authentication |
| Multisig | Fine-grained access control, M-of-N schemes | More complex setup | Requiring approval from multiple signers |
| Passkeys + session keys | Passkey security + cheap ongoing transactions | Requires session key setup | Combining passkey login with frequent transactions |

### When to use UltraRelay vs standard bundler

| Aspect | UltraRelay | Standard bundler |
|--------|-----------|------------------|
| Gas efficiency | 30% less | Baseline |
| Latency | 20% lower | Baseline |
| Paymaster | Built-in | Separate |
| Setup | Append `?provider=ULTRA_RELAY` | Default |
| Supported chains | Base, Arbitrum, Optimism, HyperEVM, Polynomial, Abstract, ZkSync, testnets | All chains |

Use UltraRelay for sponsored transactions on supported networks; request deployment for other chains.

## Workflow

### 1. Set up a ZeroDev project

1. Sign up at [dashboard.zerodev.app](https://dashboard.zerodev.app)
2. Create a new project
3. Copy the RPC URL for each chain you target
4. (If sponsoring gas) Configure at least one gas policy under **Gas Policies**

### 2. Create a smart account

1. Install `@zerodev/sdk` and validator package (`@zerodev/ecdsa-validator`, `@zerodev/passkey-validator`, etc.)
2. Create a public client pointing to your RPC
3. Create a signer (from auth provider, private key, passkey, etc.)
4. Wrap signer in validator (`signerToEcdsaValidator`, `toPasskeyValidator`, etc.)
5. Call `createKernelAccount` with the validator
6. Create a Kernel account client with `createKernelAccountClient`

### 3. Send transactions

1. Use `kernelClient.sendTransaction()` for simple calls or batches
2. Use `kernelClient.sendUserOperation()` for fine-grained control
3. Call `waitForUserOperationReceipt()` if you need to wait for on-chain confirmation
4. Handle sponsorship failures gracefully (catch errors, fall back to user-paid gas)

### 4. Configure advanced features

1. **Session keys** — Create short-lived ECDSA keys with permission policies; delegate to agents
2. **Passkeys** — Use `toWebAuthnKey` and `toPasskeyValidator` for device-native authentication
3. **Multisig** — Use `toWeightedECDSASigner` for M-of-N approval schemes
4. **Chain abstraction** — Use cross-chain swap integrations to let users spend tokens on any chain
5. **Custom gas policies** — Set up webhooks to apply dynamic sponsorship logic

### 5. Verify and deploy

1. Test on testnet (Base Sepolia, Holesky, etc.) with your RPC
2. Verify gas policies are correctly configured and rate limits are appropriate
3. Check that your auth provider integration works end-to-end
4. Monitor Admin API calls if managing policies programmatically
5. Deploy to production with your project RPC

## Common gotchas

- **No gas policy = no sponsorship** — Sponsorship is disabled by default. You must create at least one gas policy on the dashboard or via Admin API before any gas is sponsored.
- **RPC URL mismatch** — Ensure your `bundlerTransport` RPC matches the chain you're targeting. Each chain has its own RPC URL.
- **EntryPoint version mismatch** — Always specify `kernelVersion` explicitly when creating accounts. Mismatches cause silent failures on SDK upgrades.
- **Counterfactual addresses** — Account addresses are computed before deployment. If you change the signer, the address changes. Store the signer securely.
- **Passkey server required** — Passkeys require a server to store public authentication data. Use ZeroDev's hosted server or run your own.
- **Paymaster data not populated** — If you forget to attach the `paymaster` config to `createKernelAccountClient`, gas is not sponsored even if a policy exists.
- **Batch atomicity** — By default, batches revert if any call fails. Use `EXEC_TYPE.TRY_EXEC` only if your app can tolerate partial failures.
- **API key exposure** — Never expose your ZeroDev API key in client-side code or public repos. Use it only in backend/CI workflows.
- **Session key expiry** — Session keys have timestamps. Verify they haven't expired before delegating transactions.
- **Gas estimation failures** — If gas estimation fails, the UserOp is not sent. Check that your callData is valid and the target contract exists.

## Verification checklist

Before submitting work with ZeroDev:

- [ ] RPC URL is correct and matches the target chain
- [ ] EntryPoint version (0.6 or 0.7) matches your Kernel version (v2.4 or v3.x)
- [ ] Gas policy is configured on the dashboard if sponsoring gas
- [ ] Signer is securely stored (not in client-side code)
- [ ] Account address is deterministic and matches expected value
- [ ] Paymaster is attached to client config if sponsoring
- [ ] Test transaction succeeds on testnet before production
- [ ] Error handling catches sponsorship failures gracefully
- [ ] API key (if using Admin API) is stored in environment variables
- [ ] Session keys (if used) have valid timestamps
- [ ] Batch transactions are tested for atomicity
- [ ] Auth provider integration works end-to-end

## Resources

- **Comprehensive navigation** — See [llms.txt](https://ocl-de73b1a4.mintlify.app/llms.txt) for a complete page-by-page listing of all documentation.
- **[Quickstart: Send Your First Gasless Transaction](https://ocl-de73b1a4.mintlify.app/sdk/quickstart)** — Five-minute walkthrough to create an account and send a sponsored UserOp.
- **[Core API: Create an Account](https://ocl-de73b1a4.mintlify.app/sdk/core-api/create-account)** — Detailed reference for account creation, EntryPoint selection, and Kernel versions.
- **[Gas Policies Guide](https://ocl-de73b1a4.mintlify.app/meta-infra/gas-policies)** — How to configure sponsorship rules and rate limits.

---

> For additional documentation and navigation, see: https://ocl-de73b1a4.mintlify.app/llms.txt