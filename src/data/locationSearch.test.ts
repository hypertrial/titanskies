import { describe, expect, it, vi } from "vitest";
import type { AirQualityMonitor } from "./context";
import { cityIdentity, clearRecentCities, locationAirReading, locationDistanceKm, locationStorage, normalizeLocationQuery, readRecentCities, searchCities, writeRecentCity } from "./locationSearch";
import type { CityLabel } from "./ui";

const city = (name: string, searchName: string, region: string, country: CityLabel["country"], lon = -100, lat = 40, priority: CityLabel["priority"] = 2): CityLabel => ({ name, searchName, region, country, lon, lat, priority, mobile: false });
const cities = [
  city("Montréal", "Montreal", "Québec", "CAN", -73.57, 45.5, 1),
  city("Washington", "Washington, D.C.", "District of Columbia", "USA", -77, 38.9, 1),
  city("Los Angeles", "Los Angeles", "California", "USA", -118.24, 34.05, 1),
  city("San Francisco", "San Francisco", "California", "USA", -122.42, 37.77, 1),
  city("Portland, ME", "Portland", "Maine", "USA", -70.25, 43.67),
  city("Portland, OR", "Portland", "Oregon", "USA", -122.68, 45.52),
];

describe("location search", () => {
  it("normalizes accents and ranks names, aliases, regions, and countries", () => {
    expect(normalizeLocationQuery("  Montréal, QC! ")).toBe("montreal qc");
    expect(searchCities(cities, "montreal")[0].name).toBe("Montréal");
    expect(searchCities(cities, "Washington DC")[0].name).toBe("Washington");
    expect(searchCities(cities, "WashingtonDC")[0].name).toBe("Washington");
    expect(searchCities(cities, "san").map((item) => item.name)).toEqual(["San Francisco"]);
    expect(searchCities(cities, "Portland Oregon").map((item) => item.name)).toEqual(["Portland, OR"]);
    expect(searchCities(cities, "Canada")[0].name).toBe("Montréal");
    expect(searchCities(cities, "USA", 2)).toHaveLength(2);
    const many = Array.from({ length: 10 }, (_, index) => city(`City ${index}`, `City ${index}`, "State", "USA"));
    expect(searchCities(many, "United States")).toHaveLength(8);
    expect(searchCities(cities, "")).toEqual([]);
  });

  it("stores at most five deduplicated valid recent cities and tolerates storage failures", () => {
    const values = new Map<string, string>();
    const storage = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); }, removeItem: (key: string) => { values.delete(key); } };
    writeRecentCity(storage, cities[0]); writeRecentCity(storage, cities[1]); writeRecentCity(storage, cities[0]);
    expect(readRecentCities(storage, cities).map(cityIdentity)).toEqual([cityIdentity(cities[0]), cityIdentity(cities[1])]);
    values.set("titanskies.locationRecents.v1", JSON.stringify(["unknown", cityIdentity(cities[1]), cityIdentity(cities[1])]));
    expect(readRecentCities(storage, cities)).toEqual([cities[1]]);
    values.set("titanskies.locationRecents.v1", "{");
    expect(readRecentCities(storage, cities)).toEqual([]);
    clearRecentCities(storage); expect(readRecentCities(storage, cities)).toEqual([]);
    const broken = { getItem: vi.fn(() => { throw new Error("denied"); }), setItem: vi.fn(() => { throw new Error("denied"); }), removeItem: vi.fn(() => { throw new Error("denied"); }) };
    expect(() => writeRecentCity(broken, cities[0])).not.toThrow(); expect(readRecentCities(broken, cities)).toEqual([]); expect(() => clearRecentCities(broken)).not.toThrow();
    const many = Array.from({ length: 6 }, (_, index) => city(`City ${index}`, `City ${index}`, "State", "USA"));
    many.forEach((item) => writeRecentCity(storage, item));
    expect(readRecentCities(storage, many).map((item) => item.name)).toEqual(["City 5", "City 4", "City 3", "City 2", "City 1"]);
  });
});

describe("location air reading", () => {
  const now = Date.parse("2026-08-22T12:00:00Z");
  const monitor = (id: string, system: "us-epa-pm25-aqi" | "ca-aqhi", lon: number, observedAt = "2026-08-22T11:00:00Z"): AirQualityMonitor => ({ id, name: id, agency: "Agency", lat: 40, lon, observedAt, indexSystem: system, indexValue: 42, indexMethod: "provider", category: "Good", country: system === "ca-aqhi" ? "CA" : "US", source: system === "ca-aqhi" ? "aqhi" : "airnow" });

  it("uses current comparable AQI within 50 km and falls back to Canadian AQHI", () => {
    const canadian = city("City", "City", "Ontario", "CAN", -100, 40);
    const aqi = monitor("aqi", "us-epa-pm25-aqi", -99.8);
    const aqhi = monitor("aqhi", "ca-aqhi", -99.9);
    expect(locationAirReading(canadian, [aqhi, aqi], now)?.monitor.id).toBe("aqi");
    expect(locationAirReading(canadian, [aqhi], now)?.monitor.id).toBe("aqhi");
    expect(locationAirReading(city("US", "US", "State", "USA", -100, 40), [aqhi], now)).toBeNull();
  });

  it("enforces distance and freshness and breaks equal-distance ties by newest observation", () => {
    const target = city("City", "City", "State", "USA", -100, 40);
    const old = monitor("old", "us-epa-pm25-aqi", -99.9, "2026-08-22T09:59:59Z");
    const far = monitor("far", "us-epa-pm25-aqi", -99.4);
    expect(locationAirReading(target, [old, far], now)).toBeNull();
    const earlier = monitor("earlier", "us-epa-pm25-aqi", -99.9, "2026-08-22T10:00:00Z");
    const newer = monitor("newer", "us-epa-pm25-aqi", -100.1, "2026-08-22T11:30:00Z");
    expect(locationAirReading(target, [earlier], now)?.monitor.id).toBe("earlier");
    expect(locationAirReading(target, [earlier, newer], now)?.monitor.id).toBe("newer");
    const stableA = monitor("a", "us-epa-pm25-aqi", -99.9);
    const stableB = monitor("b", "us-epa-pm25-aqi", -100.1);
    expect(locationAirReading(target, [stableB, stableA], now)?.monitor.id).toBe("a");
  });

  it("includes the 50 km boundary and excludes observations beyond it", () => {
    const target = city("Equator", "Equator", "State", "USA", 0, 0);
    const boundaryLongitude = 50 / 6371 * 180 / Math.PI;
    const boundary = { ...monitor("boundary", "us-epa-pm25-aqi", boundaryLongitude), lat: 0 };
    const outside = { ...boundary, id: "outside", lon: boundaryLongitude + 0.00001 };
    expect(locationDistanceKm(target, boundary)).toBeCloseTo(50, 8);
    expect(locationAirReading(target, [boundary], now)?.monitor.id).toBe("boundary");
    expect(locationAirReading(target, [outside], now)).toBeNull();
  });
});


it("tolerates a denied localStorage getter for every recent-search operation", () => {
  vi.stubGlobal("window", { get localStorage() { throw new DOMException("Denied", "SecurityError"); } });
  try {
    expect(locationStorage()).toBeNull();
    expect(readRecentCities(locationStorage(), cities)).toEqual([]);
    expect(() => writeRecentCity(locationStorage(), cities[0])).not.toThrow();
    expect(() => clearRecentCities(locationStorage())).not.toThrow();
  } finally {
    vi.unstubAllGlobals();
  }
});
