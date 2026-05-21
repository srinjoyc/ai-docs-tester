# docs-eval

A benchmark for measuring how effectively documentation platforms (Mintlify, GitBook, etc.) enable AI coding assistants to implement embedded wallet and Account Abstraction (AA) use cases.

## Concept

Documentation platforms increasingly publish AI-optimized endpoints (`/llms.txt`, MCP servers, skills). This benchmark tests whether those endpoints actually improve implementation accuracy — using embedded wallet and AA providers as test subjects.

A clean starter app is scaffolded as the base. YAML-defined use cases specify what to build. The AI agent attempts each use case with docs delivered via different methods, and results are scored on correctness and iterations required.

**Primary question:** Does the documentation platform and the format it exposes docs in meaningfully affect AI implementation accuracy?

## How It Works

```
1. SCAFFOLD  →  2. PROMPT  →  3. AGENT LOOP  →  4. GRADE & REPORT
```

1. **Scaffold** — A fresh Vite or Next.js starter app is created in an isolated work directory. The agent sees a real, working codebase.
2. **Prompt** — The agent receives a task: *"add this feature to the existing app."* Base scenario: a user signs in with an embedded wallet and mints an NFT.
3. **Agent loop** — Claude runs in a tool-use loop (`list_files`, `read_file`, `write_file`, `run_grader`). Doc access depends on mode:
   - `web` — web search + fetch (baseline, no platform-specific AI features)
   - `llms-txt` — target's `llms-full.txt` loaded into context
   - `mcp` — target's MCP server queried on demand
4. **Grade & report** — Grader typechecks generated code and checks for required API calls/imports. Every step is logged to a `.jsonl` transcript.

## Doc Delivery Strategies

| Strategy | Method |
|---|---|
| **`web`** | Web search + fetch — the floor, no platform-specific features |
| **`llms-txt`** | Target's `/llms-full.txt` loaded into agent context |
| **`mcp`** | Target's MCP server queried on demand |

The `web` vs `llms-txt`/`mcp` delta is the value a doc platform's AI features add.

## Providers Tested

| Provider | Docs Platform | Docs URL |
|---|---|---|
| Privy | Mintlify | https://docs.privy.io |
| ZeroDev | — | https://docs.zerodev.app |

## Use Cases

Defined in `use_cases/<vendor>/*.yaml`. All grounded in a single developer journey:

> A user opens the app, signs in with an embedded wallet (no seed phrase), and mints an NFT on Sepolia testnet.

| # | Use Case | Tags |
|---|---|---|
| 01 | Embedded wallet sign-in + NFT mint | `basic` |
| 02 | Sponsored gas (paymaster) on mint tx | `gas` |
| 03 | Session key: allow minting without re-prompting | `session-key` |
| 04 | Batch: sign-in + mint in a single userOp | `batch` |
| 05 | Passkey signer instead of ECDSA | `passkey` |
| 06 | Scoped session key with spend limits | `session-key`, `advanced` |

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
docs-eval report ./results/20260519/ --format markdown > report.md
```

## Results

### Summary

| Provider | Docs Platform | Use Case | Model | Doc Strategy | Pass Rate | Avg Iterations |
|---|---|---|---|---|---|---|
| Privy | Mintlify | Embedded wallet mint | GPT-4o | `web` | 33% (2/6) | 1.3 |
| Privy | Mintlify | Gas-sponsored mint | GPT-4o | `web` | 33% (1/3) | 1.3 |
| ZeroDev | — | Kernel account mint | GPT-4o | `web` | 0% (0/3) | 2.3 |

### Key Findings

**Passing runs stopped early.** The most reliable success pattern was the agent recognizing docs were inaccessible and not writing code — avoiding hallucinated APIs. This is a documentation *availability* problem, not just a quality problem.

**ZeroDev's SDK factory pattern is high-risk for AI inference.** Without docs, no agent found `createKernelAccount` + `createKernelAccountClient`. The abstraction is too far from standard Web3 patterns to guess correctly.

**`/llms.txt` alone is insufficient.** Privy's root `llms.txt` was reachable but downstream structured URLs 404'd consistently. AI-optimized endpoints need full coverage, not just a root index.

**Hallucination pattern.** When docs were unavailable, agents invented non-existent exports (`PrivyWalletClient`, `ZeroDevProvider`) rather than stopping. Failure mode was overconfidence, not uncertainty.

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
  scripts/
    signup.py       # automated provider signup via AgentMail + Playwright
    get_privy_id.py # extract Privy App ID from dashboard
  targets/
    targets.yaml    # all doc targets with URLs + feature flags
```
