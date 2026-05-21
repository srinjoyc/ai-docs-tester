import { test, expect } from "@playwright/test";

const MOCK_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266";
// Unique prefix so the test can find it on the page
const FAKE_USEROP_HASH =
  "0xefefefef00000000000000000000000000000000000000000000000000000000";
const FAKE_PAYMASTER = "0xDFF7FA1077Bce740a6a212b3995990682c0Ba66d";

// ─── Mock all outbound network calls ─────────────────────────────────────────
// We intercept every POST and check whether it looks like Ethereum JSON-RPC.
// - ZeroDev bundler/paymaster methods → return canned responses
// - Standard eth_* calls → proxy to a free public Sepolia RPC
// - Everything else (assets, fonts, …) → pass through unchanged
async function setupNetworkMocks(page: import("@playwright/test").Page) {
  await page.route("**", async (route) => {
    const req = route.request();

    // Only care about POST requests
    if (req.method() !== "POST") return route.continue();

    let body: Record<string, unknown>;
    try {
      const raw = req.postData();
      if (!raw) return route.continue();
      body = JSON.parse(raw);
    } catch {
      return route.continue();
    }

    const method = body.method as string | undefined;
    if (!method) return route.continue();

    const id = body.id;

    switch (method) {
      // ── Gas estimation (EntryPoint v0.6 + v0.7) ─────────────────────────
      case "eth_estimateUserOperationGas":
        return route.fulfill({
          json: {
            jsonrpc: "2.0",
            id,
            result: {
              callGasLimit: "0x493e0",
              preVerificationGas: "0xc57c",
              verificationGasLimit: "0x493e0",
              paymasterVerificationGasLimit: "0x493e0",
              paymasterPostOpGasLimit: "0x493e0",
            },
          },
        });

      // ── Paymaster sponsorship (v0.6) ─────────────────────────────────────
      case "pm_sponsorUserOperation":
        return route.fulfill({
          json: {
            jsonrpc: "2.0",
            id,
            result: {
              paymasterAndData: FAKE_PAYMASTER + "00".repeat(64),
              callGasLimit: "0x493e0",
              preVerificationGas: "0xc57c",
              verificationGasLimit: "0x493e0",
            },
          },
        });

      // ── Paymaster stub + final data (EntryPoint v0.7) ────────────────────
      case "pm_getPaymasterStubData":
        return route.fulfill({
          json: {
            jsonrpc: "2.0",
            id,
            result: {
              paymaster: FAKE_PAYMASTER,
              paymasterData: "0x" + "00".repeat(32),
              paymasterVerificationGasLimit: "0x493e0",
              paymasterPostOpGasLimit: "0x493e0",
              sponsor: { name: "ZeroDev Test Sponsor" },
            },
          },
        });

      case "pm_getPaymasterData":
        return route.fulfill({
          json: {
            jsonrpc: "2.0",
            id,
            result: {
              paymaster: FAKE_PAYMASTER,
              paymasterData: "0x" + "ab".repeat(32),
            },
          },
        });

      // ── Send userOp → return our unique fake hash ────────────────────────
      case "eth_sendUserOperation":
        return route.fulfill({
          json: { jsonrpc: "2.0", id, result: FAKE_USEROP_HASH },
        });

      // ── Receipt polling (return null = pending, we never wait for inclusion)
      case "eth_getUserOperationByHash":
      case "eth_getUserOperationReceipt":
        return route.fulfill({ json: { jsonrpc: "2.0", id, result: null } });

      // ── Standard ETH RPC → forward to free public Sepolia ───────────────
      default: {
        try {
          const r = await fetch("https://rpc.ankr.com/eth_sepolia", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: req.postData()!,
          });
          return route.fulfill({
            status: r.status,
            contentType: "application/json",
            body: await r.text(),
          });
        } catch {
          return route.continue();
        }
      }
    }
  });
}

// ─── Inject a mock window.ethereum (fake MetaMask) ───────────────────────────
async function injectMockWallet(page: import("@playwright/test").Page) {
  await page.addInitScript(`
    (() => {
      const address = '${MOCK_ADDRESS}';
      const listeners = {};

      const provider = {
        isMetaMask: true,
        isConnected: () => true,
        selectedAddress: address,
        chainId: '0xaa36a7',        // Sepolia
        networkVersion: '11155111',

        request: async ({ method, params }) => {
          switch (method) {
            case 'eth_accounts':
            case 'eth_requestAccounts':
              return [address];
            case 'eth_chainId':
              return '0xaa36a7';
            case 'net_version':
              return '11155111';
            case 'wallet_getCapabilities':
              return {};
            case 'wallet_switchEthereumChain':
            case 'wallet_addEthereumChain':
              return null;
            case 'personal_sign':
            case 'eth_sign':
              return '0x' + 'ab'.repeat(32) + '1b';
            case 'eth_signTypedData':
            case 'eth_signTypedData_v3':
            case 'eth_signTypedData_v4':
              // 65-byte fake ECDSA signature
              return '0x' + 'abcdef12'.repeat(8) + '1b';
            default:
              // Forward read-only ETH calls to public Sepolia
              try {
                const resp = await fetch('https://rpc.ankr.com/eth_sepolia', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params: params || [] }),
                });
                const data = await resp.json();
                if (data.error) throw new Error(data.error.message);
                return data.result;
              } catch (e) {
                console.warn('[mock-wallet] RPC fallback failed for', method, e);
                return null;
              }
          }
        },

        on(event, handler) {
          listeners[event] = listeners[event] || [];
          listeners[event].push(handler);
          return this;
        },
        removeListener(event, handler) { return this; },
        off(event, handler) { return this; },
        emit(event, ...args) {
          (listeners[event] || []).forEach(h => h(...args));
        },
      };

      Object.defineProperty(window, 'ethereum', {
        value: provider,
        writable: true,
        configurable: true,
      });
    })();
  `);
}

// ─── Tests ────────────────────────────────────────────────────────────────────

test("MintButton sends a UserOperation and shows the hash", async ({ page }) => {
  await setupNetworkMocks(page);
  await injectMockWallet(page);

  await page.goto("/");
  await page.waitForLoadState("domcontentloaded");

  // ── Connect wallet via RainbowKit ────────────────────────────────────────
  await page.getByRole("button", { name: /connect wallet/i }).click();

  // RainbowKit renders the injected provider as "MetaMask" when isMetaMask=true.
  // Try a few selectors to handle minor version differences.
  const walletPicker = page
    .getByRole("button", { name: /metamask/i })
    .or(page.getByText(/metamask/i).first())
    .or(page.getByRole("button").filter({ hasText: /browser wallet|injected/i }));
  await walletPicker.click({ timeout: 5000 });

  // The MintButton is gated on isConnected; wait for it to appear.
  await expect(
    page.getByRole("button", { name: /mint nft/i })
  ).toBeVisible({ timeout: 10000 });

  // ── Trigger the mint ─────────────────────────────────────────────────────
  await page.getByRole("button", { name: /mint nft/i }).click();

  // The agent's code should display the userOp hash somewhere on the page.
  // We look for the distinctive prefix of our fake hash.
  await expect(page.locator("text=0xefefefef")).toBeVisible({ timeout: 30000 });

  // ── Extra check for gas-sponsorship tasks ────────────────────────────────
  // Set REQUIRE_GAS_SPONSORED=true in the environment to enforce this.
  if (process.env.REQUIRE_GAS_SPONSORED === "true") {
    await expect(page.locator("text=gas sponsored")).toBeVisible({
      timeout: 5000,
    });
  }
});
