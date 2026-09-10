import { describe, expect, it } from "vitest";
import type { AirQualityMonitor } from "./context";
import { formatForecastConcentration, rankAqiCities, rankSmokeCities } from "./topConditions";
import type { CityLabel } from "./ui";

const city = (name: string, lon: number, priority: CityLabel["priority"] = 2, country: CityLabel["country"] = "USA"): CityLabel => ({
  name, searchName: name, region: "Region", country, lon, lat: 40, priority, mobile: false,
});
const monitor = (id: string, lon: number, value: number, observedAt = "2026-08-22T11:00:00Z", system: AirQualityMonitor["indexSystem"] = "us-epa-pm25-aqi"): AirQualityMonitor => ({
  id, name: id, agency: "Agency", lat: 40, lon, observedAt, indexSystem: system, indexValue: value,
  indexMethod: "provider", category: system === "ca-aqhi" ? "High" : "Moderate", country: system === "ca-aqhi" ? "CA" : "US", source: system === "ca-aqhi" ? "aqhi" : "airnow",
});

describe("top smoke conditions", () => {
  it("ranks finite samples with stable ties and a bounded result count", () => {
    const cities = [city("Beta", -100), city("Alpha", -101, 1), city("None", -102), city("Dense", -103)];
    const values = new Map([["Beta", 10], ["Alpha", 10], ["Dense", 90]]);
    const sampled: string[] = [];
    expect(rankSmokeCities(cities, (item) => {
      sampled.push(item.name);
      return values.get(item.name) ?? null;
    }).map((item) => item.city.name)).toEqual(["Dense", "Alpha", "Beta"]);
    expect(sampled).toEqual(cities.map((item) => item.name));
    expect(rankSmokeCities(cities, () => 2, 2)).toHaveLength(2);
    expect(rankSmokeCities(cities, () => Number.NaN)).toEqual([]);
  });

  it("uses the selected display palette precision", () => {
    expect(formatForecastConcentration(0.8)).toBe("<1");
    expect(formatForecastConcentration(7.24)).toBe("7.2");
    expect(formatForecastConcentration(19.6)).toBe("20");
    expect(formatForecastConcentration(250, 250)).toBe("250+");
    expect(formatForecastConcentration(999.6, 1000)).toBe("1000");
    expect(formatForecastConcentration(1000, 1000)).toBe("1000+");
  });
});

describe("top AQI conditions", () => {
  const now = Date.parse("2026-08-22T12:00:00Z");

  it("ranks each city's nearest comparable reading and excludes AQHI", () => {
    const cities = [city("West", -100), city("East", -99), city("Canada", -98, 2, "CAN")];
    const readings = [monitor("west-near", -99.9, 60), monitor("west-high-farther", -99.7, 180), monitor("east", -99, 120), monitor("aqhi", -98, 10, undefined, "ca-aqhi")];
    expect(rankAqiCities(cities, readings, now).map((item) => [item.city.name, item.reading.monitor.id])).toEqual([
      ["East", "east"], ["West", "west-near"],
    ]);
  });

  it("deduplicates one station assigned to nearby cities using distance then priority", () => {
    const shared = monitor("shared", -100, 90);
    const cities = [city("Far", -100.1, 1), city("Near", -100.005, 2), city("Same priority", -99.99, 1)];
    const ranked = rankAqiCities(cities, [shared], now);
    expect(ranked).toHaveLength(1);
    expect(ranked[0].city.name).toBe("Near");
  });

  it("preserves freshness, radius, stable ordering, and the limit", () => {
    const cities = Array.from({ length: 7 }, (_, index) => city(`City ${index}`, -100 + index));
    const readings = cities.map((item, index) => monitor(`monitor-${index}`, item.lon, 100 - index));
    readings.push(monitor("stale", cities[0].lon, 500, "2026-08-22T09:59:59Z"));
    expect(rankAqiCities(cities, readings, now)).toHaveLength(5);
    expect(rankAqiCities(cities, readings, now).map((item) => item.reading.monitor.id)).toEqual(readings.slice(0, 5).map((item) => item.id));
  });
});
