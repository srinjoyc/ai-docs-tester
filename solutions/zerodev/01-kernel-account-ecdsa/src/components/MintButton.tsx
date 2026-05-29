import { useState } from "react";
import { useAccount, useWalletClient } from "wagmi";
import {
  createPublicClient,
  encodeFunctionData,
  http,
  type Account,
  type Chain,
  type Hex,
  type Transport,
  type WalletClient,
} from "viem";
import { arbitrumSepolia } from "viem/chains";
import { signerToEcdsaValidator } from "@zerodev/ecdsa-validator";
import {
  createKernelAccount,
  createKernelAccountClient,
  createZeroDevPaymasterClient,
} from "@zerodev/sdk";
import { KERNEL_V3_1, getEntryPoint } from "@zerodev/sdk/constants";
import {
  BUNDLER_URL,
  NFT_ABI,
  NFT_CONTRACT,
  PAYMASTER_URL,
} from "../lib/config";

const entryPoint = getEntryPoint("0.7");
const kernelVersion = KERNEL_V3_1;

export function MintButton() {
  const { address } = useAccount();
  const { data: walletClient } = useWalletClient();
  const [isPending, setIsPending] = useState(false);
  const [hash, setHash] = useState<Hex | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleMint = async () => {
    if (!address || !walletClient?.account) return;
    // WalletClient from wagmi types account as Account | undefined; narrow it
    // so it satisfies ZeroDev's Signer = WalletClient<Transport, Chain|undefined, Account>
    const signer = walletClient as WalletClient<Transport, Chain | undefined, Account>;
    setIsPending(true);
    setError(null);
    setHash(null);
    try {
      const publicClient = createPublicClient({
        chain: arbitrumSepolia,
        transport: http(),
      });

      const ecdsaValidator = await signerToEcdsaValidator(publicClient, {
        signer,
        entryPoint,
        kernelVersion,
      });

      const account = await createKernelAccount(publicClient, {
        plugins: { sudo: ecdsaValidator },
        entryPoint,
        kernelVersion,
      });

      const paymasterClient = createZeroDevPaymasterClient({
        chain: arbitrumSepolia,
        transport: http(PAYMASTER_URL),
      });

      const kernelClient = createKernelAccountClient({
        account,
        chain: arbitrumSepolia,
        bundlerTransport: http(BUNDLER_URL),
        paymaster: paymasterClient,
      });

      const callData = await account.encodeCalls([
        {
          to: NFT_CONTRACT,
          value: 0n,
          data: encodeFunctionData({
            abi: NFT_ABI,
            functionName: "mint",
            args: [account.address],
          }),
        },
      ]);

      const userOpHash = await kernelClient.sendUserOperation({ callData });
      const receipt = await kernelClient.waitForUserOperationReceipt({
        hash: userOpHash,
      });
      setHash(receipt.receipt.transactionHash);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setIsPending(false);
    }
  };

  return (
    <div>
      <button onClick={handleMint} disabled={isPending || !walletClient}>
        {isPending ? "Minting…" : "Mint NFT"}
      </button>
      {hash && <p>Minted! tx: {hash}</p>}
      {error && <p>Error: {error}</p>}
    </div>
  );
}
