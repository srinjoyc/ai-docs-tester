#!/usr/bin/env bash
# E2E grader for ZeroDev use cases.
#
# Steps:
#   1. tsc --noEmit  (fast fail — catches type errors before spinning up a browser)
#   2. Playwright    (starts Next.js dev server, connects a mock wallet, clicks
#                    "Mint NFT", and verifies the userOp hash appears in the UI)
#
# Env vars read by playwright.config.ts and e2e.test.ts:
#   WORK_DIR              — absolute path to the agent's code dir (set below)
#   PLAYWRIGHT_PORT       — free TCP port to run the dev server on
#   REQUIRE_GAS_SPONSORED — set to "true" for gas-sponsorship use cases
set -euo pipefail

WORK_DIR="$(cd "$1" && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$WORK_DIR"

# ── Step 1: typecheck ─────────────────────────────────────────────────────────
npx --yes tsc --noEmit 2>&1 || exit 1

# ── Step 2: pick a free port so parallel cells don't conflict ─────────────────
PORT=$(python3 -c \
  "import socket; s=socket.socket(); s.bind(('',0)); p=s.getsockname()[1]; s.close(); print(p)")

# ── Step 3: run the Playwright test ──────────────────────────────────────────
export WORK_DIR="$WORK_DIR"
export PLAYWRIGHT_PORT="$PORT"
export GRADER_DIR="$SCRIPT_DIR"
export PLAYWRIGHT_BROWSERS_PATH="/tmp/docs-eval-pw-browsers"

# Install Chromium on first use (cached in PLAYWRIGHT_BROWSERS_PATH)
npx --yes playwright install chromium 2>/dev/null

npx --yes playwright test \
  --config "$SCRIPT_DIR/playwright.config.ts" \
  2>&1
