---
name: Privy
description: Use when building authentication systems, creating embedded wallets, managing wallet controls and policies, signing transactions, or integrating wallet infrastructure into applications. Agents should reach for this skill when implementing user onboarding, wallet creation, transaction signing, policy enforcement, or wallet management across Ethereum, Solana, and 50+ blockchains.
metadata:
    mintlify-proj: privy
    version: "1.0"
---

# Privy Skill Reference

## Product summary

Privy is a wallet and authentication infrastructure platform that enables developers to embed wallets and user authentication directly into applications. It provides three interconnected layers: **authentication** (user login via email, social, passkeys, wallets), **wallets** (embedded wallets managed by Privy or external wallets users bring), and **controls** (policies and authorization keys that define who can act and what they can do).

**Key files and config:**
- App ID and App Secret: obtained from Privy Dashboard (Configuration > App settings > Basics)
- Client-side SDKs: React (`@privy-io/react-auth`), React Native (`@privy-io/expo`), Swift, Android, Flutter, Unity
- Server-side SDKs: Node.js (`@privy-io/node`), Python, Java, Go, Rust, Ruby
- REST API: `https://api.privy.io/v1/` with Basic Auth (app ID as username, app secret as password)
- Webhooks: configured in Dashboard (Configuration > Webhooks)

**Primary docs:** https://docs.privy.io

## When to use

Reach for this skill when:
- **Building authentication flows**: implementing email/SMS/social/wallet login, MFA, passkeys
- **Creating wallets**: provisioning embedded wallets for users or servers, managing wallet ownership and signers
- **Controlling wallet access**: setting up policies, authorization keys, key quorums, multi-sig approvals
- **Signing transactions**: executing RPC calls (eth_sendTransaction, signTransaction, etc.) on Ethereum, Solana, or other chains
- **Managing users**: creating users, linking accounts, migrating users from other systems
- **Monitoring wallet activity**: setting up webhooks for transaction status, user events, intent approvals
- **Implementing wallet actions**: transfers, swaps, earn deposits/withdrawals
- **Troubleshooting wallet errors**: policy violations, insufficient funds, authorization failures

## Quick reference

### SDK initialization

| Platform | Code |
|----------|------|
| **React** | `<PrivyProvider appId="..." clientId="..." config={{...}}>` |
| **React Native** | `<PrivyProvider appId="..." clientId="..." config={{...}}>` |
| **Node.js** | `new PrivyClient({appId: '...', appSecret: '...'})` |
| **REST API** | `curl -u "appId:appSecret" -H "privy-app-id: appId" https://api.privy.io/v1/...` |

### Common API endpoints

| Task | Endpoint | Method |
|------|----------|--------|
| Create wallet | `/v1/wallets` | POST |
| Get wallet | `/v1/wallets/{id}` | GET |
| Send transaction | `/v1/wallets/{id}/ethereum/eth_sendTransaction` | POST |
| Create user | `/v1/users` | POST |
| Get user | `/v1/users/{id}` | GET |
| Create policy | `/v1/policies` | POST |
| Get policy | `/v1/policies/{id}` | GET |

### Authentication headers (REST API)

```
Authorization: Basic base64(appId:appSecret)
privy-app-id: appId
privy-request-expiry: <unix-timestamp-ms>
privy-authorization-signature: <signature> (if required)
```

### Wallet control models

| Model | Owner | Use case |
|-------|-------|----------|
| User-owned | User ID | Self-custodial consumer wallets |
| User + server | User ID + server signer | Automated trading, limit orders |
| App-owned | Authorization key | Treasury, trading bots, agents |
| Custodial | Licensed custodian | FBO banking model |

### Policy field sources (for conditions)

| Source | Example fields | Use case |
|--------|-----------------|----------|
| `ethereum_transaction` | `to`, `value`, `chain_id` | Restrict recipients, amounts |
| `ethereum_calldata` | `function_name`, `function_name._to` | Control smart contract calls |
| `solana_token_program_instruction` | `Transfer.destination`, `Transfer.amount` | Restrict SPL token transfers |
| `system` | `current_unix_timestamp` | Time-based restrictions |

## Decision guidance

### When to use Privy authentication vs. your own auth provider

| Scenario | Use Privy auth | Use your own auth |
|----------|---|---|
| Building from scratch, want wallets + auth | ✓ | |
| Already have auth system, want to add wallets | | ✓ (via JWT integration) |
| Need email, social, passkey, wallet login | ✓ | |
| Need custom auth logic | | ✓ |

### When to create wallets automatically vs. on-demand

| Scenario | Auto-create | On-demand |
|----------|---|---|
| Consumer app, all users need wallets | ✓ | |
| Only some users need wallets | | ✓ |
| Want to control timing of wallet creation | | ✓ |
| Maximize UX simplicity | ✓ | |

### When to use policies vs. signers

| Scenario | Use policies | Use signers |
|----------|---|---|
| Enforce spending limits, recipient restrictions | ✓ | |
| Require multiple parties to approve actions | | ✓ (key quorum) |
| Prevent unintended contract interactions | ✓ | |
| Delegate scoped permissions to server | ✓ + ✓ | |

### When to use embedded wallets vs. external wallets

| Scenario | Embedded | External |
|----------|---|---|
| Onboarding new users | ✓ | |
| Users have existing wallets (MetaMask, Phantom) | | ✓ |
| Want seamless UX without key management | ✓ | |
| Users want full control of keys | | ✓ |

## Workflow

### 1. Set up a Privy app

1. Go to https://dashboard.privy.io
2. Create a new app
3. Copy your **App ID** and **App Secret** from Configuration > App settings > Basics
4. Configure login methods (Configuration > Login methods)
5. Set up app clients if deploying across multiple domains (Configuration > App clients)

### 2. Initialize the SDK in your app

**Client-side (React):**
```tsx
<PrivyProvider appId="..." clientId="..." config={{embeddedWallets: {ethereum: {createOnLogin: 'users-without-wallets'}}}}>
  {children}
</PrivyProvider>
```

**Server-side (Node.js):**
```ts
const privy = new PrivyClient({appId: '...', appSecret: '...'});
```

### 3. Authenticate users

- Use `usePrivy()` hook (React) or `privy.users.get()` (Node.js) to check authentication state
- Call login methods (email, social, wallet, passkey) via UI or SDK
- Verify user is authenticated before creating wallets or signing transactions

### 4. Create or retrieve wallets

**Client-side:**
```tsx
const {createWallet} = useCreateWallet();
const wallet = await createWallet();
```

**Server-side:**
```ts
const wallet = await privy.wallets().create({
  chain_type: 'ethereum',
  owner: {user_id: 'did:privy:xxxxx'}
});
```

### 5. Configure policies (if needed)

1. Define rules for what actions are allowed (e.g., max transfer amount, allowlisted recipients)
2. Create policy via Dashboard or API with conditions and actions
3. Attach policy to wallet at creation or update wallet with policy ID

### 6. Sign and send transactions

**Client-side (Ethereum):**
```tsx
const {sendTransaction} = useSendTransaction();
const hash = await sendTransaction({to: '0x...', value: '1000000000000000000'});
```

**Server-side (Node.js):**
```ts
const hash = await privy.wallets().ethereum().sendTransaction(walletId, {to: '0x...', value: '...'});
```

### 7. Monitor activity with webhooks

1. Go to Configuration > Webhooks in Dashboard
2. Add webhook endpoint URL (must be HTTPS)
3. Select event types (user.created, wallet_action.transfer.succeeded, etc.)
4. Verify webhook signature in your endpoint using Privy's signing key
5. React to events (e.g., notify user when transaction confirms)

### 8. Verify and test

- Check wallet creation in Dashboard (Wallets tab)
- Verify transactions on block explorer
- Test error cases (insufficient funds, policy violations)
- Monitor webhook deliveries in Dashboard

## Common gotchas

- **Policy defaults to DENY**: If a wallet has a policy, any RPC method not explicitly allowed in a rule will be denied. Always include an "allow all" rule or explicitly allowlist all methods you need.
- **User keys are time-bound**: User signing keys expire after a short period. Always request fresh keys before signing; SDKs handle this automatically with `AuthorizationContext`.
- **Policies are evaluated in secure enclaves**: You cannot inspect or debug policy evaluation directly; test thoroughly in development.
- **Rate limits on wallet creation**: Wallet creation endpoints are rate-limited. Implement exponential backoff for retries.
- **Idempotency keys prevent duplicates**: Always include `idempotency_key` on POST requests to prevent accidental duplicate operations.
- **Authorization signatures require exact payload match**: If signing a request, the payload (method, URL, headers, body) must match exactly or signature validation fails.
- **Wallets must have an owner**: Every wallet requires an owner (user ID, authorization key, or key quorum). Wallets without owners cannot be used.
- **External wallets require explicit configuration**: To use external wallets (MetaMask, Phantom), configure them in Dashboard or pass `externalWallets` config to SDK.
- **Automatic wallet creation only works with Privy login**: Auto-create on login does not trigger for custom OAuth or direct login methods.
- **Policy violations are not retryable**: If a transaction fails due to policy violation, retrying with the same parameters will fail again. Modify the transaction or policy.

## Verification checklist

Before submitting work with Privy:

- [ ] App ID and App Secret are stored securely (never in client-side code)
- [ ] PrivyProvider wraps the app at the root level (client-side)
- [ ] `ready` state is checked before consuming Privy hooks
- [ ] Wallets are created with appropriate owner (user ID or authorization key)
- [ ] Policies are attached to wallets if access control is needed
- [ ] All RPC methods used in policies are explicitly allowed (no silent denials)
- [ ] Webhook endpoint is HTTPS and signature verification is implemented
- [ ] Error handling covers policy violations, insufficient funds, and authorization failures
- [ ] Idempotency keys are included on all POST requests
- [ ] Authorization signatures are properly formatted if required
- [ ] Transactions are tested on testnet before production
- [ ] User authentication state is verified before wallet operations
- [ ] Rate limits are handled with exponential backoff

## Resources

**Comprehensive navigation:** https://docs.privy.io/llms.txt

**Critical docs:**
- [Key Concepts](https://docs.privy.io/basics/key-concepts) — understand authentication, wallets, and controls
- [API Reference](https://docs.privy.io/api-reference/introduction) — REST API endpoints and authentication
- [Controls & Policies](https://docs.privy.io/controls/overview) — wallet authorization and policy engine

---

> For additional documentation and navigation, see: https://docs.privy.io/llms.txt