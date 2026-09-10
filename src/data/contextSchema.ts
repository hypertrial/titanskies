import contract from "../../shared/context-contract-v8.json";
import capabilityRegistry from "../../shared/context-capabilities.json";

export const CONTEXT_VERSION = contract.version;
export const CONTEXT_VERSIONS = new Set(capabilityRegistry.versions.map((item) => item.version));
export const CONTEXT_BOUNDS = contract.bounds;
export const CONTEXT_RASTER = contract.raster;
export const CONTEXT_DETAIL_RASTER = { width: contract.detailGrid.tileWidth, height: contract.detailGrid.tileHeight } as const;
export const CONTEXT_DETAIL_GRID = contract.detailGrid;
export const DISPLAY_BOUNDS = contract.displayBounds;
export const MONITOR_REGIONS = contract.monitorRegions;
export type ProductView = "forecast" | "air";
export type ForecastModelId = "best" | "hrrr" | "firework";
export type ContextSource = "airnow" | "bcair" | "sinaica" | "aqhi" | "wfigs" | "cwfis" | "firework" | "hrrr";
export type AirMonitorSource = "airnow" | "bcair" | "sinaica" | "aqhi";
export type AirIndexMode = "local" | "comparable";
export type AirIndexSystem = "us-epa-pm25-aqi" | "ca-aqhi";
export type AirMonitorCountry = "US" | "CA" | "MX";
export type AqiMethod = "provider" | "epa-nowcast-2024";
export type ContextSourceStatus = "ok" | "stale" | "error" | "unavailable";
export type ContextHealthStatus = "healthy" | "degraded" | "unhealthy";
export type ContextPublicationStatus = "fresh" | "retained" | "failed" | "unknown";
export type ForecastSourceLabel = "hrrr" | "firework" | "combined" | "none";

const CONTEXT_SOURCE_LABELS: Partial<Record<ContextSource, string>> = {
  firework: "ECCC FireWork",
  hrrr: "NOAA HRRR",
  airnow: "AirNow",
  bcair: "British Columbia ENV",
  sinaica: "INECC/SINAICA",
  aqhi: "ECCC AQHI",
  wfigs: "US wildfire reports",
  cwfis: "Canadian wildfire reports",
};

export const contextSourceLabel = (source: ContextSource): string => CONTEXT_SOURCE_LABELS[source] ?? source;

export type ContextHealth = {
  schemaVersion: 1;
  ok: boolean;
  status: ContextHealthStatus;
  checkedAt: string;
  contextVersion: number | null;
  publication: ContextPublicationStatus;
  lastAttemptAt: string | null;
  lastCompleteForecastAt: string | null;
  pointerUpdatedAt: string | null;
  forecastFrameCount: number;
  forecastLastValidTime: string | null;
  remainingCoverageHours: number | null;
  sources: Record<ContextSource, Pick<SourceState, "status" | "checkedAt" | "observedAt" | "provenance"> | null>;
  issues: string[];
};

export type SourceState = {
  status: ContextSourceStatus;
  checkedAt: string;
  observedAt: string | null;
  provenance: string;
  error: string | null;
  perimeterObservedAt?: string;
};

export type AirQualityMonitor = {
  id: string;
  name: string;
  agency: string;
  lat: number;
  lon: number;
  observedAt: string;
  aqi?: number;
  category: string;
  concentration?: number;
  unit?: string;
  source?: AirMonitorSource;
  sourceUrl?: string;
  aqiMethod?: AqiMethod;
  indexSystem?: AirIndexSystem;
  indexValue?: number;
  indexMethod?: AqiMethod;
  country?: AirMonitorCountry;
  preliminary?: boolean;
  nowcastConcentration?: number;
};

export type FireIncident = {
  id: string;
  name: string;
  country: "US" | "CA";
  lat: number;
  lon: number;
  status: string;
  areaHectares: number | null;
  sourceArea: number | null;
  sourceAreaUnit: string | null;
  updatedAt: string;
  sourceUrl: string;
};

export type ForecastContributor = { source: "hrrr" | "firework"; modelRun?: string };
export type ForecastDetailTile = {
  column: number;
  row: number;
  textureUrl: string;
  sourceMaskUrl: string;
};
export type ForecastDetailGrid = typeof contract.detailGrid;
export type ForecastFrame = {
  validTime: string;
  textureUrl?: string;
  modelRun?: string;
  sourceMaskUrl?: string;
  contributors?: ForecastContributor[];
  detailTiles?: ForecastDetailTile[];
};
export type ForecastRun = {
  modelId?: ForecastModelId;
  variable?: string;
  modelRun?: string;
  units?: string;
  nativeResolutionKm?: number | null;
  ingestMethod?: string;
  horizonHours?: number;
  selectionPolicy?: string;
  featherDistanceKm?: number;
  coveragePolicy?: string;
  coastalBufferKm?: number;
  coverageMaskVersion?: string;
  paletteVersion?: string;
  sourceMaskVersion?: string;
  detailGrid?: ForecastDetailGrid;
  rasterProcessingVersion?: string;
  legendUrl?: string;
  integratedStatus?: IntegratedStatus;
  frames: ForecastFrame[];
};

export type IntegratedStatus = "complete" | "retained" | "unavailable";

export type MonitorSet = { url: string; observedAt?: string; count?: number; countVersion?: string };
export type ContextManifest = {
  version: number;
  mode: "demo" | "live";
  generatedAt: string;
  bounds: typeof CONTEXT_BOUNDS;
  displayBounds?: typeof DISPLAY_BOUNDS;
  monitorRegions?: typeof MONITOR_REGIONS;
  sources: Partial<Record<ContextSource, SourceState>>;
  air: {
    observedAt?: string;
    monitorsUrl?: string;
    bcMonitorsUrl?: string;
    sinaicaMonitorsUrl?: string;
    monitorSets?: Partial<Record<AirMonitorSource, MonitorSet>>;
  };
  fires: {
    wfigsIncidentsUrl?: string;
    wfigsPerimeterTextureUrl?: string;
    cwfisIncidentsUrl?: string;
    cwfisPerimeterTextureUrl?: string;
  };
  forecast: ForecastRun;
  forecasts?: Partial<Record<ForecastModelId, ForecastRun>>;
};

export type ContextPointer = { version: number; manifestUrl: string; manifestPath?: string; updatedAt: string };

const FORECAST_MODEL_IDS: ForecastModelId[] = ["best", "hrrr", "firework"];
const INTEGRATED_STATUSES = new Set<IntegratedStatus>(["complete", "retained", "unavailable"]);
export const SOURCE_STATUSES = new Set<ContextSourceStatus>(["ok", "stale", "error", "unavailable"]);
export const HEALTH_STATUSES = new Set<ContextHealthStatus>(["healthy", "degraded", "unhealthy"]);
export const PUBLICATION_STATUSES = new Set<ContextPublicationStatus>(["fresh", "retained", "failed", "unknown"]);
const AQI_CATEGORIES = new Set(["good", "moderate", "unhealthy for sensitive groups", "unhealthy", "very unhealthy", "hazardous", "beyond the aqi"]);
const AQHI_CATEGORIES = new Set(["low", "moderate", "high", "very high"]);
const AIR_SOURCES = new Set<AirMonitorSource>(["airnow", "bcair", "sinaica", "aqhi"]);
const INDEX_SYSTEMS = new Set<AirIndexSystem>(["us-epa-pm25-aqi", "ca-aqhi"]);
const AQI_METHODS = new Set<AqiMethod>(["provider", "epa-nowcast-2024"]);
const AQHI_COUNT_VERSION = "eccc-aqhi-latest-v1";
export const MONITOR_MATCH_METERS = 150;
export const LOCAL_FRESH_MS = 2 * 60 * 60 * 1000;

export const isIso = (value: unknown): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));
export const isUrl = (value: unknown): value is string => typeof value === "string" && (value.startsWith("/") || value.startsWith("https://"));
export const isRecord = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === "object" && !Array.isArray(value);
const sameJson = (left: unknown, right: unknown): boolean => Object.is(left, right)
  || (Array.isArray(left) && Array.isArray(right) && left.length === right.length && left.every((value, index) => sameJson(value, right[index])))
  || (isRecord(left) && isRecord(right) && Object.keys(left).length === Object.keys(right).length
    && Object.keys(left).every((key) => Object.hasOwn(right, key) && sameJson(left[key], right[key])));
const isDetailGrid = (value: unknown, expected: typeof contract.detailGrid): value is ForecastDetailGrid => isRecord(value)
  && value.version === expected.version
  && value.width === expected.width && value.height === expected.height
  && value.columns === expected.columns && value.rows === expected.rows
  && value.tileWidth === expected.tileWidth && value.tileHeight === expected.tileHeight
  && value.sharedBoundaryPixels === expected.sharedBoundaryPixels;
const CONTRACTS = new Map([[CONTEXT_VERSION, contract]]);
type ContextCapabilities = (typeof capabilityRegistry.versions)[number];
const CAPABILITIES = new Map(capabilityRegistry.versions.map((item) => [item.version, item]));
export function contextCapabilities(version: number): ContextCapabilities | null {
  return CAPABILITIES.get(version) ?? null;
}
const gridForVersion = (version: number): typeof contract.detailGrid | null => {
  const selected = CONTRACTS.get(version);
  return selected && "detailGrid" in selected ? selected.detailGrid as typeof contract.detailGrid : null;
};

const isFiniteCoordinate = (lon: unknown, lat: unknown) => Number.isFinite(lon) && Number.isFinite(lat)
  && inDisplayBounds(Number(lon), Number(lat));

function isSupportedV4Outlook(run: ForecastRun, version: number): boolean {
  const capabilities = CAPABILITIES.get(version);
  if (!capabilities) return false;
  const accepted = (capabilities as ContextCapabilities & { acceptedOutlookHorizonHours?: number[] }).acceptedOutlookHorizonHours
    ?? [capabilities.outlookHorizonHours];
  if (!accepted.includes(run.horizonHours ?? -1)) return false;
  const horizon = run.horizonHours!;
  if (run.integratedStatus === "unavailable") return run.frames.length === 0;
  return capabilities.outlookMetadataRequired && horizon === capabilities.outlookHorizonHours
    ? run.frames.length === horizon + 1
    : run.frames.length > 0 && run.frames.length <= horizon + 1;
}

function isCanonicalV7Outlook(run: ForecastRun, generatedAt: string): boolean {
  if (run.integratedStatus === "unavailable" || run.frames.length === 0) return true;
  const generated = new Date(generatedAt);
  generated.setUTCMinutes(0, 0, 0);
  const start = generated.getTime();
  return run.frames.every((frame, index) => Date.parse(frame.validTime) === start + index * 3_600_000);
}

export function unwrapLon(lon: number): number {
  return lon > 90 ? lon - 360 : lon;
}

export function inMonitorBounds(lon: number, lat: number): boolean {
  return MONITOR_REGIONS.some((region) => lon >= region.west && lon <= region.east && lat >= region.south && lat <= region.north);
}

export function inDisplayBounds(lon: number, lat: number): boolean {
  const displayLon = unwrapLon(lon);
  return displayLon >= DISPLAY_BOUNDS.west && displayLon <= DISPLAY_BOUNDS.east
    && lat >= DISPLAY_BOUNDS.south && lat <= DISPLAY_BOUNDS.north;
}

export function inForecastBounds(lon: number, lat: number): boolean {
  return lon >= CONTEXT_BOUNDS.west && lon <= CONTEXT_BOUNDS.east
    && lat >= CONTEXT_BOUNDS.south && lat <= CONTEXT_BOUNDS.north;
}

export type DetailGridShape = Pick<ForecastDetailGrid, "columns" | "rows" | "tileWidth" | "tileHeight" | "sharedBoundaryPixels" | "width" | "height">;

export function detailTileBounds(grid: DetailGridShape, column: number, row: number): typeof CONTEXT_BOUNDS {
  const strideX = grid.tileWidth - grid.sharedBoundaryPixels;
  const strideY = grid.tileHeight - grid.sharedBoundaryPixels;
  const x0 = column * strideX;
  const y0 = row * strideY;
  const lon = (pixel: number) => CONTEXT_BOUNDS.west + pixel * (CONTEXT_BOUNDS.east - CONTEXT_BOUNDS.west) / (grid.width - 1);
  const lat = (pixel: number) => CONTEXT_BOUNDS.north - pixel * (CONTEXT_BOUNDS.north - CONTEXT_BOUNDS.south) / (grid.height - 1);
  return { west: lon(x0), east: lon(x0 + grid.tileWidth - 1), north: lat(y0), south: lat(y0 + grid.tileHeight - 1) };
}

export function detailTileId(grid: DetailGridShape, lon: number, lat: number): number {
  const column = Math.min(grid.columns - 1, Math.max(0, Math.floor((lon - CONTEXT_BOUNDS.west) / (CONTEXT_BOUNDS.east - CONTEXT_BOUNDS.west) * grid.columns)));
  const row = Math.min(grid.rows - 1, Math.max(0, Math.floor((CONTEXT_BOUNDS.north - lat) / (CONTEXT_BOUNDS.north - CONTEXT_BOUNDS.south) * grid.rows)));
  return row * grid.columns + column;
}

export function isContextManifest(value: unknown): value is ContextManifest {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<ContextManifest>;
  if (!CONTEXT_VERSIONS.has(item.version as number) || !isIso(item.generatedAt) || !isRecord(item.sources)
    || !isRecord(item.air) || !isRecord(item.fires) || !isRecord(item.forecast)) return false;
  const version = item.version as number;
  const expected = CONTRACTS.get(version);
  const capabilities = CAPABILITIES.get(version);
  if (!expected || !capabilities) return false;
  if (item.mode !== "demo" && item.mode !== "live") return false;
  if (!item.bounds || item.bounds.west !== CONTEXT_BOUNDS.west || item.bounds.south !== CONTEXT_BOUNDS.south
    || item.bounds.east !== CONTEXT_BOUNDS.east || item.bounds.north !== CONTEXT_BOUNDS.north) return false;
  const requiredSources = capabilities.requiredSources as ContextSource[];
  if (!requiredSources.every((name) => {
    const state = item.sources?.[name];
    return Boolean(state && SOURCE_STATUSES.has(state.status) && isIso(state.checkedAt) && isUrl(state.provenance)
      && (state.observedAt === null || isIso(state.observedAt)) && (state.error === null || typeof state.error === "string")
      && (state.perimeterObservedAt === undefined || isIso(state.perimeterObservedAt)));
  })) return false;
  const allowedSources = capabilities.allowedSources as ContextSource[];
  if (Object.keys(item.sources).some((name) => !allowedSources.includes(name as ContextSource))) return false;
  for (const name of allowedSources.filter((name) => !requiredSources.includes(name))) {
    const state = item.sources?.[name];
    if (!state) continue;
    if (!SOURCE_STATUSES.has(state.status) || !isIso(state.checkedAt) || !isUrl(state.provenance)
      || (state.observedAt !== null && !isIso(state.observedAt)) || (state.error !== null && typeof state.error !== "string")
      || (state.perimeterObservedAt !== undefined && !isIso(state.perimeterObservedAt))) return false;
  }
  if (item.displayBounds && (item.displayBounds.west !== expected.displayBounds.west || item.displayBounds.south !== expected.displayBounds.south
    || item.displayBounds.east !== expected.displayBounds.east || item.displayBounds.north !== expected.displayBounds.north)) return false;
  if (item.monitorRegions && (
    item.monitorRegions.length !== expected.monitorRegions.length
    || item.monitorRegions.some((region, index) => {
      const expectedRegion = expected.monitorRegions[index];
      return !expectedRegion || region.west !== expectedRegion.west || region.south !== expectedRegion.south
        || region.east !== expectedRegion.east || region.north !== expectedRegion.north;
    })
  )) return false;
  if (!isForecastRun(item.forecast, capabilities.sourceMaskRequired, false, capabilities.outlookMetadataRequired, capabilities.detailTilesRequired, version)) return false;
  if (capabilities.outlookMetadataRequired && !isSupportedV4Outlook(item.forecast, version)) return false;
  if (capabilities.canonicalHourlyOutlook && !isCanonicalV7Outlook(item.forecast, item.generatedAt)) return false;
  if (capabilities.multiModelForecast) {
    if (!isRecord(item.forecasts)) return false;
    if (Object.keys(item.forecasts).some((name) => !FORECAST_MODEL_IDS.includes(name as ForecastModelId))) return false;
    if (!FORECAST_MODEL_IDS.every((name) => isForecastRun(
      item.forecasts?.[name],
      name === "best",
      !capabilities.sourceTexturesPublic && name !== "best",
      capabilities.outlookMetadataRequired && name === "best",
      capabilities.detailTilesRequired && name === "best",
      version,
    ))) return false;
    const best = item.forecasts.best;
    if (capabilities.outlookMetadataRequired && best && !isSupportedV4Outlook(best, version)) return false;
    if (capabilities.canonicalHourlyOutlook && best && !isCanonicalV7Outlook(best, item.generatedAt)) return false;
    if (!sameJson(item.forecast, best)) return false;
  }
  if (item.air.monitorsUrl && (!isUrl(item.air.monitorsUrl) || !isIso(item.air.observedAt))) return false;
  if (item.air.bcMonitorsUrl && !isUrl(item.air.bcMonitorsUrl)) return false;
  if (item.air.sinaicaMonitorsUrl && !isUrl(item.air.sinaicaMonitorsUrl)) return false;
  if (item.air.monitorSets) {
    if (!isRecord(item.air.monitorSets)) return false;
    const allowedAirSources = AIR_SOURCES;
    for (const [name, set] of Object.entries(item.air.monitorSets)) {
      if (
        !allowedAirSources.has(name as AirMonitorSource)
        || !set
        || !isUrl(set.url)
        || (set.observedAt !== undefined && !isIso(set.observedAt))
        || (set.count !== undefined && (!Number.isInteger(set.count) || set.count < 0))
        || (set.countVersion !== undefined && typeof set.countVersion !== "string")
      ) return false;
    }
    const aqhiSet = item.air.monitorSets.aqhi;
    if (capabilities.aqhi && aqhiSet && (
      aqhiSet.countVersion !== AQHI_COUNT_VERSION
      || !Number.isInteger(aqhiSet.count)
      || aqhiSet.count! <= 0
      || !isIso(aqhiSet.observedAt)
    )) return false;
  }
  if (capabilities.aqhi && item.sources.aqhi?.status === "ok" && !item.air.monitorSets?.aqhi) return false;
  for (const url of [item.fires.wfigsIncidentsUrl, item.fires.wfigsPerimeterTextureUrl, item.fires.cwfisIncidentsUrl, item.fires.cwfisPerimeterTextureUrl]) {
    if (url !== undefined && !isUrl(url)) return false;
  }
  return true;
}

function isForecastRun(
  value: unknown,
  requireMask = false,
  metadataOnly = false,
  requireOutlookMetadata = false,
  requireDetail = false,
  version = CONTEXT_VERSION,
): value is ForecastRun {
  const capabilities = CAPABILITIES.get(version);
  if (!capabilities) return false;
  if (!isRecord(value) || !Array.isArray(value.frames)) return false;
  const frames = value.frames;
  let priorForecast = -Infinity;
  const forecastTimes = new Set<number>();
  if (!frames.every((frame) => {
    if (!isRecord(frame)) return false;
    if (frame.numericUrl !== undefined || frame.fieldUrl !== undefined || frame.values !== undefined) return false;
    if (metadataOnly && (frame.textureUrl !== undefined || frame.sourceMaskUrl !== undefined)) return false;
    const time = Date.parse(String(frame.validTime));
    const detailTiles = frame.detailTiles;
    const grid = gridForVersion(version);
    const validDetail = !requireDetail || (grid && Array.isArray(detailTiles) && detailTiles.length === grid.columns * grid.rows
      && detailTiles.every((tile, index) => isRecord(tile)
        && tile.column === index % grid.columns && tile.row === Math.floor(index / grid.columns)
        && isUrl(tile.textureUrl) && isUrl(tile.sourceMaskUrl)));
    const valid = isIso(frame.validTime) && time > priorForecast && !forecastTimes.has(time)
      && (metadataOnly || isUrl(frame.textureUrl))
      && (frame.modelRun === undefined || isIso(frame.modelRun))
      && (frame.sourceMaskUrl === undefined || isUrl(frame.sourceMaskUrl))
      && (!requireMask || frames.length === 0 || isUrl(frame.sourceMaskUrl));
    if (!validDetail || (!requireDetail && detailTiles !== undefined)) return false;
    priorForecast = time;
    forecastTimes.add(time);
    return valid;
  })) return false;
  if (value.integratedStatus !== undefined && !INTEGRATED_STATUSES.has(value.integratedStatus as IntegratedStatus)) return false;
  if (frames.length && requireOutlookMetadata && (
    value.selectionPolicy !== "feathered-max-v1"
    || value.featherDistanceKm !== 200
    || value.paletteVersion !== capabilities.paletteVersion
    || value.sourceMaskVersion !== "contribution-v2"
  )) return false;
  const hasCoverageMetadata = value.coveragePolicy !== undefined
    || value.coastalBufferKm !== undefined
    || value.coverageMaskVersion !== undefined;
  if (hasCoverageMetadata && (
    value.coveragePolicy !== "land-plus-coastal-water-v1"
    || value.coastalBufferKm !== 200
    || value.coverageMaskVersion !== capabilities.coverageMaskVersion
  )) return false;
  if (requireDetail && frames.length > 0 && (
    !gridForVersion(version) || !isDetailGrid(value.detailGrid, gridForVersion(version)!)
    || value.rasterProcessingVersion !== capabilities.detailRasterProcessingVersion
  )) return false;
  if (!requireDetail && (value.detailGrid !== undefined || value.rasterProcessingVersion !== undefined)) return false;
  return frames.length === 0 || (isIso(value.modelRun) && (metadataOnly || isUrl(value.legendUrl)));
}

export function normalizeContextManifest(manifest: ContextManifest): ContextManifest {
  if (!contextCapabilities(manifest.version)?.multiModelForecast) {
    const firework = { ...manifest.forecast, modelId: "firework" as const };
    return {
      ...manifest,
      forecast: firework,
      forecasts: { best: firework, hrrr: { frames: [] }, firework },
    };
  }
  const namedBest = manifest.forecasts?.best;
  const firework = manifest.forecasts?.firework ?? { frames: [] };
  const hrrr = manifest.forecasts?.hrrr ?? { frames: [] };
  const best = namedBest ?? { frames: [], integratedStatus: "unavailable" as const };
  return {
    ...manifest,
    forecast: best,
    forecasts: { best, hrrr, firework },
  };
}

export function forecastRun(manifest: ContextManifest, model: ForecastModelId): ForecastRun {
  const normalized = normalizeContextManifest(manifest);
  return normalized.forecasts?.[model] ?? { frames: [] };
}

export const UI_FORECAST_HORIZON_HOURS = 24;
export const V7_UI_FORECAST_HORIZON_HOURS = 36;

export function uiForecastHorizonHours(manifest: ContextManifest): number {
  return contextCapabilities(manifest.version)?.outlookHorizonHours ?? UI_FORECAST_HORIZON_HOURS;
}

export function visibleForecastRun(
  manifest: ContextManifest,
  horizonHours = uiForecastHorizonHours(manifest),
  nowMs = Date.now(),
): ForecastRun {
  const run = forecastRun(manifest, "best");
  const generated = Date.parse(manifest.generatedAt);
  const clock = manifest.mode === "live" ? nowMs : generated;
  const firstAfter = run.frames.findIndex((frame) => Date.parse(frame.validTime) > clock);
  const startFrame = firstAfter < 0
    ? manifest.mode === "live" ? run.frames.at(-1) : run.frames[0]
    : run.frames[Math.max(0, firstAfter - 1)];
  if (!startFrame) {
    return { ...run, horizonHours, frames: [] };
  }
  const startMs = Date.parse(startFrame.validTime);
  const endMs = startMs + horizonHours * 3_600_000;
  return {
    ...run,
    horizonHours,
    frames: run.frames.filter((frame) => {
      const time = Date.parse(frame.validTime);
      return time >= startMs && time <= endMs;
    }),
  };
}

export function forecastFrameRun(frame: ForecastFrame | null | undefined, source?: ForecastSourceLabel): string {
  if (!frame) return "";
  if (source === "hrrr" || source === "firework") {
    return frame.contributors?.find((item) => item.source === source)?.modelRun ?? frame.modelRun ?? "";
  }
  return frame.modelRun ?? "";
}

export function isAirQualityMonitor(value: unknown): value is AirQualityMonitor {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<AirQualityMonitor>;
  if (!(typeof item.id === "string" && Boolean(item.id) && typeof item.name === "string" && typeof item.agency === "string"
    && Number.isFinite(item.lon) && Number.isFinite(item.lat) && inMonitorBounds(Number(item.lon), Number(item.lat))
    && isIso(item.observedAt) && typeof item.category === "string")) return false;
  const system = item.indexSystem;
  const legacy = system === undefined && Number.isFinite(item.aqi) && item.aqi! >= 0
    && Number.isFinite(item.concentration) && item.concentration! >= 0 && typeof item.unit === "string"
    && AQI_CATEGORIES.has(item.category.toLowerCase());
  const indexed = system !== undefined && INDEX_SYSTEMS.has(system) && Number.isFinite(item.indexValue) && item.indexValue! >= 0
    && (system === "ca-aqhi" ? item.indexValue! > 0 && item.source === "aqhi" && item.country === "CA" && AQHI_CATEGORIES.has(item.category.toLowerCase())
      : AQI_CATEGORIES.has(item.category.toLowerCase()) && Number.isFinite(item.concentration) && item.concentration! >= 0 && typeof item.unit === "string");
  if (!legacy && !indexed) return false;
  if (item.source !== undefined && !AIR_SOURCES.has(item.source)) return false;
  if (item.sourceUrl !== undefined && !isUrl(item.sourceUrl)) return false;
  if (item.aqiMethod !== undefined && !AQI_METHODS.has(item.aqiMethod)) return false;
  if (item.indexMethod !== undefined && !AQI_METHODS.has(item.indexMethod)) return false;
  if (item.country !== undefined && !(["US", "CA", "MX"] as const).includes(item.country)) return false;
  if (item.nowcastConcentration !== undefined && !(Number.isFinite(item.nowcastConcentration) && item.nowcastConcentration >= 0)) return false;
  return true;
}

export function isFireIncident(value: unknown): value is FireIncident {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<FireIncident>;
  return typeof item.id === "string" && Boolean(item.id) && typeof item.name === "string" && (item.country === "US" || item.country === "CA")
    && isFiniteCoordinate(item.lon, item.lat) && typeof item.status === "string" && isIso(item.updatedAt) && isUrl(item.sourceUrl)
    && (item.areaHectares === null || (Number.isFinite(item.areaHectares) && item.areaHectares! >= 0));
}

export type ContextSelection =
  | { kind: "air-monitor"; monitor: AirQualityMonitor }
  | { kind: "air-cluster"; monitors: AirQualityMonitor[] }
  | { kind: "incident"; incident: FireIncident }
  | { kind: "incident-cluster"; incidents: FireIncident[] }
  | {
    kind: "forecast";
    lon: number;
    lat: number;
    modelRun: string;
    validTime: string;
    source?: ForecastSourceLabel;
    concentration?: number | null;
    concentrationMax?: number;
    horizonHours?: number;
    interpolation?: {
      fromValidTime: string;
      toValidTime: string;
      t: number;
      fromSource: ForecastSourceLabel;
      toSource: ForecastSourceLabel;
    };
  };
