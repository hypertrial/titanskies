import { monitorIndexSystem } from "./airQuality";
import type { AirQualityMonitor } from "./contextSchema";
import type { CityLabel } from "./ui";

export const LOCATION_RECENTS_KEY = "titanskies.locationRecents.v1";
export const LOCATION_RESULT_LIMIT = 8;
export const LOCATION_AIR_RADIUS_KM = 50;
export const LOCATION_AIR_MAX_AGE_MS = 2 * 60 * 60 * 1000;

export type LocationAirReading = { monitor: AirQualityMonitor; distanceKm: number };

export const cityIdentity = (city: CityLabel) => `${city.country}:${city.region}:${city.searchName}`;

export function normalizeLocationQuery(value: string): string {
  return value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim().replace(/\s+/g, " ");
}

function citySearchText(city: CityLabel): string {
  const canonical = normalizeLocationQuery(city.searchName);
  const country = city.country === "CAN" ? "Canada" : city.country === "USA" ? "United States America" : "Mexico";
  return normalizeLocationQuery(`${city.name} ${canonical} ${city.region} ${city.country} ${country}`);
}

export function searchCities(cities: CityLabel[], query: string, limit = LOCATION_RESULT_LIMIT): CityLabel[] {
  const normalized = normalizeLocationQuery(query);
  if (!normalized) return [];
  const terms = normalized.split(" ");
  return cities.flatMap((city) => {
    const name = normalizeLocationQuery(city.name);
    const canonical = normalizeLocationQuery(city.searchName);
    const text = citySearchText(city);
    const compactAliasMatch = canonical.replace(/ /g, "").startsWith(normalized.replace(/ /g, ""));
    if (!compactAliasMatch && !terms.every((term) => text.includes(term))) return [];
    const words = text.split(" ");
    const rank = name === normalized || canonical === normalized ? 0
      : name.startsWith(normalized) || canonical.startsWith(normalized) || compactAliasMatch ? 1
        : terms.every((term) => words.some((word) => word.startsWith(term))) ? 2 : 3;
    return [{ city, rank }];
  }).sort((left, right) => left.rank - right.rank
    || left.city.priority - right.city.priority
    || left.city.name.localeCompare(right.city.name)
    || left.city.region.localeCompare(right.city.region))
    .slice(0, Math.max(0, limit)).map(({ city }) => city);
}

export function locationStorage(): Storage | null {
  try { return window.localStorage; } catch { return null; }
}

export function readRecentCities(storage: Pick<Storage, "getItem"> | null, cities: CityLabel[]): CityLabel[] {
  try {
    const value: unknown = JSON.parse(storage?.getItem(LOCATION_RECENTS_KEY) ?? "[]");
    if (!Array.isArray(value)) return [];
    const byId = new Map(cities.map((city) => [cityIdentity(city), city]));
    const seen = new Set<string>();
    return value.flatMap((id) => {
      if (typeof id !== "string" || seen.has(id) || !byId.has(id)) return [];
      seen.add(id);
      return [byId.get(id)!];
    }).slice(0, 5);
  } catch { return []; }
}

export function writeRecentCity(storage: Pick<Storage, "getItem" | "setItem"> | null, city: CityLabel): void {
  try {
    const current: unknown = JSON.parse(storage?.getItem(LOCATION_RECENTS_KEY) ?? "[]");
    const id = cityIdentity(city);
    const ids = [id, ...(Array.isArray(current) ? current.filter((item): item is string => typeof item === "string" && item !== id) : [])].slice(0, 5);
    storage?.setItem(LOCATION_RECENTS_KEY, JSON.stringify(ids));
  } catch { /* Storage can be disabled or full; search still works. */ }
}

export function clearRecentCities(storage: Pick<Storage, "removeItem"> | null): void {
  try { storage?.removeItem(LOCATION_RECENTS_KEY); } catch { /* Optional local convenience only. */ }
}

export function locationDistanceKm(a: Pick<CityLabel, "lat" | "lon">, b: Pick<AirQualityMonitor, "lat" | "lon">): number {
  const radians = (value: number) => value * Math.PI / 180;
  const dLat = radians(b.lat - a.lat);
  const dLon = radians(b.lon - a.lon);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(radians(a.lat)) * Math.cos(radians(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 6371 * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h));
}

export function locationAirReading(city: CityLabel, monitors: AirQualityMonitor[], referenceTime: number): LocationAirReading | null {
  const eligible = (system: "us-epa-pm25-aqi" | "ca-aqhi") => monitors.flatMap((monitor) => {
    const observed = Date.parse(monitor.observedAt);
    if (monitorIndexSystem(monitor) !== system || !Number.isFinite(observed) || referenceTime - observed < 0 || referenceTime - observed > LOCATION_AIR_MAX_AGE_MS) return [];
    const distanceKm = locationDistanceKm(city, monitor);
    return distanceKm <= LOCATION_AIR_RADIUS_KM ? [{ monitor, distanceKm }] : [];
  }).sort((left, right) => left.distanceKm - right.distanceKm
    || Date.parse(right.monitor.observedAt) - Date.parse(left.monitor.observedAt)
    || left.monitor.id.localeCompare(right.monitor.id))[0] ?? null;
  return eligible("us-epa-pm25-aqi") ?? (city.country === "CAN" ? eligible("ca-aqhi") : null);
}
