import { describe, expect, it, vi } from "vitest";
import { FORECAST_DISPLAY_LUT } from "../data/context";
import { decodeForecastPixels, disposeForecastRaster, FORECAST_CONCENTRATIONS, validateForecastRasterDimensions, type ForecastRaster } from "./forecastRaster";
import { allocateForecastBuffers, allocateForecastDecode, decodeForecastPixelRange, decodeForecastRgba, finalizeForecastDecodeRange, toHalfFloat } from "./forecastRasterDecode";
import { interpolateSurfaceSample } from "./surfaceInterpolation";
import { DataUtils } from "three";

const pixels = (...values: number[][]) => new Uint8ClampedArray(values.flat());

describe("concentration raster decoding", () => {
  it("decodes exact palette colors and contribution validity", () => {
    const color = FORECAST_DISPLAY_LUT[8].rgba;
    const decoded = decodeForecastPixels(
      pixels([0, 0, 0, 0], color),
      pixels([0, 0, 0, 0], [71, 166, 139, 255]),
    );
    expect([...decoded.valid]).toEqual([0, 255]);
    expect(FORECAST_CONCENTRATIONS[decoded.indexes[1]]).toBeCloseTo(FORECAST_DISPLAY_LUT[8].concentration, 5);
  });

  it("rejects unknown palette colors and malformed masks", () => {
    expect(() => decodeForecastPixels(pixels([1, 2, 3, 255]), pixels([32, 96, 176, 255]))).toThrow(/unknown display-palette/);
    expect(() => decodeForecastPixels(pixels(FORECAST_DISPLAY_LUT[0].rgba), pixels([1, 2, 3, 255]))).toThrow(/mask is malformed/);
    expect(() => decodeForecastPixels(pixels([0, 0, 0, 0]), new Uint8ClampedArray())).toThrow(/pixel data is malformed/);
  });

  it("accepts only the small RGBA rounding introduced by canvas un-premultiplication", () => {
    const color = FORECAST_DISPLAY_LUT[0].rgba.map((value, index) => index < 3 ? Math.max(0, value - 1) : value);
    expect(() => decodeForecastPixels(pixels(color), pixels([32, 96, 176, 255]))).not.toThrow();
  });

  it("accepts one-channel canvas rounding in contribution masks only", () => {
    const color = FORECAST_DISPLAY_LUT[0].rgba;
    const decoded = decodeForecastPixels(
      pixels(color, color, color, color),
      pixels([0, 0, 1, 0], [33, 96, 176, 255], [208, 89, 48, 255], [71, 166, 138, 255]),
    );
    expect([...decoded.valid]).toEqual([0, 255, 255, 255]);
    expect([...decoded.sourceCodes]).toEqual([0, 1, 2, 3]);
    expect(() => decodeForecastPixels(pixels(color), pixels([34, 96, 176, 255]))).toThrow(/mask is malformed/);
  });

  it("requires texture, mask, and published grid dimensions to agree", () => {
    expect(() => validateForecastRasterDimensions({ width: 1024, height: 635 }, { width: 1024, height: 635 }, { width: 1024, height: 635 })).not.toThrow();
    expect(() => validateForecastRasterDimensions({ width: 1, height: 1 }, { width: 1, height: 1 }, { width: 1024, height: 635 })).toThrow(/published grid/);
    expect(() => validateForecastRasterDimensions({ width: 1024, height: 635 }, { width: 1023, height: 635 }, { width: 1024, height: 635 })).toThrow(/dimensions differ/);
  });

  it("builds identical transferable buffers from the shared decode kernel", () => {
    const color = FORECAST_DISPLAY_LUT[4].rgba;
    const rgba = pixels(color, [0, 0, 0, 0]);
    const mask = pixels([32, 96, 176, 255], [71, 166, 139, 255]);
    const buffers = decodeForecastRgba(2, 1, rgba, mask);
    expect([...buffers.sourceCodes]).toEqual([1, 3]);
    expect([...buffers.valid]).toEqual([255, 255]);
    expect(buffers.concentrations[0]).toBeCloseTo(FORECAST_DISPLAY_LUT[4].concentration);
    expect(buffers.concentrations[1]).toBe(0);
    expect(buffers.weights[1]).toBe(255);
  });

  it("filters concentration with validity rather than display opacity", () => {
    const low = FORECAST_DISPLAY_LUT[0];
    const high = FORECAST_DISPLAY_LUT.find((entry) => entry.concentration === 100)!;
    const buffers = decodeForecastRgba(
      2,
      1,
      pixels(low.rgba, high.rgba),
      pixels([32, 96, 176, 255], [32, 96, 176, 255]),
    );
    const midpoint = (
      DataUtils.fromHalfFloat(buffers.weighted[0]) + DataUtils.fromHalfFloat(buffers.weighted[1])
    ) / 2 * buffers.concentrationScale / ((buffers.weights[0] + buffers.weights[1]) / 2 / 255);
    expect([...buffers.weights]).toEqual([255, 255]);
    expect(midpoint).toBeCloseTo((low.concentration + high.concentration) / 2, 1);
  });

  it("uses the same half-float encoding as Three", () => {
    for (const value of [0, 0.0001, 0.1, 0.5, 1, 10, 65_504]) {
      expect(toHalfFloat(value)).toBe(DataUtils.toHalfFloat(value));
    }
  });

  it("disposes each GPU texture exactly once", () => {
    const scalarDispose = vi.fn();
    const weightDispose = vi.fn();
    disposeForecastRaster({
      scalarTexture: { dispose: scalarDispose },
      weightTexture: { dispose: weightDispose },
    } as unknown as ForecastRaster);
    expect(scalarDispose).toHaveBeenCalledTimes(1);
    expect(weightDispose).toHaveBeenCalledTimes(1);
  });
});


describe("covered transparent forecast pixels", () => {
  it.each(["whole", "chunked"])("preserves zero-valued spatial and temporal endpoints in the %s decoder", (mode) => {
    const high = FORECAST_DISPLAY_LUT.find((entry) => entry.concentration === 100)!;
    const rgba = pixels([0, 0, 0, 0], high.rgba, [0, 0, 0, 0]);
    const mask = pixels([71, 166, 139, 255], [32, 96, 176, 255], [0, 0, 0, 0]);
    const decoded = allocateForecastDecode(3);
    const buffers = mode === "whole" ? decodeForecastRgba(3, 1, rgba, mask)
      : allocateForecastBuffers(3, 1, decoded.valid, decoded.sourceCodes);
    if (mode === "chunked") {
      for (let start = 0; start < 3; start += 1) {
        decodeForecastPixelRange(rgba, mask, decoded.indexes, decoded.valid, decoded.sourceCodes, start, start + 1);
        finalizeForecastDecodeRange(decoded.indexes, buffers, start, start + 1);
      }
    }
    expect([...buffers.weights]).toEqual([255, 255, 0]);
    expect(buffers.concentrations[0]).toBe(0);
    const spatialMidpoint = (DataUtils.fromHalfFloat(buffers.weighted[0]) + DataUtils.fromHalfFloat(buffers.weighted[1]))
      * buffers.concentrationScale / ((buffers.weights[0] + buffers.weights[1]) / 255);
    expect(spatialMidpoint).toBeCloseTo(50, 1);
    const sample = (index: number) => ({ concentration: buffers.concentrations[index], covered: buffers.weights[index] > 0, spatialCoverage: buffers.weights[index] / 255 });
    for (const mix of [0, 0.5, 1]) {
      expect(interpolateSurfaceSample(sample(0), sample(1), mix)?.concentration).toBeCloseTo(100 * mix);
      expect(interpolateSurfaceSample(sample(2), sample(1), mix)?.concentration).toBe(100);
    }
  });
});
