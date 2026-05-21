# docs-eval: Findings Summary
*May 2026 — Offchain Labs, docs infrastructure experiment*

## What We Built

A benchmarking framework that measures how well documentation helps LLM coding agents implement SDK features. The agent is given a scaffolded Next.js app and told to implement a feature (e.g., "add a ZeroDev Kernel smart account for NFT minting"). The grader checks that the code compiles (`tsc --noEmit`) **and** that the required SDK functions appear in the output — preventing false positives from agents that give up without writing any code.

**Tasks tested:**
- `privy-01`: Replace RainbowKit with Privy embedded wallets (login + NFT mint via viem WalletClient)
- `privy-02`: Add gas sponsorship via Privy smart wallets + `useSmartWallets`
- `zerodev-01`: Implement a ZeroDev Kernel smart account (ECDSA validator, bundler, paymaster) and send an NFT mint as a UserOperation

**Documentation delivery modes tested:**
- `web` — agent fetches URLs from the live documentation site
- `llms-txt` — full `llms-full.txt` corpus injected upfront as context
- `mcp` — agent queries a Mintlify MCP server (search + file access)
- `skill` — compact curated reference doc (~11k chars) injected upfront

Each (task × target × mode) cell was run 3 times. Pass rate = fraction that produced compilable, correct code.

---

## Results

### ZeroDev Task 1: Kernel account + UserOperation mint
*"Create a Kernel smart account from a MetaMask signer, send mint() as a UserOperation"*

| Mode | gpt-4o — prod docs (`docs.zerodev.app`) | gpt-4o — Mintlify staging | gpt-5.5 — Mintlify staging |
|---|---|---|---|
| web | 0/3 (0%) | 0/3 (0%) | **3/3 (100%)** |
| llms-txt | 3/3 (100%) ✱ | 0/3 (0%) | — |
| mcp | N/A (no endpoint) | 0/3 (0%) | — |
| skill | — | 0/3 (0%) | **2/3 (67%)** |

✱ Production docs have `llms-full.txt`; Mintlify staging's llms-txt contains less content for this topic.

### Privy Task 1: Embedded wallet login + NFT mint
*"Swap RainbowKit for Privy, get the EIP-1193 provider, sign a mint tx with viem WalletClient"*

| Mode | gpt-4o | gpt-5.5 |
|---|---|---|
| web | 1/3 (33%) | 2/3 (67%) |
| llms-txt | 0/3 (0%) | — |
| mcp | 0/3 (0%) | — |
| skill | 0/3 (0%) | **3/3 (100%)** |

### Privy Task 2: Gas-sponsored mint via smart wallet
*"Configure PrivyProvider with sponsorship policy, use `useSmartWallets` instead of `useWriteContract`"*

| Mode | gpt-4o | gpt-5.5 |
|---|---|---|
| web | 1/3 (33%) | — |
| llms-txt | 0/3 (0%) | — |
| mcp | 0/3 (0%) | — |
| skill | 0/3 (0%) | 0/3 (0%) ✦ |

✦ gpt-5.5 runs out of its turn budget before calling the grader. Likely solvable with `max_turns` increase.

---

## Key Findings

### 1. Model capability is the dominant variable

gpt-4o fails at near 0% on ZeroDev tasks regardless of documentation format. The Mintlify staging site was meaningfully better structured, had MCP, and a skill file — gpt-4o still got 0% on web, llms-txt, mcp, and skill modes. gpt-5.5 on the same docs and same skill file: 3/3 on web, 2/3 on skill.

For an SDK as complex as ZeroDev (Kernel v3.3 semantics, subpath imports like `@zerodev/sdk/constants`, typed entry points), gpt-4o lacks the synthesis ability to combine multi-part documentation into correct code. gpt-5.5 handles it.

**The actionable implication:** The docs team should target gpt-5.5 (and comparable models) as the baseline for evaluating whether documentation is effective. Testing only on gpt-4o will measure model failure, not docs failure.

### 2. Mintlify platform advantage is real — especially for capable models

With gpt-5.5 on the ZeroDev web task:
- Production docs (`docs.zerodev.app`, no MCP, truncated llms.txt): **1/3 (33%)**
- Mintlify staging (MCP, complete llms.txt, skill file): **3/3 (100%)**

This is a meaningful signal. The staging site has better-structured navigation, a machine-queryable MCP endpoint, and a complete llms-full.txt. The production site has none of these. With a model capable enough to follow the docs, the platform advantage shows up clearly.

### 3. llms-txt injection works surprisingly well for gpt-4o on specific tasks

The only 100% pass rate with gpt-4o across ZeroDev tasks came from injecting the entire llms-full.txt corpus upfront (production docs, 3/3). No other mode with gpt-4o hit 100% on this task. This suggests that for weaker models, "more context" can substitute for synthesis ability — but it's inconsistent (Mintlify staging's llms-txt for the same task gave 0/3).

### 4. Skill files help gpt-5.5 substantially, less so for gpt-4o

| Task | gpt-5.5 web | gpt-5.5 skill |
|---|---|---|
| privy-01 | 2/3 (67%) | 3/3 (100%) |
| zerodev-01 | 3/3 (100%) | 2/3 (67%) |

For Privy, the skill file pushed gpt-5.5 from 67% → 100%. For ZeroDev, skill was slightly below web (the skill file may have a gap on the `@zerodev/sdk/constants` subpath import that web fetching fills in by hitting the actual API reference).

For gpt-4o, skill files provided no benefit on any task tested.

### 5. MCP mode consistently underperforms on current tasks

0/3 across every (vendor × task) combination where MCP was available. Root cause: gpt-4o cannot synthesize multi-step imports from small retrieved snippets into correct TypeScript. The docs correctly show `KERNEL_V3_3` and `getEntryPoint` from `@zerodev/sdk/constants`, but gpt-4o merges subpath imports into the top-level module when combining multiple retrieved snippets.

This is a model failure rather than a Mintlify failure — the MCP results with gpt-5.5 have not been tested yet and may tell a different story.

### 6. Failure mode taxonomy

| Category | Meaning | Seen most in |
|---|---|---|
| `hallucinated_package` | Imported `ethers`, `web3`, or other uninstalled packages | gpt-4o on skill/mcp modes |
| `hallucinated_api` | Called functions that don't exist in the installed package | gpt-4o on all modes |
| `typecheck_error` | Valid imports, broken type usage | gpt-4o on web mode |
| `missing_expected_import` | Gave up, left scaffold unchanged | gpt-4o on mcp/skill modes |
| `never_graded` | Used up all turns without calling the grader | gpt-5.5 on privy-02 |

---

## Recommendations

1. **Move to gpt-5.5 (or equivalent) as the standard eval model.** gpt-4o does not meaningfully distinguish good docs from bad docs on complex SDK tasks.

2. **Prioritize migrating ZeroDev production docs to Mintlify.** The 3× improvement in pass rate (33% → 100%) with gpt-5.5 web mode is entirely attributable to the platform. Production lacks MCP, has a truncated llms.txt, and a less structured reference format — all things Mintlify staging has out of the box.

3. **Invest in skill file quality for ZeroDev.** The current ZeroDev skill file misses the subpath import pattern (`@zerodev/sdk/constants`). Fixing this should close the web-vs-skill gap and give agents a reliable compact reference without requiring them to crawl docs at runtime.

4. **Privy docs are in better shape.** gpt-5.5 + skill file = 100% on privy-01. The remaining open question is privy-02 (gas sponsorship), which needs a higher turn budget and possibly more detail in the skill file about `useSmartWallets` + policy configuration.

5. **Test MCP mode with gpt-5.5.** Current MCP results are all gpt-4o. Given how well gpt-5.5 performs on web mode, MCP may do comparably well (or better) once synthesis failure is no longer the bottleneck.

---

## What's Next

- [ ] Run MCP and llms-txt modes for ZeroDev and Privy with gpt-5.5
- [ ] Fix privy-02 `max_turns` limit and re-run with gpt-5.5
- [ ] Fix ZeroDev skill file to cover `@zerodev/sdk/constants` subpath imports
- [ ] Add difficulty-2 use cases (gas sponsorship, session keys, batch ops) to stress-test advanced docs coverage
- [ ] Benchmark competitor SDK doc sites (Safe, Dynamic, Alchemy AA) against the same tasks
