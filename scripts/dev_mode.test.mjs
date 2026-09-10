import { describe, expect, it } from "vitest";
import { devConfiguration } from "./dev_mode.mjs";

describe("local data modes", () => {
  it("uses an existing local publication by default", () => {
    const config = devConfiguration([], {});
    expect(config.mode).toBe("local");
    expect(config.watch).toBe(false);
    expect(config.env.NEXT_PUBLIC_CONTEXT_URL).toBe("/api/context-data");
    expect(config.env.TITANSKIES_DATA_DIR).toBe(".local/data");
  });

  it("keeps demo network-free and live ingest explicit", () => {
    const demo = devConfiguration(["--demo-data"], {});
    expect(demo.env.CONTEXT_SOURCE).toBe("demo");
    expect(demo.env.NEXT_PUBLIC_CONTEXT_URL).toBe("/demo/context/latest.json");
    expect(demo.watch).toBe(false);
    const live = devConfiguration(["--local-ingest"], {});
    expect(live.env.CONTEXT_SOURCE).toBe("live");
    expect(live.env.NEXT_PUBLIC_CONTEXT_URL).toBe("/api/context-data");
    expect(live.watch).toBe(true);
  });
});
