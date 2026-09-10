import { describe, expect, it } from "vitest";
import type { FireIncident } from "@/data/context";
import { clusterProjectedIncidents, compareIncidents, incidentClusterIdentity, incidentHoverLabel } from "./incidentClusters";

function incident(id: string, country: "US" | "CA" = "US"): FireIncident {
  return {
    id,
    name: `Fire ${id}`,
    country,
    lat: 45,
    lon: -110,
    status: country === "US" ? "Active" : "Out of control",
    areaHectares: 100,
    sourceArea: 100,
    sourceAreaUnit: "hectares",
    updatedAt: "2026-08-30T12:00:00Z",
    sourceUrl: "https://example.test/incidents",
  };
}

function referenceClusters(points: Parameters<typeof clusterProjectedIncidents>[0], minimumDistance: number) {
  const clusters: ReturnType<typeof clusterProjectedIncidents> = [];
  for (const point of [...points].sort((left, right) => compareIncidents(left.incident, right.incident))) {
    const cluster = clusters.find((candidate) => Math.hypot(candidate.x - point.x, candidate.y - point.y) < minimumDistance);
    if (cluster) cluster.incidents.push(point.incident);
    else clusters.push({ representative: point.incident, incidents: [point.incident], x: point.x, y: point.y });
  }
  return clusters;
}

describe("reported wildfire clustering", () => {
  it("uses stable ids for representative and member ordering across providers", () => {
    const clusters = clusterProjectedIncidents([
      { incident: incident("US:z-fire"), x: 2, y: 2 },
      { incident: incident("CA:a-fire", "CA"), x: 0, y: 0 },
      { incident: incident("US:b-fire"), x: 1, y: 1 },
    ], 40);
    expect(clusters[0].representative.id).toBe("CA:a-fire");
    expect(clusters[0].incidents.map((item) => item.id)).toEqual(["CA:a-fire", "US:b-fire", "US:z-fire"]);
    expect(incidentClusterIdentity(clusters)).toBe("CA:a-fire,US:b-fire,US:z-fire");
  });

  it("keeps the exact-distance boundary and chooses the earliest eligible cluster", () => {
    expect(clusterProjectedIncidents([
      { incident: incident("a"), x: 0, y: 0 },
      { incident: incident("b"), x: 40, y: 0 },
    ], 40)).toHaveLength(2);

    const ambiguous = clusterProjectedIncidents([
      { incident: incident("a"), x: 0, y: 0 },
      { incident: incident("b"), x: 70, y: 0 },
      { incident: incident("c"), x: 35, y: 0 },
    ], 60);
    expect(ambiguous.map((cluster) => cluster.incidents.map((item) => item.id))).toEqual([["a", "c"], ["b"]]);
  });

  it("matches the first-match reference for 900 production-scale points without mutating input", () => {
    const points = Array.from({ length: 900 }, (_, index) => ({
      incident: incident(`${index % 2 ? "US" : "CA"}:${index.toString().padStart(4, "0")}`, index % 2 ? "US" : "CA"),
      x: (index * 73) % 1_280 + (index % 5) * 0.2,
      y: (index * 151) % 720 + (index % 7) * 0.2,
    }));
    const before = points.map((point) => ({ ...point }));
    expect(clusterProjectedIncidents(points, 40)).toEqual(referenceClusters(points, 40));
    const sorted = [...points].sort((left, right) => compareIncidents(left.incident, right.incident));
    expect(clusterProjectedIncidents(sorted, 40, true)).toEqual(referenceClusters(points, 40));
    expect(points).toEqual(before);
  });

  it("keeps identity and member order stable when source objects are refreshed", () => {
    const first = clusterProjectedIncidents([
      { incident: incident("US:2"), x: 1, y: 1 },
      { incident: incident("CA:1", "CA"), x: 0, y: 0 },
    ], 40);
    const refreshed = clusterProjectedIncidents([
      { incident: { ...incident("CA:1", "CA") }, x: 0, y: 0 },
      { incident: { ...incident("US:2") }, x: 1, y: 1 },
    ], 40);
    expect(incidentClusterIdentity(refreshed)).toBe(incidentClusterIdentity(first));
    expect(refreshed[0].incidents.map((item) => item.id)).toEqual(first[0].incidents.map((item) => item.id));
  });

  it("labels individual and grouped reports without implying one fire", () => {
    const us = incident("US:1");
    const ca = incident("CA:1", "CA");
    expect(incidentHoverLabel({ representative: us, incidents: [us] })).toEqual({ title: "Fire US:1", detail: "WFIGS · Active" });
    expect(incidentHoverLabel({ representative: ca, incidents: [ca, us] })).toEqual({ title: "2 reported wildfires", detail: "Open to review each agency report" });
  });
});
