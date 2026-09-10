import type { TimelineEntry } from "./contracts";
import { positionForTime } from "./timeline";
import { CONTEXT_VERSIONS, HEALTH_STATUSES, PUBLICATION_STATUSES, SOURCE_STATUSES, isContextManifest, isIso, isRecord, isUrl, normalizeContextManifest, uiForecastHorizonHours, visibleForecastRun } from "./contextSchema";
import type { ContextHealth, ContextHealthStatus, ContextManifest, ContextPointer, ContextPublicationStatus, ContextSource, ContextSourceStatus } from "./contextSchema";


export async function fetchJson<T>(url: string, { allowHttpError = false } = {}): Promise<T> {
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), 15_000);
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok && !allowHttpError) throw new Error(`Failed to fetch ${url}`);
    const type = response.headers?.get?.("content-type") ?? "";
    if (type.includes("text/html")) throw new Error("Unexpected HTML response");
    return await response.json() as T;
  } catch (error) {
    if (controller.signal.aborted) throw new Error("Current data request timed out. Please try again.");
    throw error;
  } finally {
    clearTimeout(deadline);
  }
}

export function contextPointerUrl(): string {
  const configured = process.env.NEXT_PUBLIC_CONTEXT_URL?.trim();
  if (configured) return configured;
  return "/api/context-data";
}

export function contextHealthUrl(expectedVersion: number, expectedSources: ContextSource[] = []): string | null {
  const params = new URLSearchParams({ expectedVersion: String(expectedVersion) });
  expectedSources.forEach((source) => params.append("expectedSource", source));
  return `/api/context-health?${params}`;
}

function isContextHealth(value: unknown): value is ContextHealth {
  if (!isRecord(value)) return false;
  return value.schemaVersion === 1
    && typeof value.ok === "boolean"
    && HEALTH_STATUSES.has(value.status as ContextHealthStatus)
    && isIso(value.checkedAt)
    && (value.contextVersion === null || Number.isInteger(value.contextVersion))
    && PUBLICATION_STATUSES.has(value.publication as ContextPublicationStatus)
    && (value.lastAttemptAt === null || isIso(value.lastAttemptAt))
    && (value.lastCompleteForecastAt === null || isIso(value.lastCompleteForecastAt))
    && (value.pointerUpdatedAt === null || isIso(value.pointerUpdatedAt))
    && Number.isInteger(value.forecastFrameCount)
    && (value.forecastLastValidTime === null || isIso(value.forecastLastValidTime))
    && (value.remainingCoverageHours === null || typeof value.remainingCoverageHours === "number" && Number.isFinite(value.remainingCoverageHours))
    && isRecord(value.sources)
    && Object.values(value.sources).every((source) => source === null || isRecord(source)
      && SOURCE_STATUSES.has(source.status as ContextSourceStatus)
      && isIso(source.checkedAt)
      && (source.observedAt === null || isIso(source.observedAt))
      && isUrl(source.provenance))
    && Array.isArray(value.issues) && value.issues.every((issue) => typeof issue === "string");
}

export async function loadContextHealth(expectedVersion: number, url = contextHealthUrl(expectedVersion)): Promise<ContextHealth> {
  if (!url) throw new Error("Context health is unavailable for local ingest data");
  const value = await fetchJson<unknown>(url, { allowHttpError: true });
  if (!isContextHealth(value)) throw new Error("Invalid context health response");
  return value;
}

export async function loadContext(url = contextPointerUrl()): Promise<ContextManifest> {
  const pointer = await fetchJson<ContextPointer>(url);
  if (!CONTEXT_VERSIONS.has(pointer.version) || !isUrl(pointer.manifestUrl) || !isIso(pointer.updatedAt)) throw new Error("Invalid context pointer");
  const manifest = await fetchJson<unknown>(pointer.manifestUrl);
  if (!isContextManifest(manifest)) throw new Error("Invalid context manifest");
  if (pointer.version !== manifest.version) throw new Error("Context pointer and manifest versions differ");
  return normalizeContextManifest(manifest);
}

export const CONTEXT_POINTER_POLL_MS = 2 * 60_000;

function forecastTimelineEntries(manifest: ContextManifest, nowMs = Date.now()): TimelineEntry[] {
  return visibleForecastRun(manifest, uiForecastHorizonHours(manifest), nowMs).frames.map((frame, index) => ({
    scanId: `forecast-${index}`,
    observationStart: frame.validTime,
    manifestUrl: frame.textureUrl ?? "",
  }));
}

export function forecastTimelineIdentity(manifest: ContextManifest, nowMs = Date.now()): string {
  const forecast = visibleForecastRun(manifest, uiForecastHorizonHours(manifest), nowMs);
  return [forecast.legendUrl ?? "", ...forecast.frames.map((frame) => `${frame.validTime}\t${frame.textureUrl}`)].join("\n");
}

export function contextPublicationKey(manifest: ContextManifest, nowMs = Date.now()): string {
  const forecast = visibleForecastRun(manifest, uiForecastHorizonHours(manifest), nowMs);
  return JSON.stringify({
    generatedAt: manifest.generatedAt,
    integratedStatus: forecast.integratedStatus ?? "",
    legendUrl: forecast.legendUrl ?? "",
    frames: forecast.frames.map((frame) => [
      frame.validTime,
      frame.textureUrl,
      frame.sourceMaskUrl ?? "",
      ...(frame.detailTiles ?? []).flatMap((tile) => [tile.textureUrl, tile.sourceMaskUrl]),
    ]),
    sources: manifest.sources,
  });
}

export function playbackPositionAfterContextRefresh(args: {
  previous: ContextManifest | null;
  next: ContextManifest;
  previousPositionMs: number;
  nowMs?: number;
  previousNowMs?: number;
  followingLive?: boolean;
}): number {
  const nowMs = args.nowMs ?? Date.now();
  const entries = forecastTimelineEntries(args.next, nowMs);
  if (entries.length === 0) return 0;
  if (args.followingLive) return positionForTime(entries, nowMs);
  if (!args.previous) return 0;
  const previousNowMs = args.previousNowMs ?? nowMs;
  if (forecastTimelineIdentity(args.previous, previousNowMs) === forecastTimelineIdentity(args.next, nowMs)) {
    return args.previousPositionMs;
  }
  const previousEntries = forecastTimelineEntries(args.previous, previousNowMs);
  const previousStart = Date.parse(previousEntries[0]?.observationStart ?? "");
  if (!Number.isFinite(previousStart)) return 0;
  return positionForTime(entries, previousStart + args.previousPositionMs);
}


export async function loadContextImage(url: string, signal?: AbortSignal): Promise<ImageBitmap | HTMLImageElement> {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`Failed to fetch texture ${url}`);
  const blob = await response.blob();
  if ("createImageBitmap" in window) return createImageBitmap(blob, { premultiplyAlpha: "none" });
  return new Promise((resolve, reject) => {
    const image = new Image();
    const objectUrl = URL.createObjectURL(blob);
    image.onload = () => { URL.revokeObjectURL(objectUrl); resolve(image); };
    image.onerror = () => { URL.revokeObjectURL(objectUrl); reject(new Error(`Unable to decode texture ${url}`)); };
    image.src = objectUrl;
  });
}

export function closeContextImage(image: ImageBitmap | HTMLImageElement | null): void {
  if (image && "close" in image) image.close();
}
