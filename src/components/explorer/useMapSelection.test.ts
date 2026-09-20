import { describe, expect, it } from "vitest";
import { INITIAL_MAP_SELECTION, reduceMapSelection } from "./useMapSelection";
import { newestTime } from "@/data/airQuality";

const city = {
  name: "Seattle",
  searchName: "Seattle",
  region: "Washington",
  country: "USA" as const,
  lon: -122.33,
  lat: 47.61,
  priority: 1 as const,
  mobile: false,
};

const monitor = {
  id: "airnow:1",
  name: "Seattle",
  lat: 47.61,
  lon: -122.33,
  observedAt: "2024-07-15T18:00:00Z",
  agency: "EPA",
  category: "Good",
  country: "US" as const,
  aqi: 42,
};

describe("map selection reducer paths", () => {
  it("clears selection independently of a pinned inspect point", () => {
    const inspecting = reduceMapSelection(INITIAL_MAP_SELECTION, { type: "select-map", lon: -122.3, lat: 47.6 });
    expect(inspecting.inspectPoint).toEqual({ lon: -122.3, lat: 47.6 });
    expect(inspecting.selectedCity).toBeNull();
    expect(inspecting.focusLocation).toBeNull();

    const withForecast = reduceMapSelection(inspecting, {
      type: "set-selection",
      selection: { kind: "forecast", lon: -122.3, lat: 47.6, modelRun: "2024-07-15T12:00:00Z", validTime: "2024-07-15T18:00:00Z", source: "firework", concentration: 8 },
    });
    expect(withForecast.selection?.kind).toBe("forecast");
    expect(reduceMapSelection(withForecast, { type: "clear-selection" }).selection).toBeNull();
    expect(reduceMapSelection(withForecast, { type: "clear-selection" }).inspectPoint).toEqual({ lon: -122.3, lat: 47.6 });
  });

  it("selects a city and a scene marker through distinct paths", () => {
    const ranked = reduceMapSelection(INITIAL_MAP_SELECTION, {
      type: "select-city",
      city,
      preserveForecastTime: true,
      requestId: 4,
    });
    expect(ranked).toMatchObject({
      selection: null,
      selectedCity: city,
      selectedFromRanking: true,
      inspectPoint: null,
      focusLocation: { lon: city.lon, lat: city.lat, requestId: 4 },
    });

    const marker = reduceMapSelection(ranked, {
      type: "select-marker",
      selection: { kind: "air-monitor", monitor },
    });
    expect(marker.selection).toEqual({ kind: "air-monitor", monitor });
    expect(marker.selectedCity).toBeNull();
    expect(marker.selectedFromRanking).toBe(false);
    expect(marker.focusLocation).toBeNull();
    expect(marker.inspectPoint).toBeNull();
    expect(reduceMapSelection(marker, { type: "clear-location" })).toEqual(INITIAL_MAP_SELECTION);
  });

  it("reuses newestTime when ranking clustered observation clocks", () => {
    expect(newestTime(["2024-07-15T17:00:00Z", "2024-07-15T19:10:00Z", undefined, "2024-07-15T18:00:00Z"])).toBe("2024-07-15T19:10:00Z");
    expect(newestTime([])).toBe("");
  });
});
