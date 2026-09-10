"use client";

import { closeContextImage, loadContextImage } from "@/data/contextClient";
import { decodeForecastRasterAsync, disposeForecastRaster, rasterFromBuffers, type ForecastRaster } from "./forecastRaster";
import type { ForecastRasterWorkerMessage, ForecastRasterWorkerReply } from "./forecastRaster.worker";
import { useEffect, useMemo, useSyncExternalStore } from "react";

export type ForecastRasterSource = {
  frameIndex: number;
  textureUrl: string;
  maskUrl: string;
  expected: { width: number; height: number };
  tileId?: number;
  optional?: boolean;
  paletteVersion: string;
  priority?: number;
  group?: string;
};

type KeyedForecastRasterSource = ForecastRasterSource & { key: string };
export type ResidentForecastRaster = KeyedForecastRasterSource & { raster: ForecastRaster };

export type ForecastRasterCacheMetrics = {
  publications: number;
  workerDecodes: number;
  fallbackDecodes: number;
  optionalDecodesDuringPlayback: number;
  optionalDisposalsDuringPlayback: number;
  decodeDurationMs: number;
  pending: number;
  resident: number;
  retainedBytes: number;
  retainedCpuBytes: number;
  estimatedGpuBytes: number;
  disposals: number;
  peakResident: number;
  peakRetainedBytes: number;
  peakRetainedCpuBytes: number;
  peakEstimatedGpuBytes: number;
  peakFrameIndicesPerSurface: number;
};

type CacheEntry = {
  source: KeyedForecastRasterSource;
  generation: number;
  loading?: boolean;
  failed?: boolean;
  raster?: ForecastRaster;
  controller?: AbortController;
  workerId?: number;
};

type WorkerPending = {
  resolve: (value: ForecastRaster) => void;
  reject: (reason: Error) => void;
};

class RasterWorkerUnavailableError extends Error {}

const MAX_CONCURRENT_RASTER_LOADS = 2;

const EMPTY_METRICS: ForecastRasterCacheMetrics = {
  publications: 0, workerDecodes: 0, fallbackDecodes: 0, decodeDurationMs: 0, pending: 0,
  optionalDecodesDuringPlayback: 0, optionalDisposalsDuringPlayback: 0,
  resident: 0, retainedBytes: 0, retainedCpuBytes: 0, estimatedGpuBytes: 0,
  disposals: 0, peakResident: 0, peakRetainedBytes: 0, peakRetainedCpuBytes: 0, peakEstimatedGpuBytes: 0,
  peakFrameIndicesPerSurface: 0,
};

function mipTexelCount(width: number, height: number): number {
  let texels = 0;
  while (true) {
    texels += width * height;
    if (width === 1 && height === 1) return texels;
    width = Math.max(1, Math.floor(width / 2));
    height = Math.max(1, Math.floor(height / 2));
  }
}

function forecastRasterMemory(width: number, height: number): { cpu: number; gpu: number } {
  const pixels = width * height;
  return {
    cpu: pixels * 8,
    gpu: mipTexelCount(width, height) * 3,
  };
}

export function estimatedForecastRasterBytes(width: number, height: number): number {
  const memory = forecastRasterMemory(width, height);
  return memory.cpu + memory.gpu;
}

function rasterMemory(raster: ForecastRaster): { cpu: number; gpu: number } {
  const scalar = raster.scalarTexture.image.data as ArrayBufferView;
  const weight = raster.weightTexture.image.data as ArrayBufferView;
  const textureBytes = scalar.byteLength + weight.byteLength;
  return {
    cpu: raster.concentrations.byteLength + raster.sourceCodes.byteLength + textureBytes,
    gpu: forecastRasterMemory(raster.width, raster.height).gpu,
  };
}

function surfaceKey(source: ForecastRasterSource): string {
  return source.tileId === undefined ? "base" : `detail-${source.tileId}`;
}

export function forecastRasterCacheKey(source: ForecastRasterSource): string {
  return JSON.stringify([
    surfaceKey(source),
    source.frameIndex,
    source.expected.width,
    source.expected.height,
    source.paletteVersion,
    source.textureUrl,
    source.maskUrl,
  ]);
}

export function forecastRasterDesiredSignature(sources: ForecastRasterSource[]): string {
  return JSON.stringify(sources.map((source) => [
    forecastRasterCacheKey(source),
    source.optional ?? false,
    source.priority ?? 0,
    source.group ?? null,
  ]));
}

const estimatedRasterBytes = (source: ForecastRasterSource) => estimatedForecastRasterBytes(source.expected.width, source.expected.height);

export function boundForecastRasterSources(sources: ForecastRasterSource[], maxBytes = Number.POSITIVE_INFINITY): KeyedForecastRasterSource[] {
  const seen = new Set<string>();
  const frames = new Map<string, Set<number>>();
  const candidates = sources.flatMap((source, order) => {
    const key = forecastRasterCacheKey(source);
    if (seen.has(key)) return [];
    const group = surfaceKey(source);
    const indexes = frames.get(group) ?? new Set<number>();
    if (!indexes.has(source.frameIndex) && indexes.size >= 3) return [];
    indexes.add(source.frameIndex);
    frames.set(group, indexes);
    seen.add(key);
    return [{ ...source, key, order }];
  });
  const groups = new Map<string, typeof candidates>();
  for (const candidate of candidates) {
    const group = candidate.optional ? candidate.group ?? candidate.key : candidate.key;
    groups.set(group, [...(groups.get(group) ?? []), candidate]);
  }
  let retained = 0;
  const admitted: typeof candidates = [];
  for (const group of [...groups.values()].sort((left, right) => Math.min(...left.map((item) => item.priority ?? 0)) - Math.min(...right.map((item) => item.priority ?? 0)) || left[0].order - right[0].order)) {
    const bytes = group.reduce((sum, source) => sum + estimatedRasterBytes(source), 0);
    if (group.some((source) => !source.optional) || retained + bytes <= maxBytes) {
      admitted.push(...group);
      retained += bytes;
    }
  }
  return admitted.sort((left, right) => left.order - right.order).map(({ order, ...source }) => {
    void order;
    return source;
  });
}

function workerSupported(): boolean {
  const diagnosticFallback = process.env.NEXT_PUBLIC_PERF_DIAGNOSTICS === "1"
    && typeof location !== "undefined"
    && new URLSearchParams(location.search).get("rasterDecoder") === "fallback";
  return typeof Worker !== "undefined" && typeof OffscreenCanvas !== "undefined" && typeof createImageBitmap !== "undefined"
    && process.env.NEXT_PUBLIC_FORCE_RASTER_FALLBACK !== "1" && !diagnosticFallback;
}

export class ForecastRasterCache {
  private entries = new Map<string, CacheEntry>();
  private listeners = new Set<() => void>();
  private revision = 0;
  private generation = 0;
  private worker: Worker | null = null;
  private workerDisabled = false;
  private workerSequence = 0;
  private workerPending = new Map<number, WorkerPending>();
  private metricsValue: ForecastRasterCacheMetrics = { ...EMPTY_METRICS };
  private playbackActive = false;

  constructor(private readonly onError: (source: ForecastRasterSource, message: string) => void, private readonly maxBytes = Number.POSITIVE_INFINITY) {}

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): number => this.revision;

  metrics = (): ForecastRasterCacheMetrics => ({ ...this.metricsValue });

  setPlaybackActive(active: boolean): void {
    this.playbackActive = active;
  }

  resident(): ResidentForecastRaster[] {
    return [...this.entries.values()].flatMap((entry) => entry.raster ? [{ ...entry.source, raster: entry.raster }] : []);
  }

  get(key: string): ForecastRaster | null {
    return this.entries.get(key)?.raster ?? null;
  }

  isReady(keys: string[]): boolean {
    return keys.every((key) => Boolean(this.entries.get(key)?.raster));
  }

  setDesired(nextSources: ForecastRasterSource[]): void {
    const desired = boundForecastRasterSources(nextSources, this.maxBytes);
    const keys = new Set(desired.map((source) => source.key));
    for (const [key, entry] of this.entries) {
      if (!keys.has(key)) {
        this.cancelEntry(entry);
        this.entries.delete(key);
      }
    }
    for (const source of desired) {
      const existing = this.entries.get(source.key);
      if (existing) {
        existing.source = source;
        continue;
      }
      const entry: CacheEntry = { source, generation: this.generation };
      this.entries.set(source.key, entry);
    }
    this.refreshMetrics();
    this.emit();
    this.pump();
  }

  retry(): void {
    const desired = [...this.entries.values()].map((entry) => entry.source);
    this.generation += 1;
    this.workerDisabled = false;
    for (const entry of this.entries.values()) this.cancelEntry(entry);
    this.entries.clear();
    this.setDesired(desired);
  }

  dispose(): void {
    this.generation += 1;
    for (const entry of this.entries.values()) this.cancelEntry(entry);
    this.entries.clear();
    for (const pending of this.workerPending.values()) pending.reject(new DOMException("Smoke raster decoding was cancelled.", "AbortError"));
    this.workerPending.clear();
    this.worker?.terminate();
    this.worker = null;
    this.refreshMetrics();
    this.emit();
  }

  private emit(): void {
    this.revision += 1;
    this.metricsValue.publications += 1;
    this.listeners.forEach((listener) => listener());
    if (process.env.NEXT_PUBLIC_PERF_DIAGNOSTICS === "1") {
      (globalThis as typeof globalThis & { __TITANSKIES_PERF__?: Record<string, unknown> }).__TITANSKIES_PERF__ = {
        ...(globalThis as typeof globalThis & { __TITANSKIES_PERF__?: Record<string, unknown> }).__TITANSKIES_PERF__,
        rasterCache: this.metrics(),
      };
    }
  }

  private async load(entry: CacheEntry): Promise<void> {
    const start = performance.now();
    const optionalDuringPlayback = Boolean(entry.source.optional && this.playbackActive);
    entry.loading = true;
    this.metricsValue.pending += 1;
    this.refreshMetrics();
    try {
      let raster: ForecastRaster;
      if (workerSupported() && !this.workerDisabled) {
        try {
          raster = await this.decodeInWorker(entry);
        } catch (error) {
          if (!(error instanceof RasterWorkerUnavailableError)) throw error;
          raster = await this.decodeFallback(entry);
        }
      } else raster = await this.decodeFallback(entry);
      if (entry.generation !== this.generation || this.entries.get(entry.source.key) !== entry) {
        disposeForecastRaster(raster);
        this.metricsValue.disposals += 1;
        return;
      }
      entry.raster = raster;
      if (optionalDuringPlayback) this.metricsValue.optionalDecodesDuringPlayback += 1;
      this.metricsValue.decodeDurationMs += performance.now() - start;
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError") && entry.generation === this.generation) {
        entry.failed = true;
        this.onError(entry.source, error instanceof Error ? error.message : "Unable to decode the smoke raster.");
      }
    } finally {
      entry.loading = false;
      this.metricsValue.pending = Math.max(0, this.metricsValue.pending - 1);
      this.refreshMetrics();
      this.emit();
      this.pump();
    }
  }

  private pump(): void {
    const active = [...this.entries.values()].filter((entry) => entry.loading).length;
    const available = Math.max(0, MAX_CONCURRENT_RASTER_LOADS - active);
    if (!available) return;
    const queued = [...this.entries.values()]
      .filter((entry) => !entry.loading && !entry.raster && !entry.failed)
      .sort((left, right) => (left.source.priority ?? 0) - (right.source.priority ?? 0));
    for (const entry of queued.slice(0, available)) void this.load(entry);
  }

  private decodeInWorker(entry: CacheEntry): Promise<ForecastRaster> {
    if (!this.worker) {
      try {
        this.worker = new Worker(new URL("./forecastRaster.worker.ts", import.meta.url), { type: "module" });
      } catch (error) {
        this.workerDisabled = true;
        throw new RasterWorkerUnavailableError(error instanceof Error ? error.message : "The smoke raster worker is unavailable.");
      }
      this.worker.onmessage = (event: MessageEvent<ForecastRasterWorkerReply>) => {
        const pending = this.workerPending.get(event.data.id);
        if (!pending) return;
        this.workerPending.delete(event.data.id);
        if (event.data.type === "failure" && event.data.fallback) {
          pending.reject(new RasterWorkerUnavailableError(event.data.message));
          this.disableWorker(event.data.message);
        } else if (event.data.type === "failure") pending.reject(new Error(event.data.message));
        else {
          this.metricsValue.workerDecodes += 1;
          pending.resolve(rasterFromBuffers(event.data.buffers));
        }
      };
      this.worker.onerror = () => {
        this.disableWorker("The smoke raster worker failed.");
      };
    }
    const id = ++this.workerSequence;
    entry.workerId = id;
    const message: ForecastRasterWorkerMessage = {
      type: "decode", id, textureUrl: entry.source.textureUrl, maskUrl: entry.source.maskUrl, expected: entry.source.expected,
      paletteVersion: entry.source.paletteVersion,
    };
    return new Promise((resolve, reject) => {
      this.workerPending.set(id, { resolve, reject });
      try {
        this.worker!.postMessage(message);
      } catch (error) {
        this.workerPending.delete(id);
        const message = error instanceof Error ? error.message : "The smoke raster worker is unavailable.";
        this.disableWorker(message);
        reject(new RasterWorkerUnavailableError(message));
      }
    });
  }

  private disableWorker(message: string): void {
    this.workerDisabled = true;
    for (const pending of this.workerPending.values()) pending.reject(new RasterWorkerUnavailableError(message));
    this.workerPending.clear();
    this.worker?.terminate();
    this.worker = null;
  }

  private terminateWorkerForCancellation(): void {
    const cancellation = new DOMException("Smoke raster decoding was cancelled.", "AbortError");
    this.worker?.terminate();
    this.worker = null;
    for (const pending of this.workerPending.values()) pending.reject(cancellation);
    this.workerPending.clear();
  }

  private async decodeFallback(entry: CacheEntry): Promise<ForecastRaster> {
    const controller = new AbortController();
    entry.controller = controller;
    const loaded = await Promise.allSettled([
      loadContextImage(entry.source.textureUrl, controller.signal),
      loadContextImage(entry.source.maskUrl, controller.signal),
    ]);
    const image = loaded[0].status === "fulfilled" ? loaded[0].value : null;
    const mask = loaded[1].status === "fulfilled" ? loaded[1].value : null;
    try {
      const failure = loaded.find((result): result is PromiseRejectedResult => result.status === "rejected");
      if (failure) throw failure.reason;
      if (!image || !mask) throw new Error("Unable to decode the smoke raster.");
      const raster = await decodeForecastRasterAsync(image, mask, entry.source.expected, controller.signal, entry.source.paletteVersion);
      this.metricsValue.fallbackDecodes += 1;
      return raster;
    } finally {
      if (image) closeContextImage(image);
      if (mask) closeContextImage(mask);
    }
  }

  private cancelEntry(entry: CacheEntry): void {
    entry.controller?.abort();
    if (entry.workerId && this.worker) {
      if (entry.source.optional && this.playbackActive) {
        // Worker decode is synchronous after image loading, so a queued cancel
        // message cannot overtake it. Restart the worker when playback drops
        // optional work, then let any still-desired base entry retry promptly.
        this.terminateWorkerForCancellation();
      } else {
        const message: ForecastRasterWorkerMessage = { type: "cancel", id: entry.workerId };
        this.worker.postMessage(message);
        this.workerPending.get(entry.workerId)?.reject(new DOMException("Smoke raster decoding was cancelled.", "AbortError"));
        this.workerPending.delete(entry.workerId);
      }
    }
    if (entry.raster) {
      if (entry.source.optional && this.playbackActive) this.metricsValue.optionalDisposalsDuringPlayback += 1;
      disposeForecastRaster(entry.raster);
      this.metricsValue.disposals += 1;
      entry.raster = undefined;
    }
  }

  private refreshMetrics(): void {
    const rasters = this.resident();
    const groups = new Map<string, Set<number>>();
    rasters.forEach((entry) => {
      const group = surfaceKey(entry);
      const indexes = groups.get(group) ?? new Set<number>();
      indexes.add(entry.frameIndex);
      groups.set(group, indexes);
    });
    this.metricsValue.resident = rasters.length;
    const memory = rasters.reduce((sum, entry) => {
      const next = rasterMemory(entry.raster);
      return { cpu: sum.cpu + next.cpu, gpu: sum.gpu + next.gpu };
    }, { cpu: 0, gpu: 0 });
    this.metricsValue.retainedCpuBytes = memory.cpu;
    this.metricsValue.estimatedGpuBytes = memory.gpu;
    this.metricsValue.retainedBytes = memory.cpu + memory.gpu;
    this.metricsValue.peakResident = Math.max(this.metricsValue.peakResident, rasters.length);
    this.metricsValue.peakRetainedBytes = Math.max(this.metricsValue.peakRetainedBytes, memory.cpu + memory.gpu);
    this.metricsValue.peakRetainedCpuBytes = Math.max(this.metricsValue.peakRetainedCpuBytes, memory.cpu);
    this.metricsValue.peakEstimatedGpuBytes = Math.max(this.metricsValue.peakEstimatedGpuBytes, memory.gpu);
    this.metricsValue.peakFrameIndicesPerSurface = Math.max(this.metricsValue.peakFrameIndicesPerSurface, ...[...groups.values()].map((indexes) => indexes.size), 0);
  }
}

export function useForecastRasterCache(sources: ForecastRasterSource[], retryKey: number, onError: (source: ForecastRasterSource, message: string) => void, maxBytes = Number.POSITIVE_INFINITY, playbackActive = false): ForecastRasterCache {
  const cache = useMemo(() => new ForecastRasterCache(onError, maxBytes), [maxBytes, onError]);
  const signature = forecastRasterDesiredSignature(sources);
  useEffect(() => cache.setPlaybackActive(playbackActive), [cache, playbackActive]);
  useEffect(() => {
    cache.setDesired(sources);
  // The signature is the bounded desired window; source objects are recreated by the parent.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cache, signature]);
  useEffect(() => { if (retryKey) cache.retry(); }, [cache, retryKey]);
  useEffect(() => () => cache.dispose(), [cache]);
  useSyncExternalStore(cache.subscribe, cache.getSnapshot, cache.getSnapshot);
  return cache;
}
