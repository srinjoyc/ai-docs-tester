#!/usr/bin/env bash
# Scaffolds a Privy+ZeroDev integration starter app.
#
# Structure mirrors privy-base but also installs ZeroDev packages and adds
# a lib/kernelAccount.ts stub for the agent to implement.
#
# The scenario: Privy handles auth + embedded wallet (the EOA signer),
# ZeroDev wraps it in a Kernel smart account for AA features.
set -euo pipefail
WORK_DIR="$1"
cd "$WORK_DIR"

# -- package.json -------------------------------------------------------------
cat > package.json <<'PKGJSON'
{
  "name": "privy-zerodev-mint-app",
  "version": "0.0.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite --port 5173"
  }
}
PKGJSON

# -- tsconfig.json ------------------------------------------------------------
cat > tsconfig.json <<'TSCJSON'
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "noEmit": true,
    "allowImportingTsExtensions": false,
    "types": ["node"]
  },
  "include": ["src/**/*"]
}
TSCJSON

# -- directory structure ------------------------------------------------------
mkdir -p src/components src/lib

# -- src/lib/privyConfig.ts ---------------------------------------------------
cat > src/lib/privyConfig.ts <<'CONFIGTS'
export const PRIVY_APP_ID = process.env.PRIVY_APP_ID ?? "clxxxxxxxxxxxxxxxx";
export const ZERODEV_PROJECT_ID = process.env.ZERODEV_PROJECT_ID ?? "";

export const NFT_CONTRACT = (
  process.env.NFT_CONTRACT ?? "0x34bE7f35132E97915633BC1fc020364EA5134863"
) as `0x${string}`;

export const BUNDLER_URL =
  process.env.BUNDLER_URL ??
  `https://staging-rpc.zerodev.app/api/v3/${ZERODEV_PROJECT_ID}/chain/421614`;
export const PAYMASTER_URL =
  process.env.PAYMASTER_URL ??
  `https://staging-rpc.zerodev.app/api/v3/${ZERODEV_PROJECT_ID}/chain/421614`;
CONFIGTS

# -- src/lib/kernelAccount.ts -------------------------------------------------
cat > src/lib/kernelAccount.ts <<'KERNELTS'
// TODO: implement getKernelClientFromPrivy(walletClient).
//
// This module bridges Privy's embedded wallet into a ZeroDev Kernel smart account.
//
// Pattern:
//   import { createKernelAccount, createKernelAccountClient } from "@zerodev/sdk";
//   import { KERNEL_V3_3, getEntryPoint } from "@zerodev/sdk/constants";
//   import { signerToEcdsaValidator } from "@zerodev/ecdsa-validator";
//   import { walletClientToSmartAccountSigner } from "permissionless";
//   import { http, createPublicClient } from "viem";
//   import { sepolia } from "viem/chains";
//
//   export async function getKernelClientFromPrivy(walletClient) {
//     const signer = walletClientToSmartAccountSigner(walletClient);
//     const publicClient = createPublicClient({ chain: sepolia, transport: http() });
//     const entryPoint = getEntryPoint("0.7");
//     const kernelVersion = KERNEL_V3_3;
//     const ecdsaValidator = await signerToEcdsaValidator(publicClient, {
//       signer, entryPoint, kernelVersion,
//     });
//     const account = await createKernelAccount(publicClient, {
//       plugins: { sudo: ecdsaValidator },
//       entryPoint,
//       kernelVersion,
//     });
//     return createKernelAccountClient({
//       account, chain: sepolia, bundlerTransport: http(BUNDLER_URL),
//     });
//   }

export {};
KERNELTS

# -- src/components/LoginButton.tsx -------------------------------------------
cat > src/components/LoginButton.tsx <<'LOGINBTN'
import React from "react";
import { usePrivy } from "@privy-io/react-auth";

export function LoginButton(): React.ReactElement {
  const { ready, authenticated, login, logout } = usePrivy();
  if (!ready) return <span>Loading…</span>;
  return authenticated ? (
    <button onClick={logout}>Log out</button>
  ) : (
    <button onClick={login}>Sign in with Privy</button>
  );
}
LOGINBTN

# -- src/components/MintButton.tsx --------------------------------------------
cat > src/components/MintButton.tsx <<'MINTBTN'
import React, { useState } from "react";
// TODO: implement mint using ZeroDev Kernel with the Privy embedded wallet as signer.
//
// Steps:
//   1. useWallets() to get the embedded wallet.
//   2. wallet.getEthereumProvider() -> createWalletClient (viem) -> walletClientToSmartAccountSigner
//   3. getKernelClientFromPrivy(walletClient) from lib/kernelAccount.ts
//   4. kernelClient.sendUserOperation({ calls: [{ to: NFT_CONTRACT, data: encodedMint }] })

export function MintButton(): React.ReactElement {
  const [status, setStatus] = useState<string>("");

  const handleMint = async (): Promise<void> => {
    setStatus("Minting…");
    // TODO: implement
    setStatus("Not yet implemented");
  };

  return (
    <div>
      <button onClick={handleMint}>Mint NFT</button>
      {status && <p>{status}</p>}
    </div>
  );
}
MINTBTN

# -- src/App.tsx --------------------------------------------------------------
cat > src/App.tsx <<'APPTSX'
import React from "react";
import { usePrivy } from "@privy-io/react-auth";
import { LoginButton } from "./components/LoginButton.js";
import { MintButton } from "./components/MintButton.js";

export default function App(): React.ReactElement {
  const { ready, authenticated } = usePrivy();
  return (
    <div>
      <h1>Privy + ZeroDev Mint App</h1>
      <LoginButton />
      {ready && authenticated && <MintButton />}
    </div>
  );
}
APPTSX

# -- src/main.tsx -------------------------------------------------------------
cat > src/main.tsx <<'MAINTSX'
import React from "react";
import ReactDOM from "react-dom/client";
import { PrivyProvider } from "@privy-io/react-auth";
import App from "./App.js";
import { PRIVY_APP_ID } from "./lib/privyConfig.js";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <PrivyProvider appId={PRIVY_APP_ID} config={{
      embeddedWallets: { createOnLogin: "users-without-wallets" },
    }}>
      <App />
    </PrivyProvider>
  </React.StrictMode>
);
MAINTSX

# -- vite.config.ts -----------------------------------------------------------
cat > vite.config.ts <<'VITECFG'
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
});
VITECFG

# -- index.html (Vite entry point) --------------------------------------------
cat > index.html <<'INDEXHTML'
<!doctype html>
<html lang="en">
  <head><meta charset="UTF-8" /><title>Privy + ZeroDev Mint App</title></head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
INDEXHTML

# -- install dependencies (cached) --------------------------------------------
CACHE_DIR="/tmp/docs-eval-cache/privy-zerodev-base"
if [ ! -d "$CACHE_DIR/node_modules" ]; then
  mkdir -p "$CACHE_DIR"
  (cd "$CACHE_DIR" && npm install --silent --no-audit --no-fund \
    @privy-io/react-auth@latest \
    @zerodev/sdk@latest \
    @zerodev/ecdsa-validator@latest \
    @zerodev/permissions@latest \
    permissionless@latest \
    viem@latest \
    wagmi@latest \
    @tanstack/react-query@latest \
    react@latest \
    react-dom@latest \
    @types/react@latest \
    @types/react-dom@latest \
    @types/node@latest \
    typescript@latest \
    vite@latest \
    @vitejs/plugin-react@latest)
fi
ln -sf "$CACHE_DIR/node_modules" node_modules
