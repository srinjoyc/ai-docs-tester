# Root Cause Analysis

Running log of failure root causes per use case. Updated after each eval session.

---

## Environment

| component | version |
|---|---|
| model | gpt-4o |
| modes tested | llms-txt, mcp |
| starter template | Next.js 16 (Pages Router) + RainbowKit + wagmi |

### Package versions (as of 2026-05-20)

| package | zerodev base | privy base |
|---|---|---|
| wagmi | 3.6.15 | 3.6.15 |
| viem | 2.50.4 | 2.50.4 |
| next | 16.2.6 | 16.2.6 |
| typescript | 6.0.3 | 6.0.3 |
| @rainbow-me/rainbowkit | 2.2.11 | 2.2.11 |
| @tanstack/react-query | 5.100.11 | 5.100.11 |
| @zerodev/sdk | 5.5.10 | — |
| @zerodev/ecdsa-validator | 5.4.9 | — |
| permissionless | 0.3.5 | — |
| @privy-io/react-auth | — | 3.27.0 |

---

## zerodev-01-kernel-mint

**Date:** 2026-05-20  
**Result:** FAIL — `hallucinated_api`  
**Doc source:** `https://docs.zerodev.app/llms-full.txt` (truncated to 40k chars)

### What the model got right
- Listed and read all project files before writing
- Understood the task (wrap MetaMask EOA in a Kernel smart account, send mint as UserOp)

### Root cause
GPT-4o wrote from **wagmi v1 training memory** instead of reading the docs:
- Used `useProvider` from wagmi — removed in wagmi v2 (now `usePublicClient`)
- Used `useSigner` from wagmi — removed in wagmi v2 (now `useWalletClient`)
- Used `connector.getSigner()` — not a wagmi v2 API
- Wrong `entryPoint` type (passed a string `"0.7"` instead of the typed constant from `getEntryPoint()`)
- Wrong `createKernelAccountClient` shape (wrong `bundlerTransport` and `paymaster` types)

### Correct pattern (wagmi v2 + @zerodev/sdk v5)
```ts
import { useWalletClient } from "wagmi";
import { walletClientToSmartAccountSigner } from "permissionless/adapters/viem";
import { signerToEcdsaValidator } from "@zerodev/ecdsa-validator";
import { createKernelAccount, createKernelAccountClient } from "@zerodev/sdk";
import { KERNEL_V3_3, getEntryPoint } from "@zerodev/sdk/constants";  // ← subpath, NOT @zerodev/sdk
import { createPublicClient, http } from "viem";
import { sepolia } from "viem/chains";

const { data: walletClient } = useWalletClient();
const signer = walletClientToSmartAccountSigner(walletClient!);
const publicClient = createPublicClient({ chain: sepolia, transport: http() });
const entryPoint = getEntryPoint("0.7");  // from @zerodev/sdk/constants
const ecdsaValidator = await signerToEcdsaValidator(publicClient, { signer, entryPoint, kernelVersion: KERNEL_V3_3 });  // KERNEL_V3_3 from @zerodev/sdk/constants
const account = await createKernelAccount(publicClient, { plugins: { sudo: ecdsaValidator }, entryPoint, kernelVersion: KERNEL_V3_3 });
const kernelClient = createKernelAccountClient({ account, chain: sepolia, bundlerTransport: http(BUNDLER_URL) });
```

### What to try next
- Check if `llms-full.txt` (40k chars) actually contains a complete wagmi v2 + SDK v5 example
- Consider bumping truncation limit or switching to a targeted chunk (the quickstart section)
- Provide a more explicit hint in the prompt: "wagmi v3, use useWalletClient not useProvider/useSigner"

---

## zerodev-01-kernel-mint — MCP mode (zerodev-mintlify-staging)

**Date:** 2026-05-20  
**Result:** FAIL — `typecheck_error` (all 3 runs)  
**Doc source:** `https://ocl-de73b1a4.mintlify.app/mcp` (Mintlify MCP server)  
**Tools used by agent:** `search_zero_dev`, `query_docs_filesystem_zero_dev`

### Improvement over llms-txt mode
MCP mode is clearly better — the agent successfully:
- Located the right doc pages (`/sdk/core-api/create-account.mdx`, `/sdk/core-api/send-transactions.mdx`)
- Got the correct function names (`createKernelAccount`, `createKernelAccountClient`, `signerToEcdsaValidator`)
- Got the correct API shape (entryPoint + kernelVersion params, `bundlerTransport` not `bundler`)

### Run-by-run breakdown

| run | failure mode | key error |
|-----|-------------|-----------|
| r0 | import path | `KERNEL_V3_1`, `getEntryPoint` imported from `@zerodev/sdk` (wrong — must be `@zerodev/sdk/constants`) |
| r1 | regression | wagmi v1 hallucination (`useProvider` removed in v2) + `ethers` usage |
| r2 | placeholder values | used string `"KERNEL_V3_1"` and `"0xEntryPointAddress"` instead of actual constants |

### Root cause: synthesis failure on isolated import snippets
The Mintlify staging docs show imports as **isolated single-purpose code blocks**, e.g.:
```typescript
// Standalone snippet 1
import { getEntryPoint } from "@zerodev/sdk/constants"
const entryPoint = getEntryPoint("0.7")

// Standalone snippet 2
import { KERNEL_V3_1 } from "@zerodev/sdk/constants"
const kernelVersion = KERNEL_V3_1

// Standalone snippet 3
import { createKernelAccount } from "@zerodev/sdk"
...
```

When GPT-4o synthesizes these into a single file, it merges all `@zerodev/sdk/*` imports into one line:
```typescript
// Wrong — model drops the /constants subpath
import { createKernelAccount, createKernelAccountClient, KERNEL_V3_1, getEntryPoint } from "@zerodev/sdk";
```

The docs are correct; the model is failing at **multi-snippet synthesis**.

### Documentation quality issue
The `create-account.mdx` page has no **complete, copy-paste-ready combined example** at the bottom. Every import is shown in an isolated snippet. A "Full Example" section with all imports merged would prevent this class of synthesis error.

### Confirmed: `@zerodev/sdk/constants` IS the right subpath
- `KERNEL_V3_1` through `KERNEL_V3_3` live in `@zerodev/sdk/constants.ts`
- They are NOT top-level exports of `@zerodev/sdk`
- `grep` on `@zerodev/sdk/_types/index.d.ts` confirms they are absent from top-level

### What to try next
- Add a "Complete Example" hint to the use case prompt or config.ts comment
- Or: request ZeroDev add a combined import example to the docs
- Try with a model that follows structured doc snippets more faithfully (e.g. Claude)
- Run `--modes mcp` against the production `zerodev-current` target once its MCP endpoint is known

---

## privy-01-embedded-wallet-mint

**Date:** 2026-05-20  
**Result:** FAIL — `used_forbidden_api`  
**Doc source:** `https://docs.privy.io/skill.md` (10.7k chars, not truncated)

### What the model got right
- Updated `_app.tsx` with `PrivyProvider` correctly (used `PRIVY_APP_ID`, correct config shape)
- Updated `index.tsx` with `usePrivy()` login/logout correctly
- Auth layer was completely correct — only the transaction layer failed

### Root cause
GPT-4o hallucinated **non-existent Privy transaction hooks**:
- `useCreateWallet` — doesn't exist in `@privy-io/react-auth`
- `useSignTransaction` — doesn't exist
- Used `ethers.utils.Interface` to encode calldata (forbidden by grader — `ethers` not installed)

The model doesn't know the correct "get embedded wallet → get EIP-1193 provider → wrap in viem WalletClient → sendTransaction" flow from training alone, and `skill.md` apparently doesn't show it clearly enough.

### Correct pattern (@privy-io/react-auth v3)
```ts
import { useWallets } from "@privy-io/react-auth";
import { createWalletClient, custom, encodeFunctionData } from "viem";
import { sepolia } from "viem/chains";

const { wallets } = useWallets();
const wallet = wallets.find(w => w.walletClientType === "privy");
const provider = await wallet.getEthereumProvider();
const walletClient = createWalletClient({ account: wallet.address as `0x${string}`, chain: sepolia, transport: custom(provider) });
const hash = await walletClient.sendTransaction({ to: NFT_CONTRACT, data: encodeFunctionData({ abi: NFT_ABI, functionName: "mint", args: [wallet.address] }) });
```

### What to try next
- Check if `skill.md` actually contains the `useWallets` + `getEthereumProvider` + `createWalletClient` example
- If not: either improve Privy's `skill.md` or add a concrete viem snippet to the use case prompt
- The auth layer worked perfectly — the gap is specifically in the "send tx from embedded wallet" step

---

## privy-02-sponsored-mint

*(not yet run with Next.js base — pending)*
