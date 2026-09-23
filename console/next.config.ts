import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  serverExternalPackages: ["pg"],
  // Self-contained server for the container image: .next/standalone carries only the files it needs.
  output: "standalone",
};

export default nextConfig;
