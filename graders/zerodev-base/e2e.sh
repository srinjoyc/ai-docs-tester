#!/usr/bin/env bash
# E2E grader for ZeroDev use cases.
#
# Steps:
#   1. Load .env.test credentials (if present) — enables REAL MODE
#   2. tsc --noEmit  (fast fail — catches type errors before spinning up a browser)
#   3. Playwright    (starts Next.js dev server, connects a mock or real wallet,
#                    clicks "Mint NFT", and verifies the userOp hash in the UI;
#                    in REAL MODE also polls for on-chain receipt via Arb Sepolia)
#
# REAL MODE requires: TEST_WALLET_PRIVATE_KEY + ZERODEV_PROJECT_ID
# (set in .env.test at the project root, or passed as env vars directly)
#
# Env vars forwarded to playwright.config.ts / e2e.test.ts:
#   WORK_DIR              — absolute path to the agent's code dir (set below)
#   PLAYWRIGHT_PORT       — free TCP port to run the dev server on
#   REQUIRE_GAS_SPONSORED — "true" to assert "gas sponsored" text in the UI
#   TEST_WALLET_PRIVATE_KEY, ZERODEV_PROJECT_ID, BUNDLER_URL, PAYMASTER_URL
set -euo pipefail

WORK_DIR="$(cd "$1" && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$WORK_DIR"

# ── Step 1: load test credentials ────────────────────────────────────────────
# Only sets vars that are not already present in the environment, so runner-
# supplied values (e.g., ZERODEV_PROJECT_ID from .env) are never clobbered.
if [ -f "$PROJECT_ROOT/.env.test" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue   # skip comments
    [[ -z "${line// }" ]] && continue              # skip blank lines
    key="${line%%=*}"; key="${key// /}"            # var name
    val="${line#*=}"; val="${val%%#*}"             # value (strip inline comment)
    val="${val#"${val%%[![:space:]]*}"}"; val="${val%"${val##*[![:space:]]}"}"  # trim
    [[ -z "$key" || -z "$val" ]] && continue       # skip unset / empty values
    env | grep -q "^${key}=" && continue           # don't override existing vars
    export "$key=$val"
  done < "$PROJECT_ROOT/.env.test"
fi

# ── Step 2: typecheck ─────────────────────────────────────────────────────────
./node_modules/.bin/tsc --noEmit 2>&1 || exit 1

# ── Step 2b: verify ZeroDev project accessibility (fall back to mock if blocked) ─
if [ -n "${ZERODEV_PROJECT_ID:-}" ]; then
  _check_url="${BUNDLER_URL:-https://staging-rpc.zerodev.app/api/v3/${ZERODEV_PROJECT_ID}/chain/421614}"
  _status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 \
    -X POST "$_check_url" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}' 2>/dev/null || echo "000")
  if [ "$_status" = "403" ] || [ "$_status" = "000" ]; then
    echo "⚠ ZeroDev project not accessible (HTTP $_status) — falling back to mock mode"
    unset ZERODEV_PROJECT_ID
    unset TEST_WALLET_PRIVATE_KEY
  fi
fi

# ── Step 3: pick a free port so parallel cells don't conflict ─────────────────
PORT=$(python3 -c \
  "import socket; s=socket.socket(); s.bind(('',0)); p=s.getsockname()[1]; s.close(); print(p)")

echo "[e2e] port: $PORT"

# ── Step 4: start Next.js dev server in background ───────────────────────────
export WORK_DIR="$WORK_DIR"
export PLAYWRIGHT_PORT="$PORT"
export GRADER_DIR="$SCRIPT_DIR"
export PLAYWRIGHT_BROWSERS_PATH="/tmp/docs-eval-pw-browsers"
# NODE_PATH is set for playwright (so the test file can import viem from WORK_DIR),
# but we launch Next.js WITHOUT it: having NODE_PATH point at a symlinked
# node_modules causes Next.js to resolve react to two different physical paths
# (symlink vs real) on the first compile, triggering "multiple copies of React".
export NODE_PATH="$WORK_DIR/node_modules"

echo "[e2e] starting Next.js dev server on port ${PORT}..."
NODE_PATH="" npm run dev -- --port "${PORT}" >"/tmp/nextjs-${PORT}.log" 2>&1 &
NEXT_PID=$!

# Poll until the server responds or timeout (120s)
_ready=0
for _i in $(seq 1 24); do
  sleep 5
  _code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${PORT}" 2>/dev/null || echo "000")
  echo "[e2e] waiting for server... attempt ${_i}/24 (HTTP ${_code})"
  if [[ "$_code" =~ ^[2-4] ]]; then
    _ready=1
    break
  fi
done

if [ "$_ready" -eq 0 ]; then
  echo "[e2e] ERROR: Next.js server did not start within 120s"
  echo "[e2e] Last 20 lines of server log:"
  tail -20 /tmp/nextjs-$PORT.log 2>/dev/null || true
  kill "$NEXT_PID" 2>/dev/null
  exit 1
fi
echo "[e2e] server ready"

# ── Step 5: run the Playwright test ──────────────────────────────────────────
# Install Chromium on first use (cached in PLAYWRIGHT_BROWSERS_PATH)
echo "[e2e] installing/checking Chromium…"
npx --yes playwright install chromium 2>/dev/null

echo "[e2e] running Playwright test…"
npx --yes playwright test \
  --config "$SCRIPT_DIR/playwright.config.ts" \
  2>&1
_pw_exit=$?

# ── Step 6: cleanup ───────────────────────────────────────────────────────────
kill "$NEXT_PID" 2>/dev/null
wait "$NEXT_PID" 2>/dev/null || true

exit $_pw_exit
