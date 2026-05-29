#!/usr/bin/env bash
# Scaffolds the RainbowKit starter, then adds Privy-specific config + packages.
#
# Starting point for all Privy use cases:
#   - Working RainbowKit + wagmi EOA mint app (shared base)
#   - @privy-io/react-auth installed
#   - src/lib/config.ts includes PRIVY_APP_ID alongside the common wagmi config
#
# Use cases upgrade this app to use Privy for auth and/or embedded wallets.
set -euo pipefail
WORK_DIR="$(cd "$1" && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$WORK_DIR"

bash "$SCRIPT_DIR/../shared/rainbowkit-base.sh" "$WORK_DIR"

# -- src/lib/config.ts (Privy-specific) ----------------------------------------
cat > src/lib/config.ts <<'EOF'
import { http } from "viem";
import { sepolia } from "wagmi/chains";
import { getDefaultConfig } from "@rainbow-me/rainbowkit";

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

export const wagmiConfig = getDefaultConfig({
  appName: "Mint App",
  projectId: process.env.WALLETCONNECT_PROJECT_ID ?? "demo",
  chains: [sepolia],
  transports: { [sepolia.id]: http() },
});

export const PRIVY_APP_ID = process.env.PRIVY_APP_ID ?? "clxxxxxxxxxxxxxxxx";
export const SPONSORSHIP_POLICY_ID = process.env.SPONSORSHIP_POLICY_ID ?? "";
EOF

# -- install dependencies (cached) --------------------------------------------
CACHE_DIR="/tmp/docs-eval-cache/privy-base-v4"
if [ ! -d "$CACHE_DIR/node_modules" ]; then
  mkdir -p "$CACHE_DIR"
  (cd "$CACHE_DIR" && npm install --silent --no-audit --no-fund --legacy-peer-deps \
    @rainbow-me/rainbowkit@latest \
    wagmi@latest \
    viem@latest \
    @tanstack/react-query@latest \
    @privy-io/react-auth@latest \
    next@latest \
    react@latest \
    react-dom@latest \
    @types/react@latest \
    @types/react-dom@latest \
    @types/node@latest \
    typescript@latest)
fi
ln -sf "$CACHE_DIR/node_modules" node_modules
