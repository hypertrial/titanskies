import type { FireIncident } from "@/data/contextSchema";

export type ProjectedIncident = { incident: FireIncident; x: number; y: number };
export type IncidentCluster = {
  representative: FireIncident;
  incidents: FireIncident[];
  x: number;
  y: number;
};

export function compareIncidents(left: FireIncident, right: FireIncident): number {
  return left.id.localeCompare(right.id);
}

export function clusterProjectedIncidents(points: ProjectedIncident[], minimumDistance: number, alreadySorted = false): IncidentCluster[] {
  const clusters: IncidentCluster[] = [];
  const buckets = new Map<number, number[]>();
  const cellSize = Math.max(minimumDistance, Number.EPSILON);
  const minimumDistanceSquared = minimumDistance * minimumDistance;
  const ordered = alreadySorted ? points : [...points].sort((left, right) => compareIncidents(left.incident, right.incident));
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
      cluster.incidents.push(point.incident);
      continue;
    }
    clusters.push({ representative: point.incident, incidents: [point.incident], x: point.x, y: point.y });
    const key = (cellX << 16) ^ (cellY & 0xffff);
    const bucket = buckets.get(key);
    if (bucket) bucket.push(clusters.length - 1);
    else buckets.set(key, [clusters.length - 1]);
  }
  return clusters;
}

export function incidentClusterIdentity(clusters: IncidentCluster[]): string {
  return clusters.map((cluster) => cluster.incidents.map((incident) => incident.id).join(",")).join("|");
}

export function incidentHoverLabel(cluster: Pick<IncidentCluster, "representative" | "incidents">) {
  return cluster.incidents.length === 1
    ? { title: cluster.representative.name, detail: `${cluster.representative.country === "US" ? "WFIGS" : "CWFIS"} · ${cluster.representative.status}` }
    : { title: `${cluster.incidents.length} reported wildfires`, detail: "Open to review each agency report" };
}
