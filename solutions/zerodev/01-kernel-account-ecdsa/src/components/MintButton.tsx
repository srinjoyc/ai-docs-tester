import { useState } from "react";
import { useAccount, useWalletClient } from "wagmi";
import {
  createPublicClient,
  encodeFunctionData,
  http,
  type Account,
  type Chain,
  type Transport,
  type WalletClient,
} from "viem";
import { arbitrumSepolia } from "viem/chains";
import {
  createKernelAccount,
  createKernelAccountClient,
  createZeroDevPaymasterClient,
} from "@zerodev/sdk";
import { signerToEcdsaValidator } from "@zerodev/ecdsa-validator";
import { KERNEL_V3_1, getEntryPoint } from "@zerodev/sdk/constants";
import { NFT_CONTRACT, NFT_ABI } from "../lib/config";

const ZERODEV_PROJECT_ID = process.env.NEXT_PUBLIC_ZERODEV_PROJECT_ID ?? process.env.ZERODEV_PROJECT_ID ?? "";
const BUNDLER_URL = `https://rpc.zerodev.app/api/v3/${ZERODEV_PROJECT_ID}/chain/421614`;
const PAYMASTER_URL = `https://rpc.zerodev.app/api/v3/${ZERODEV_PROJECT_ID}/chain/421614`;

export function MintButton() {
  const { address } = useAccount();
  const { data: walletClient } = useWalletClient();
  const [isPending, setIsPending] = useState(false);
  const [txHash, setTxHash] = useState<`0x${string}` | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleMint = async () => {
    if (!address || !walletClient?.account) return;
    const signer = walletClient as WalletClient<Transport, Chain | undefined, Account>;

    setIsPending(true);
    setError(null);
    setTxHash(null);

    try {
      const entryPoint = getEntryPoint("0.7");
      const publicClient = createPublicClient({
        chain: arbitrumSepolia,
        transport: http(),
      });

      const ecdsaValidator = await signerToEcdsaValidator(publicClient, {
        signer,
        entryPoint,
        kernelVersion: KERNEL_V3_1,
      });

      const account = await createKernelAccount(publicClient, {
        plugins: { sudo: ecdsaValidator },
        entryPoint,
        kernelVersion: KERNEL_V3_1,
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

      const userOpHash = await kernelClient.sendUserOperation({
        callData: await account.encodeCalls([
          {
            to: NFT_CONTRACT,
            value: 0n,
            data: encodeFunctionData({ abi: NFT_ABI, functionName: "mint", args: [account.address] }),
          },
        ]),
      });

      const receipt = await kernelClient.waitForUserOperationReceipt({ hash: userOpHash });
      setTxHash(receipt.receipt.transactionHash);
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
      {txHash && <p>Minted! tx: {txHash}</p>}
      {error && <p>Error: {error}</p>}
    </div>
  );
}
