import { describe, expect, it } from "vitest";

import { healthIssueLabel } from "./SourceStatusPanel";

describe("healthIssueLabel", () => {
  it("explains publication asset-integrity failures", () => {
    expect(healthIssueLabel("invalid-assets")).toBe("One or more published data assets are missing or invalid.");
    expect(healthIssueLabel("future-issue")).toBe("An automatic data-health check reported a problem.");
  });
});
