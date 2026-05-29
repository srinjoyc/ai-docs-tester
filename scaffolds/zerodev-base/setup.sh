#!/usr/bin/env bash
# Scaffolds the RainbowKit starter, then adds ZeroDev-specific config + packages.
#
# Starting point for all ZeroDev use cases:
#   - Working RainbowKit + wagmi EOA mint app (shared base)
#   - @zerodev/sdk, @zerodev/ecdsa-validator, permissionless, etc. installed
#   - src/lib/config.ts includes ZeroDev config keys, but no working project or
#     paymaster values by default. Agents should ask for the missing ZeroDev
#     configuration instead of inventing it.
#
# Use cases upgrade MintButton to use a ZeroDev Kernel smart account on top of
# the connected EOA, sending the mint as a UserOperation.
set -euo pipefail
WORK_DIR="$(cd "$1" && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$WORK_DIR"

bash "$SCRIPT_DIR/../shared/rainbowkit-base.sh" "$WORK_DIR"

# -- next.config.ts (override to bake in NFT_CONTRACT default) ----------------
# rainbowkit-base.sh sets NFT_CONTRACT to "" when the env var isn't set.
# The null-coalescing ?? in config.ts never fires on "" (only on null/undefined),
# so Next.js would inline "" and the SDK would throw "Missing to address".
# Overwrite with the actual contract address as the default.
cat > "$WORK_DIR/next.config.ts" <<'EOF'
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  env: {
    BUNDLER_URL: process.env.BUNDLER_URL ?? "",
    PAYMASTER_URL: process.env.PAYMASTER_URL ?? "",
    ZERODEV_PROJECT_ID: process.env.ZERODEV_PROJECT_ID ?? "",
    SMART_ROUTING_SERVER_URL: process.env.SMART_ROUTING_SERVER_URL ?? "",
    NFT_CONTRACT: process.env.NFT_CONTRACT ?? "0x34bE7f35132E97915633BC1fc020364EA5134863",
  },
};
export default nextConfig;
EOF

# -- src/lib/config.ts (ZeroDev-specific) -------------------------------------
cat > src/lib/config.ts <<'EOF'
import { http } from "viem";
import { arbitrumSepolia } from "viem/chains";
import { createConfig, injected } from "wagmi";

// NFT contract with a public mint() function deployed on Arbitrum Sepolia
export const NFT_CONTRACT = (
  process.env.NFT_CONTRACT ?? "0x34bE7f35132E97915633BC1fc020364EA5134863"
) as `0x${string}`;

export const NFT_ABI = [
  {
    name: "mint",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [{ name: "to", type: "address" }],
    outputs: [],
  },
] as const;

// Use the plain injected() connector — works with any window.ethereum provider
// (avoids optional peer dep requirements of MetaMask/Coinbase/WalletConnect connectors)
export const wagmiConfig = createConfig({
  chains: [arbitrumSepolia],
  transports: { [arbitrumSepolia.id]: http() },
  connectors: [injected()],
});

// Intentionally empty by default. A real sponsored ZeroDev flow needs project
// and paymaster/bundler configuration from the developer's ZeroDev dashboard.
// Agents should ask for these values if the task needs live sponsorship.
export const ZERODEV_PROJECT_ID = process.env.ZERODEV_PROJECT_ID ?? "";
export const BUNDLER_URL = process.env.BUNDLER_URL ?? "";
export const PAYMASTER_URL = process.env.PAYMASTER_URL ?? "";
export const SMART_ROUTING_SERVER_URL = process.env.SMART_ROUTING_SERVER_URL ?? "";
EOF

# -- install dependencies (cached) --------------------------------------------
# Avoid /tmp — on macOS /tmp is a symlink to /private/tmp, which causes
# Node.js to find two React module cache entries and break SSR hooks.
# Use XDG_CACHE_HOME if set (Linux standard), else ~/.cache (works everywhere).
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/docs-eval/zerodev-base-v9"
if [ ! -d "$CACHE_DIR/node_modules" ]; then
  mkdir -p "$CACHE_DIR"
  # next@15 pinned: Next.js 16 uses Turbopack by default which rejects symlinks
  # pointing outside the project root (a known regression in Next.js 16).
  # accounts@~0.12 is a required peer dep of @wagmi/core when using wagmi@3.
  (cd "$CACHE_DIR" && npm install --silent --no-audit --no-fund --legacy-peer-deps \
    @rainbow-me/rainbowkit@latest \
    wagmi@latest \
    viem@latest \
    @tanstack/react-query@latest \
    @zerodev/sdk@latest \
    @zerodev/ecdsa-validator@latest \
    @zerodev/permissions@latest \
    @zerodev/passkey-validator@latest \
    @zerodev/session-key@latest \
    @zerodev/smart-routing-address@latest \
    permissionless@latest \
    "next@^15" \
    react@latest \
    react-dom@latest \
    @types/react@latest \
    @types/react-dom@latest \
    @types/node@latest \
    typescript@latest \
    @playwright/test@latest \
    "accounts@~0.12" \
    @metamask/connect-evm@latest)
fi
ln -sf "$CACHE_DIR/node_modules" node_modules
