import { describe, expect, it } from "vitest";
import { interpolateSurfaceSample, subThresholdAlpha, TEMPORAL_SHADER_SAMPLE_COUNT } from "./surfaceInterpolation";

describe("forecast surface output semantics", () => {
  it("uses exactly two temporal samples", () => {
    expect(TEMPORAL_SHADER_SAMPLE_COUNT).toBe(2);
  });

  it("interpolates scalar concentration and spatial coverage before palette lookup", () => {
    const from = { concentration: 0, covered: true, spatialCoverage: 0.25 };
    const to = { concentration: 20, covered: true, spatialCoverage: 0.75 };
    expect(interpolateSurfaceSample(from, to, 0)).toEqual(from);
    expect(interpolateSurfaceSample(from, to, 0.5)).toEqual({ concentration: 10, covered: true, spatialCoverage: 0.5 });
    expect(interpolateSurfaceSample(from, to, 1)).toEqual(to);
  });

  it("preserves a sole valid endpoint and rejects two missing endpoints", () => {
    const covered = { concentration: 12, covered: true, spatialCoverage: 0.8 };
    const missing = { concentration: 0, covered: false, spatialCoverage: 0 };
    expect(interpolateSurfaceSample(covered, missing, 0.75)).toBe(covered);
    expect(interpolateSurfaceSample(missing, covered, 0.25)).toBe(covered);
    expect(interpolateSurfaceSample(missing, missing, 0.5)).toBeNull();
  });

  it("ramps sub-threshold alpha continuously", () => {
    expect(subThresholdAlpha(0)).toBe(0);
    expect(subThresholdAlpha(0.5)).toBeCloseTo(20 / 255);
    expect(subThresholdAlpha(1)).toBeCloseTo(40 / 255);
  });
});
