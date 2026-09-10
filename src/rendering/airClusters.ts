import { airRelativeSeverity, airRiskRank, monitorIndexLabel, monitorIndexSystem } from "@/data/airQuality";
import type { AirQualityMonitor } from "@/data/contextSchema";

export type ProjectedAirMonitor = { monitor: AirQualityMonitor; x: number; y: number };
export type AirMonitorCluster = {
  representative: AirQualityMonitor;
  monitors: AirQualityMonitor[];
  x: number;
  y: number;
  mixed: boolean;
};

export function compareAirMonitors(left: AirQualityMonitor, right: AirQualityMonitor): number {
  return airRiskRank(right) - airRiskRank(left)
    || airRelativeSeverity(right) - airRelativeSeverity(left)
    || Date.parse(right.observedAt) - Date.parse(left.observedAt)
    || left.id.localeCompare(right.id);
}

export function clusterProjectedAirMonitors(points: ProjectedAirMonitor[], minimumDistance: number, alreadySorted = false): AirMonitorCluster[] {
  const clusters: AirMonitorCluster[] = [];
  const buckets = new Map<number, number[]>();
  const cellSize = Math.max(minimumDistance, Number.EPSILON);
  const minimumDistanceSquared = minimumDistance * minimumDistance;
  const ordered = alreadySorted ? points : [...points].sort((left, right) => compareAirMonitors(left.monitor, right.monitor));
  for (const point of ordered) {
    const cellX = Math.floor(point.x / cellSize);
    const cellY = Math.floor(point.y / cellSize);
    let clusterIndex = Number.POSITIVE_INFINITY;
    for (let y = cellY - 1; y <= cellY + 1; y += 1) {
      for (let x = cellX - 1; x <= cellX + 1; x += 1) {
        for (const candidateIndex of buckets.get((x << 16) ^ (y & 0xffff)) ?? []) {
          if (candidateIndex >= clusterIndex) continue;
          const candidate = clusters[candidateIndex];
          const dx = candidate.x - point.x;
          const dy = candidate.y - point.y;
          if (dx * dx + dy * dy < minimumDistanceSquared) clusterIndex = candidateIndex;
        }
      }
    }
    const cluster = clusters[clusterIndex];
    if (cluster) {
      cluster.monitors.push(point.monitor);
      cluster.mixed = cluster.mixed || monitorIndexSystem(cluster.representative) !== monitorIndexSystem(point.monitor);
      continue;
    }
    clusters.push({ representative: point.monitor, monitors: [point.monitor], x: point.x, y: point.y, mixed: false });
    const key = (cellX << 16) ^ (cellY & 0xffff);
    const bucket = buckets.get(key);
    if (bucket) bucket.push(clusters.length - 1);
    else buckets.set(key, [clusters.length - 1]);
  }
  return clusters;
}

export function airClusterIdentity(clusters: AirMonitorCluster[]): string {
  return clusters.map((cluster) => cluster.monitors.map((monitor) => monitor.id).join(",")).join("|");
}

export function airMarkerShape(cluster: Pick<AirMonitorCluster, "mixed" | "representative">): "circle" | "diamond" | "ring" {
  if (cluster.mixed) return "ring";
  return cluster.representative.country === "CA" || monitorIndexSystem(cluster.representative) === "ca-aqhi"
    ? "diamond"
    : "circle";
}

export type AirHoverLabel = { title: string; detail: string };

export function airHoverLabel(cluster: Pick<AirMonitorCluster, "representative" | "monitors">): AirHoverLabel {
  const monitor = cluster.representative;
  return {
    title: cluster.monitors.length === 1 ? monitor.name : `${cluster.monitors.length} stations`,
    detail: `${monitorIndexLabel(monitor)} · ${monitor.category}`,
  };
}
