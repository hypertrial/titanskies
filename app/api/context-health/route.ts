import { CONTEXT_VERSION, contextCapabilities, isContextManifest, isIso, type ContextManifest, type ContextSource, type SourceState } from "@/data/contextSchema";
import { blobBaseUrl, blobManifestValid, readBlobJson } from "@/server/blobData";
import { localManifestAssetsAvailable, readLocalJson } from "@/server/localData";
import { isContextPointer } from "@/server/publicationContract";
import runtimeLimits from "../../../shared/runtime-limits.json";

const DEFAULT_WATCH_SECONDS = runtimeLimits.watchSeconds.default;
const MIN_WATCH_SECONDS = runtimeLimits.watchSeconds.min;
const MAX_WATCH_SECONDS = runtimeLimits.watchSeconds.max;
const MIN_FRESH_AGE_MS = 30 * 60_000;
const MIN_COVERAGE_MS = 6 * 3_600_000;
const UPSTREAM_DEADLINE_MS = 5_000;
const SOURCES = contextCapabilities(CONTEXT_VERSION)!.allowedSources as ContextSource[];
const SOURCE_SET = new Set<ContextSource>(SOURCES);
const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };

type RunStatus = {
  version: 1;
  lastAttemptAt: string;
  lastSuccessfulPublicationAt: string | null;
  lastCompleteForecastAt: string | null;
  outcome: "fresh" | "retained" | "failed";
  contextVersion: number | null;
  forecastFrameCount: number;
  forecastLastValidTime: string | null;
  consecutiveNonFresh: number;
};

function maximumFreshAgeMs(): number {
  const parsed = Number(process.env.CONTEXT_WATCH_SECONDS ?? DEFAULT_WATCH_SECONDS);
  const interval = Number.isInteger(parsed)
    ? Math.min(MAX_WATCH_SECONDS, Math.max(MIN_WATCH_SECONDS, parsed))
    : DEFAULT_WATCH_SECONDS;
  return Math.max(MIN_FRESH_AGE_MS, interval * 2_000);
}

function validStatus(value: unknown): value is RunStatus {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<RunStatus>;
  return item.version === 1 && isIso(item.lastAttemptAt)
    && (item.lastSuccessfulPublicationAt === null || isIso(item.lastSuccessfulPublicationAt))
    && (item.lastCompleteForecastAt === null || isIso(item.lastCompleteForecastAt))
    && (item.outcome === "fresh" || item.outcome === "retained" || item.outcome === "failed")
    && (item.contextVersion === null || item.contextVersion === 8)
    && Number.isInteger(item.forecastFrameCount) && Number.isInteger(item.consecutiveNonFresh)
    && (item.forecastLastValidTime === null || isIso(item.forecastLastValidTime));
}

function publicSources(manifest: ContextManifest | null): Record<ContextSource, Pick<SourceState, "status" | "checkedAt" | "observedAt" | "provenance"> | null> {
  return Object.fromEntries(SOURCES.map((source) => {
    const state = manifest?.sources[source];
    return [source, state ? {
      status: state.status,
      checkedAt: state.checkedAt,
      observedAt: state.observedAt,
      provenance: state.provenance,
    } : null];
  })) as Record<ContextSource, Pick<SourceState, "status" | "checkedAt" | "observedAt" | "provenance"> | null>;
}

export async function GET(request: Request): Promise<Response> {
  const checkedAt = new Date();
  const issues: string[] = [];
  let status: RunStatus | null = null;
  let manifest: ContextManifest | null = null;
  let pointerUpdatedAt: string | null = null;
  const search = new URL(request.url).searchParams;
  const rawVersion = search.get("expectedVersion");
  const expectedVersion = rawVersion && /^[1-9]\d*$/.test(rawVersion) ? Number(rawVersion) : null;
  if (rawVersion !== null && (expectedVersion === null || !Number.isSafeInteger(expectedVersion))) {
    issues.push("invalid-expected-version");
  }
  const rawSources = search.getAll("expectedSource");
  if (!rawSources.every((source) => SOURCE_SET.has(source as ContextSource))) {
    issues.push("invalid-expected-source");
  }

  if (issues.length === 0) {
    try {
      const storageBackend = process.env.STORAGE_BACKEND?.trim() || "local";
      const configuredBlob = storageBackend === "blob" ? process.env.PUBLIC_BLOB_BASE_URL?.trim() : undefined;
      if (storageBackend !== "local" && storageBackend !== "blob") throw new Error("invalid storage backend");
      if (storageBackend === "blob" && (process.env.VERCEL_ENV === "preview" || !configuredBlob)) {
        throw new Error("Blob storage is unavailable in this deployment");
      }
      const base = configuredBlob ? blobBaseUrl(configuredBlob) : null;
      if (configuredBlob && !base) throw new Error("invalid Blob origin");
      const controller = new AbortController();
      const deadline = setTimeout(() => controller.abort(), UPSTREAM_DEADLINE_MS);
      let statusValue: unknown;
      let pointerValue: unknown;
      try {
        if (base) {
          [statusValue, pointerValue] = await Promise.all([
            readBlobJson(`${base}/context/status.json`, controller.signal).then((item) => item.value),
            readBlobJson(`${base}/context/latest.json`, controller.signal).then((item) => item.value),
          ]);
        } else {
          [statusValue, pointerValue] = await Promise.all([
            readLocalJson("context/status.json"),
            readLocalJson("context/latest.json"),
          ]);
        }
      } finally {
        controller.abort();
        clearTimeout(deadline);
      }
      if (!validStatus(statusValue)) issues.push("invalid-status");
      else status = statusValue;
      if (!isContextPointer(pointerValue, (manifestPath) => (
        base ? `${base}/${manifestPath}` : `/data/${manifestPath}`
      ))) {
        issues.push("invalid-pointer");
      } else {
        const pointer = pointerValue;
        pointerUpdatedAt = pointer.updatedAt;
        if (base) {
          const controller = new AbortController();
          const deadline = setTimeout(() => controller.abort(), UPSTREAM_DEADLINE_MS);
          try {
            const item = await readBlobJson(pointer.manifestUrl, controller.signal);
            if (!isContextManifest(item.value) || item.value.version !== pointer.version
              || !blobManifestValid(pointer, item.bytes, item.value, base)) issues.push("invalid-manifest");
            else manifest = item.value;
          } finally {
            controller.abort();
            clearTimeout(deadline);
          }
        } else {
          const value = await readLocalJson(pointer.manifestPath);
          if (!isContextManifest(value) || value.version !== pointer.version) issues.push("invalid-manifest");
          else if (!await localManifestAssetsAvailable(pointer.manifestPath, value)) issues.push("invalid-assets");
          else manifest = value;
        }
      }
    } catch {
      issues.push("initializing");
    }
  }

  const now = checkedAt.getTime();
  const maximumFreshAge = maximumFreshAgeMs();
  if (manifest && expectedVersion !== null && manifest.version !== expectedVersion) issues.push("unexpected-version");
  if (status && now - Date.parse(status.lastAttemptAt) > maximumFreshAge) issues.push("stale-heartbeat");
  if (!status?.lastCompleteForecastAt || now - Date.parse(status.lastCompleteForecastAt) > maximumFreshAge) {
    issues.push("stale-forecast");
  }
  const frames = manifest?.forecast.frames ?? [];
  const lastValidTime = frames.at(-1)?.validTime ?? status?.forecastLastValidTime ?? null;
  const remainingCoverageHours = lastValidTime ? (Date.parse(lastValidTime) - now) / 3_600_000 : null;
  if (remainingCoverageHours === null || remainingCoverageHours * 3_600_000 < MIN_COVERAGE_MS) {
    issues.push("insufficient-forecast-coverage");
  }
  if (manifest?.forecast.integratedStatus === "unavailable") issues.push("forecast-unavailable");

  const fatal = issues.length > 0;
  const selectedSources = rawSources.length ? [...new Set(rawSources)] as ContextSource[] : SOURCES;
  const sourceIssues = manifest ? selectedSources.flatMap((source) => {
    const state = manifest?.sources[source];
    return state?.status === "ok" ? [] : [`source-${source}-${state?.status ?? "missing"}`];
  }) : [];
  issues.push(...sourceIssues);
  const degraded = !fatal && (
    sourceIssues.length > 0
    || status?.outcome !== "fresh"
    || manifest?.forecast.integratedStatus === "retained"
  );
  const health = fatal ? "unhealthy" : degraded ? "degraded" : "healthy";
  return Response.json({
    schemaVersion: 1,
    ok: !fatal,
    status: health,
    checkedAt: checkedAt.toISOString(),
    contextVersion: manifest?.version ?? status?.contextVersion ?? null,
    publication: status?.outcome ?? "unknown",
    lastAttemptAt: status?.lastAttemptAt ?? null,
    lastCompleteForecastAt: status?.lastCompleteForecastAt ?? null,
    pointerUpdatedAt,
    forecastFrameCount: frames.length || status?.forecastFrameCount || 0,
    forecastLastValidTime: lastValidTime,
    remainingCoverageHours: remainingCoverageHours === null ? null : Math.round(remainingCoverageHours * 10) / 10,
    sources: publicSources(manifest),
    issues,
  }, { status: fatal ? 503 : 200, headers });
}
