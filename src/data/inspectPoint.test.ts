import { describe, expect, it } from "vitest";
import { inspectMarkerCopy, keyboardInspectSelection, nearestLonLat, reduceInspectPoint, shouldInspectMapClick, shouldKeepInspectPanel } from "./inspectPoint";

describe("inspect point lifetime", () => {
  it("replaces on a second click and clears on dismiss", () => {
    const first = reduceInspectPoint({ type: "set", lon: -122.3, lat: 47.6 });
    expect(first).toEqual({ lon: -122.3, lat: 47.6 });
    const replaced = reduceInspectPoint({ type: "set", lon: -114.1, lat: 51.0 });
    expect(replaced).toEqual({ lon: -114.1, lat: 51.0 });
    expect(reduceInspectPoint({ type: "clear" })).toBeNull();
    expect(shouldKeepInspectPanel(replaced, "selection")).toBe(true);
    expect(shouldKeepInspectPanel(replaced, "layers")).toBe(false);
    expect(shouldKeepInspectPanel(null, "selection")).toBe(false);
    expect(shouldInspectMapClick({ forecast: true })).toBe(true);
    expect(shouldInspectMapClick({ forecast: false })).toBe(false);
  });

  it("samples forecast on Enter even when wildfires are visible", () => {
    const cursor = { lon: -100, lat: 52 };
    const monitors = [{ lon: -114, lat: 51, id: "airnow:1" }];
    const far = { lon: -114, lat: 51, id: "airnow:far" };
    const near = { lon: -100.1, lat: 52, id: "airnow:near" };
    expect(keyboardInspectSelection({ air: false, monitors, cursor })).toEqual({ kind: "forecast" });
    expect(keyboardInspectSelection({ air: false, monitors: [], cursor })).toEqual({ kind: "forecast" });
    expect(keyboardInspectSelection({ air: true, monitors, cursor })).toEqual({ kind: "air-monitor", monitor: monitors[0] });
    expect(keyboardInspectSelection({ air: true, monitors: [far, near], cursor })).toEqual({ kind: "air-monitor", monitor: near });
    expect(keyboardInspectSelection({ air: true, monitors: [], cursor })).toBeNull();
    expect(nearestLonLat([{ lon: -106.7, lat: 57.1 }, { lon: -100, lat: 52.5 }], cursor)).toEqual({ lon: -100, lat: 52.5 });
  });

  it("uses surface distance when choosing the nearest high-latitude monitor", () => {
    const cursor = { lon: -100, lat: 70 };
    const south = { id: "south", lon: -100, lat: 69.5 };
    const east = { id: "east", lon: -99, lat: 70 };
    expect(nearestLonLat([south, east], cursor)).toBe(east);
    expect(nearestLonLat([east, south], cursor)).toBe(east);
  });
});

describe("inspect marker copy", () => {
  it("formats display-precision modeled smoke and coverage gaps", () => {
    expect(inspectMarkerCopy({ hasPoint: false, loading: false, inBounds: true })).toBeNull();
    expect(inspectMarkerCopy({ hasPoint: true, loading: true, inBounds: true })).toEqual({
      title: "Modeled smoke",
      value: "Loading…",
    });
    expect(inspectMarkerCopy({ hasPoint: true, loading: false, inBounds: false })).toEqual({
      title: "Modeled smoke",
      value: "Unavailable",
    });
    expect(inspectMarkerCopy({ hasPoint: true, loading: false, inBounds: true, source: "none", concentration: 12 })).toEqual({
      title: "Modeled smoke",
      value: "Unavailable",
    });
    expect(inspectMarkerCopy({
      hasPoint: true,
      loading: false,
      inBounds: true,
      source: "firework",
      concentration: 7.24,
      concentrationMax: 1000,
    })).toEqual({
      title: "Modeled smoke",
      value: "7.2 µg/m³",
    });
  });
});
