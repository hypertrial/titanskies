import { describe, expect, it } from "vitest";
import { airMarkerPointSize, markerEmphasis } from "./markerVisuals";

describe("marker visual states", () => {
  it("prioritizes selected markers and keeps severity and cluster sizing bounded", () => {
    expect(markerEmphasis({ selected: false, hovered: false })).toBe(0);
    expect(markerEmphasis({ selected: false, hovered: true })).toBe(0.72);
    expect(markerEmphasis({ selected: true, hovered: true })).toBe(1);
    expect(airMarkerPointSize(0, 1, { selected: false, hovered: false })).toBe(8);
    expect(airMarkerPointSize(0.5, 1, { selected: false, hovered: true })).toBeGreaterThan(airMarkerPointSize(0.5, 1, { selected: false, hovered: false }));
    expect(airMarkerPointSize(0.5, 1, { selected: true, hovered: false })).toBeGreaterThan(airMarkerPointSize(0.5, 1, { selected: false, hovered: true }));
    expect(airMarkerPointSize(1, 10, { selected: true, hovered: false })).toBe(16);
    expect(airMarkerPointSize(5, 1, { selected: false, hovered: false })).toBe(12);
  });
});
