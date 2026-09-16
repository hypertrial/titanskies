import { describe, expect, it, vi } from "vitest";
import nextConfig from "../next.config";

describe("self-hosted Next configuration", () => {
  it("builds a standalone server and does not define platform redirects", () => {
    expect(nextConfig.output).toBe("standalone");
    expect(nextConfig.agentRules).toBe(false);
    expect(nextConfig.redirects).toBeUndefined();
    expect(nextConfig.headers).toBeUndefined();
  });

  it("lets Vercel produce its platform build output", async () => {
    vi.stubEnv("VERCEL", "1");
    vi.resetModules();

    const { default: vercelConfig } = await import("../next.config");

    expect(vercelConfig.output).toBeUndefined();
    vi.unstubAllEnvs();
  });
});
