import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  env: {
    NFT_CONTRACT: process.env.NFT_CONTRACT ?? "0x34bE7f35132E97915633BC1fc020364EA5134863",
    ZERODEV_PROJECT_ID: process.env.ZERODEV_PROJECT_ID ?? "",
    BUNDLER_URL: process.env.BUNDLER_URL ?? "",
    PAYMASTER_URL: process.env.PAYMASTER_URL ?? "",
  },
};
export default nextConfig;
