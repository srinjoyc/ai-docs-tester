# Use case schema

Each use case is a YAML file under `use_cases/<vendor>/`. Vendor-namespaced so cross-vendor comparisons stay sane (you compare zerodev/01 to privy/01 to safe/01).

```yaml
id: string                # unique, e.g. "zerodev-01-kernel-ecdsa"
vendor: string            # zerodev | privy | safe | dynamic | alchemy
title: string             # human-readable
difficulty: 1-5           # 1=hello world, 5=advanced/newest features
tags: [string]            # gas-sponsorship, session-keys, passkey, 7702, chain-abstraction

prompt: |                 # the literal prompt sent to the agent
  Multi-line prompt text.
  Should read like a real dev asked an agent to do this.
  Specify chain, framework, signer if it matters.

# What "working" looks like. The grader uses these.
expected:
  imports:                # packages that MUST appear in the code
    - "@zerodev/sdk"
  calls:                  # function/method names that MUST be invoked
    - "createKernelAccount"
    - "createKernelAccountClient"
  forbidden:              # things that indicate the agent went wrong
    - "ethers"            # e.g. if we want viem-based code
  chain: "sepolia"        # which chain the code targets

# How the starter project is created before the agent runs.
scaffold:
  setup: ./scaffolds/zerodev-base/setup.sh  # installs deps, prepares env

# How the grader validates the agent's output after the agent runs.
grader:
  type: compile | runtime | onchain
  # compile: tsc/typecheck only. Cheap. Catches API hallucinations.
  # runtime: actually run in sandbox, hit testnet. Slow but real.
  # onchain: assert onchain effect (userOp landed, gas sponsored, etc).

  run: ./graders/zerodev-base/run.sh        # executes validation
  assertions: ./graders/zerodev-01-kernel.ts # checks expected outcome

# Budget for the agent
budget:
  max_turns: 8            # how many iterations before we call it failed
  max_seconds: 300        # wall-clock timeout
```

## Tagging conventions

Use `tags` so you can slice the report by feature category:

- `basic` — SDK setup, send a userOp
- `gas-sponsorship` — paymaster integration  
- `passkey` / `webauthn` — passkey signers
- `session-keys` — session key creation and use
- `batching` — multi-call userOps
- `7702` — EIP-7702 flows (newer, less likely in training data)
- `chain-abstraction` — cross-chain balance/spending
- `recovery` — guardian/social recovery
- `integration:privy` / `integration:dynamic` / `integration:magic` — third-party signer integration

Newer/rarer features are where docs matter most — if it's in every training set already, doc quality matters less.
