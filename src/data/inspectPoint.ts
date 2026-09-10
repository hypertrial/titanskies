import { formatForecastConcentration } from "./topConditions";
import { locationDistanceKm } from "./locationSearch";

export type InspectPoint = {
  lon: number;
  lat: number;
};

export type InspectPointEvent =
  | { type: "set"; lon: number; lat: number }
  | { type: "clear" };

export function reduceInspectPoint(event: InspectPointEvent): InspectPoint | null {
  if (event.type === "clear") return null;
  return { lon: event.lon, lat: event.lat };
}

export function shouldKeepInspectPanel(inspectPoint: InspectPoint | null, panel: string | null): boolean {
  return inspectPoint != null && panel === "selection";
}

export function shouldInspectMapClick(layers: { forecast: boolean }): boolean {
  return layers.forecast;
}

export function nearestLonLat<T extends { lon: number; lat: number }>(
  items: T[],
  cursor: InspectPoint,
): T | null {
  return items.reduce<T | null>((best, item) => {
    if (!best) return item;
    const distance = locationDistanceKm(item, cursor);
    const bestDistance = locationDistanceKm(best, cursor);
    return distance < bestDistance ? item : best;
  }, null);
}

export type KeyboardInspectSelection<M extends { lon: number; lat: number }> =
  | { kind: "air-monitor"; monitor: M }
  | { kind: "forecast" };

export function keyboardInspectSelection<M extends { lon: number; lat: number }>(input: {
  air: boolean;
  monitors: M[];
  cursor: InspectPoint;
}): KeyboardInspectSelection<M> | null {
  if (input.air) {
    const monitor = nearestLonLat(input.monitors, input.cursor);
    return monitor ? { kind: "air-monitor", monitor } : null;
  }
  return { kind: "forecast" };
}

export function inspectMarkerCopy(input: {
  hasPoint: boolean;
  loading: boolean;
  inBounds: boolean;
  source?: string;
  concentration?: number | null;
  concentrationMax?: number;
}): { title: string; value: string } | null {
  if (!input.hasPoint) return null;
  if (input.loading) return { title: "Modeled smoke", value: "Loading…" };
  if (!input.inBounds || input.source === "none" || input.concentration == null) {
    return { title: "Modeled smoke", value: "Unavailable" };
  }
  return {
    title: "Modeled smoke",
    value: `${formatForecastConcentration(input.concentration, input.concentrationMax)} µg/m³`,
  };
}
