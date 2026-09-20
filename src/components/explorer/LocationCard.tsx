import { airIndexColor, monitorIndexLabel, monitorIndexSystem } from "@/data/airQuality";
import { inForecastBounds } from "@/data/contextSchema";
import { formatExplorerTime, formatObservationAge, PUBLIC_DATA_WARNING } from "@/data/interfacePresentation";
import { integratedSourceLabel } from "@/data/forecastRaster";
import type { LocationAirReading } from "@/data/locationSearch";
import { formatForecastConcentration } from "@/data/topConditions";
import { COUNTRY_LABELS, type CityLabel, type MapSelection } from "@/data/ui";
import { SelectionMore } from "./DetailsCard";

export function LocationCard({ city, forecast, smokeLoading, smokeWarning, airReading, airLoading, airError, airWarning, referenceTime, compact, onRetryAir, onMethodology }: {
  city: CityLabel;
  forecast: Extract<MapSelection, { kind: "forecast" }> | null;
  smokeLoading: boolean;
  smokeWarning: string | null;
  airReading: LocationAirReading | null;
  airLoading: boolean;
  airError: string | null;
  airWarning: string | null;
  referenceTime: number;
  compact: boolean;
  onRetryAir: () => void;
  onMethodology: () => void;
}) {
  const outsideCoverage = !inForecastBounds(city.lon, city.lat);
  const smokeValue = outsideCoverage ? "Unavailable" : smokeLoading ? "Loading…"
    : forecast?.source === "none" || forecast?.concentration == null ? "Unavailable"
      : `${formatForecastConcentration(forecast.concentration, forecast.concentrationMax)} µg/m³`;
  const monitor = airReading?.monitor;
  const indexColor = monitor ? airIndexColor(monitor) : undefined;
  const distance = airReading ? airReading.distanceKm < 10 ? airReading.distanceKm.toFixed(1) : Math.round(airReading.distanceKm).toString() : null;
  const sourceLabel = forecast ? integratedSourceLabel(forecast.source) : "";
  const guidanceUrl = monitor && monitorIndexSystem(monitor) === "ca-aqhi"
    ? "https://www.canada.ca/en/environment-climate-change/services/air-quality-health-index/about.html"
    : "https://www.airnow.gov/aqi/aqi-basics/using-air-quality-index/";
  const provenance = <>{forecast ? <p>Forecast valid <time dateTime={forecast.validTime}>{formatExplorerTime(forecast.validTime)}</time>; model updated <time dateTime={forecast.modelRun}>{formatExplorerTime(forecast.modelRun)}</time> by {sourceLabel}.</p> : null}{smokeWarning ? <p>{smokeWarning}</p> : null}{monitor ? <p>{monitor.name} · {distance} km · {monitor.agency}. Station observation <time dateTime={monitor.observedAt}>{formatExplorerTime(monitor.observedAt)}</time>.</p> : null}{airWarning ? <p>{airWarning}</p> : null}{airError ? <p>{airError}</p> : null}</>;
  const supportingDetails = <>{monitor ? <a className="details-link" href={guidanceUrl} target="_blank" rel="noreferrer">Read official {monitorIndexSystem(monitor) === "ca-aqhi" ? "AQHI" : "AQI"} guidance</a> : null}<p className="details-note">Forecast smoke and station air quality are separate datasets.</p><p className="details-note" data-testid="selection-data-warning">{PUBLIC_DATA_WARNING}</p><button className="context-link" type="button" onClick={onMethodology}>About these values</button></>;
  const extras = <><div className="selection-provenance">{provenance}</div>{supportingDetails}</>;
  return <div className="location-card" data-testid="location-card" aria-label={`Smoke and air quality for ${city.name}`}>
    <p className="location-region">{city.region}, {COUNTRY_LABELS[city.country]}</p>
    <div className="location-metrics">
      <section><span className="eyebrow">Modeled smoke</span><strong data-testid="location-smoke-value">{smokeValue}</strong>{forecast && !outsideCoverage ? <><small>Valid <time dateTime={forecast.validTime}>{formatExplorerTime(forecast.validTime)}</time></small>{!compact ? <><small>Updated {formatObservationAge(forecast.modelRun, referenceTime)} · {sourceLabel}</small>{smokeWarning ? <small>{smokeWarning}</small> : null}</> : null}</> : <small>{outsideCoverage ? compact ? "Outside forecast coverage" : "Modeled smoke unavailable outside forecast coverage." : smokeLoading ? compact ? "Loading current forecast" : "Loading the current forecast pair" : compact ? "No modeled value" : "No modeled value at this location"}</small>}</section>
      <section><span className="eyebrow">{monitor && monitorIndexSystem(monitor) === "ca-aqhi" ? "AQHI" : "PM2.5 AQI"}</span>{airLoading ? <strong>Loading…</strong> : monitor ? <><strong data-testid="location-air-value"><i style={{ backgroundColor: indexColor, color: indexColor }} />{monitorIndexLabel(monitor)} · {monitor.category}</strong><small>Observed {formatObservationAge(monitor.observedAt, referenceTime)}</small>{!compact ? <><small>{monitor.name} · {distance} km · {monitor.agency}</small>{airWarning ? <small>{airWarning}</small> : null}{airError ? <small>{airError}</small> : null}</> : null}</> : <><strong>Unavailable</strong><small>{airError ?? "No monitor within 50 km"}</small>{airError ? <button className="text-button" type="button" onClick={onRetryAir}>Retry air data</button> : null}</>}</section>
    </div>
    {compact ? <SelectionMore>{extras}</SelectionMore> : <><details className="source-details"><summary>Source details</summary>{provenance}</details>{supportingDetails}</>}
  </div>;
}
