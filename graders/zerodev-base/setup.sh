#!/usr/bin/env bash
# Scaffolds the RainbowKit starter, then adds ZeroDev-specific config + packages.
#
# Starting point for all ZeroDev use cases:
#   - Working RainbowKit + wagmi EOA mint app (shared base)
#   - @zerodev/sdk, @zerodev/ecdsa-validator, permissionless, etc. installed
#   - src/lib/config.ts includes ZERODEV_PROJECT_ID, BUNDLER_URL, PAYMASTER_URL
#
# Use cases upgrade MintButton to use a ZeroDev Kernel smart account on top of
# the connected EOA, sending the mint as a UserOperation.
set -euo pipefail
WORK_DIR="$(cd "$1" && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$WORK_DIR"

bash "$SCRIPT_DIR/../shared/rainbowkit-base.sh" "$WORK_DIR"

# -- src/lib/config.ts (ZeroDev-specific) -------------------------------------
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

export const ZERODEV_PROJECT_ID = process.env.ZERODEV_PROJECT_ID ?? "";
export const BUNDLER_URL = `https://rpc.zerodev.app/api/v2/bundler/${ZERODEV_PROJECT_ID}`;
export const PAYMASTER_URL = `https://rpc.zerodev.app/api/v2/paymaster/${ZERODEV_PROJECT_ID}`;
EOF

# -- install dependencies (cached) --------------------------------------------
CACHE_DIR="/tmp/docs-eval-cache/zerodev-base-v5"
if [ ! -d "$CACHE_DIR/node_modules" ]; then
  mkdir -p "$CACHE_DIR"
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
    permissionless@latest \
    next@latest \
    react@latest \
    react-dom@latest \
    @types/react@latest \
    @types/react-dom@latest \
    @types/node@latest \
    typescript@latest \
    @playwright/test@latest)
fi
ln -sf "$CACHE_DIR/node_modules" node_modules
