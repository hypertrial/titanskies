"use client";

import { airIndexColor, monitorIndexLabel } from "@/data/airQuality";
import type { ProductView } from "@/data/contextSchema";
import { formatForecastConcentration, type RankedAqiCity, type RankedSmokeCity } from "@/data/topConditions";
import { COUNTRY_LABELS, type CityLabel } from "@/data/ui";
import { useEffect, useId, useRef, useState } from "react";

type Metric = "smoke" | "aqi";
const TIME_FORMATTER = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" });

function ageLabel(iso: string, referenceTime: number): string {
  const minutes = Math.max(0, Math.round((referenceTime - Date.parse(iso)) / 60_000));
  return minutes < 60 ? `${minutes} min old` : `${Math.floor(minutes / 60)} hr ${minutes % 60} min old`;
}

export function TopConditions({
  view,
  cityCount,
  smokeRows,
  smokeLoading,
  smokeError,
  smokeWarning,
  smokeValidTime,
  concentrationMax,
  aqiRows,
  aqiLoading,
  aqiError,
  aqiWarning,
  referenceTime,
  onSelect,
  onMethodology,
}: {
  view: ProductView;
  cityCount: number;
  smokeRows: RankedSmokeCity[];
  smokeLoading: boolean;
  smokeError: string | null;
  smokeWarning: string | null;
  smokeValidTime: string;
  concentrationMax: number;
  aqiRows: RankedAqiCity[];
  aqiLoading: boolean;
  aqiError: string | null;
  aqiWarning: string | null;
  referenceTime: number;
  onSelect: (city: CityLabel) => void;
  onMethodology: () => void;
}) {
  const id = useId();
  const smokeTab = useRef<HTMLButtonElement>(null);
  const aqiTab = useRef<HTMLButtonElement>(null);
  const [metric, setMetric] = useState<Metric>(view === "air" ? "aqi" : "smoke");

  useEffect(() => {
    const next = view === "air" ? "aqi" : "smoke";
    setMetric(next);
    const frame = window.requestAnimationFrame(() => (next === "aqi" ? aqiTab : smokeTab).current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [view]);

  const selectMetric = (next: Metric) => {
    setMetric(next);
    (next === "aqi" ? aqiTab : smokeTab).current?.focus();
  };
  const tabKey = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    selectMetric(metric === "smoke" ? "aqi" : "smoke");
  };
  return <section className="top-conditions-panel-content">
      <div className="top-conditions-tabs" role="tablist" aria-label="Ranked condition type">
        <button ref={smokeTab} id={`${id}-smoke-tab`} role="tab" aria-selected={metric === "smoke"} aria-controls={`${id}-smoke-panel`} tabIndex={metric === "smoke" ? 0 : -1} onKeyDown={tabKey} onClick={() => selectMetric("smoke")}>Modeled smoke</button>
        <button ref={aqiTab} id={`${id}-aqi-tab`} role="tab" aria-selected={metric === "aqi"} aria-controls={`${id}-aqi-panel`} tabIndex={metric === "aqi" ? 0 : -1} onKeyDown={tabKey} onClick={() => selectMetric("aqi")}>PM2.5 AQI</button>
      </div>
      {metric === "smoke" ? <div id={`${id}-smoke-panel`} className="top-conditions-content" role="tabpanel" aria-labelledby={`${id}-smoke-tab`}>
        <p className="top-conditions-scope">{cityCount} city centers{smokeValidTime ? ` · Valid ${TIME_FORMATTER.format(new Date(smokeValidTime))}` : ""}</p>
        {smokeLoading ? <p className="top-conditions-status" role="status">Ranking the displayed forecast…</p>
          : smokeRows.length ? <ol className="top-conditions-list">{smokeRows.map((row, index) => <li key={`${row.city.country}:${row.city.region}:${row.city.searchName}`}><button type="button" onClick={() => onSelect(row.city)} aria-label={`View ${row.city.name}, ${row.city.region}, ${COUNTRY_LABELS[row.city.country]}, ranked ${index + 1} for modeled smoke at ${formatForecastConcentration(row.concentration, concentrationMax)} micrograms per cubic meter`}><span className="top-condition-rank">{index + 1}</span><span className="top-condition-place"><strong>{row.city.name}</strong><small>{row.city.region}, {COUNTRY_LABELS[row.city.country]}</small></span><span className="top-condition-value"><strong>{formatForecastConcentration(row.concentration, concentrationMax)}</strong><small>µg/m³</small></span></button></li>)}</ol>
            : <p className="top-conditions-status">{smokeError ?? "No modeled city-center values are available for this time."}</p>}
        {smokeWarning ? <p className="top-conditions-warning">{smokeWarning}</p> : null}
        <button className="context-link" type="button" onClick={onMethodology}>How rankings work</button>
      </div> : <div id={`${id}-aqi-panel`} className="top-conditions-content" role="tabpanel" aria-labelledby={`${id}-aqi-tab`}>
        <p className="top-conditions-scope">{cityCount} city centers · Stations within 50 km</p>
        {aqiLoading ? <p className="top-conditions-status" role="status">Loading official monitor readings…</p>
          : aqiRows.length ? <ol className="top-conditions-list">{aqiRows.map((row, index) => { const monitor = row.reading.monitor; const distance = row.reading.distanceKm < 10 ? row.reading.distanceKm.toFixed(1) : Math.round(row.reading.distanceKm); return <li key={monitor.id}><button type="button" onClick={() => onSelect(row.city)} aria-label={`View ${row.city.name}, ${row.city.region}, ${COUNTRY_LABELS[row.city.country]}, ranked ${index + 1} at ${monitorIndexLabel(monitor)}, ${monitor.category}`}><span className="top-condition-rank">{index + 1}</span><span className="top-condition-place"><strong>{row.city.name}</strong><small>{row.city.region}, {COUNTRY_LABELS[row.city.country]} · {distance} km to {monitor.name}</small><small>{ageLabel(monitor.observedAt, referenceTime)}</small></span><span className="top-condition-value"><i style={{ backgroundColor: airIndexColor(monitor), color: airIndexColor(monitor) }} /><strong>{monitorIndexLabel(monitor).replace("AQI ", "")}</strong><small>{monitor.category}</small></span></button></li>; })}</ol>
            : <p className="top-conditions-status">{aqiError ?? "No recent comparable city monitor readings are available."}</p>}
        {aqiWarning ? <p className="top-conditions-warning">{aqiWarning}</p> : null}
        <button className="context-link" type="button" onClick={onMethodology}>How rankings work</button>
      </div>}
  </section>;
}
