# docs-eval

A benchmark for measuring how effectively documentation platforms (Mintlify, Fumadocs, etc.) enable AI coding assistants to implement embedded wallet and Account Abstraction (AA) use cases.

> **Note on model selection:** GPT-4o and below are not viable for this benchmark without significant human intervention. These models fail ZeroDev tasks at near 0% regardless of documentation format or platform — the limiting factor is model synthesis ability, not docs quality. All results below use **GPT-5.5** unless otherwise noted. GPT-4o runs are archived in `results/archived/`.

## What We're Measuring

Documentation platforms increasingly publish AI-optimized endpoints — `/llms.txt`, MCP servers, skill files. This project tests whether those features actually improve implementation accuracy when an AI agent tries to build real Web3 features from scratch.

**Primary question:** Does the documentation platform and the format it exposes docs in meaningfully affect AI implementation accuracy?

**Test subjects:** Embedded wallet and AA providers whose docs are hosted on these platforms.

| Provider | Docs Platform (prod) | Docs Platform (staging) |
|---|---|---|
| Privy | Mintlify | — |
| ZeroDev | Custom | Mintlify (staging), Fumadocs (staging) |

---

## How It Works

```
1. SCAFFOLD  →  2. PROMPT  →  3. AGENT LOOP  →  4. GRADE & REPORT
```

1. **Scaffold** — A fresh Next.js + RainbowKit + wagmi starter app is created in an isolated work directory. The agent sees a real, working codebase — not a blank file.
2. **Prompt** — The agent is told to add a feature to the existing app. Base scenario: replace the wallet connection with an embedded wallet and send an NFT mint as a transaction or UserOperation.
3. **Agent loop** — The model runs in a tool-use loop (`list_files`, `read_file`, `write_file`, `run_grader`). Doc access depends on the mode being tested.
4. **Grade** — The grader runs `tsc --noEmit` and checks that required SDK functions appear in the output. Both must pass.

### Doc Delivery Modes

| Mode | How Docs Are Provided |
|---|---|
| `web` | Agent searches and fetches from the live docs site |
| `llms-txt` | Full `llms-full.txt` corpus injected into context upfront |
| `mcp` | Agent queries a Mintlify MCP server (`search` + file access) on demand |
| `skill` | Compact curated reference (~11k chars) injected upfront |

The `web` baseline is the most apples-to-apples cross-platform comparison. The delta between `web` and `llms-txt`/`mcp`/`skill` is the value the platform's AI features add.

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

## Results (GPT-5.5)

Each cell was run 3 times. Pass rate = fraction producing compilable, correct code.

### ZeroDev — Kernel account + UserOperation mint
*"Create a Kernel smart account from a MetaMask signer, send mint() as a UserOperation"*

| Mode | Mintlify staging | Fumadocs staging |
|---|---|---|
| `web` | **3/3 (100%)** | 0/3 (0%) |
| `llms-txt` | — | 0/3 (0%) |
| `mcp` | 0/3 (0%) | N/A |
| `skill` | 2/3 (67%) | N/A |

Fumadocs failures: `typecheck_error` (3×), `hallucinated_api` (2×), `hallucinated_package` (1×). GPT-5.5 gets closer than GPT-4o (fewer total hallucinations, more type errors) but still cannot produce passing code from fumadocs docs alone.

### Privy — Embedded wallet login + NFT mint
*"Swap RainbowKit for Privy, get the EIP-1193 provider, sign a mint tx with viem WalletClient"*

| Mode | Pass rate |
|---|---|
| `web` | 2/3 (67%) |
| `skill` | **3/3 (100%)** |

### Privy — Gas-sponsored mint via smart wallet
*"Configure PrivyProvider with a sponsorship policy, use `useSmartWallets` instead of `useWriteContract`"*

| Mode | Pass rate |
|---|---|
| `skill` | 0/3 (0%) ✦ |

✦ GPT-5.5 exhausts its turn budget before calling the grader. Fixable with a higher `max_turns`.

---

## Key Findings

### 1. Platform matters more than delivery format — for capable models

The clearest signal in this dataset is the Mintlify vs. Fumadocs comparison on ZeroDev with GPT-5.5:

| Platform | web mode |
|---|---|
| Mintlify staging | **3/3 (100%)** |
| Fumadocs staging | 0/3 (0%) |

Same model, same use case, same grader. The only variable is the docs platform. GPT-5.5 is capable of implementing the task correctly — it just can't do it from fumadocs' current content structure.

Root cause: ZeroDev's SDK requires subpath imports (`@zerodev/sdk/constants` for `KERNEL_V3_3` and `getEntryPoint`). Mintlify staging surfaces this pattern prominently; fumadocs' truncated `llms-full.txt` does not. Even with full `llms-full.txt` injection (40k chars, truncated), GPT-5.5 on fumadocs produces typecheck errors rather than the correct import structure.

### 2. Mintlify skill files add value on top of web

| Task | web | skill |
|---|---|---|
| ZeroDev 01 (Mintlify) | 3/3 (100%) | 2/3 (67%) |
| Privy 01 | 2/3 (67%) | **3/3 (100%)** |

Skill files help Privy (67% → 100%) by providing a compact, structured reference the agent navigates efficiently. For ZeroDev, the skill file slightly underperforms web — it's missing the `@zerodev/sdk/constants` subpath import pattern, which the agent finds by fetching the live API reference. **Fix: update the ZeroDev skill file to include the subpath import pattern.**

### 3. MCP mode underperforms — untested with the right model

0/3 on MCP for every combination tested. Root cause: the agent retrieves small snippets that omit cross-module import context. These tests used GPT-4o (archived). MCP mode with GPT-5.5 is not yet tested and may tell a different story.

### 4. Failure taxonomy

| Category | What It Means |
|---|---|
| `typecheck_error` | Valid imports, broken type usage — agent got close but has wrong API shape |
| `hallucinated_api` | Called functions that don't exist in the installed SDK |
| `hallucinated_package` | Imported `ethers`, `web3`, or other uninstalled packages |
| `missing_expected_import` | Gave up, left scaffold unchanged |
| `never_graded` | Exhausted turn budget without calling grader |

---

## What's Next

- [ ] Run `llms-txt` and `mcp` modes for ZeroDev Mintlify staging with GPT-5.5
- [ ] Fix ZeroDev skill file — add `@zerodev/sdk/constants` subpath import pattern, re-run
- [ ] Fix privy-02 `max_turns` and re-run with GPT-5.5
- [ ] Run ZeroDev use case against prod docs (`docs.zerodev.app`) with GPT-5.5
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
docs-eval report ./results/fumadocs-gpt55-20260522/ --format markdown > report.md
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env
# Fill in .env
docs-eval --help
```

### Model backends

The runner supports two backends selected automatically by model name:

- **Claude models** (`claude-*`): invoked via the `claude -p` CLI subprocess using Claude Code's existing authentication. No `ANTHROPIC_API_KEY` needed — the Claude Code CLI handles auth. Custom tools are exposed through an MCP stdio server (`mcp_server.py`) that Claude Code starts per cell.
- **OpenAI models** (`gpt-*`): invoked via the OpenAI Python SDK. Requires `CHAT_GPT_API_KEY`.

The Claude path exists specifically because we don't hold a raw Anthropic API key; we rely on the authenticated Claude Code session instead.

### Environment Variables

| Variable | Description |
|---|---|
| `CHAT_GPT_API_KEY` | OpenAI API key (only needed for GPT model runs) |
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
  results/
    archived/       # GPT-4o runs (not viable without human intervention)
  targets/
    targets.yaml    # all doc targets with URLs + feature flags
```

Things that should work:
- Gas sponsorship, session keys, and batching, SRA
- Viem compatibility only (no ethers)
- Using Privy as signer to ZeroDev
