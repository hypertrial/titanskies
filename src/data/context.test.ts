import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  CONTEXT_DETAIL_GRID,
  CONTEXT_POINTER_POLL_MS,
  contextCapabilities,
  contextHealthUrl,
  contextPointerUrl,
  detailTileBounds,
  detailTileId,
  forecastTimelineIdentity,
  isContextManifest,
  loadContext,
  loadContextHealth,
  playbackPositionAfterContextRefresh,
  visibleForecastRun,
} from "./context";

const registry = JSON.parse(readFileSync("shared/context-capabilities.json", "utf8"));
const pointer = JSON.parse(readFileSync("public/demo/context/latest.json", "utf8")) as { manifestPath: string };
const manifest = JSON.parse(readFileSync(`public/demo/${pointer.manifestPath}`, "utf8"));

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("v8 context", () => {
  it("publishes one canonical contract with all eight sources", () => {
    expect(registry.versions.map((item: { version: number }) => item.version)).toEqual([8]);
    expect(contextCapabilities(8)?.requiredSources).toEqual([
      "airnow", "bcair", "sinaica", "aqhi", "wfigs", "cwfis", "firework", "hrrr",
    ]);
  });

  it("accepts the bundled synthetic 37-hour publication", () => {
    expect(isContextManifest(manifest)).toBe(true);
    expect(visibleForecastRun(manifest).frames).toHaveLength(37);
    expect(visibleForecastRun(manifest).detailGrid).toEqual(CONTEXT_DETAIL_GRID);
  });

  it("rejects corrupt, incomplete, and legacy-shaped publications", () => {
    const missing = structuredClone(manifest);
    delete missing.sources.airnow;
    expect(isContextManifest(missing)).toBe(false);
    const divergent = structuredClone(manifest);
    divergent.forecast.frames[0].textureUrl = "/different.png";
    expect(isContextManifest(divergent)).toBe(false);
    const legacy = structuredClone(manifest);
    legacy.version = 7;
    expect(isContextManifest(legacy)).toBe(false);
    const retired = structuredClone(manifest);
    retired.sources.hms = { ...retired.sources.airnow };
    expect(isContextManifest(retired)).toBe(false);
    for (const contributors of [{}, [{ source: "hrrr" }, { source: "hrrr" }], [{ source: "other" }], [{ source: "hrrr", modelRun: "invalid" }]]) {
      const invalid = structuredClone(manifest);
      invalid.forecast.frames[0].contributors = contributors;
      invalid.forecasts.best.frames[0].contributors = contributors;
      expect(isContextManifest(invalid)).toBe(false);
    }
  });

  it("maps all sixteen detail tiles without gaps", () => {
    const ids = new Set<number>();
    for (let row = 0; row < CONTEXT_DETAIL_GRID.rows; row += 1) {
      for (let column = 0; column < CONTEXT_DETAIL_GRID.columns; column += 1) {
        const bounds = detailTileBounds(CONTEXT_DETAIL_GRID, column, row);
        ids.add(detailTileId(CONTEXT_DETAIL_GRID, (bounds.west + bounds.east) / 2, (bounds.south + bounds.north) / 2));
      }
    }
    expect(ids.size).toBe(16);
  });

  it("uses only local read-only APIs", () => {
    expect(contextPointerUrl()).toBe("/api/context-data");
    expect(contextHealthUrl(8, ["firework", "airnow"])).toBe("/api/context-health?expectedVersion=8&expectedSource=firework&expectedSource=airnow");
    expect(CONTEXT_POINTER_POLL_MS).toBe(120_000);
  });

  it("loads a pointer and matching manifest", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, headers: { get: () => "application/json" }, json: async () => ({ version: 8, manifestUrl: "/data/context/manifests/abc.json", updatedAt: manifest.generatedAt }) })
      .mockResolvedValueOnce({ ok: true, headers: { get: () => "application/json" }, json: async () => manifest });
    vi.stubGlobal("fetch", fetchMock);
    await expect(loadContext()).resolves.toMatchObject({ version: 8, mode: "demo" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("validates health schema and source redaction shape", async () => {
    const sources = Object.fromEntries(registry.versions[0].requiredSources.map((source: string) => [source, {
      status: "ok", checkedAt: manifest.generatedAt, observedAt: manifest.generatedAt, provenance: "https://example.gov/data",
    }]));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      headers: { get: () => "application/json" },
      json: async () => ({
        schemaVersion: 1, ok: true, status: "healthy", checkedAt: manifest.generatedAt,
        contextVersion: 8, publication: "fresh", lastAttemptAt: manifest.generatedAt,
        lastCompleteForecastAt: manifest.generatedAt, pointerUpdatedAt: manifest.generatedAt,
        forecastFrameCount: 37, forecastLastValidTime: manifest.forecast.frames.at(-1).validTime,
        remainingCoverageHours: 36, sources, issues: [],
      }),
    }));
    await expect(loadContextHealth(8)).resolves.toMatchObject({ schemaVersion: 1, status: "healthy" });
  });

  it("preserves manual playback when publication identity is unchanged", () => {
    expect(forecastTimelineIdentity(manifest)).toContain(manifest.forecast.frames[0].textureUrl);
    expect(playbackPositionAfterContextRefresh({ previous: manifest, next: structuredClone(manifest), previousPositionMs: 7200_000, followingLive: false })).toBe(7200_000);
  });
});
