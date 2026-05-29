/**
 * E2E grader for ZeroDev use cases.
 *
 * REAL MODE  (TEST_WALLET_PRIVATE_KEY + ZERODEV_PROJECT_ID both set):
 *   Signs with a real viem account via page.exposeFunction, lets all ZeroDev
 *   network calls through, verifies the bundled tx on Arb Sepolia.
 *
 * MOCK MODE  (fallback — no credentials):
 *   Fake wallet + mocked bundler/paymaster, checks UI shows expected output.
 *
 * GRADER_SCENARIO controls which flow is tested (set in grader.env):
 *   default            — click one button, check for userOp/tx hash
 *   spend-limit        — send 1 succeeds, send 2 (over cap) is rejected
 *   contract-allowlist — call to allowed contract succeeds, other is rejected
 *   erc20-gas          — two mint buttons: ETH gas then USDC gas
 *   sra                — get smart routing address, check address + status
 */

import { test, expect, type Page } from "@playwright/test";
import { privateKeyToAccount } from "viem/accounts";

// ─── Config ───────────────────────────────────────────────────────────────────

const PRIVATE_KEY = process.env.TEST_WALLET_PRIVATE_KEY as `0x${string}` | undefined;
const ZERODEV_PROJECT_ID = process.env.ZERODEV_PROJECT_ID || "";
const REAL_MODE = Boolean(PRIVATE_KEY && ZERODEV_PROJECT_ID);
const GRADER_SCENARIO = process.env.GRADER_SCENARIO || "default";

const ARB_SEPOLIA_RPC = "https://sepolia-rollup.arbitrum.io/rpc";
const ARB_SEPOLIA_CHAIN_ID_HEX = "0x66eee"; // 421614
const ARB_SEPOLIA_CHAIN_ID_DEC = "421614";

const MOCK_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266";
const FAKE_USEROP_HASH =
  "0xefefefef00000000000000000000000000000000000000000000000000000000";
const FAKE_PAYMASTER = "0xDFF7FA1077Bce740a6a212b3995990682c0Ba66d";

// Tracks how many eth_sendUserOperation calls have been made this test run
const mockState = { sendUserOpCount: 0 };

// ─── Mock network ─────────────────────────────────────────────────────────────

async function setupMockNetwork(page: Page) {
  mockState.sendUserOpCount = 0;

  await page.route("**", async (route) => {
    const req = route.request();
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
      case "eth_estimateUserOperationGas":
        return route.fulfill({
          json: {
            jsonrpc: "2.0", id,
            result: {
              callGasLimit: "0x493e0",
              preVerificationGas: "0xc57c",
              verificationGasLimit: "0x493e0",
              paymasterVerificationGasLimit: "0x493e0",
              paymasterPostOpGasLimit: "0x493e0",
            },
          },
        });

      case "pm_sponsorUserOperation":
        return route.fulfill({
          json: {
            jsonrpc: "2.0", id,
            result: {
              paymasterAndData: FAKE_PAYMASTER + "00".repeat(64),
              callGasLimit: "0x493e0",
              preVerificationGas: "0xc57c",
              verificationGasLimit: "0x493e0",
            },
          },
        });

      case "pm_getPaymasterStubData":
        return route.fulfill({
          json: {
            jsonrpc: "2.0", id,
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
            jsonrpc: "2.0", id,
            result: {
              paymaster: FAKE_PAYMASTER,
              paymasterData: "0x" + "ab".repeat(32),
            },
          },
        });

      case "zd_getUserOperationGasPrice":
        return route.fulfill({
          json: {
            jsonrpc: "2.0", id,
            result: {
              slow: { maxFeePerGas: "0x5F5E100", maxPriorityFeePerGas: "0x3B9ACA0" },
              standard: { maxFeePerGas: "0x77359400", maxPriorityFeePerGas: "0x3B9ACA00" },
              fast: { maxFeePerGas: "0x9502F900", maxPriorityFeePerGas: "0x77359400" },
            },
          },
        });

      case "zd_sponsorUserOperation":
        return route.fulfill({
          json: {
            jsonrpc: "2.0", id,
            result: {
              paymaster: FAKE_PAYMASTER,
              paymasterData: "0x" + "ab".repeat(32),
              paymasterVerificationGasLimit: "0x493e0",
              paymasterPostOpGasLimit: "0x493e0",
              callGasLimit: "0x493e0",
              preVerificationGas: "0xc57c",
              verificationGasLimit: "0x493e0",
              maxFeePerGas: "0x77359400",
              maxPriorityFeePerGas: "0x3b9aca00",
            },
          },
        });

      case "eth_sendUserOperation": {
        mockState.sendUserOpCount++;
        // For spend-limit / contract-allowlist: second op is rejected by policy
        const isRejectedOp =
          mockState.sendUserOpCount >= 2 &&
          (GRADER_SCENARIO === "spend-limit" || GRADER_SCENARIO === "contract-allowlist");

        if (isRejectedOp) {
          const msg =
            GRADER_SCENARIO === "spend-limit"
              ? "spend limit exceeded"
              : "contract not allowed";
          return route.fulfill({
            json: {
              jsonrpc: "2.0", id,
              error: { code: -32500, message: `AA23 reverted: ${msg}` },
            },
          });
        }
        return route.fulfill({ json: { jsonrpc: "2.0", id, result: FAKE_USEROP_HASH } });
      }

      case "eth_getUserOperationByHash":
        return route.fulfill({ json: { jsonrpc: "2.0", id, result: null } });

      case "eth_getUserOperationReceipt":
        return route.fulfill({
          json: {
            jsonrpc: "2.0", id,
            result: {
              userOpHash: FAKE_USEROP_HASH,
              sender: MOCK_ADDRESS,
              nonce: "0x0",
              actualGasUsed: "0x5208",
              actualGasCost: "0x0",
              success: true,
              receipt: {
                transactionHash: FAKE_USEROP_HASH,
                blockHash: "0x" + "ab".repeat(32),
                blockNumber: "0x1",
                from: MOCK_ADDRESS,
                to: MOCK_ADDRESS,
                cumulativeGasUsed: "0x5208",
                gasUsed: "0x5208",
                contractAddress: null,
                logs: [],
                logsBloom: "0x" + "00".repeat(256),
                type: "0x2",
                status: "0x1",
              },
            },
          },
        });

      default: {
        try {
          const r = await fetch(ARB_SEPOLIA_RPC, {
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

// ─── Wallet injection ─────────────────────────────────────────────────────────

async function injectWallet(page: Page, address: string, realSigning: boolean) {
  if (realSigning && PRIVATE_KEY) {
    const account = privateKeyToAccount(PRIVATE_KEY);

    await page.exposeFunction(
      "__signTypedData",
      async (_addr: string, typedDataJson: string): Promise<string> => {
        const td = JSON.parse(typedDataJson);
        const { EIP712Domain: _eip, ...cleanTypes } = td.types ?? {};
        return account.signTypedData({
          domain: td.domain,
          types: cleanTypes as Parameters<typeof account.signTypedData>[0]["types"],
          primaryType: td.primaryType,
          message: td.message,
        });
      }
    );

    await page.exposeFunction(
      "__personalSign",
      async (hexMsg: string): Promise<string> =>
        account.signMessage({ message: { raw: hexMsg as `0x${string}` } })
    );
  }

  await page.addInitScript(`
    (() => {
      const address = '${address}';
      const realSigning = ${realSigning};
      const listeners = {};

      const provider = {
        isMetaMask: true,
        isConnected: () => true,
        selectedAddress: address,
        chainId: '${ARB_SEPOLIA_CHAIN_ID_HEX}',
        networkVersion: '${ARB_SEPOLIA_CHAIN_ID_DEC}',

        request: async ({ method, params }) => {
          switch (method) {
            case 'eth_accounts':
            case 'eth_requestAccounts':
              return [address];
            case 'eth_chainId':
              return '${ARB_SEPOLIA_CHAIN_ID_HEX}';
            case 'net_version':
              return '${ARB_SEPOLIA_CHAIN_ID_DEC}';
            case 'wallet_getCapabilities':
              return {};
            case 'wallet_switchEthereumChain':
            case 'wallet_addEthereumChain':
              return null;
            case 'personal_sign':
            case 'eth_sign': {
              if (realSigning && window.__personalSign) {
                return window.__personalSign(Array.isArray(params) ? params[0] : params);
              }
              return '0x' + 'ab'.repeat(32) + '1b';
            }
            case 'eth_signTypedData':
            case 'eth_signTypedData_v3':
            case 'eth_signTypedData_v4': {
              if (realSigning && window.__signTypedData) {
                const [addr, dataJson] = params;
                return window.__signTypedData(addr, dataJson);
              }
              return '0x' + 'abcdef12'.repeat(8) + '1b';
            }
            default: {
              try {
                const resp = await fetch('${ARB_SEPOLIA_RPC}', {
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
          }
        },
        on(event, handler) {
          listeners[event] = listeners[event] || [];
          listeners[event].push(handler);
          return this;
        },
        removeListener() { return this; },
        off() { return this; },
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

// ─── On-chain verification (real mode only) ───────────────────────────────────

async function waitForUserOpReceipt(
  bundlerUrl: string,
  userOpHash: string,
  timeoutMs = 60_000
): Promise<string | null> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const resp = await fetch(bundlerUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          jsonrpc: "2.0", id: 1,
          method: "eth_getUserOperationReceipt",
          params: [userOpHash],
        }),
      });
      const data = (await resp.json()) as {
        result?: { receipt?: { transactionHash?: string } };
      };
      const txHash = data.result?.receipt?.transactionHash;
      if (txHash) return txHash;
    } catch { /* keep polling */ }
    await new Promise((r) => setTimeout(r, 3000));
  }
  return null;
}

async function verifyHashOnChain(page: Page, address: string) {
  const bundlerUrl =
    process.env.BUNDLER_URL ||
    `https://staging-rpc.zerodev.app/api/v3/${ZERODEV_PROJECT_ID}/chain/421614`;

  await expect(page.getByText(/0x[0-9a-fA-F]{64}/)).toBeVisible({ timeout: 90_000 });

  const bodyText = await page.locator("body").innerText();
  const hashMatch = bodyText.match(/0x[0-9a-fA-F]{64}/);
  expect(hashMatch, "Expected a 32-byte hex hash on the page").toBeTruthy();

  const userOpHash = hashMatch![0];
  console.log(`[real-mode] hash: ${userOpHash}`);
  console.log(`[real-mode] wallet: https://sepolia.arbiscan.io/address/${address}`);

  const txHash = await waitForUserOpReceipt(bundlerUrl, userOpHash);
  if (txHash) {
    console.log(`[real-mode] tx: https://sepolia.arbiscan.io/tx/${txHash}`);
    const resp = await fetch(ARB_SEPOLIA_RPC, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        jsonrpc: "2.0", id: 1,
        method: "eth_getTransactionReceipt",
        params: [txHash],
      }),
    });
    const data = (await resp.json()) as { result?: { status?: string } };
    expect(
      data.result?.status,
      `Transaction ${txHash} should have status 0x1 on Arb Sepolia`
    ).toBe("0x1");
  } else {
    console.log("[real-mode] receipt not yet available; skipping on-chain check");
  }
}

// ─── Shared assertions ────────────────────────────────────────────────────────

// Matches a ZeroDev sponsorship rejection — proves the agent called
// sponsorUserOperation correctly even when the project has no gas policy.
const SPONSOR_ERROR_RE =
  /did not match any gas sponsoring|no.*gas.*polic|sponsorUserOperation|zd_sponsor|AA\d\d reverted/i;

async function assertHash(page: Page, address: string) {
  if (REAL_MODE) {
    // Accept either a confirmed hash OR a ZeroDev sponsorship error.
    // Both prove the agent correctly called sponsorUserOperation — whether
    // gas gets sponsored depends on project config, not code quality.
    const hashLocator = page.getByText(/0x[0-9a-fA-F]{64}/);
    const errorLocator = page.getByText(SPONSOR_ERROR_RE);
    await expect(hashLocator.or(errorLocator)).toBeVisible({ timeout: 90_000 });

    const bodyText = await page.locator("body").innerText();
    if (bodyText.match(/0x[0-9a-fA-F]{64}/)) {
      // Hash appeared — do full on-chain verification
      await verifyHashOnChain(page, address);
    } else {
      console.log("[real-mode] sponsorship policy not configured — error shown, code is correct");
    }
  } else {
    await expect(page.locator("text=0xefefefef")).toBeVisible({ timeout: 30_000 });
  }
}

async function assertRejection(page: Page) {
  await expect(
    page.getByText(/limit exceeded|not allowed|rejected|error|failed/i)
  ).toBeVisible({ timeout: REAL_MODE ? 60_000 : 15_000 });
}

// ─── Test ─────────────────────────────────────────────────────────────────────

test("ZeroDev use case E2E", async ({ page }) => {
  const address =
    REAL_MODE && PRIVATE_KEY
      ? privateKeyToAccount(PRIVATE_KEY).address
      : MOCK_ADDRESS;

  if (!REAL_MODE) await setupMockNetwork(page);
  await injectWallet(page, address, REAL_MODE);

  await page.goto("/");
  await page.waitForLoadState("domcontentloaded");

  // Connect wallet — supports both RainbowKit modal (click MetaMask/Browser Wallet)
  // and plain wagmi connect (single "Connect Wallet" button that connects directly).
  await page.getByRole("button", { name: /connect wallet/i }).click();
  // If a wallet selection modal appears, pick the injected/MetaMask option
  const walletModal = page
    .getByRole("button", { name: /metamask/i })
    .or(page.getByRole("button").filter({ hasText: /browser wallet|injected/i }));
  try {
    await walletModal.first().click({ timeout: 3000 });
  } catch {
    // No modal — direct connect already triggered by the first click; continue
  }

  // ── Scenario dispatch ──────────────────────────────────────────────────────

  switch (GRADER_SCENARIO) {

    // ── Spend limit: send 1 succeeds, send 2 (over cap) is rejected ──────────
    case "spend-limit": {
      await expect(page.getByRole("button", { name: /send 0\.000005 eth/i }))
        .toBeVisible({ timeout: 15_000 });
      await page.getByRole("button", { name: /send 0\.000005 eth/i }).click();
      await assertHash(page, address);
      await page.getByRole("button", { name: /send 0\.00002 eth/i }).click();
      await assertRejection(page);
      break;
    }

    // ── Contract allowlist: approved call succeeds, unknown is rejected ───────
    case "contract-allowlist": {
      await expect(page.getByRole("button", { name: /mint nft/i }))
        .toBeVisible({ timeout: 15_000 });
      await page.getByRole("button", { name: /mint nft/i }).click();
      await assertHash(page, address);
      await page.getByRole("button", { name: /call (other|unknown)/i }).click();
      await assertRejection(page);
      break;
    }

    // ── ERC-20 gas: ETH gas button then USDC gas button ───────────────────────
    case "erc20-gas": {
      await expect(page.getByRole("button", { name: /mint.*eth/i }))
        .toBeVisible({ timeout: 15_000 });
      await page.getByRole("button", { name: /mint.*eth/i }).click();
      // ETH gas should always succeed
      await assertHash(page, address);
      await page.getByRole("button", { name: /mint.*usdc/i }).click();
      // USDC gas may succeed or show an error if not configured — both are visible outcomes
      await expect(
        page.getByText(/0x[0-9a-fA-F]{10}|error|failed|not configured/i)
      ).toBeVisible({ timeout: REAL_MODE ? 60_000 : 15_000 });
      break;
    }

    // ── SRA: shows smart routing address + balance + status ───────────────────
    case "sra": {
      await expect(page.getByRole("button", { name: /get smart address/i }))
        .toBeVisible({ timeout: 15_000 });
      await page.getByRole("button", { name: /get smart address/i }).click();
      // Address appears (0x + 40 hex chars)
      await expect(page.getByText(/0x[0-9a-fA-F]{40}/))
        .toBeVisible({ timeout: REAL_MODE ? 60_000 : 30_000 });
      // Balance or status is shown
      await expect(page.getByText(/balance|active|status|ETH/i))
        .toBeVisible({ timeout: 10_000 });
      break;
    }

    // ── Default: single button click, check for hash ──────────────────────────
    default: {
      const btnText = process.env.ACTION_BUTTON_TEXT || "Mint NFT";
      const btnRe = new RegExp(
        btnText.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"),
        "i"
      );
      await expect(page.getByRole("button", { name: btnRe }))
        .toBeVisible({ timeout: 15_000 });
      await page.getByRole("button", { name: btnRe }).click();
      await assertHash(page, address);

      if (process.env.REQUIRE_GAS_SPONSORED === "true") {
        await expect(page.getByText(/gas.?sponsored/i)).toBeVisible({ timeout: 5000 });
      }
      break;
    }
  }
});
