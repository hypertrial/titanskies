import { airIndexColor, monitorIndexLabel, monitorIndexSystem } from "@/data/airQuality";
import { inForecastBounds, type AirQualityMonitor, type FireIncident } from "@/data/contextSchema";
import { formatExplorerTime, formatLonLat, formatObservationAge, PUBLIC_DATA_WARNING } from "@/data/interfacePresentation";
import { integratedSourceLabel } from "@/data/forecastRaster";
import { formatForecastConcentration } from "@/data/topConditions";
import { compareAirMonitors } from "@/rendering/airClusters";
import { compareIncidents } from "@/rendering/incidentClusters";
import type { MapSelection } from "@/data/ui";
import type { ReactNode } from "react";

export function detailsTitle(selection: MapSelection): string {
  if (selection.kind === "air-monitor") return selection.monitor.name;
  if (selection.kind === "air-cluster") return `Nearby observations (${selection.monitors.length})`;
  if (selection.kind === "incident") return selection.incident.name;
  if (selection.kind === "incident-cluster") return `Nearby reported wildfires (${selection.incidents.length})`;
  if (!inForecastBounds(selection.lon, selection.lat) || selection.source === "none") return "Forecast unavailable";
  if (selection.concentration === undefined) return "Concentration unavailable";
  return `${formatForecastConcentration(selection.concentration, selection.concentrationMax)} µg/m³`;
}

export function detailsEyebrow(selection: MapSelection): string {
  if (selection.kind === "air-monitor") return monitorIndexSystem(selection.monitor) === "ca-aqhi" ? "Air Quality Health Index" : "Official PM2.5 AQI";
  if (selection.kind === "air-cluster") return "Official monitor readings";
  if (selection.kind === "incident") return "Reported wildfire";
  if (selection.kind === "incident-cluster") return "Agency incident reports";
  return "Modeled smoke";
}

function incidentAgency(incident: FireIncident): string {
  return incident.country === "US" ? "NIFC WFIGS" : "Canadian CWFIS";
}

function incidentArea(incident: FireIncident): string {
  return incident.areaHectares == null ? "Area unavailable" : `${incident.areaHectares.toLocaleString()} ha`;
}

function SelectionMore({ children }: { children: ReactNode }) {
  return <details className="selection-more"><summary>More details</summary><div className="selection-more-content">{children}</div></details>;
}

export function DetailsCard({ selection, referenceTime, compact, onMonitorSelect, onIncidentSelect, onMethodology }: { selection: MapSelection | null; referenceTime: number; compact: boolean; onMonitorSelect: (monitor: AirQualityMonitor) => void; onIncidentSelect: (incident: FireIncident) => void; onMethodology: () => void }) {
  if (!selection) return null;
  let body: ReactNode = null;
  let sourceDetails: ReactNode = null;
  let note = "";
  if (selection.kind === "air-monitor") {
    const monitor = selection.monitor;
    const canadian = monitorIndexSystem(monitor) === "ca-aqhi";
    const method = (monitor.indexMethod ?? monitor.aqiMethod) === "epa-nowcast-2024" ? "EPA NowCast (TitanSkies)" : "Provider-reported";
    body = <><div className="selection-value"><i style={{ backgroundColor: airIndexColor(monitor), color: airIndexColor(monitor) }} /><strong>{monitorIndexLabel(monitor)} · {monitor.category}</strong></div><p className="selection-meta">Observed {formatObservationAge(monitor.observedAt, referenceTime)} · {monitor.agency}</p></>;
    sourceDetails = <dl className="data-grid">{monitor.concentration != null && monitor.unit ? <div><dt>PM2.5</dt><dd>{monitor.concentration} {monitor.unit.toUpperCase() === "UG/M3" ? "µg/m³" : monitor.unit}</dd></div> : null}{monitor.nowcastConcentration != null ? <div><dt>NowCast PM2.5</dt><dd>{monitor.nowcastConcentration} µg/m³</dd></div> : null}<div><dt>Method</dt><dd>{method}</dd></div><div><dt>Observed</dt><dd><time dateTime={monitor.observedAt}>{formatExplorerTime(monitor.observedAt)}</time></dd></div><div><dt>Agency</dt><dd>{monitor.agency}{monitor.preliminary ? " · preliminary" : ""}</dd></div></dl>;
    note = canadian
      ? "AQHI describes short-term health risk from a mixture of PM2.5, ozone, and nitrogen dioxide. It is not numerically comparable with U.S. AQI."
      : monitor.aqiMethod === "epa-nowcast-2024"
      ? "US EPA PM2.5 AQI calculated by TitanSkies from official hourly concentrations. This is not the jurisdiction’s native index."
      : "AQI describes particle pollution at this monitor; it does not prove the particles came from wildfire smoke.";
  } else if (selection.kind === "air-cluster") {
    const monitors = [...selection.monitors].sort(compareAirMonitors);
    const observations = <ul className="nearby-observations">{monitors.map((monitor) => <li key={monitor.id}><button type="button" onClick={() => onMonitorSelect(monitor)}><strong>{monitorIndexLabel(monitor)} · {monitor.category}</strong><span>{monitor.name}</span><small>{formatExplorerTime(monitor.observedAt)}</small></button></li>)}</ul>;
    body = compact ? <p className="selection-meta">{monitors.length} official readings · newest {formatObservationAge(monitors[0].observedAt, referenceTime)}</p> : observations;
    sourceDetails = compact ? observations : null;
    note = new Set(monitors.map(monitorIndexSystem)).size > 1
      ? "Nearby readings remain separate observations. Their national index systems use different scales."
      : "Nearby readings remain separate official observations on the same index system.";
  } else if (selection.kind === "incident") {
    const incident = selection.incident;
    body = <dl className={`data-grid${compact ? " selection-primary-grid" : ""}`}>{!compact ? <div><dt>Agency</dt><dd>{incidentAgency(incident)}</dd></div> : null}<div><dt>Status</dt><dd>{incident.status}</dd></div><div><dt>Location</dt><dd>{formatLonLat(incident.lat, incident.lon)}</dd></div><div><dt>Reported area</dt><dd>{incident.areaHectares == null ? "Unavailable" : `${incident.areaHectares.toLocaleString()} ha`}</dd></div><div><dt>Updated</dt><dd><time dateTime={incident.updatedAt}>{formatExplorerTime(incident.updatedAt)}</time></dd></div></dl>;
    sourceDetails = compact ? <dl className="data-grid"><div><dt>Agency</dt><dd>{incidentAgency(incident)}</dd></div></dl> : null;
    note = "Reported by a US or Canadian fire agency. Incident locations and areas may change as reporting improves.";
  } else if (selection.kind === "incident-cluster") {
    const incidents = [...selection.incidents].sort(compareIncidents);
    const reports = <ul className="nearby-observations incident-observations">{incidents.map((incident) => <li key={incident.id}><button type="button" aria-label={`Open ${incident.name} reported wildfire details`} onClick={() => onIncidentSelect(incident)}><strong>{incident.name}</strong><span>{incidentAgency(incident)} · {incident.status}</span><small>{incidentArea(incident)} · Updated {formatExplorerTime(incident.updatedAt)}</small></button></li>)}</ul>;
    body = compact ? <p className="selection-meta">{incidents.length} agency reports · newest updated {formatObservationAge(incidents[0].updatedAt, referenceTime)}</p> : reports;
    sourceDetails = compact ? reports : null;
    note = "Markers are grouped only to keep the map legible. Each item remains a separate agency report; locations, status, and area may change as reporting improves, and no report attributes a forecast plume to a fire.";
  } else {
    const outsideCoverage = !inForecastBounds(selection.lon, selection.lat);
    const interpolation = selection.interpolation;
    const guidance = interpolation
      ? interpolation.fromSource === interpolation.toSource
        ? integratedSourceLabel(interpolation.fromSource)
        : interpolation.fromSource === "none" || interpolation.toSource === "none"
          ? `${integratedSourceLabel(interpolation.fromSource === "none" ? interpolation.toSource : interpolation.fromSource)} · available in one bracketing hour`
          : `Interpolated from ${integratedSourceLabel(interpolation.fromSource)} to ${integratedSourceLabel(interpolation.toSource)}`
      : integratedSourceLabel(selection.source);
    body = <><p className="forecast-location">{formatLonLat(selection.lat, selection.lon)}</p><p className="selection-meta">Valid <time dateTime={selection.validTime}>{formatExplorerTime(selection.validTime)}</time></p></>;
    sourceDetails = <dl className="data-grid forecast-details"><div><dt>Updated</dt><dd><time dateTime={selection.modelRun}>{formatExplorerTime(selection.modelRun)}</time></dd></div><div className="data-grid-wide"><dt>Guidance</dt><dd>{guidance}</dd></div></dl>;
    note = outsideCoverage
      ? "Forecast coverage unavailable here."
      : selection.source === "none"
        ? "No forecast value is available here for this time."
        : selection.concentration === undefined
          ? "This publication does not use the current TitanSkies display palette. Guidance provenance remains available."
          : interpolation
            ? "Modeled wildfire-smoke PM2.5 interpolated from display-precision hourly guidance — a forecast, not a measurement."
            : "Modeled wildfire-smoke PM2.5 at 8 m above ground — a forecast, not a measurement.";
  }
  const supportingDetails = <><p className="details-note">{note}</p><p className="details-note" data-testid="selection-data-warning">{PUBLIC_DATA_WARNING}</p>{selection.kind === "air-monitor" ? <a className="details-link" href={selection.monitor.sourceUrl && selection.monitor.source !== "airnow" ? selection.monitor.sourceUrl : "https://www.airnow.gov/aqi/aqi-basics/"} target="_blank" rel="noreferrer">{selection.monitor.source === "aqhi" ? "Read official ECCC AQHI guidance" : selection.monitor.source === "bcair" ? "Read B.C. air-quality data notes" : selection.monitor.source === "sinaica" ? "Open INECC/SINAICA" : "Read official AirNow action guidance"}</a> : selection.kind === "incident" ? <a className="details-link" href={selection.incident.sourceUrl} target="_blank" rel="noreferrer">Open the {incidentAgency(selection.incident)} source</a> : null}<button className="context-link" type="button" onClick={onMethodology}>About these values</button></>;
  const extras = <><div className="selection-provenance">{sourceDetails}</div>{supportingDetails}</>;
  const selectionKey = selection.kind === "air-monitor" ? selection.monitor.id
    : selection.kind === "air-cluster" ? selection.monitors.map((monitor) => monitor.id).join("|")
      : selection.kind === "incident" ? selection.incident.id
        : selection.kind === "incident-cluster" ? selection.incidents.map((incident) => incident.id).join("|")
          : `${selection.lon},${selection.lat}`;
  return <div className={`selection-details${selection.kind === "forecast" ? " forecast-card" : ""}`} data-testid="details-card">{body}{compact ? <SelectionMore key={selectionKey}>{extras}</SelectionMore> : <>{sourceDetails ? <details className="source-details"><summary>Source details</summary>{sourceDetails}</details> : null}{supportingDetails}</>}</div>;
}

export { SelectionMore };
