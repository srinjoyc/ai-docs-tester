#!/usr/bin/env bash
# Clean ZeroDev docs-discovery scaffold.
#
# This intentionally starts as a generic wagmi EOA mint app:
#   - no @zerodev packages in package.json or node_modules
#   - no BUNDLER_URL/PAYMASTER_URL/ZERODEV_PROJECT_ID constants
#   - no ZeroDev comments or API hints in app source
#
# The use case prompt and target docs must teach the agent the ZeroDev-specific
# package names, config shape, and sponsored smart-account flow.
set -euo pipefail
WORK_DIR="$(cd "$1" && pwd)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$WORK_DIR"

bash "$SCRIPT_DIR/../shared/rainbowkit-base.sh" "$WORK_DIR"

cat > "$WORK_DIR/package.json" <<'EOF'
{
  "name": "dapp-starter",
  "version": "0.0.0",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "typecheck": "tsc --noEmit",
    "start": "next start"
  },
  "dependencies": {
    "@tanstack/react-query": "latest",
    "next": "^15",
    "react": "latest",
    "react-dom": "latest",
    "viem": "latest",
    "wagmi": "latest"
  },
  "devDependencies": {
    "@playwright/test": "latest",
    "@types/node": "latest",
    "@types/react": "latest",
    "@types/react-dom": "latest",
    "typescript": "latest"
  }
}
EOF

cat > "$WORK_DIR/next.config.ts" <<'EOF'
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  env: {
    NFT_CONTRACT: process.env.NFT_CONTRACT ?? "0x34bE7f35132E97915633BC1fc020364EA5134863",
  },
};
export default nextConfig;
EOF

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

export const wagmiConfig = createConfig({
  chains: [arbitrumSepolia],
  transports: { [arbitrumSepolia.id]: http() },
  connectors: [injected()],
});
EOF

CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/docs-eval/zerodev-clean-base-v1"
if [ ! -d "$CACHE_DIR/node_modules" ]; then
  mkdir -p "$CACHE_DIR"
  cp "$WORK_DIR/package.json" "$CACHE_DIR/package.json"
  (cd "$CACHE_DIR" && npm install --silent --no-audit --no-fund --legacy-peer-deps)
fi
ln -sf "$CACHE_DIR/node_modules" node_modules
