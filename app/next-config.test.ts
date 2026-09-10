import { describe, expect, it } from "vitest";
import nextConfig from "../next.config";

describe("self-hosted Next configuration", () => {
  it("builds a standalone server and does not define platform redirects", () => {
    expect(nextConfig.output).toBe("standalone");
    expect(nextConfig.agentRules).toBe(false);
    expect(nextConfig.redirects).toBeUndefined();
    expect(nextConfig.headers).toBeUndefined();
  });
});
