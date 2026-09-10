import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  agentRules: false,
  poweredByHeader: false,
  output: "standalone",
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
  env: {
    NEXT_PUBLIC_PERF_DIAGNOSTICS: process.env.NEXT_PUBLIC_PERF_DIAGNOSTICS ?? "0",
  },
  transpilePackages: ["three", "@react-three/fiber", "@react-three/drei"],
  allowedDevOrigins: ["127.0.0.1", "localhost"],
};

export default nextConfig;
