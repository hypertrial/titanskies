import type { ContextHealthStatus, ContextPublicationStatus, ContextSourceStatus } from "./contextSchema";

export const PUBLIC_DATA_WARNING = "TitanSkies uses preliminary observations, model forecasts, and agency-reported incidents that may be delayed, incomplete, inaccurate, retained from an earlier update, or unavailable. It does not identify smoke origin and is not an emergency alert or medical service. Check source timestamps, official alerts, and local health guidance before acting.";

const DATE_FORMATTER = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" });
const TIME_FORMATTER = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", hourCycle: "h12", timeZoneName: "short" });
const CLOCK_FORMATTER = new Intl.DateTimeFormat("en-US", {
  hour: "numeric",
  minute: "2-digit",
  hourCycle: "h12",
  timeZoneName: "short",
});
const MOBILE_CLOCK_FORMATTER = new Intl.DateTimeFormat("en-US", {
  hour: "numeric",
  minute: "2-digit",
  hourCycle: "h12",
});
const WEEKDAY_FORMATTER = new Intl.DateTimeFormat("en-US", { weekday: "short" });
const EXACT_FORMATTER = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  hourCycle: "h12",
  timeZoneName: "short",
});

const sameLocalDay = (left: Date, right: Date) => left.getFullYear() === right.getFullYear()
  && left.getMonth() === right.getMonth()
  && left.getDate() === right.getDate();

export function formatCompactTimelineTime(iso: string, referenceTime: number, live: boolean): string {
  const date = new Date(iso);
  if (!iso || !Number.isFinite(date.getTime())) return "—";
  const clock = CLOCK_FORMATTER.format(date);
  if (sameLocalDay(date, new Date(referenceTime))) return `${live ? "Now" : "Today"} · ${clock}`;
  return `${WEEKDAY_FORMATTER.format(date)} ${date.getDate()} · ${clock}`;
}

export function formatMobileTimelineTime(iso: string, referenceTime: number): string {
  const date = new Date(iso);
  if (!iso || !Number.isFinite(date.getTime())) return "—";
  const clock = MOBILE_CLOCK_FORMATTER.format(date);
  if (sameLocalDay(date, new Date(referenceTime))) return clock;
  return `${WEEKDAY_FORMATTER.format(date)} ${clock}`;
}

export function formatExactInterfaceTime(iso: string): string {
  const date = new Date(iso);
  return !iso || !Number.isFinite(date.getTime()) ? "Unavailable" : EXACT_FORMATTER.format(date);
}

export function formatExplorerTime(iso: string): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return `${DATE_FORMATTER.format(date)} · ${TIME_FORMATTER.format(date)}`;
}

export function formatObservationAge(iso: string, referenceTime: number): string {
  const minutes = Math.max(0, Math.round((referenceTime - Date.parse(iso)) / 60_000));
  return minutes < 60 ? `${minutes} min old` : `${Math.floor(minutes / 60)} hr ${minutes % 60} min old`;
}

export function formatLonLat(lat: number, lon: number): string {
  return `${Math.abs(lat).toFixed(2)}°${lat >= 0 ? "N" : "S"}, ${Math.abs(lon).toFixed(2)}°${lon >= 0 ? "E" : "W"}`;
}

export function userFacingLoadError(error: string): string {
  const message = error.trim();
  if (!message) return "Current data couldn’t be downloaded. Check your connection and try again.";
  if (/(?:failed to fetch|fetch failed|load failed|network(?: request)? (?:failed|error))/i.test(message)) {
    return "Current data couldn’t be downloaded. Check your connection and try again.";
  }
  return message;
}

function compactAge(age: string): string {
  return age.replace(/\s+(?:ago|old)$/i, "");
}

export function compactFreshnessLabel({
  mode,
  health,
  publication,
  source,
  publicationAge,
  compact,
}: {
  mode: "live" | "demo" | null;
  health: ContextHealthStatus | "checking" | "unavailable";
  publication: ContextPublicationStatus | null;
  source: ContextSourceStatus | null;
  publicationAge: string;
  compact: boolean;
}): string {
  if (!mode) return "Loading";
  if (mode === "demo") return compact ? "Demo" : "Demo data";
  if (health === "checking") return "Loading";
  const age = compactAge(publicationAge);
  if (health === "unhealthy") return compact ? "Stale" : `Stale · ${age}`;
  if (publication === "retained") return compact ? "Retained" : `Retained · ${age}`;
  if (health === "degraded" || source === "error" || source === "unavailable") return compact ? "Partial" : `Partial · ${age}`;
  if (source === "stale") return compact ? "Stale" : `Stale · ${age}`;
  if (health === "unavailable") return "Unavailable";
  return compact ? publicationAge : `Updated ${age}`;
}
