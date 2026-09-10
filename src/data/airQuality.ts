import { fetchJson } from "./contextClient";
import { LOCAL_FRESH_MS, MONITOR_MATCH_METERS, contextCapabilities, contextSourceLabel, isAirQualityMonitor, isFireIncident, isIso } from "./contextSchema";
import type { AirIndexMode, AirIndexSystem, AirMonitorSource, AirQualityMonitor, ContextManifest, FireIncident, SourceState } from "./contextSchema";

export async function loadMonitors(url: string): Promise<AirQualityMonitor[]> {
  const payload = await fetchJson<{ monitors?: unknown[] }>(url);
  return (payload.monitors ?? []).filter(isAirQualityMonitor).map(normalizeAirQualityMonitor);
}

export function normalizeAirQualityMonitor(monitor: AirQualityMonitor): AirQualityMonitor {
  if (monitor.indexSystem && monitor.indexValue !== undefined) return monitor;
  return {
    ...monitor,
    indexSystem: "us-epa-pm25-aqi",
    indexValue: monitor.aqi ?? 0,
    indexMethod: monitor.aqiMethod ?? "provider",
  };
}

export function monitorIndexSystem(monitor: AirQualityMonitor): AirIndexSystem {
  return monitor.indexSystem ?? "us-epa-pm25-aqi";
}

export function monitorIndexValue(monitor: AirQualityMonitor): number {
  return monitor.indexValue ?? monitor.aqi ?? 0;
}

export function aqhiDisplayValue(value: number): number | "10+" {
  if (value > 10) return "10+";
  return Math.max(1, Math.min(10, Math.floor(value + 0.5)));
}

export function monitorIndexLabel(monitor: AirQualityMonitor): string {
  return monitorIndexSystem(monitor) === "ca-aqhi"
    ? `AQHI ${aqhiDisplayValue(monitorIndexValue(monitor))}`
    : `AQI ${Math.round(monitorIndexValue(monitor))}`;
}

export function filterAirMonitors(monitors: AirQualityMonitor[], mode: AirIndexMode): AirQualityMonitor[] {
  return monitors.filter((monitor) => mode === "local"
    ? monitorIndexSystem(monitor) === "ca-aqhi" || (monitor.source === "airnow" && monitor.country === "US")
    : monitorIndexSystem(monitor) === "us-epa-pm25-aqi");
}

export function localAirIndicesAvailable(manifest: ContextManifest | null | undefined): boolean {
  return Boolean(manifest && contextCapabilities(manifest.version)?.aqhi && manifest.air.monitorSets?.aqhi?.url);
}

export function airMonitorUrls(manifest: ContextManifest, mode?: AirIndexMode): string[] {
  const sets = manifest.air.monitorSets;
  const urls: Record<AirMonitorSource, string | undefined> = {
    airnow: sets?.airnow?.url ?? manifest.air.monitorsUrl,
    bcair: sets?.bcair?.url ?? manifest.air.bcMonitorsUrl,
    sinaica: sets?.sinaica?.url ?? manifest.air.sinaicaMonitorsUrl,
    aqhi: sets?.aqhi?.url,
  };
  const names: AirMonitorSource[] = mode === "local" ? ["airnow", "aqhi"] : mode === "comparable" ? ["airnow", "bcair", "sinaica"] : ["airnow", "bcair", "sinaica", "aqhi"];
  return [...new Set(names.map((name) => urls[name]).filter((url): url is string => Boolean(url)))];
}

export function airObservationTime(manifest: ContextManifest, mode?: AirIndexMode): string {
  const names: AirMonitorSource[] = mode === "local" ? ["airnow", "aqhi"] : mode === "comparable" ? ["airnow", "bcair", "sinaica"] : ["airnow", "bcair", "sinaica", "aqhi"];
  return [
    ...(mode !== "local" ? [manifest.air.observedAt] : []),
    ...names.map((name) => manifest.air.monitorSets?.[name]?.observedAt),
  ].filter((value): value is string => Boolean(value) && isIso(value))
    .sort((left, right) => Date.parse(right) - Date.parse(left))[0] ?? "";
}

export function airSourceStates(manifest: ContextManifest, mode?: AirIndexMode): Array<[string, SourceState]> {
  const names: AirMonitorSource[] = mode === "local" ? ["airnow", "aqhi"] : mode === "comparable" ? ["airnow", "bcair", "sinaica"] : ["airnow", "bcair", "sinaica", "aqhi"];
  return names.flatMap((name) => {
    const state = manifest.sources[name];
    return state ? [[contextSourceLabel(name), state] as [string, SourceState]] : [];
  });
}

export function forecastSourceStates(manifest: ContextManifest): Array<[string, SourceState]> {
  const rows: Array<[string, SourceState | undefined]> = [
    [contextSourceLabel("firework"), manifest.sources.firework],
    [contextSourceLabel("hrrr"), manifest.sources.hrrr],
  ];
  return rows.filter((entry): entry is [string, SourceState] => Boolean(entry[1]));
}

export function preferredAirSourceState(manifest: ContextManifest, mode?: AirIndexMode): SourceState | null {
  const states = airSourceStates(manifest, mode).map(([, state]) => state);
  return states.find((state) => state.status === "ok")
    ?? states.find((state) => state.status === "stale")
    ?? states.find((state) => state.status === "error")
    ?? states[0]
    ?? null;
}

export function airViewUnavailable(manifest: ContextManifest): boolean {
  const states = airSourceStates(manifest, "comparable").map(([, state]) => state);
  if (!states.length) return !manifest.air.monitorsUrl;
  return states.every((state) => state.status === "unavailable") && airMonitorUrls(manifest, "comparable").length === 0;
}

export function airViewReady(airLayerOn: boolean, monitorsSettled: boolean): boolean {
  return !airLayerOn || monitorsSettled;
}

export function emptyAirMonitorWarning(count: number, unavailable: boolean): string | null {
  if (unavailable || count > 0) return null;
  return "No official monitors were available in the published air-quality data.";
}

export function fallbackSourceEntries(manifest: ContextManifest): Array<[string, SourceState]> {
  const rows: Array<[string, SourceState | undefined]> = [
    ...forecastSourceStates(manifest),
    ...airSourceStates(manifest),
    [contextSourceLabel("wfigs"), manifest.sources.wfigs],
    [contextSourceLabel("cwfis"), manifest.sources.cwfis],
  ];
  return rows.filter((entry): entry is [string, SourceState] => Boolean(entry[1]));
}

function haversineMeters(a: AirQualityMonitor, b: AirQualityMonitor): number {
  const toRad = (value: number) => value * Math.PI / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLon = toRad(b.lon - a.lon);
  const lat1 = toRad(a.lat);
  const lat2 = toRad(b.lat);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 6371000 * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
}

function monitorSource(monitor: AirQualityMonitor): AirMonitorSource {
  if (monitor.source) return monitor.source;
  if (monitor.id.startsWith("aqhi:")) return "aqhi";
  if (monitor.id.startsWith("bcair:")) return "bcair";
  if (monitor.id.startsWith("sinaica:")) return "sinaica";
  return "airnow";
}

function preferMonitor(current: AirQualityMonitor, candidate: AirQualityMonitor, now: number): AirQualityMonitor {
  const currentSource = monitorSource(current);
  const candidateSource = monitorSource(candidate);
  const currentAge = now - Date.parse(current.observedAt);
  const candidateAge = now - Date.parse(candidate.observedAt);
  const currentFresh = currentAge >= 0 && currentAge <= LOCAL_FRESH_MS;
  const candidateFresh = candidateAge >= 0 && candidateAge <= LOCAL_FRESH_MS;
  const candidateLocal = candidateSource !== "airnow";
  const currentLocal = currentSource !== "airnow";
  if (currentFresh !== candidateFresh) return candidateFresh ? candidate : current;
  if (candidateLocal && candidateFresh && !(currentLocal && currentFresh && Date.parse(current.observedAt) >= Date.parse(candidate.observedAt))) return candidate;
  if (currentLocal && currentFresh && !candidateFresh) return current;
  if (!candidateLocal && !currentFresh && candidateFresh) return candidate;
  if (!currentLocal && candidateLocal && !currentFresh) return candidate;
  return Date.parse(candidate.observedAt) > Date.parse(current.observedAt) ? candidate : current;
}

function nameTokens(value: string): Set<string> {
  return new Set(value.toLowerCase().split(/[^a-z0-9]+/i).filter((token) => token.length >= 4));
}

function sharesIdentity(left: AirQualityMonitor, right: AirQualityMonitor): boolean {
  if (left.id === right.id) return true;
  const leftTokens = nameTokens(left.name);
  const rightTokens = nameTokens(right.name);
  return leftTokens.size > 0 && ([...leftTokens].every((token) => rightTokens.has(token))
    || [...rightTokens].every((token) => leftTokens.has(token)));
}

export function mergeAirMonitors(groups: AirQualityMonitor[][], now = Date.now()): AirQualityMonitor[] {
  const merged: AirQualityMonitor[] = [];
  for (const monitor of groups.flat()) {
    const match = merged.find((item) => item.id === monitor.id
      || (monitorSource(item) !== monitorSource(monitor)
        && monitorIndexSystem(item) === monitorIndexSystem(monitor)
        && haversineMeters(item, monitor) <= MONITOR_MATCH_METERS && sharesIdentity(item, monitor)));
    if (!match) {
      merged.push(monitor);
      continue;
    }
    const preferred = preferMonitor(match, monitor, now);
    merged[merged.indexOf(match)] = preferred;
  }
  return merged.sort((left, right) => left.id.localeCompare(right.id));
}

export async function loadAirMonitors(
  manifest: ContextManifest,
  onPartialFailure?: (message: string) => void,
  mode?: AirIndexMode,
  onProgress?: (monitors: AirQualityMonitor[]) => void,
): Promise<AirQualityMonitor[]> {
  const urls = airMonitorUrls(manifest, mode);
  const loadedGroups: AirQualityMonitor[][] = urls.map(() => []);
  const results = await Promise.allSettled(urls.map(async (url, index) => {
    const monitors = await loadMonitors(url);
    loadedGroups[index] = monitors;
    // Keep source precedence stable even when downloads finish out of order.
    onProgress?.(mergeAirMonitors(loadedGroups));
    return monitors;
  }));
  const groups = results.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);
  if (urls.length && !groups.length) throw (results[0] as PromiseRejectedResult).reason;
  if (results.some((result) => result.status === "rejected")) {
    onPartialFailure?.("Some official air-quality sources could not load.");
  }
  return mergeAirMonitors(groups);
}

export async function loadIncidents(
  urls: Array<string | undefined>,
  onPartialFailure?: (message: string) => void,
): Promise<FireIncident[]> {
  const requested = urls.filter((url): url is string => Boolean(url));
  const results = await Promise.allSettled(requested.map((url) => fetchJson<{ incidents?: unknown[] }>(url)));
  const payloads = results.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);
  if (requested.length && !payloads.length) throw (results[0] as PromiseRejectedResult).reason;
  if (results.some((result) => result.status === "rejected")) {
    onPartialFailure?.("Some reported wildfire data could not load.");
  }
  const unique = new Map<string, FireIncident>();
  payloads.flatMap((payload) => payload.incidents ?? []).filter(isFireIncident).forEach((incident) => unique.set(incident.id, incident));
  return [...unique.values()];
}


export const EPA_AQI_CATEGORIES = [
  { label: "Good", range: "0–50", maximum: 50, color: "#00e400", className: "good" },
  { label: "Moderate", range: "51–100", maximum: 100, color: "#ffff00", className: "moderate" },
  { label: "Sensitive groups", range: "101–150", maximum: 150, color: "#ff7e00", className: "sensitive" },
  { label: "Unhealthy", range: "151–200", maximum: 200, color: "#ff0000", className: "unhealthy" },
  { label: "Very unhealthy", range: "201–300", maximum: 300, color: "#8f3f97", className: "very-unhealthy" },
  { label: "Hazardous", range: "301+", maximum: null, color: "#7e0023", className: "hazardous" },
] as const;

export function aqiColor(aqi: number): string {
  return EPA_AQI_CATEGORIES.find((category) => category.maximum === null || aqi <= category.maximum)?.color ?? EPA_AQI_CATEGORIES.at(-1)!.color;
}

const AQHI_COLORS = ["#7dd7e8", "#4fc3e0", "#2694c6", "#f5de59", "#f2be3e", "#ef8534", "#e76f91", "#de4f5b", "#c53645", "#9f2634"];

export function airIndexColor(monitor: AirQualityMonitor): string {
  if (monitorIndexSystem(monitor) !== "ca-aqhi") return aqiColor(monitorIndexValue(monitor));
  const display = aqhiDisplayValue(monitorIndexValue(monitor));
  return display === "10+" ? "#651a2b" : AQHI_COLORS[display - 1];
}

export function airRiskRank(monitor: AirQualityMonitor): number {
  const category = monitor.category.toLowerCase();
  if (monitorIndexSystem(monitor) === "ca-aqhi") {
    return category === "very high" ? 3 : category === "high" ? 2 : category === "moderate" ? 1 : 0;
  }
  if (["unhealthy", "very unhealthy", "hazardous", "beyond the aqi"].includes(category)) return 3;
  if (category === "unhealthy for sensitive groups") return 2;
  return category === "moderate" ? 1 : 0;
}

export function airRelativeSeverity(monitor: AirQualityMonitor): number {
  const value = monitorIndexValue(monitor);
  return monitorIndexSystem(monitor) === "ca-aqhi" ? Math.min(1, value / 11) : Math.min(1, value / 500);
}

export function sourceFreshness(state: SourceState, now = Date.now()): string {
  if (state.status === "unavailable") return "Unavailable";
  if (state.status === "error") return state.observedAt ? "Update failed" : "Error";
  if (!state.observedAt) return state.status === "stale" ? "Stale" : "Current";
  const minutes = Math.max(0, Math.floor((now - Date.parse(state.observedAt)) / 60_000));
  if (minutes < 60) return `${minutes}m old`;
  return `${Math.floor(minutes / 60)}h old`;
}

export function publicationFreshness(iso: string, now = Date.now()): string {
  const minutes = Math.max(0, Math.floor((now - Date.parse(iso)) / 60_000));
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return hours < 24 ? `${hours}h ago` : `${Math.floor(hours / 24)}d ago`;
}
