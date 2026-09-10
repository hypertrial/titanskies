import { describe, expect, it } from "vitest";
import { compactFreshnessLabel, formatCompactTimelineTime, formatExactInterfaceTime, formatMobileTimelineTime, userFacingLoadError } from "./interfacePresentation";

describe("interface presentation", () => {
  it("formats live, same-day, future-day, and invalid forecast times compactly", () => {
    const reference = new Date(2024, 6, 15, 12, 0).getTime();
    expect(formatCompactTimelineTime(new Date(2024, 6, 15, 13, 37).toISOString(), reference, true)).toMatch(/^Now · 1:37 PM \S+$/);
    expect(formatCompactTimelineTime(new Date(2024, 6, 15, 14, 5).toISOString(), reference, false)).toMatch(/^Today · 2:05 PM \S+$/);
    expect(formatCompactTimelineTime(new Date(2024, 6, 16, 9, 30).toISOString(), reference, false)).toMatch(/^Tue 16 · 9:30 AM \S+$/);
    expect(formatMobileTimelineTime(new Date(2024, 6, 15, 13, 37).toISOString(), reference)).toBe("1:37 PM");
    expect(formatMobileTimelineTime(new Date(2024, 6, 15, 14, 5).toISOString(), reference)).toBe("2:05 PM");
    expect(formatMobileTimelineTime(new Date(2024, 6, 16, 9, 30).toISOString(), reference)).toBe("Tue 9:30 AM");
    expect(formatExactInterfaceTime(new Date(2024, 6, 15, 13, 37).toISOString())).toMatch(/1:37 PM \S+$/);
    expect(formatCompactTimelineTime("", reference, false)).toBe("—");
    expect(formatMobileTimelineTime("", reference)).toBe("—");
    expect(formatExactInterfaceTime("")).toBe("Unavailable");
  });

  it("keeps compact freshness explicit across publication states", () => {
    const base = { mode: "live" as const, health: "healthy" as const, publication: "fresh" as const, source: "ok" as const, publicationAge: "8m ago" };
    expect(compactFreshnessLabel({ ...base, compact: false })).toBe("Updated 8m");
    expect(compactFreshnessLabel({ ...base, compact: true })).toBe("8m ago");
    expect(compactFreshnessLabel({ ...base, publication: "retained", compact: false })).toBe("Retained · 8m");
    expect(compactFreshnessLabel({ ...base, health: "degraded", compact: true })).toBe("Partial");
    expect(compactFreshnessLabel({ ...base, health: "unhealthy", compact: false })).toBe("Stale · 8m");
    expect(compactFreshnessLabel({ ...base, source: "stale", compact: true })).toBe("Stale");
    expect(compactFreshnessLabel({ ...base, source: "error", compact: false })).toBe("Partial · 8m");
    expect(compactFreshnessLabel({ ...base, source: "unavailable", compact: false })).toBe("Partial · 8m");
    expect(compactFreshnessLabel({ ...base, health: "unavailable", compact: false })).toBe("Unavailable");
    expect(compactFreshnessLabel({ ...base, mode: "demo", compact: false })).toBe("Demo data");
    expect(compactFreshnessLabel({ ...base, mode: "demo", health: "checking", compact: false })).toBe("Demo data");
    expect(compactFreshnessLabel({ ...base, mode: "demo", compact: true })).toBe("Demo");
    expect(compactFreshnessLabel({ ...base, mode: null, compact: false })).toBe("Loading");
  });

  it("turns browser transport failures into actionable copy without hiding validation errors", () => {
    const guidance = "Current data couldn’t be downloaded. Check your connection and try again.";
    expect(userFacingLoadError("Failed to fetch /airnow.json")).toBe(guidance);
    expect(userFacingLoadError("Load failed")).toBe(guidance);
    expect(userFacingLoadError("Network request failed")).toBe(guidance);
    expect(userFacingLoadError("Invalid context manifest")).toBe("Invalid context manifest");
  });
});
