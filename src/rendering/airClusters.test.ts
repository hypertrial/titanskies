import { describe, expect, it } from "vitest";
import type { AirQualityMonitor } from "@/data/context";
import { airClusterIdentity, airHoverLabel, airMarkerShape, clusterProjectedAirMonitors, compareAirMonitors } from "./airClusters";

function monitor(id: string, category: string, value: number, system: "us-epa-pm25-aqi" | "ca-aqhi" = "us-epa-pm25-aqi"): AirQualityMonitor {
  return { id, name: id, agency: "Agency", lat: 50, lon: -120, observedAt: "2026-08-19T17:00:00Z", category, indexSystem: system, indexValue: value };
}

function referenceClusters(points: Parameters<typeof clusterProjectedAirMonitors>[0], minimumDistance: number) {
  const clusters: ReturnType<typeof clusterProjectedAirMonitors> = [];
  for (const point of [...points].sort((left, right) => compareAirMonitors(left.monitor, right.monitor))) {
    const cluster = clusters.find((candidate) => Math.hypot(candidate.x - point.x, candidate.y - point.y) < minimumDistance);
    if (cluster) {
      cluster.monitors.push(point.monitor);
      cluster.monitors.sort(compareAirMonitors);
      cluster.mixed = cluster.mixed || cluster.representative.indexSystem !== point.monitor.indexSystem;
    } else {
      clusters.push({ representative: point.monitor, monitors: [point.monitor], x: point.x, y: point.y, mixed: false });
    }
  }
  return clusters;
}

describe("air monitor clustering", () => {
  it("keeps representatives at least the requested CSS-pixel distance apart", () => {
    const points = [
      { monitor: monitor("a", "Good", 20), x: 0, y: 0 },
      { monitor: monitor("b", "Moderate", 70), x: 39, y: 0 },
      { monitor: monitor("c", "Good", 10), x: 80, y: 0 },
    ];
    const clusters = clusterProjectedAirMonitors(points, 40);
    expect(clusters).toHaveLength(2);
    expect(clusters[0].representative.id).toBe("b");
    expect(clusters[0].monitors.map((item) => item.id)).toEqual(["b", "a"]);
    expect(airClusterIdentity(clusters)).toBe("b,a|c");
  });

  it("prioritizes shared health risk, relative severity, freshness, and stable id", () => {
    const high = monitor("high", "High", 8, "ca-aqhi");
    const moderate = monitor("moderate", "Moderate", 100);
    expect(compareAirMonitors(high, moderate)).toBeLessThan(0);
    const clusters = clusterProjectedAirMonitors([
      { monitor: high, x: 10, y: 10 },
      { monitor: moderate, x: 11, y: 11 },
    ], 44);
    expect(clusters[0].mixed).toBe(true);
    expect(clusters[0].representative).toBe(high);
  });

  it("uses the requested touch spacing without changing the underlying members", () => {
    const points = [
      { monitor: monitor("a", "Good", 20), x: 0, y: 0 },
      { monitor: monitor("b", "Good", 21), x: 42, y: 0 },
    ];
    expect(clusterProjectedAirMonitors(points, 40)).toHaveLength(2);
    expect(clusterProjectedAirMonitors(points, 44)[0].monitors).toHaveLength(2);
  });

  it("keeps the strict distance boundary and earliest eligible cluster", () => {
    const boundary = [
      { monitor: monitor("boundary-a", "Unhealthy", 300), x: 0, y: 0 },
      { monitor: monitor("boundary-b", "Moderate", 100), x: 40, y: 0 },
    ];
    expect(clusterProjectedAirMonitors(boundary, 40)).toHaveLength(2);

    const ambiguous = [
      { monitor: monitor("first", "Unhealthy", 300), x: 0, y: 0 },
      { monitor: monitor("second", "Unhealthy", 200), x: 70, y: 0 },
      { monitor: monitor("shared", "Moderate", 100), x: 35, y: 0 },
    ];
    expect(clusterProjectedAirMonitors(ambiguous, 60).map((cluster) => cluster.monitors.map((item) => item.id))).toEqual([["first", "shared"], ["second"]]);
  });

  it("matches the reference algorithm for a production-scale input without mutating it", () => {
    const points = Array.from({ length: 1_510 }, (_, index) => ({
      monitor: monitor(`station-${index.toString().padStart(4, "0")}`, index % 7 === 0 ? "Unhealthy" : index % 3 === 0 ? "Moderate" : "Good", (index * 37) % 500),
      x: (index * 73) % 1_280 + (index % 5) * 0.2,
      y: (index * 151) % 720 + (index % 7) * 0.2,
    }));
    const before = points.map((point) => ({ ...point }));
    expect(clusterProjectedAirMonitors(points, 40)).toEqual(referenceClusters(points, 40));
    const sorted = [...points].sort((left, right) => compareAirMonitors(left.monitor, right.monitor));
    expect(clusterProjectedAirMonitors(sorted, 40, true)).toEqual(referenceClusters(points, 40));
    expect(points).toEqual(before);
  });

  it("uses country-specific shapes and a ring for mixed systems", () => {
    const us = monitor("us", "Good", 20);
    const ca = { ...monitor("ca", "Moderate", 70), country: "CA" as const };
    expect(airMarkerShape({ representative: us, mixed: false })).toBe("circle");
    expect(airMarkerShape({ representative: ca, mixed: false })).toBe("diamond");
    expect(airMarkerShape({ representative: us, mixed: true })).toBe("ring");
  });
});

describe("air hover labels", () => {
  it("names a single station and its comparable AQI", () => {
    const station = { ...monitor("a", "Unhealthy", 172), name: "Calgary Northwest" };
    expect(airHoverLabel({ representative: station, monitors: [station] })).toEqual({
      title: "Calgary Northwest",
      detail: "AQI 172 · Unhealthy",
    });
  });

  it("summarizes a cluster with the representative reading", () => {
    const representative = { ...monitor("worst", "Unhealthy", 172), name: "Calgary Northwest" };
    const monitors = [representative, ...Array.from({ length: 8 }, (_, index) => monitor(`s${index}`, "Moderate", 70))];
    expect(airHoverLabel({ representative, monitors })).toEqual({
      title: "9 stations",
      detail: "AQI 172 · Unhealthy",
    });
  });

  it("keeps AQHI copy on the representative index", () => {
    const station = monitor("high", "High", 8, "ca-aqhi");
    const moderate = monitor("moderate", "Moderate", 100);
    expect(airHoverLabel({ representative: station, monitors: [station, moderate] })).toEqual({
      title: "2 stations",
      detail: "AQHI 8 · High",
    });
  });
});
