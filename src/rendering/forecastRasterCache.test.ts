import { afterEach, describe, expect, it, vi } from "vitest";
import { boundForecastRasterSources, estimatedForecastRasterBytes, ForecastRasterCache, forecastRasterCacheKey, forecastRasterDesiredSignature, type ForecastRasterSource } from "./forecastRasterCache";

const source = (frameIndex: number, tileId?: number): ForecastRasterSource => ({
  frameIndex,
  tileId,
  textureUrl: `/texture-${frameIndex}.png`,
  maskUrl: `/mask-${frameIndex}.png`,
  expected: { width: 1, height: 1 },
  paletteVersion: "titanskies-smoke-display-v1",
});

describe("forecast raster residency", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("bounds base and each detail surface to three frame indices", () => {
    const bounded = boundForecastRasterSources([
      ...[0, 1, 2, 3].map((frame) => source(frame)),
      ...[0, 1, 2, 3].map((frame) => source(frame, 0)),
      ...[0, 1, 2, 3].map((frame) => source(frame, 1)),
    ]);
    const frames = (tileId?: number) => new Set(bounded.filter((item) => item.tileId === tileId).map((item) => item.frameIndex));
    expect([...frames()]).toEqual([0, 1, 2]);
    expect([...frames(0)]).toEqual([0, 1, 2]);
    expect([...frames(1)]).toEqual([0, 1, 2]);
  });

  it("deduplicates identical sources", () => {
    const repeated = source(0);
    expect(boundForecastRasterSources([repeated, repeated])).toHaveLength(1);
  });

  it("keeps frame, surface, and grid identity even when asset URLs repeat", () => {
    const repeatedUrls = { textureUrl: "/shared.png", maskUrl: "/shared-mask.png" };
    const first = { ...source(0), ...repeatedUrls };
    const nextFrame = { ...source(1), ...repeatedUrls };
    const detail = { ...source(0, 0), ...repeatedUrls };
    const otherGrid = { ...first, expected: { width: 2, height: 1 } };
    expect(new Set([first, nextFrame, detail, otherGrid].map(forecastRasterCacheKey)).size).toBe(4);
    expect(boundForecastRasterSources([first, nextFrame, detail, otherGrid])).toHaveLength(4);
  });

  it("includes palette identity in cache keys", () => {
    const legacy = source(0);
    const dense = { ...legacy, paletteVersion: "titanskies-smoke-display-v2" };
    expect(forecastRasterCacheKey(legacy)).not.toBe(forecastRasterCacheKey(dense));
  });

  it("reschedules when desired priority, atomic group, or optionality changes", () => {
    const first = { ...source(0, 1), optional: true, priority: 10, group: "tile-1" };
    expect(forecastRasterDesiredSignature([first])).not.toBe(forecastRasterDesiredSignature([{ ...first, priority: 20 }]));
    expect(forecastRasterDesiredSignature([first])).not.toBe(forecastRasterDesiredSignature([{ ...first, group: "prefetch-1" }]));
    expect(forecastRasterDesiredSignature([first])).not.toBe(forecastRasterDesiredSignature([{ ...first, optional: false }]));
  });

  it("admits optional current-frame pairs atomically in priority order", () => {
    const base = { ...source(0), optional: false };
    const pair = (tileId: number, priority: number) => [0, 1].map((frameIndex) => ({
      ...source(frameIndex, tileId), optional: true, group: `tile-${tileId}`, priority,
    }));
    const candidates = [base, ...pair(2, 20), ...pair(1, 10)];
    // One 1x1 decoded raster reserves 11 bytes; base is mandatory and one
    // complete two-frame tile pair fits in the remaining 28-byte budget.
    const admitted = boundForecastRasterSources(candidates, 39);
    expect(admitted.filter((item) => item.tileId === 1)).toHaveLength(2);
    expect(admitted.filter((item) => item.tileId === 2)).toHaveLength(0);
    expect(admitted.filter((item) => item.tileId === undefined)).toHaveLength(1);
  });

  it("reserves decoded CPU and generated GPU mipmap bytes at production dimensions", () => {
    expect(estimatedForecastRasterBytes(1024, 635)).toBe(7_801_105);

    const raster = (frameIndex: number, tileId?: number): ForecastRasterSource => ({
      ...source(frameIndex, tileId),
      expected: { width: 1024, height: 635 },
      optional: tileId !== undefined,
      group: tileId === undefined ? undefined : `tile-${tileId}`,
      priority: tileId ?? 0,
    });
    const candidates = [
      ...[0, 1, 2].map((frameIndex) => raster(frameIndex)),
      ...Array.from({ length: 16 }, (_, tileId) => [raster(0, tileId), raster(1, tileId)]).flat(),
    ];

    expect(boundForecastRasterSources(candidates, 128 * 1024 * 1024)).toHaveLength(17);
    expect(boundForecastRasterSources(candidates, 256 * 1024 * 1024)).toHaveLength(33);
  });

  it("terminates queued optional worker work when playback starts", async () => {
    const workers: Array<{ terminate: ReturnType<typeof vi.fn>; postMessage: ReturnType<typeof vi.fn> }> = [];
    class FakeWorker {
      onmessage = null;
      onerror = null;
      terminate = vi.fn();
      postMessage = vi.fn();
      constructor() { workers.push(this); }
    }
    vi.stubGlobal("Worker", FakeWorker);
    vi.stubGlobal("OffscreenCanvas", class {});
    vi.stubGlobal("createImageBitmap", vi.fn());

    const cache = new ForecastRasterCache(() => {});
    cache.setDesired([{ ...source(0, 1), optional: true }]);
    expect(workers).toHaveLength(1);
    cache.setPlaybackActive(true);
    cache.setDesired([]);
    expect(workers[0].terminate).toHaveBeenCalledTimes(1);
    await Promise.resolve();
    cache.dispose();
  });
});
