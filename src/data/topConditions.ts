import { monitorIndexSystem, monitorIndexValue } from "./airQuality";
import type { AirQualityMonitor } from "./contextSchema";
import { cityIdentity, locationAirReading, type LocationAirReading } from "./locationSearch";
import type { CityLabel } from "./ui";

export const TOP_CONDITIONS_LIMIT = 5;

export type RankedSmokeCity = { city: CityLabel; concentration: number };
export type RankedAqiCity = { city: CityLabel; reading: LocationAirReading };

export function formatForecastConcentration(value: number | null, maximum = 250): string {
  if (value == null || value < 1) return "<1";
  if (value >= maximum) return `${maximum}+`;
  return value < 10 ? value.toFixed(1) : Math.round(value).toString();
}

export function rankSmokeCities(
  cities: CityLabel[],
  sample: (city: CityLabel) => number | null,
  limit = TOP_CONDITIONS_LIMIT,
): RankedSmokeCity[] {
  return cities.flatMap((city) => {
    const concentration = sample(city);
    return concentration == null || !Number.isFinite(concentration) ? [] : [{ city, concentration }];
  }).sort((left, right) => right.concentration - left.concentration
    || left.city.priority - right.city.priority
    || cityIdentity(left.city).localeCompare(cityIdentity(right.city)))
    .slice(0, Math.max(0, limit));
}

export function rankAqiCities(
  cities: CityLabel[],
  monitors: AirQualityMonitor[],
  referenceTime: number,
  limit = TOP_CONDITIONS_LIMIT,
): RankedAqiCity[] {
  const comparable = monitors.filter((monitor) => monitorIndexSystem(monitor) === "us-epa-pm25-aqi");
  const byMonitor = new Map<string, RankedAqiCity>();
  for (const city of cities) {
    const reading = locationAirReading(city, comparable, referenceTime);
    if (!reading) continue;
    const candidate = { city, reading };
    const previous = byMonitor.get(reading.monitor.id);
    if (!previous || reading.distanceKm < previous.reading.distanceKm
      || (reading.distanceKm === previous.reading.distanceKm && (city.priority < previous.city.priority
        || (city.priority === previous.city.priority && cityIdentity(city).localeCompare(cityIdentity(previous.city)) < 0)))) {
      byMonitor.set(reading.monitor.id, candidate);
    }
  }
  return [...byMonitor.values()].sort((left, right) => monitorIndexValue(right.reading.monitor) - monitorIndexValue(left.reading.monitor)
    || Date.parse(right.reading.monitor.observedAt) - Date.parse(left.reading.monitor.observedAt)
    || left.reading.distanceKm - right.reading.distanceKm
    || left.city.priority - right.city.priority
    || cityIdentity(left.city).localeCompare(cityIdentity(right.city)))
    .slice(0, Math.max(0, limit));
}
