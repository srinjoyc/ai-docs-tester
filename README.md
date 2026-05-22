# docs-eval

A benchmark for measuring how effectively documentation platforms (Mintlify, GitBook, etc.) enable AI coding assistants to implement embedded wallet and Account Abstraction (AA) use cases.

## What We're Measuring

Documentation platforms increasingly publish AI-optimized endpoints — `/llms.txt`, MCP servers, skill files. This project tests whether those features actually improve implementation accuracy when an AI agent tries to build real Web3 features from scratch.

**Primary question:** Does the documentation platform and the format it exposes docs in meaningfully affect AI implementation accuracy — and does it depend on the model?

**Test subjects:** Embedded wallet and AA providers whose docs are hosted on these platforms.

| Provider | Docs Platform (prod) | Docs Platform (staging) |
|---|---|---|
| Privy | Mintlify | — |
| ZeroDev | Custom | Mintlify (staging) |

---

## How It Works

```
1. SCAFFOLD  →  2. PROMPT  →  3. AGENT LOOP  →  4. GRADE & REPORT
```

1. **Scaffold** — A fresh Next.js + RainbowKit + wagmi starter app is created in an isolated work directory. The agent sees a real, working codebase — not a blank file.
2. **Prompt** — The agent is told to add a feature to the existing app. Base scenario: replace the wallet connection with an embedded wallet and send an NFT mint as a transaction or UserOperation.
3. **Agent loop** — The model runs in a tool-use loop (`list_files`, `read_file`, `write_file`, `run_grader`). Doc access depends on the mode being tested.
4. **Grade** — The grader runs `tsc --noEmit` and checks that required SDK functions appear in the output. Both must pass. This prevents false positives from agents that give up without writing code.

### Doc Delivery Modes

| Mode | How Docs Are Provided |
|---|---|
| `web` | Agent searches and fetches from the live docs site |
| `llms-txt` | Full `llms-full.txt` corpus injected into context upfront |
| `mcp` | Agent queries a Mintlify MCP server (`search` + file access) on demand |
| `skill` | Compact curated reference (~11k chars) injected upfront |

The `web` baseline is the most apples-to-apples cross-vendor comparison — no platform-specific AI features. The delta between `web` and `llms-txt`/`mcp`/`skill` is the value the platform's AI features add.

---

## Use Cases

Defined in `use_cases/<vendor>/*.yaml`. All grounded in a single developer journey:

> A user opens the app, signs in with an embedded wallet (no seed phrase, no external wallet), and mints an NFT on Sepolia testnet.

| # | Use Case | Tags |
|---|---|---|
| 01 | Embedded wallet sign-in + NFT mint | `basic` |
| 02 | Sponsored gas (paymaster) on mint tx | `gas` |
| 03 | Session key: allow minting without re-prompting | `session-key` |
| 04 | Batch: sign-in + mint in a single userOp | `batch` |
| 05 | Passkey signer instead of ECDSA | `passkey` |
| 06 | Scoped session key with spend limits | `session-key`, `advanced` |

---

## Results

Each cell was run 3 times. Pass rate = fraction producing compilable, correct code.

### ZeroDev — Kernel account + UserOperation mint
*"Create a Kernel smart account from a MetaMask signer, send mint() as a UserOperation"*

| Mode | GPT-4o — prod docs | GPT-4o — Mintlify staging | GPT-5.5 — Mintlify staging |
|---|---|---|---|
| `web` | 0/3 (0%) | 0/3 (0%) | **3/3 (100%)** |
| `llms-txt` | **3/3 (100%)** ✱ | 0/3 (0%) | — |
| `mcp` | N/A | 0/3 (0%) | — |
| `skill` | — | 0/3 (0%) | **2/3 (67%)** |

✱ Production `llms-full.txt` is more complete for this topic than Mintlify staging's.

### Privy — Embedded wallet login + NFT mint
*"Swap RainbowKit for Privy, get the EIP-1193 provider, sign a mint tx with viem WalletClient"*

| Mode | GPT-4o | GPT-5.5 |
|---|---|---|
| `web` | 1/3 (33%) | 2/3 (67%) |
| `llms-txt` | 0/3 (0%) | — |
| `mcp` | 0/3 (0%) | — |
| `skill` | 0/3 (0%) | **3/3 (100%)** |

### Privy — Gas-sponsored mint via smart wallet
*"Configure PrivyProvider with a sponsorship policy, use `useSmartWallets` instead of `useWriteContract`"*

| Mode | GPT-4o | GPT-5.5 |
|---|---|---|
| `web` | 1/3 (33%) | — |
| `llms-txt` | 0/3 (0%) | — |
| `mcp` | 0/3 (0%) | — |
| `skill` | 0/3 (0%) | 0/3 (0%) ✦ |

✦ GPT-5.5 exhausts its turn budget before calling the grader. Likely fixable with a higher `max_turns`.

---

## Key Findings

### 1. Model capability is the dominant variable

GPT-4o fails at near 0% on ZeroDev tasks regardless of documentation format. The Mintlify staging site has better structure, an MCP endpoint, and a skill file — GPT-4o still got 0% across all modes. GPT-5.5 on the same docs: 3/3 on `web`, 2/3 on `skill`.

For an SDK as complex as ZeroDev (Kernel v3.3 semantics, subpath imports like `@zerodev/sdk/constants`, typed entry points), GPT-4o lacks the synthesis ability to combine multi-part documentation into correct code. GPT-5.5 handles it.

**Implication:** Evaluating docs quality with GPT-4o on complex SDK tasks measures model failure, not docs failure. GPT-5.5 (or equivalent) should be the baseline.

### 2. Mintlify platform advantage is real — for capable models

With GPT-5.5 on the ZeroDev web task:
- Production docs (no MCP, truncated llms.txt): **1/3 (33%)**
- Mintlify staging (MCP, complete llms.txt, skill file): **3/3 (100%)**

The 3× improvement is entirely attributable to the platform. With a model capable of following docs, the platform advantage shows up clearly.

### 3. `llms-txt` injection works for GPT-4o on specific tasks

The only 100% result with GPT-4o across ZeroDev tasks came from injecting the full `llms-full.txt` corpus upfront (3/3 on production docs). No other mode with GPT-4o hit 100%. For weaker models, more context can substitute for synthesis ability — but inconsistently (Mintlify staging's truncated llms-txt gave 0/3 on the same task).

### 4. Skill files help GPT-5.5, not GPT-4o

| Task | GPT-5.5 `web` | GPT-5.5 `skill` |
|---|---|---|
| privy-01 | 2/3 (67%) | **3/3 (100%)** |
| zerodev-01 | 3/3 (100%) | 2/3 (67%) |

For Privy, the skill file pushed GPT-5.5 from 67% → 100%. For ZeroDev, `skill` slightly underperformed `web` — the current ZeroDev skill file misses the `@zerodev/sdk/constants` subpath import pattern, which web fetching fills in by hitting the actual API reference. GPT-4o got 0% on skill mode across all tasks.

### 5. MCP mode underperforms — but hasn't been tested with GPT-5.5

0/3 across every combination where MCP was available. Root cause: GPT-4o cannot synthesize multi-step imports from small retrieved snippets. The docs correctly show `KERNEL_V3_3` and `getEntryPoint` from `@zerodev/sdk/constants`, but GPT-4o merges subpath imports when combining snippets:

```ts
// What the model writes (wrong)
import { createKernelAccount, KERNEL_V3_3, getEntryPoint } from "@zerodev/sdk";

// What's correct
import { createKernelAccount } from "@zerodev/sdk";
import { KERNEL_V3_3, getEntryPoint } from "@zerodev/sdk/constants";
```

This is a model failure, not a Mintlify failure. MCP results with GPT-5.5 are untested and may tell a different story.

### 6. Failure taxonomy

| Category | What It Means | Most Common In |
|---|---|---|
| `hallucinated_api` | Called functions that don't exist in the installed SDK | GPT-4o on all modes |
| `hallucinated_package` | Imported `ethers`, `web3`, or other uninstalled packages | GPT-4o on skill/mcp modes |
| `typecheck_error` | Valid imports, broken type usage | GPT-4o on web mode |
| `missing_expected_import` | Gave up, left scaffold unchanged | GPT-4o on mcp/skill modes |
| `never_graded` | Exhausted turn budget without calling grader | GPT-5.5 on privy-02 |

---

## What's Next

- [ ] Run `mcp` and `llms-txt` modes for ZeroDev and Privy with GPT-5.5
- [ ] Fix privy-02 `max_turns` and re-run with GPT-5.5
- [ ] Fix ZeroDev skill file — add `@zerodev/sdk/constants` subpath import pattern
- [ ] Add difficulty-2 use cases (gas sponsorship, session keys, batch ops)
- [ ] Benchmark additional providers (Safe, Dynamic, Alchemy AA)

---

## CLI

```bash
# Run full matrix (all use cases × all targets × all modes, 3 runs each)
docs-eval run --runs 3 --out ./results/$(date +%Y%m%d)

# Cross-vendor signal: ZeroDev vs Privy, web mode only
docs-eval run --targets zerodev-current,privy-current --modes web --runs 3

# Single cell, verbose (useful for debugging a grader or prompt)
docs-eval run \
  --use-cases ./use_cases/zerodev/01-kernel-account-ecdsa.yaml \
  --targets zerodev-current \
  --modes mcp \
  --runs 1 --verbose

# Report from saved results
docs-eval report ./results/20260520/ --format markdown > report.md
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env
# Fill in .env
docs-eval --help
```

### Environment Variables

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key |
| `CHAT_GPT_API_KEY` | OpenAI API key |
| `PRIVY_APP_ID` | Privy App ID from [dashboard.privy.io](https://dashboard.privy.io) |
| `ZERODEV_PROJECT_ID` | ZeroDev project ID |
| `BUNDLER_URL` | ZeroDev bundler RPC URL |
| `PAYMASTER_URL` | ZeroDev paymaster RPC URL |
| `AGENTMAIL_API_KEY` | AgentMail API key (used by signup scripts) |

## Project Layout

```
docs-eval/
  src/docs_eval/
    runner.py       # agent loop, tool execution, transcript logging
    cli.py          # CLI entry points
    config.py       # UseCase + Target dataclasses, YAML loading
    reporter.py     # summary.json -> report.md
    llms_txt.py     # fetch + truncate llms-full.txt
  use_cases/
    SCHEMA.md       # YAML schema reference
    zerodev/        # ZeroDev use case definitions
    privy/          # Privy use case definitions
  graders/
    zerodev-base/   # scaffold + typecheck scripts
    privy-base/     # scaffold + typecheck scripts
  analysis/
    root-causes.md  # per-run failure analysis
    team-summary.md # high-level findings
  scripts/
    signup.py       # automated provider signup via AgentMail + Playwright
    get_privy_id.py # extract Privy App ID from dashboard
  targets/
    targets.yaml    # all doc targets with URLs + feature flags
```

Things that should work:
- Gas sponorship, session keys, and batching, SRA
- Viem compatability is only supported (don't need to worry about ethers)
- Using privy as signer to zerodev

