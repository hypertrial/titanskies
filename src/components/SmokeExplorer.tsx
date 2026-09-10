"use client";

import {
  CONTEXT_BOUNDS,
  CONTEXT_DETAIL_GRID,
  CONTEXT_DETAIL_RASTER,
  CONTEXT_RASTER,
  UI_FORECAST_HORIZON_HOURS,
  contextSourceLabel,
  detailTileBounds,
  detailTileId,
  forecastFrameRun,
  inForecastBounds,
  uiForecastHorizonHours,
  visibleForecastRun,
  type AirQualityMonitor,
  type ContextManifest,
  type ContextSource,
  type FireIncident,
  type ProductView,
  type SourceState,
} from "@/data/contextSchema";
import {
  closeContextImage,
  contextPointerUrl,
  loadContextImage,
  playbackPositionAfterContextRefresh,
} from "@/data/contextClient";
import {
  EPA_AQI_CATEGORIES,
  airIndexColor,
  airObservationTime,
  airSourceStates,
  airViewReady,
  airViewUnavailable,
  emptyAirMonitorWarning,
  fallbackSourceEntries,
  filterAirMonitors,
  forecastSourceStates,
  monitorIndexLabel,
  monitorIndexSystem,
  preferredAirSourceState,
  publicationFreshness,
  sourceFreshness,
} from "@/data/airQuality";
import {
  FORECAST_DISPLAY_PALETTE_VERSION,
  LEGACY_FORECAST_DISPLAY_PALETTE_VERSION,
  forecastLegendScale,
  integratedSourceLabel,
  interpolateForecastConcentration,
  sampleDecodedForecastConcentration,
  sampleDecodedForecastMask,
  sampleForecastConcentration,
  sampleForecastMask,
} from "@/data/forecastRaster";
import { inspectMarkerCopy, reduceInspectPoint, shouldKeepInspectPanel, type InspectPoint } from "@/data/inspectPoint";
import { cityIdentity, locationAirReading, type LocationAirReading } from "@/data/locationSearch";
import { formatForecastConcentration, rankAqiCities, rankSmokeCities } from "@/data/topConditions";
import { contextSurfaceReducer, shouldIgnoreExplorerPlaybackKeys, visibleMapLayers, type ContextSurface } from "@/data/explorerUi";
import { PUBLIC_DATA_WARNING, compactFreshnessLabel, userFacingLoadError } from "@/data/interfacePresentation";
import { compareAirMonitors } from "@/rendering/airClusters";
import { compareIncidents } from "@/rendering/incidentClusters";
import { livePosition, playbackAt, steppedPosition, type PlaybackSpeed } from "@/data/timeline";
import { COUNTRY_LABELS, type CityLabel, type GeoContext, type LayerVisibility, type MapSelection } from "@/data/ui";
import { PlaybackUiStore, playbackIsActive, playbackMixForRenderedPair, playbackPair, playbackPhaseReducer, syncTimelineElement, useForecastPlaybackClock, type PlaybackPhase } from "@/playback/forecastPlayback";
import { forecastRasterCacheKey, useForecastRasterCache, type ForecastRasterSource } from "@/rendering/forecastRasterCache";
import { TEMPORAL_SHADER_SAMPLE_COUNT } from "@/rendering/surfaceInterpolation";
import dynamic from "next/dynamic";
import Image from "next/image";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { Icon } from "./Icon";
import { ContextDrawer, type ContextDrawerCloseOrigin } from "./ContextDrawer";
import { LocationSearch } from "./LocationSearch";
import { PlaybackControls } from "./PlaybackControls";
import { SourceStatusPanel } from "./SourceStatusPanel";
import { TopConditions } from "./TopConditions";
import { useExplorerEnvironment } from "./explorer/useExplorerEnvironment";
import { useContextPolling } from "./explorer/useContextPolling";
import { useExplorerAssets } from "./explorer/useExplorerAssets";
import { useForecastPlaybackModel } from "./explorer/useForecastPlaybackModel";

const SmokeScene = dynamic(() => import("./scene/SmokeScene").then((mod) => mod.SmokeScene), { ssr: false });
type ImageSource = ImageBitmap | HTMLImageElement;
const LOOP_FADE_MS = 350;
const PLAYBACK_SPEEDS: PlaybackSpeed[] = [0.25, 1, 3];
const playbackSpeedLabel = (speed: PlaybackSpeed) => speed === 0.25 ? "¼×" : `${speed}×`;

const DATE_FORMATTER = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" });
const TIME_FORMATTER = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", hourCycle: "h12", timeZoneName: "short" });
const VIEWS: Array<{ id: ProductView; label: string }> = [
  { id: "forecast", label: "Forecast" },
  { id: "air", label: "Air quality" },
];
function formatTime(iso: string): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return `${DATE_FORMATTER.format(date)} · ${TIME_FORMATTER.format(date)}`;
}

function formatObservationAge(iso: string, referenceTime: number): string {
  const minutes = Math.max(0, Math.round((referenceTime - Date.parse(iso)) / 60_000));
  return minutes < 60 ? `${minutes} min old` : `${Math.floor(minutes / 60)} hr ${minutes % 60} min old`;
}

function newestTime(values: Array<string | undefined>): string {
  return values.filter((value): value is string => Boolean(value)).sort((left, right) => Date.parse(right) - Date.parse(left))[0] ?? "";
}

function formatLonLat(lat: number, lon: number): string {
  return `${Math.abs(lat).toFixed(2)}°${lat >= 0 ? "N" : "S"}, ${Math.abs(lon).toFixed(2)}°${lon >= 0 ? "E" : "W"}`;
}

function useImages(urls: string[], onError: (message: string | null) => void, retryKey: number): Record<string, ImageSource> {
  const cache = useRef(new Map<string, ImageSource>());
  const previousRetryKey = useRef(retryKey);
  const [images, setImages] = useState<Record<string, ImageSource>>({});
  const signature = urls.join("|");
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    const desired = [...new Set(urls.filter(Boolean))];
    if (previousRetryKey.current !== retryKey) {
      cache.current.forEach(closeContextImage);
      cache.current.clear();
      previousRetryKey.current = retryKey;
    }
    for (const [url, image] of cache.current) {
      if (!desired.includes(url)) { closeContextImage(image); cache.current.delete(url); }
    }
    setImages(Object.fromEntries(desired.flatMap((url) => {
      const image = cache.current.get(url);
      return image ? [[url, image]] : [];
    })));
    void Promise.all(desired.map(async (url) => {
      if (cache.current.has(url)) return;
      const image = await loadContextImage(url, controller.signal);
      if (cancelled) closeContextImage(image);
      else {
        cache.current.set(url, image);
        setImages((current) => ({ ...current, [url]: image }));
      }
    })).then(() => { if (!cancelled) onError(null); }).catch((error: unknown) => {
      if (!cancelled) onError(error instanceof Error ? error.message : "Unable to load a map layer.");
    });
    return () => { cancelled = true; controller.abort(); };
  // signature is a stable representation of the intended resident image window.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [retryKey, signature]);
  useEffect(() => () => { cache.current.forEach(closeContextImage); cache.current.clear(); }, []);
  return images;
}

function LayerToggle({ checked, disabled, label, description, onChange, testId }: { checked: boolean; disabled?: boolean; label: string; description: string; onChange: (checked: boolean) => void; testId?: string }) {
  const id = testId ?? label.toLowerCase().replace(/\s+/g, "-");
  return <label className={`layer-row${disabled ? " disabled" : ""}`} htmlFor={id}><span><strong>{label}</strong><small>{description}</small></span><span className="switch"><input id={id} type="checkbox" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} data-testid={testId} /><span aria-hidden="true" /></span></label>;
}

function detailsTitle(selection: MapSelection): string {
  if (selection.kind === "air-monitor") return selection.monitor.name;
  if (selection.kind === "air-cluster") return `Nearby observations (${selection.monitors.length})`;
  if (selection.kind === "incident") return selection.incident.name;
  if (selection.kind === "incident-cluster") return `Nearby reported wildfires (${selection.incidents.length})`;
  if (!inForecastBounds(selection.lon, selection.lat) || selection.source === "none") return "Forecast unavailable";
  if (selection.concentration === undefined) return "Concentration unavailable";
  return `${formatForecastConcentration(selection.concentration, selection.concentrationMax)} µg/m³`;
}

function detailsEyebrow(selection: MapSelection): string {
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

function SelectionMore({ children }: { children: React.ReactNode }) {
  return <details className="selection-more"><summary>More details</summary><div className="selection-more-content">{children}</div></details>;
}

function DetailsCard({ selection, referenceTime, compact, onMonitorSelect, onIncidentSelect, onMethodology }: { selection: MapSelection | null; referenceTime: number; compact: boolean; onMonitorSelect: (monitor: AirQualityMonitor) => void; onIncidentSelect: (incident: FireIncident) => void; onMethodology: () => void }) {
  if (!selection) return null;
  let body: React.ReactNode = null;
  let sourceDetails: React.ReactNode = null;
  let note = "";
  if (selection.kind === "air-monitor") {
    const monitor = selection.monitor;
    const canadian = monitorIndexSystem(monitor) === "ca-aqhi";
    const method = (monitor.indexMethod ?? monitor.aqiMethod) === "epa-nowcast-2024" ? "EPA NowCast (TitanSkies)" : "Provider-reported";
    body = <><div className="selection-value"><i style={{ backgroundColor: airIndexColor(monitor), color: airIndexColor(monitor) }} /><strong>{monitorIndexLabel(monitor)} · {monitor.category}</strong></div><p className="selection-meta">Observed {formatObservationAge(monitor.observedAt, referenceTime)} · {monitor.agency}</p></>;
    sourceDetails = <dl className="data-grid">{monitor.concentration != null && monitor.unit ? <div><dt>PM2.5</dt><dd>{monitor.concentration} {monitor.unit.toUpperCase() === "UG/M3" ? "µg/m³" : monitor.unit}</dd></div> : null}{monitor.nowcastConcentration != null ? <div><dt>NowCast PM2.5</dt><dd>{monitor.nowcastConcentration} µg/m³</dd></div> : null}<div><dt>Method</dt><dd>{method}</dd></div><div><dt>Observed</dt><dd><time dateTime={monitor.observedAt}>{formatTime(monitor.observedAt)}</time></dd></div><div><dt>Agency</dt><dd>{monitor.agency}{monitor.preliminary ? " · preliminary" : ""}</dd></div></dl>;
    note = canadian
      ? "AQHI describes short-term health risk from a mixture of PM2.5, ozone, and nitrogen dioxide. It is not numerically comparable with U.S. AQI."
      : monitor.aqiMethod === "epa-nowcast-2024"
      ? "US EPA PM2.5 AQI calculated by TitanSkies from official hourly concentrations. This is not the jurisdiction’s native index."
      : "AQI describes particle pollution at this monitor; it does not prove the particles came from wildfire smoke.";
  } else if (selection.kind === "air-cluster") {
    const monitors = [...selection.monitors].sort(compareAirMonitors);
    const observations = <ul className="nearby-observations">{monitors.map((monitor) => <li key={monitor.id}><button type="button" onClick={() => onMonitorSelect(monitor)}><strong>{monitorIndexLabel(monitor)} · {monitor.category}</strong><span>{monitor.name}</span><small>{formatTime(monitor.observedAt)}</small></button></li>)}</ul>;
    body = compact ? <p className="selection-meta">{monitors.length} official readings · newest {formatObservationAge(monitors[0].observedAt, referenceTime)}</p> : observations;
    sourceDetails = compact ? observations : null;
    note = new Set(monitors.map(monitorIndexSystem)).size > 1
      ? "Nearby readings remain separate observations. Their national index systems use different scales."
      : "Nearby readings remain separate official observations on the same index system.";
  } else if (selection.kind === "incident") {
    const incident = selection.incident;
    body = <dl className={`data-grid${compact ? " selection-primary-grid" : ""}`}>{!compact ? <div><dt>Agency</dt><dd>{incidentAgency(incident)}</dd></div> : null}<div><dt>Status</dt><dd>{incident.status}</dd></div><div><dt>Location</dt><dd>{formatLonLat(incident.lat, incident.lon)}</dd></div><div><dt>Reported area</dt><dd>{incident.areaHectares == null ? "Unavailable" : `${incident.areaHectares.toLocaleString()} ha`}</dd></div><div><dt>Updated</dt><dd><time dateTime={incident.updatedAt}>{formatTime(incident.updatedAt)}</time></dd></div></dl>;
    sourceDetails = compact ? <dl className="data-grid"><div><dt>Agency</dt><dd>{incidentAgency(incident)}</dd></div></dl> : null;
    note = "Reported by a US or Canadian fire agency. Incident locations and areas may change as reporting improves.";
  } else if (selection.kind === "incident-cluster") {
    const incidents = [...selection.incidents].sort(compareIncidents);
    const reports = <ul className="nearby-observations incident-observations">{incidents.map((incident) => <li key={incident.id}><button type="button" aria-label={`Open ${incident.name} reported wildfire details`} onClick={() => onIncidentSelect(incident)}><strong>{incident.name}</strong><span>{incidentAgency(incident)} · {incident.status}</span><small>{incidentArea(incident)} · Updated {formatTime(incident.updatedAt)}</small></button></li>)}</ul>;
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
    body = <><p className="forecast-location">{formatLonLat(selection.lat, selection.lon)}</p><p className="selection-meta">Valid <time dateTime={selection.validTime}>{formatTime(selection.validTime)}</time></p></>;
    sourceDetails = <dl className="data-grid forecast-details"><div><dt>Updated</dt><dd><time dateTime={selection.modelRun}>{formatTime(selection.modelRun)}</time></dd></div><div className="data-grid-wide"><dt>Guidance</dt><dd>{guidance}</dd></div></dl>;
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

function LocationCard({ city, forecast, smokeLoading, smokeWarning, airReading, airLoading, airError, airWarning, referenceTime, compact, onRetryAir, onMethodology }: {
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
  const provenance = <>{forecast ? <p>Forecast valid <time dateTime={forecast.validTime}>{formatTime(forecast.validTime)}</time>; model updated <time dateTime={forecast.modelRun}>{formatTime(forecast.modelRun)}</time> by {sourceLabel}.</p> : null}{smokeWarning ? <p>{smokeWarning}</p> : null}{monitor ? <p>{monitor.name} · {distance} km · {monitor.agency}. Station observation <time dateTime={monitor.observedAt}>{formatTime(monitor.observedAt)}</time>.</p> : null}{airWarning ? <p>{airWarning}</p> : null}{airError ? <p>{airError}</p> : null}</>;
  const supportingDetails = <>{monitor ? <a className="details-link" href={guidanceUrl} target="_blank" rel="noreferrer">Read official {monitorIndexSystem(monitor) === "ca-aqhi" ? "AQHI" : "AQI"} guidance</a> : null}<p className="details-note">Forecast smoke and station air quality are separate datasets.</p><p className="details-note" data-testid="selection-data-warning">{PUBLIC_DATA_WARNING}</p><button className="context-link" type="button" onClick={onMethodology}>About these values</button></>;
  const extras = <><div className="selection-provenance">{provenance}</div>{supportingDetails}</>;
  return <div className="location-card" data-testid="location-card" aria-label={`Smoke and air quality for ${city.name}`}>
    <p className="location-region">{city.region}, {COUNTRY_LABELS[city.country]}</p>
    <div className="location-metrics">
      <section><span className="eyebrow">Modeled smoke</span><strong data-testid="location-smoke-value">{smokeValue}</strong>{forecast && !outsideCoverage ? <><small>Valid <time dateTime={forecast.validTime}>{formatTime(forecast.validTime)}</time></small>{!compact ? <><small>Updated {formatObservationAge(forecast.modelRun, referenceTime)} · {sourceLabel}</small>{smokeWarning ? <small>{smokeWarning}</small> : null}</> : null}</> : <small>{outsideCoverage ? compact ? "Outside forecast coverage" : "Modeled smoke unavailable outside forecast coverage." : smokeLoading ? compact ? "Loading current forecast" : "Loading the current forecast pair" : compact ? "No modeled value" : "No modeled value at this location"}</small>}</section>
      <section><span className="eyebrow">{monitor && monitorIndexSystem(monitor) === "ca-aqhi" ? "AQHI" : "PM2.5 AQI"}</span>{airLoading ? <strong>Loading…</strong> : monitor ? <><strong data-testid="location-air-value"><i style={{ backgroundColor: indexColor, color: indexColor }} />{monitorIndexLabel(monitor)} · {monitor.category}</strong><small>Observed {formatObservationAge(monitor.observedAt, referenceTime)}</small>{!compact ? <><small>{monitor.name} · {distance} km · {monitor.agency}</small>{airWarning ? <small>{airWarning}</small> : null}{airError ? <small>{airError}</small> : null}</> : null}</> : <><strong>Unavailable</strong><small>{airError ?? "No monitor within 50 km"}</small>{airError ? <button className="text-button" type="button" onClick={onRetryAir}>Retry air data</button> : null}</>}</section>
    </div>
    {compact ? <SelectionMore>{extras}</SelectionMore> : <><details className="source-details"><summary>Source details</summary>{provenance}</details>{supportingDetails}</>}
  </div>;
}

export function SmokeExplorer() {
  const explorerRenderCount = useRef(0);
  explorerRenderCount.current += 1;
  if (process.env.NEXT_PUBLIC_PERF_DIAGNOSTICS === "1") {
    (globalThis as typeof globalThis & { __TITANSKIES_PERF__?: Record<string, unknown> }).__TITANSKIES_PERF__ = {
      ...(globalThis as typeof globalThis & { __TITANSKIES_PERF__?: Record<string, unknown> }).__TITANSKIES_PERF__,
      explorerRenderCount: explorerRenderCount.current,
      temporalShaderSamples: TEMPORAL_SHADER_SAMPLE_COUNT,
    };
  }
  const methodologyReturnFocus = useRef<HTMLElement | null>(null);
  const methodologyDialog = useRef<HTMLDialogElement>(null);
  const [view, setView] = useState<ProductView>("forecast");
  const [geo, setGeo] = useState<GeoContext | null>(null);
  const [incidentsVisible, setIncidentsVisible] = useState(false);
  const activeLayers = useMemo<LayerVisibility>(() => visibleMapLayers(view, incidentsVisible), [incidentsVisible, view]);
  const [assetError, setAssetError] = useState<string | null>(null);
  const [imageError, setImageError] = useState<string | null>(null);
  const [detailImageError, setDetailImageError] = useState<string | null>(null);
  const [forecastPosition, setForecastPosition] = useState(0);
  const [playbackPhase, dispatchPlayback] = useReducer(playbackPhaseReducer, "manual-paused");
  const [playbackSpeed, setPlaybackSpeed] = useState<PlaybackSpeed>(1);
  const [methodologyOpen, setMethodologyOpen] = useState(false);
  const [detailTileIds, setDetailTileIds] = useState<number[]>([]);
  const [detailFadeOutKeys, setDetailFadeOutKeys] = useState<string[]>([]);
  const [renderedPair, setRenderedPair] = useState({ fromIndex: 0, toIndex: 0 });
  const [followingLive, setFollowingLive] = useState(true);
  const playingRef = useRef(false);
  const playbackPhaseRef = useRef<PlaybackPhase>("manual-paused");
  const followingLiveRef = useRef(true);
  const [selection, setSelection] = useState<MapSelection | null>(null);
  const [selectedCity, setSelectedCity] = useState<CityLabel | null>(null);
  const [selectedFromRanking, setSelectedFromRanking] = useState(false);
  const [topConditionsReferenceTime, setTopConditionsReferenceTime] = useState(() => Date.now());
  const [focusLocation, setFocusLocation] = useState<{ lon: number; lat: number; requestId: number } | null>(null);
  const [inspectPoint, setInspectPoint] = useState<InspectPoint | null>(null);
  const [contextPanel, dispatchContextPanel] = useReducer(contextSurfaceReducer, null);
  const contextReturnFocusRef = useRef<HTMLElement | null>(null);
  const [resetSignal, setResetSignal] = useState(0);
  const [sceneReady, setSceneReady] = useState(false);
  const [forecastRasterReady, setForecastRasterReady] = useState(false);
  const [sceneError, setSceneError] = useState<string | null>(null);
  const handleSceneReady = useCallback(() => setSceneReady(true), []);
  const forecastPositionRef = useRef(0);
  const forecastEntriesRef = useRef<Array<{ scanId: string; observationStart: string; manifestUrl: string }>>([]);
  const selectionRequestRef = useRef(0);
  const topConditionsAirOnlyRef = useRef(false);
  const wasOfflineRef = useRef(false);
  const pendingSelectionRef = useRef<{ lon: number; lat: number } | null>(null);
  const inspectPointRef = useRef<InspectPoint | null>(null);
  const sampleMapRef = useRef<(point: { lon: number; lat: number }, options?: { preserveLive?: boolean; allowInAir?: boolean; returnFocus?: HTMLElement | null; pause?: boolean; preserveSelection?: boolean }) => void>(() => {});
  const timelineInputRef = useRef<HTMLInputElement>(null);
  const timelineScrubbingRef = useRef(false);
  const contextMixRef = useRef(0);
  const smokeOpacityRef = useRef(1);
  const playbackUiStore = useMemo(() => new PlaybackUiStore(0), []);
  const sceneRenderRequestRef = useRef<() => void>(() => {});
  const explorerRef = useRef<HTMLElement>(null);
  const renderedPairRef = useRef({ fromIndex: 0, toIndex: 0 });
  const requestedPairRef = useRef<{ fromIndex: number; toIndex: number } | null>(null);
  const forecastInitializedRef = useRef(false);
  const forecastRasterInitializedRef = useRef(false);
  const resetTargetRef = useRef<{ now: number; position: number } | null>(null);
  const topConditionsOpen = contextPanel === "top-conditions";
  const layersOpen = contextPanel === "layers";
  const playing = playbackPhase === "playing";
  const buffering = playbackPhase === "buffering";
  const animationActive = playbackIsActive(playbackPhase);
  playingRef.current = playing;
  playbackPhaseRef.current = playbackPhase;
  followingLiveRef.current = followingLive;
  inspectPointRef.current = inspectPoint;
  const changePlaybackPhase = useCallback((next: PlaybackPhase) => {
    playbackPhaseRef.current = next;
    playingRef.current = next === "playing";
    dispatchPlayback({ type: "set", phase: next });
  }, []);
  const changeSmokeOpacity = useCallback((next: number) => {
    smokeOpacityRef.current = next;
    if (explorerRef.current) explorerRef.current.dataset.smokeOpacity = next.toFixed(3);
    sceneRenderRequestRef.current();
  }, []);
  const handleMotionReduced = useCallback(() => {
    changeSmokeOpacity(1);
    changePlaybackPhase("manual-paused");
  }, [changePlaybackPhase, changeSmokeOpacity]);
  const {
    documentVisible,
    installPrompt,
    ios,
    mobile,
    networkOnline,
    reducedMotion,
    setInstallPrompt,
    setStandalone,
    shortLandscape,
    standalone,
    webgl,
  } = useExplorerEnvironment(handleMotionReduced);
  const commitPlaybackPosition = useCallback((next: number) => {
    forecastPositionRef.current = next;
    const entries = forecastEntriesRef.current;
    if (entries.length) {
      const state = playbackAt(entries, next);
      const desiredPair = playbackPair(entries, next);
      contextMixRef.current = playbackMixForRenderedPair(state, desiredPair, renderedPairRef.current);
    }
    syncTimelineElement(timelineInputRef.current, next, { writeValue: !timelineScrubbingRef.current });
    if (explorerRef.current) {
      explorerRef.current.dataset.renderMix = contextMixRef.current.toFixed(6);
      explorerRef.current.dataset.playbackPosition = String(Math.round(next));
    }
    playbackUiStore.update(next, typeof performance === "undefined" ? Date.now() : performance.now(), true);
    setForecastPosition(next);
    sceneRenderRequestRef.current();
  }, [playbackUiStore]);
  const pauseForInteraction = useCallback(() => {
    if (!playbackIsActive(playbackPhaseRef.current)) return;
    if (playbackPhaseRef.current !== "playing") changeSmokeOpacity(1);
    setDetailFadeOutKeys([]);
    if (!timelineScrubbingRef.current) commitPlaybackPosition(forecastPositionRef.current);
    changePlaybackPhase("interaction-paused");
  }, [changePlaybackPhase, changeSmokeOpacity, commitPlaybackPosition]);
  const clearSelection = useCallback(() => {
    selectionRequestRef.current += 1;
    pendingSelectionRef.current = null;
    setSelection(null);
  }, []);
  const clearLocation = useCallback(() => {
    clearSelection();
    setSelectedCity(null);
    setSelectedFromRanking(false);
    setFocusLocation(null);
    setInspectPoint(null);
  }, [clearSelection]);
  const closeContextPanel = useCallback((restoreFocus = false, origin: ContextDrawerCloseOrigin = "keyboard") => {
    const selectionWasOpen = contextPanel === "selection";
    if (selectionWasOpen) clearLocation();
    dispatchContextPanel({ type: "close" });
    if (topConditionsAirOnlyRef.current) setImageError(null);
    topConditionsAirOnlyRef.current = false;
    const target = contextReturnFocusRef.current;
    contextReturnFocusRef.current = null;
    if (restoreFocus) window.requestAnimationFrame(() => {
      const searchTarget = selectionWasOpen && target?.matches('[data-testid="location-search-input"], .location-search-trigger');
      const focusTarget = searchTarget && origin === "pointer" && (mobile || shortLandscape)
        ? document.querySelector<HTMLElement>("[data-map-keyboard]")
        : target;
      if (searchTarget && focusTarget === target && target) target.dataset.suppressFocusOpen = "true";
      focusTarget?.focus();
    });
  }, [clearLocation, contextPanel, mobile, shortLandscape]);
  const openContextPanel = useCallback((panel: Exclude<ContextSurface, null>, returnFocus?: HTMLElement | null, options: { pause?: boolean } = {}) => {
    if (options.pause !== false) pauseForInteraction();
    if (panel !== "selection" && contextPanel === "selection") clearLocation();
    contextReturnFocusRef.current = returnFocus ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null);
    dispatchContextPanel({ type: "open", surface: panel });
  }, [clearLocation, contextPanel, pauseForInteraction]);
  const handleDetailTiles = useCallback((tiles: number[]) => {
    setDetailTileIds((current) => current.length === tiles.length && current.every((value, index) => value === tiles[index]) ? current : tiles);
  }, []);

  const handleSamePublication = useCallback(({ manifest, previous, now, previousNow }: {
    manifest: ContextManifest; previous: ContextManifest | null; now: number; previousNow: number;
  }) => {
    if (manifest.mode !== "live" || !followingLiveRef.current) return;
    commitPlaybackPosition(playbackPositionAfterContextRefresh({
      previous,
      next: manifest,
      previousPositionMs: forecastPositionRef.current,
      previousNowMs: previousNow,
      nowMs: now,
      followingLive: true,
    }));
  }, [commitPlaybackPosition]);
  const handleChangedPublication = useCallback(({ manifest, previous, reason, now, previousNow }: {
    manifest: ContextManifest; previous: ContextManifest | null; reason: "load" | "poll"; now: number; previousNow: number;
  }) => {
    if (reason === "load") {
      forecastRasterInitializedRef.current = false;
      setAssetError(null);
      setImageError(null);
      setDetailImageError(null);
    }
    clearLocation();
    dispatchContextPanel({ type: "close-selection" });
    commitPlaybackPosition(playbackPositionAfterContextRefresh({
      previous: reason === "load" ? null : previous,
      next: manifest,
      previousPositionMs: forecastPositionRef.current,
      previousNowMs: previousNow,
      nowMs: now,
      followingLive: manifest.mode === "live" && followingLiveRef.current,
    }));
  }, [clearLocation, commitPlaybackPosition]);
  const healthExpectedSources = useMemo<ContextSource[]>(() => view === "air"
    ? ["airnow", "bcair", "sinaica"]
    : ["firework", "hrrr", ...(activeLayers.incidents ? ["wfigs", "cwfis"] as const : [])], [activeLayers.incidents, view]);
  const {
    context,
    contextError,
    contextHealth,
    contextRef,
    contextRetry,
    displayNow,
    displayNowRef,
    healthError,
    retryContext,
    retryHealth,
    setDisplayNow,
  } = useContextPolling({
    expectedSources: healthExpectedSources,
    followingLiveRef,
    onChangedPublication: handleChangedPublication,
    onSamePublication: handleSamePublication,
  });

  useEffect(() => {
    let cancelled = false;
    setGeo(null);
    fetch("/geo/north-america-v3.json")
      .then((response) => { if (!response.ok) throw new Error("Failed to load geographic context"); return response.json() as Promise<GeoContext>; })
      .then((payload) => {
        if (cancelled) return;
        if (payload.version !== 3 || !Array.isArray(payload.cities) || !Array.isArray(payload.mapLabels)) throw new Error("Unsupported geographic context");
        setGeo(payload);
        setSceneError(null);
      })
      .catch(() => { if (!cancelled) setSceneError("Unable to load North American political boundaries."); });
    return () => { cancelled = true; };
  }, [contextRetry]);

  const airDataNeeded = view === "air" || selectedCity !== null || topConditionsOpen;
  const {
    airError,
    airMapReady,
    airReady,
    clearIncidentError,
    incidentError,
    incidents,
    monitors,
    retryAir,
  } = useExplorerAssets({ airDataNeeded, context, contextRetry, incidentsVisible: activeLayers.incidents });

  const visibleMonitors = useMemo(() => filterAirMonitors(monitors, "comparable"), [monitors]);
  const {
    activeDetailGrid,
    activeForecast,
    forecastA,
    forecastB,
    forecastEntries,
    forecastFrames,
    forecastPlayback,
    forecastSpan,
    horizonHours,
    live,
    liveActive,
    liveAvailable,
    resident: baseResident,
    surfaceFrameB,
    surfaceToIndex,
  } = useForecastPlaybackModel(context, displayNow, forecastPosition, followingLive);
  forecastEntriesRef.current = forecastEntries;
  const handlePairCommitted = useCallback((fromIndex: number, toIndex: number) => {
    renderedPairRef.current = { fromIndex, toIndex };
    setRenderedPair((current) => current.fromIndex === fromIndex && current.toIndex === toIndex ? current : { fromIndex, toIndex });
    if (requestedPairRef.current?.fromIndex === fromIndex && requestedPairRef.current.toIndex === toIndex) {
      requestedPairRef.current = null;
    }
    const state = playbackAt(forecastEntriesRef.current, forecastPositionRef.current);
    const desiredPair = playbackPair(forecastEntriesRef.current, forecastPositionRef.current);
    contextMixRef.current = playbackMixForRenderedPair(state, desiredPair, { fromIndex, toIndex });
    if (explorerRef.current) {
      explorerRef.current.dataset.renderMix = contextMixRef.current.toFixed(6);
      explorerRef.current.dataset.renderedFromIndex = String(fromIndex);
    }
    sceneRenderRequestRef.current();
    if (process.env.NEXT_PUBLIC_PERF_DIAGNOSTICS === "1") {
      const target = globalThis as typeof globalThis & { __TITANSKIES_PERF__?: Record<string, unknown> };
      const current = Number(target.__TITANSKIES_PERF__?.pairCommits ?? 0);
      target.__TITANSKIES_PERF__ = { ...target.__TITANSKIES_PERF__, pairCommits: current + 1 };
    }
  }, []);
  const adjacentPairFor = (entries: typeof forecastEntries, position: number) => {
    const state = playbackAt(entries, position);
    return [state.fromIndex, state.interpolating ? state.toIndex : Math.min(entries.length - 1, state.fromIndex + 1)];
  };
  const currentResetTarget = () => {
    const now = Date.now();
    if (context?.mode === "live") {
      const preloadTarget = livePosition(forecastEntries, now);
      const resetFrames = visibleForecastRun(context, uiForecastHorizonHours(context), now).frames;
      const resetEntries = resetFrames.map((frame, index) => ({ scanId: `forecast-${index}`, observationStart: frame.validTime, manifestUrl: frame.textureUrl ?? "" }));
      const target = livePosition(resetEntries, now);
      const targetState = playbackAt(resetEntries, target.positionMs);
      const targetHasPair = targetState.interpolating || targetState.fromIndex < resetEntries.length - 1;
      if (preloadTarget.available && target.available && targetHasPair) return { now, position: target.positionMs, preloadPosition: target.positionMs };
    }
    return { now: displayNowRef.current, position: 0, preloadPosition: 0 };
  };
  const atEndpoint = forecastSpan > 0 && forecastPosition >= forecastSpan;
  const detailPlaybackMode = playbackPhase === "preparing-detail" ? "fading-out"
    : animationActive || (playbackPhase === "waiting" && atEndpoint) ? "base-only"
      : "visible";
  const resetTarget = atEndpoint ? currentResetTarget() : { now: displayNowRef.current, position: 0, preloadPosition: 0 };
  const resetForecastFrames = atEndpoint && context?.mode === "live"
    ? visibleForecastRun(context, uiForecastHorizonHours(context), resetTarget.now).frames
    : forecastFrames;
  const resetForecastEntries = resetForecastFrames.map((frame, index) => ({ scanId: `forecast-${index}`, observationStart: frame.validTime, manifestUrl: frame.textureUrl ?? "" }));
  const resetPair = adjacentPairFor(resetForecastEntries, resetTarget.preloadPosition);
  const endpointResident = atEndpoint
    ? [...new Set([forecastFrames.length - 1, ...resetPair])]
    : null;
  const resident = endpointResident ?? baseResident;
  const forwardResident = resident.filter((index) => index >= forecastPlayback.fromIndex);
  const legacyForecast = context?.version === 1;
  const residentFrameSources = atEndpoint
    ? [
        { frameIndex: forecastFrames.length - 1, frame: forecastFrames.at(-1) },
        ...resetPair.map((frameIndex) => ({ frameIndex, frame: resetForecastFrames[frameIndex] })),
      ]
    : resident.map((frameIndex) => ({ frameIndex, frame: forecastFrames[frameIndex] }));
  const forecastDataNeeded = activeLayers.forecast || selectedCity !== null || topConditionsOpen;
  const legacySurfaceUrls = forecastDataNeeded && legacyForecast ? residentFrameSources.flatMap(({ frame }) => {
    return [frame?.textureUrl, frame?.sourceMaskUrl].filter((url): url is string => Boolean(url));
  }) : [];
  const legacyContextImages = useImages(legacySurfaceUrls, setImageError, contextRetry);
  const detailFadeOutKeySet = new Set(detailFadeOutKeys);
  const rasterSources = forecastDataNeeded && !legacyForecast ? residentFrameSources.flatMap(({ frameIndex, frame }) => {
    const paletteVersion = activeForecast.paletteVersion ?? LEGACY_FORECAST_DISPLAY_PALETTE_VERSION;
    const base: ForecastRasterSource[] = frame?.textureUrl && frame.sourceMaskUrl ? [{
      frameIndex,
      textureUrl: frame.textureUrl,
      maskUrl: frame.sourceMaskUrl,
      expected: CONTEXT_RASTER,
      paletteVersion,
      priority: 0,
    }] : [];
    const details = detailPlaybackMode !== "base-only" && (activeLayers.forecast || selectedCity !== null) ? detailTileIds.flatMap((tileId): ForecastRasterSource[] => {
      const tile = frame?.detailTiles?.[tileId];
      const currentPair = frameIndex === forecastPlayback.fromIndex || frameIndex === surfaceToIndex;
      const tilePriority = detailTileIds.indexOf(tileId);
      return tile ? [{
        frameIndex,
        tileId,
        optional: true,
        textureUrl: tile.textureUrl,
        maskUrl: tile.sourceMaskUrl,
        expected: CONTEXT_DETAIL_RASTER,
        paletteVersion,
        priority: (currentPair ? 10 : 100) + tilePriority,
        group: currentPair ? `detail-${tileId}-current-pair` : `detail-${tileId}-prefetch-${frameIndex}`,
      }] : [];
    }) : [];
    return [...base, ...details.filter((source) => detailPlaybackMode !== "fading-out" || detailFadeOutKeySet.has(forecastRasterCacheKey(source)))];
  }) : [];
  const handleRasterError = useCallback((source: ForecastRasterSource, message: string) => {
    if (source.optional) setDetailImageError(message);
    else setImageError(message);
  }, []);
  const rasterCache = useForecastRasterCache(rasterSources, contextRetry, handleRasterError, mobile ? 128 * 1024 * 1024 : 256 * 1024 * 1024, animationActive);
  const decodedResidents = rasterCache.resident();
  const residentRasters = decodedResidents.filter((source) => source.tileId === undefined);
  const readyDetailTileIds = detailTileIds.filter((tileId) => decodedResidents.some((source) => source.tileId === tileId && source.frameIndex === forecastPlayback.fromIndex)
    && decodedResidents.some((source) => source.tileId === tileId && source.frameIndex === surfaceToIndex));
  const readyDetailTileSet = new Set(readyDetailTileIds);
  const readyDetailFadeKeys = decodedResidents.filter((source) => source.tileId !== undefined
    && readyDetailTileSet.has(source.tileId)
    && (source.frameIndex === forecastPlayback.fromIndex || source.frameIndex === surfaceToIndex)).map((source) => source.key);
  const frameRenderReady = useCallback((indexes: number[]) => [...new Set(indexes)].every((index) => {
    const frame = forecastFrames[index];
    if (!frame?.textureUrl) return false;
    if (legacyForecast) return Boolean(legacyContextImages[frame.textureUrl]);
    if (!frame.sourceMaskUrl) return false;
    return rasterCache.isReady([forecastRasterCacheKey({
      frameIndex: index,
      textureUrl: frame.textureUrl,
      maskUrl: frame.sourceMaskUrl,
      expected: CONTEXT_RASTER,
      paletteVersion: activeForecast.paletteVersion ?? LEGACY_FORECAST_DISPLAY_PALETTE_VERSION,
    })]);
  }), [activeForecast.paletteVersion, forecastFrames, legacyContextImages, legacyForecast, rasterCache]);
  const resetAssetsReady = [...new Set(resetPair)].every((index) => {
    const frame = resetForecastFrames[index];
    if (!frame?.textureUrl) return false;
    if (legacyForecast) return Boolean(legacyContextImages[frame.textureUrl]);
    if (!frame.sourceMaskUrl) return false;
    return rasterCache.isReady([forecastRasterCacheKey({
      frameIndex: index,
      textureUrl: frame.textureUrl,
      maskUrl: frame.sourceMaskUrl,
      expected: CONTEXT_RASTER,
      paletteVersion: activeForecast.paletteVersion ?? LEGACY_FORECAST_DISPLAY_PALETTE_VERSION,
    })]);
  });
  const finishDetailPreparation = useCallback(() => {
    if (playbackPhaseRef.current !== "preparing-detail") return;
    const ready = frameRenderReady(forwardResident);
    setDetailFadeOutKeys([]);
    playbackPhaseRef.current = ready ? "playing" : "buffering";
    playingRef.current = ready;
    dispatchPlayback({ type: "detail-hidden", ready });
  }, [forwardResident, frameRenderReady]);
  useEffect(() => {
    if (playbackPhase !== "preparing-detail") return;
    const fallback = window.setTimeout(finishDetailPreparation, 500);
    return () => window.clearTimeout(fallback);
  }, [finishDetailPreparation, playbackPhase]);
  useEffect(() => {
    if (!followingLive || context?.mode !== "live" || !forecastEntries.length) return;
    const tick = () => {
      if (!followingLiveRef.current) return;
      const now = Date.now();
      displayNowRef.current = now;
      setDisplayNow(now);
      commitPlaybackPosition(livePosition(forecastEntries, now).positionMs);
    };
    tick();
    const id = window.setInterval(tick, 1_000);
    const catchUp = () => { if (document.visibilityState === "visible") tick(); };
    document.addEventListener("visibilitychange", catchUp);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", catchUp);
    };
  }, [commitPlaybackPosition, context?.mode, displayNowRef, followingLive, forecastEntries, setDisplayNow]);

  const commitPlaybackPair = useCallback((position: number, pair: { fromIndex: number; toIndex: number }) => {
    if (requestedPairRef.current?.fromIndex === pair.fromIndex && requestedPairRef.current.toIndex === pair.toIndex) return;
    requestedPairRef.current = pair;
    forecastPositionRef.current = position;
    setForecastPosition(position);
  }, []);
  const enterBuffering = useCallback(() => changePlaybackPhase("buffering"), [changePlaybackPhase]);
  const reachEndpoint = useCallback(() => {
    commitPlaybackPosition(forecastSpan);
    changePlaybackPhase("waiting");
  }, [changePlaybackPhase, commitPlaybackPosition, forecastSpan]);
  const requestSceneRender = useCallback(() => sceneRenderRequestRef.current(), []);
  useForecastPlaybackClock({
    enabled: playing && view === "forecast",
    mobile,
    reducedMotion,
    entries: forecastEntries,
    spanMs: forecastSpan,
    speed: playbackSpeed,
    positionRef: forecastPositionRef,
    mixRef: contextMixRef,
    renderedPairRef,
    timelineRef: timelineInputRef,
    explorerRef,
    uiStore: playbackUiStore,
    pairReady: frameRenderReady,
    onPairBoundary: commitPlaybackPair,
    requestRender: requestSceneRender,
    onBuffering: enterBuffering,
    onEndpoint: reachEndpoint,
    scrubbingRef: timelineScrubbingRef,
  });

  useEffect(() => { setAssetError(null); setImageError(null); setDetailImageError(null); clearIncidentError(); }, [clearIncidentError, view]);
  useEffect(() => {
    if (!forecastEntries.length) return;
    commitPlaybackPosition(Math.min(forecastPositionRef.current, forecastSpan));
    if (!inspectPointRef.current) clearSelection();
  }, [clearSelection, commitPlaybackPosition, forecastSpan, forecastEntries.length]);

  const leaveLive = useCallback(() => { followingLiveRef.current = false; setFollowingLive(false); }, []);
  const dismissNonInspectPanel = useCallback(() => {
    if (shouldKeepInspectPanel(inspectPointRef.current, contextPanel)) return;
    closeContextPanel();
    if (!inspectPointRef.current) clearLocation();
  }, [clearLocation, closeContextPanel, contextPanel]);
  const step = useCallback((delta: -1 | 1) => {
    pauseForInteraction();
    leaveLive();
    dismissNonInspectPanel();
    if (view === "forecast") {
      const next = steppedPosition(forecastEntries, forecastPositionRef.current, delta);
      commitPlaybackPosition(next);
    }
  }, [commitPlaybackPosition, dismissNonInspectPanel, forecastEntries, leaveLive, pauseForInteraction, view]);
  const canPlay = !reducedMotion && view === "forecast" && forecastEntries.length > 1;
  const togglePlay = useCallback(() => {
    if (!canPlay) return;
    if (playbackIsActive(playbackPhaseRef.current)) {
      changeSmokeOpacity(1);
      setDetailFadeOutKeys([]);
      commitPlaybackPosition(forecastPositionRef.current);
      changePlaybackPhase("manual-paused");
      return;
    }
    dismissNonInspectPanel();
    leaveLive();
    if (forecastPositionRef.current >= forecastSpan) {
      changePlaybackPhase("waiting");
      return;
    }
    setDetailFadeOutKeys(readyDetailFadeKeys);
    const event = { type: "play", canPlay, ready: frameRenderReady(forwardResident), atEndpoint: false, detailVisible: readyDetailFadeKeys.length > 0 } as const;
    const next = playbackPhaseReducer(playbackPhaseRef.current, event);
    playbackPhaseRef.current = next;
    playingRef.current = next === "playing";
    dispatchPlayback(event);
  }, [canPlay, changePlaybackPhase, changeSmokeOpacity, commitPlaybackPosition, dismissNonInspectPanel, forecastSpan, forwardResident, frameRenderReady, leaveLive, readyDetailFadeKeys]);
  const goLive = useCallback(() => {
    if (!context || context.mode !== "live" || !liveAvailable) return;
    const now = Date.now();
    followingLiveRef.current = true;
    setDetailFadeOutKeys([]);
    changeSmokeOpacity(1);
    changePlaybackPhase("manual-paused");
    setFollowingLive(true);
    dismissNonInspectPanel();
    displayNowRef.current = now;
    setDisplayNow(now);
    commitPlaybackPosition(livePosition(forecastEntries, now).positionMs);
  }, [changePlaybackPhase, changeSmokeOpacity, commitPlaybackPosition, context, dismissNonInspectPanel, displayNowRef, forecastEntries, liveAvailable, setDisplayNow]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (shouldIgnoreExplorerPlaybackKeys(event.target)) return;
      if (event.key === " ") { event.preventDefault(); togglePlay(); }
      if (event.key === "ArrowLeft") step(-1);
      if (event.key === "ArrowRight") step(1);
    };
    window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey);
  }, [step, togglePlay]);

  const airSources = context ? airSourceStates(context, "comparable") : [];
  const forecastSources = context ? forecastSourceStates(context) : [];
  const incidentSources: Array<[string, SourceState]> = context ? [
    ...(context.sources.wfigs ? [[contextSourceLabel("wfigs"), context.sources.wfigs] as [string, SourceState]] : []),
    ...(context.sources.cwfis ? [[contextSourceLabel("cwfis"), context.sources.cwfis] as [string, SourceState]] : []),
  ] : [];
  const statusSources = view === "forecast"
    ? [...forecastSources, ...(activeLayers.incidents ? incidentSources : [])]
    : airSources;
  const sourceState = context ? view === "forecast"
    ? forecastSources.find(([, state]) => state.status === "ok")?.[1]
      ?? forecastSources.find(([, state]) => state.status === "stale")?.[1]
      ?? forecastSources.find(([, state]) => state.status === "error")?.[1]
      ?? forecastSources[0]?.[1]
      ?? null
    : preferredAirSourceState(context, "comparable") ?? context.sources.airnow ?? null : null;
  const activeSourceStates = statusSources.map(([, state]) => state);
  const activeSourceStatus: SourceState["status"] | null = activeSourceStates.some((state) => state.status === "error" || state.status === "unavailable") ? "error"
    : activeSourceStates.some((state) => state.status === "stale") ? "stale"
      : sourceState?.status ?? null;
  const auxiliarySources: Array<[string, SourceState]> = [];
  if (context && view === "forecast") {
    if (forecastFrames.length) auxiliarySources.push(...forecastSources.filter(([, state]) => state.status !== "ok"));
    if (activeLayers.incidents) {
      if (context.sources.wfigs && context.sources.wfigs.status !== "ok") auxiliarySources.push([contextSourceLabel("wfigs"), context.sources.wfigs]);
      if (context.sources.cwfis && context.sources.cwfis.status !== "ok") auxiliarySources.push([contextSourceLabel("cwfis"), context.sources.cwfis]);
    }
  }
  if (context && view === "air") auxiliarySources.push(...airSources.filter(([, state]) => state.status !== "ok"));
  const sourceStatusSummary = statusSources
    .map(([label, state]) => `${label} ${state.status}`)
    .join(", ");
  const publicationTime = contextHealth?.pointerUpdatedAt ?? context?.generatedAt ?? "";
  const publicationAge = publicationTime ? publicationFreshness(publicationTime, displayNow) : "unavailable";
  const observationAge = sourceState ? sourceFreshness(sourceState, displayNow) : "unavailable";
  const sourceTimeLabel = view === "forecast" ? "Guidance" : "Observed";
  const publicationStatus = contextHealth?.status ?? (healthError ? "unavailable" : "checking");
  const statusLabel = !context || !sourceState ? "Loading sources" : context.mode === "demo" ? `Demo ${view === "forecast" ? "smoke outlook" : "air quality"}` : `${view === "forecast" ? "Smoke outlook" : "Air quality"}; publication ${publicationStatus}, published ${publicationAge}; ${sourceTimeLabel.toLowerCase()} ${observationAge}; ${sourceStatusSummary}`;
  const statusClass = context?.mode === "demo" ? "demo"
    : !contextHealth && !healthError ? "loading"
      : contextHealth?.status === "unhealthy" ? "error"
        : contextHealth?.status === "degraded" || healthError ? "degraded"
          : activeSourceStatus === "error" ? "degraded"
            : activeSourceStatus ?? "loading";
  const clock = selection?.kind === "air-monitor" ? selection.monitor.observedAt
      : selection?.kind === "air-cluster" ? newestTime(selection.monitors.map((monitor) => monitor.observedAt))
        : context ? airObservationTime(context, "comparable") : "";
  const selectedReferenceTime = context?.mode === "demo" ? Date.parse(context.generatedAt)
    : selectedFromRanking ? topConditionsReferenceTime : displayNow;
  const detailsReferenceTime = context?.mode === "demo" ? Date.parse(context.generatedAt) : Date.now();
  const selectedAirReading = useMemo(() => selectedCity && airReady && context
    ? locationAirReading(selectedCity, monitors, selectedReferenceTime)
    : null, [airReady, context, monitors, selectedCity, selectedReferenceTime]);
  const selectedForecast = selectedCity && selection?.kind === "forecast" ? selection : null;
  const selectedAirSourceState = selectedAirReading?.monitor.source ? context?.sources[selectedAirReading.monitor.source] : null;
  const selectedAirWarning = selectedAirSourceState && selectedAirSourceState.status !== "ok" ? "This official source is currently retained or partially unavailable." : null;
  const selectedSmokeSources = selectedForecast?.source === "firework" ? [context?.sources.firework]
    : selectedForecast?.source === "hrrr" ? [context?.sources.hrrr]
      : [context?.sources.firework, context?.sources.hrrr];
  const selectedSmokeWarning = selectedSmokeSources.some((state) => state && state.status !== "ok") ? "Some smoke guidance sources are currently retained or unavailable." : null;
  const contextSurfaceA = legacyForecast && forecastA?.textureUrl ? legacyContextImages[forecastA.textureUrl] ?? null : null;
  const contextMaskA = legacyForecast && forecastA?.sourceMaskUrl ? legacyContextImages[forecastA.sourceMaskUrl] ?? null : null;
  const loadedSurfaceB = legacyForecast && surfaceFrameB?.textureUrl ? legacyContextImages[surfaceFrameB.textureUrl] ?? null : contextSurfaceA;
  const contextSurfaceB = loadedSurfaceB ?? contextSurfaceA;
  const loadedMaskB = legacyForecast && surfaceFrameB?.sourceMaskUrl ? legacyContextImages[surfaceFrameB.sourceMaskUrl] ?? null : contextMaskA;
  const contextMaskB = loadedMaskB ?? contextMaskA;
  const rankingFromRaster = residentRasters.find((source) => source.frameIndex === forecastPlayback.fromIndex)?.raster ?? null;
  const rankingToRaster = residentRasters.find((source) => source.frameIndex === forecastPlayback.toIndex)?.raster ?? rankingFromRaster;
  const rankingPairReady = legacyForecast
    ? Boolean(contextSurfaceA && (!forecastPlayback.interpolating || (forecastB?.textureUrl && legacyContextImages[forecastB.textureUrl])))
    : Boolean(rankingFromRaster && rankingToRaster);
  const rankingPairDisplayed = view !== "forecast" || !activeLayers.forecast
    || (renderedPair.fromIndex === forecastPlayback.fromIndex && (!forecastPlayback.interpolating || renderedPair.toIndex === forecastPlayback.toIndex));
  const topSmokeRows = topConditionsOpen && geo && rankingPairReady && rankingPairDisplayed ? rankSmokeCities(geo.cities, (city) => {
    if (!inForecastBounds(city.lon, city.lat)) return null;
    if (legacyForecast && contextSurfaceA && contextSurfaceB) {
      const fromSource = contextMaskA ? sampleForecastMask(contextMaskA, city.lon, city.lat) : undefined;
      const toSource = contextMaskB ? sampleForecastMask(contextMaskB, city.lon, city.lat) : undefined;
      const fromConcentration = fromSource !== "none" ? sampleForecastConcentration(contextSurfaceA, city.lon, city.lat, CONTEXT_BOUNDS, activeForecast.paletteVersion) : null;
      const toConcentration = toSource !== "none" ? sampleForecastConcentration(contextSurfaceB, city.lon, city.lat, CONTEXT_BOUNDS, activeForecast.paletteVersion) : null;
      return interpolateForecastConcentration(fromConcentration, toConcentration, forecastPlayback.t,
        fromSource ? fromSource !== "none" : fromConcentration !== null,
        toSource ? toSource !== "none" : toConcentration !== null);
    }
    if (!rankingFromRaster || !rankingToRaster) return null;
    const fromSource = sampleDecodedForecastMask(rankingFromRaster, city.lon, city.lat);
    const toSource = sampleDecodedForecastMask(rankingToRaster, city.lon, city.lat);
    const fromConcentration = fromSource === "none" ? null : sampleDecodedForecastConcentration(rankingFromRaster, city.lon, city.lat);
    const toConcentration = toSource === "none" ? null : sampleDecodedForecastConcentration(rankingToRaster, city.lon, city.lat);
    return interpolateForecastConcentration(fromConcentration, toConcentration, forecastPlayback.t, fromSource !== "none", toSource !== "none");
  }) : [];
  const topAqiRows = topConditionsOpen && geo && airReady
    ? rankAqiCities(geo.cities, visibleMonitors, context?.mode === "demo" ? Date.parse(context.generatedAt) : topConditionsReferenceTime)
    : [];
  const detailImages = detailPlaybackMode === "base-only" ? [] : readyDetailTileIds.flatMap((tileId) => {
    const tileA = forecastA?.detailTiles?.[tileId];
    const tileB = surfaceFrameB?.detailTiles?.[tileId] ?? tileA;
    if (!tileA || !tileB) return [];
    const tileResident = decodedResidents.filter((source) => source.tileId === tileId);
    return [{ tileId, column: tileA.column, row: tileA.row, fromIndex: forecastPlayback.fromIndex, toIndex: surfaceToIndex, resident: tileResident }];
  });
  const interpolationReady = legacyForecast ? Boolean(contextSurfaceA && contextSurfaceB) : frameRenderReady([forecastPlayback.fromIndex, surfaceToIndex]);
  forecastInitializedRef.current ||= interpolationReady;
  forecastRasterInitializedRef.current ||= forecastRasterReady;
  const forecastReady = !activeLayers.forecast || (legacyForecast
    ? Boolean(contextSurfaceA && (interpolationReady || forecastInitializedRef.current))
    : Boolean(frameRenderReady([forecastPlayback.fromIndex]) && (interpolationReady || forecastInitializedRef.current) && (forecastRasterReady || forecastRasterInitializedRef.current)));
  const viewReady = sceneReady && (view === "forecast" ? Boolean(context && forecastReady) : Boolean(context && airViewReady(activeLayers.air, airMapReady)));
  const unavailable = context && sourceState && (view === "forecast"
    ? (sourceState.status === "error" || sourceState.status === "unavailable") && forecastFrames.length === 0
    : airViewUnavailable(context) && visibleMonitors.length === 0)
    ? (view === "air" ? airSources.find(([, state]) => state.error)?.[1].error : sourceState.error) ?? `${view} data is currently unavailable.` : null;
  const layerError = assetError ?? imageError ?? detailImageError;
  const visibleLayerError = layerError ?? (view === "air" ? airError : null) ?? (activeLayers.incidents ? incidentError : null);
  const publicError = contextError ?? unavailable ?? (!viewReady ? (view === "air" ? airError : layerError) : null);
  const displayError = publicError ?? sceneError;
  useEffect(() => {
    if (!networkOnline) {
      wasOfflineRef.current = true;
      return;
    }
    if (!wasOfflineRef.current || !(publicError || sceneError)) return;
    wasOfflineRef.current = false;
    retryContext();
  }, [networkOnline, publicError, retryContext, sceneError]);
  const publicationWarning = context?.mode !== "live" ? null
    : contextHealth?.status === "unhealthy" ? "Live data has not refreshed on schedule. Showing the last complete publication."
      : contextHealth?.status === "degraded" && contextHealth.publication !== "fresh" ? "The latest scheduled update did not publish fresh data. Showing the last complete publication."
        : healthError ? "Automatic update health could not be verified. Showing the last loaded publication."
          : null;
  const warningMessages = [
    publicationWarning,
    !publicationWarning && activeForecast.integratedStatus === "retained" ? "Showing the last complete smoke outlook while a newer update is unavailable." : null,
    ...auxiliarySources.map(([label, state]) => `${label}: ${state.status === "stale" ? "stale" : state.status === "unavailable" ? "unavailable" : "update failed"}`),
    view === "air" && airReady ? emptyAirMonitorWarning(visibleMonitors.length, Boolean(unavailable)) : null,
    visibleLayerError && viewReady ? visibleLayerError : null,
  ].filter((message): message is string => Boolean(message));

  const resetPairReady = atEndpoint && resetForecastEntries.length > 1 && resetAssetsReady;
  const inspectPlaybackAllowed = Boolean(inspectPoint) && contextPanel === "selection" && !selectedCity
    && (selection == null || selection.kind === "forecast");
  const interactionBlocked = reducedMotion || !documentVisible || methodologyOpen
    || (Boolean(contextPanel) && !inspectPlaybackAllowed)
    || Boolean(selectedCity)
    || (Boolean(selection) && !inspectPlaybackAllowed)
    || view !== "forecast" || !activeLayers.forecast || !viewReady || Boolean(imageError);

  useEffect(() => {
    if (playbackPhase !== "buffering" || interactionBlocked || !frameRenderReady(forwardResident)) return;
    changePlaybackPhase("playing");
  }, [changePlaybackPhase, forwardResident, frameRenderReady, interactionBlocked, playbackPhase]);

  useEffect(() => {
    const onActivity = (event: Event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (event.type === "keydown") {
        if (target?.closest(".transport")) return;
        const key = (event as KeyboardEvent).key;
        const timelineStep = (key === "ArrowLeft" || key === "ArrowRight") && !target?.closest("[data-map-keyboard]");
        const mapZoom = ["+", "=", "-", "_"].includes(key) && target?.closest("[data-map-keyboard]");
        if (key !== " " && !timelineStep && (!mapZoom || playbackPhaseRef.current === "preparing-detail")) pauseForInteraction();
        return;
      }
      if (event.type === "wheel" && playbackPhaseRef.current !== "preparing-detail") return;
      const mapInteraction = target?.closest("[data-map-keyboard]") && (event.type === "touchstart" || event.type === "pointerdown");
      if (mapInteraction && playbackPhaseRef.current !== "preparing-detail") return;
      if (target?.closest(".explorer") && !target.closest(".transport, .timeline, .timeline-wrap")) pauseForInteraction();
    };
    window.addEventListener("pointerdown", onActivity, { passive: true });
    window.addEventListener("wheel", onActivity, { passive: true });
    window.addEventListener("touchstart", onActivity, { passive: true });
    window.addEventListener("keydown", onActivity);
    return () => {
      window.removeEventListener("pointerdown", onActivity);
      window.removeEventListener("wheel", onActivity);
      window.removeEventListener("touchstart", onActivity);
      window.removeEventListener("keydown", onActivity);
    };
  }, [pauseForInteraction]);

  useEffect(() => {
    if (!animationActive || !interactionBlocked) return;
    changeSmokeOpacity(1);
    changePlaybackPhase("interaction-paused");
  }, [animationActive, changePlaybackPhase, changeSmokeOpacity, interactionBlocked]);

  useEffect(() => {
    if (!activeLayers.forecast) forecastRasterInitializedRef.current = false;
  }, [activeLayers.forecast]);

  useEffect(() => {
    if (playbackPhase !== "waiting" || forecastPosition < forecastSpan || forecastSpan <= 0) return;
    if (imageError || !resetPairReady) {
      if (imageError) changePlaybackPhase("interaction-paused");
      return;
    }
    resetTargetRef.current = { now: resetTarget.now, position: resetTarget.position };
    changePlaybackPhase("fading-out");
  }, [changePlaybackPhase, forecastPosition, forecastSpan, imageError, playbackPhase, resetPairReady, resetTarget.now, resetTarget.position]);

  useEffect(() => {
    if (playbackPhase !== "fading-out" && playbackPhase !== "fading-in") return;
    const fadingOut = playbackPhase === "fading-out";
    const started = performance.now();
    let frame = 0;
    let renderedIntermediate = false;
    const fade = (now: number) => {
      const elapsedProgress = Math.min(1, (now - started) / LOOP_FADE_MS);
      // A heavily stalled renderer still gets one visible fade sample before
      // the terminal opacity and phase are committed on the following frame.
      const progress = !renderedIntermediate && elapsedProgress >= 1 ? 0.5 : elapsedProgress;
      changeSmokeOpacity(fadingOut ? 1 - progress : progress);
      if (progress < 1) {
        renderedIntermediate = true;
        frame = window.requestAnimationFrame(fade);
      }
      else changePlaybackPhase(fadingOut ? "resetting" : "playing");
    };
    frame = window.requestAnimationFrame(fade);
    return () => window.cancelAnimationFrame(frame);
  }, [changePlaybackPhase, changeSmokeOpacity, playbackPhase]);

  useEffect(() => {
    if (playbackPhase !== "resetting") return;
    const target = resetTargetRef.current ?? currentResetTarget();
    resetTargetRef.current = null;
    displayNowRef.current = target.now;
    setDisplayNow(target.now);
    commitPlaybackPosition(target.position);
    const frame = window.requestAnimationFrame(() => changePlaybackPhase("fading-in"));
    return () => window.cancelAnimationFrame(frame);
  // Reset only after the verified pair is resident, while layer opacity is zero.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [changePlaybackPhase, commitPlaybackPosition, playbackPhase]);

  const updateIncidents = (value: boolean) => {
    pauseForInteraction();
    setIncidentsVisible(value);
  };
  const toggleLayers = (trigger?: HTMLElement | null) => {
    pauseForInteraction();
    if (layersOpen) closeContextPanel(true);
    else {
      clearLocation();
      openContextPanel("layers", trigger);
    }
  };
  const sampleMap = ({ lon, lat }: { lon: number; lat: number }, options: { preserveLive?: boolean; allowInAir?: boolean; returnFocus?: HTMLElement | null; pause?: boolean; preserveSelection?: boolean; openWithoutForecast?: boolean } = {}) => {
    if (view === "air" && !options.allowInAir) return;
    const currentPlayback = playbackAt(forecastEntries, forecastPositionRef.current);
    if (!currentPlayback.observationTime && !options.openWithoutForecast) return;
    if (options.pause !== false) pauseForInteraction();
    if (contextPanel !== "selection") {
      const active = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      const returnFocus = options.returnFocus
        ?? (active?.closest(".context-drawer") ? contextReturnFocusRef.current : null)
        ?? (options.preserveLive ? active : document.querySelector<HTMLElement>("[data-map-keyboard]"));
      openContextPanel("selection", returnFocus, { pause: options.pause });
    }
    if (!options.preserveLive) leaveLive();
    if (!currentPlayback.observationTime) return;
    const requestId = ++selectionRequestRef.current;
    if (!options.preserveSelection) setSelection(null);
    const fromFrame = forecastFrames[currentPlayback.fromIndex] ?? null;
    const toFrame = forecastFrames[currentPlayback.toIndex] ?? fromFrame;
    const modelRun = newestTime([fromFrame?.modelRun, toFrame?.modelRun, activeForecast.modelRun]);
    const payload = { kind: "forecast" as const, lon, lat, modelRun, validTime: currentPlayback.observationTime, horizonHours: activeForecast.horizonHours, concentrationMax: activeForecast.paletteVersion === FORECAST_DISPLAY_PALETTE_VERSION ? 1000 : 250 };
    if (!inForecastBounds(lon, lat)) {
      pendingSelectionRef.current = null;
      setSelection({ ...payload, source: "none" });
      return;
    }
    if (legacyForecast && contextSurfaceA && contextSurfaceB) {
      const fromSource = contextMaskA ? sampleForecastMask(contextMaskA, lon, lat) : undefined;
      const toSource = contextMaskB ? sampleForecastMask(contextMaskB, lon, lat) : undefined;
      const canDecode = activeForecast.paletteVersion === FORECAST_DISPLAY_PALETTE_VERSION || activeForecast.paletteVersion === LEGACY_FORECAST_DISPLAY_PALETTE_VERSION;
      const fromConcentration = canDecode && fromSource !== "none" ? sampleForecastConcentration(contextSurfaceA, lon, lat, CONTEXT_BOUNDS, activeForecast.paletteVersion) : null;
      const toConcentration = canDecode && toSource !== "none" ? sampleForecastConcentration(contextSurfaceB, lon, lat, CONTEXT_BOUNDS, activeForecast.paletteVersion) : null;
      const fromCovered = fromSource ? fromSource !== "none" : fromConcentration !== null;
      const toCovered = toSource ? toSource !== "none" : toConcentration !== null;
      const concentration = canDecode ? interpolateForecastConcentration(fromConcentration, toConcentration, currentPlayback.t, fromCovered, toCovered) : undefined;
      const source = fromSource && toSource
        ? (fromSource === toSource ? fromSource : undefined)
        : fromCovered || toCovered ? "firework" : "none";
      setSelection({ ...payload, source, concentration });
      pendingSelectionRef.current = null;
      return;
    }
    if (fromFrame?.sourceMaskUrl && toFrame?.sourceMaskUrl) {
      const tileId = detailTileId(activeDetailGrid, lon, lat);
      const fromTile = fromFrame.detailTiles?.[tileId];
      const toTile = toFrame.detailTiles?.[tileId];
      const fromDetail = decodedResidents.find((source) => source.tileId === tileId && source.frameIndex === currentPlayback.fromIndex)?.raster;
      const toDetail = decodedResidents.find((source) => source.tileId === tileId && source.frameIndex === currentPlayback.toIndex)?.raster;
      const detailReady = Boolean(fromTile && toTile && fromDetail && toDetail);
      const bounds = detailReady && fromTile ? detailTileBounds(activeDetailGrid, fromTile.column, fromTile.row) : undefined;
      const fromRaster = detailReady ? fromDetail : residentRasters.find((source) => source.frameIndex === currentPlayback.fromIndex)?.raster;
      const toRaster = detailReady ? toDetail : residentRasters.find((source) => source.frameIndex === currentPlayback.toIndex)?.raster;
      if (fromRaster && toRaster) {
        const fromSource = sampleDecodedForecastMask(fromRaster, lon, lat, bounds);
        const toSource = sampleDecodedForecastMask(toRaster, lon, lat, bounds);
        const canDecode = activeForecast.paletteVersion === FORECAST_DISPLAY_PALETTE_VERSION || activeForecast.paletteVersion === LEGACY_FORECAST_DISPLAY_PALETTE_VERSION;
        const fromConcentration = canDecode && fromSource !== "none" ? sampleDecodedForecastConcentration(fromRaster, lon, lat, bounds) : null;
        const toConcentration = canDecode && toSource !== "none" ? sampleDecodedForecastConcentration(toRaster, lon, lat, bounds) : null;
        const concentration = canDecode
          ? interpolateForecastConcentration(fromConcentration, toConcentration, currentPlayback.t, fromSource !== "none", toSource !== "none")
          : undefined;
        const source = fromSource === toSource ? fromSource : undefined;
        const selectedRun = newestTime([
          fromSource === "none" ? undefined : forecastFrameRun(fromFrame, fromSource),
          toSource === "none" ? undefined : forecastFrameRun(toFrame, toSource),
        ]) || payload.modelRun;
        const interpolation = currentPlayback.interpolating ? {
          fromValidTime: fromFrame.validTime,
          toValidTime: toFrame.validTime,
          t: currentPlayback.t,
          fromSource,
          toSource,
        } : undefined;
        if (requestId === selectionRequestRef.current) setSelection({ ...payload, source, concentration, modelRun: selectedRun, interpolation });
        pendingSelectionRef.current = null;
      } else {
        pendingSelectionRef.current = { lon, lat };
        if (requestId === selectionRequestRef.current) setSelection(null);
      }
      return;
    }
  };
  const selectMap = (payload: { lon: number; lat: number }) => {
    if (view === "air") return;
    setSelectedCity(null);
    setSelectedFromRanking(false);
    setFocusLocation(null);
    setInspectPoint(reduceInspectPoint({ type: "set", lon: payload.lon, lat: payload.lat }));
    sampleMap(payload, { pause: false, preserveLive: true });
  };
  const selectCity = (city: CityLabel, preserveForecastTime = false, returnFocus?: HTMLElement | null) => {
    pauseForInteraction();
    clearSelection();
    setInspectPoint(null);
    const requestId = selectionRequestRef.current;
    setSelectedCity(city);
    setSelectedFromRanking(preserveForecastTime);
    setFocusLocation({ lon: city.lon, lat: city.lat, requestId });
    if (!preserveForecastTime && context?.mode === "live" && liveAvailable) {
      const now = Date.now();
      followingLiveRef.current = true;
      setFollowingLive(true);
      displayNowRef.current = now;
      setDisplayNow(now);
      commitPlaybackPosition(livePosition(forecastEntries, now).positionMs);
    } else if (!preserveForecastTime && context?.mode === "demo") {
      followingLiveRef.current = false;
      setFollowingLive(false);
      commitPlaybackPosition(0);
    }
    changeSmokeOpacity(1);
    changePlaybackPhase("manual-paused");
    sampleMap(city, { preserveLive: true, allowInAir: true, returnFocus, openWithoutForecast: true });
  };
  const sampleInspect = (point: { lon: number; lat: number }, options: { allowInAir?: boolean; preserveSelection?: boolean } = {}) => {
    sampleMap(point, {
      pause: false,
      preserveLive: true,
      allowInAir: options.allowInAir,
      preserveSelection: options.preserveSelection,
    });
  };
  useEffect(() => {
    const pending = pendingSelectionRef.current ?? inspectPoint;
    if (pending && (legacyForecast ? contextSurfaceA : residentRasters.length > 0)) {
      sampleInspect(pending, { allowInAir: Boolean(selectedCity), preserveSelection: Boolean(inspectPoint) });
    }
  // Retry the user's pending inspection when its exact temporal image pair arrives.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contextSurfaceA, decodedResidents.length, forecastPosition, inspectPoint, legacyForecast, residentRasters.length]);
  const selectedDetailKey = (() => {
    const point = selectedCity ?? inspectPoint;
    if (!point || legacyForecast || !inForecastBounds(point.lon, point.lat)) return "";
    const tileId = detailTileId(activeDetailGrid, point.lon, point.lat);
    const required = new Set([forecastPlayback.fromIndex, forecastPlayback.toIndex]);
    const matching = decodedResidents.filter((source) => source.tileId === tileId && required.has(source.frameIndex));
    if (![...required].every((frameIndex) => matching.some((source) => source.frameIndex === frameIndex))) return "";
    const identity = selectedCity ? cityIdentity(selectedCity) : `${point.lon},${point.lat}`;
    return `${identity}:${matching.map((source) => source.key).sort().join("|")}`;
  })();
  const sampledDetailKey = useRef("");
  useEffect(() => {
    const point = selectedCity ?? inspectPoint;
    if (!point || !selectedDetailKey || sampledDetailKey.current === selectedDetailKey) return;
    sampledDetailKey.current = selectedDetailKey;
    sampleInspect(point, { allowInAir: Boolean(selectedCity), preserveSelection: Boolean(inspectPoint) });
  // Resample once the selected location's exact regional pair replaces the base fallback.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDetailKey]);
  sampleMapRef.current = sampleMap;
  useEffect(() => {
    if (!inspectPoint) return;
    const resample = () => {
      const point = inspectPointRef.current;
      if (!point) return;
      sampleMapRef.current(point, { pause: false, preserveLive: true, preserveSelection: true });
    };
    resample();
    return playbackUiStore.subscribe(resample);
  }, [inspectPoint, playbackUiStore]);
  const openTopConditions = (trigger?: HTMLElement | null) => {
    pauseForInteraction();
    clearLocation();
    const referenceTime = context?.mode === "demo" ? Date.parse(context.generatedAt) : Date.now();
    topConditionsAirOnlyRef.current = view === "air";
    setTopConditionsReferenceTime(referenceTime);
    if (view === "air") {
      displayNowRef.current = referenceTime;
      setDisplayNow(referenceTime);
      commitPlaybackPosition(context?.mode === "demo" ? 0 : livePosition(forecastEntries, referenceTime).positionMs);
    }
    openContextPanel("top-conditions", trigger);
  };
  const switchView = (next: ProductView) => { pauseForInteraction(); closeContextPanel(); clearLocation(); setView(next); };
  const moveViewFocus = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const tabs = [...(event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]:not(:disabled)') ?? [])];
    const current = tabs.indexOf(event.currentTarget);
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault();
    tabs[next]?.focus();
    tabs[next]?.click();
  };
  const showMethodology = (returnFocus?: HTMLElement | null) => {
    const candidate = returnFocus ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null);
    methodologyReturnFocus.current = candidate?.closest(".context-drawer")
      ? contextReturnFocusRef.current ?? document.querySelector<HTMLElement>("[data-map-keyboard]")
      : candidate;
    pauseForInteraction();
    closeContextPanel();
    setMethodologyOpen(true);
    methodologyDialog.current?.showModal();
  };
  const openMethodology = (event: React.MouseEvent<HTMLButtonElement>) => showMethodology(event.currentTarget);
  const openMethodologyFromCurrent = () => showMethodology();
  const cyclePlaybackSpeed = () => {
    const next = PLAYBACK_SPEEDS[(PLAYBACK_SPEEDS.indexOf(playbackSpeed) + 1) % PLAYBACK_SPEEDS.length];
    setPlaybackSpeed(next);
  };
  const requestInstall = async () => {
    if (!installPrompt) {
      openContextPanel("install", contextReturnFocusRef.current);
      return;
    }
    const prompt = installPrompt;
    try {
      await prompt.prompt();
      const choice = await prompt.userChoice;
      setInstallPrompt(null);
      if (choice.outcome === "accepted") {
        setStandalone(true);
        closeContextPanel();
      }
    } catch {
      setInstallPrompt(null);
      openContextPanel("install", contextReturnFocusRef.current);
    }
  };

  if (!webgl) {
    const fallbackSources = context ? fallbackSourceEntries(context) : [];
    return <main className="fallback-shell"><div className="fallback-card panel"><span className="eyebrow">Visualization unavailable</span><h1>WebGL2 is required for the interactive globe.</h1><p>You can still inspect the current publication and source status.</p>{contextError ? <p role="alert">{userFacingLoadError(contextError)}</p> : null}{context ? <ul className="fallback-sources" data-testid="fallback-sources">{fallbackSources.map(([label, state]) => <li key={label} data-status={state.status}><strong>{label}</strong><span>{context.mode === "demo" ? `Demo · ${state.status}` : sourceFreshness(state)}</span></li>)}</ul> : contextError ? null : <p role="status">Loading dataset states…</p>}<div className="source-links"><a href={contextPointerUrl()}>Open current data</a></div></div></main>;
  }

  const layerControls = <LayerToggle checked={activeLayers.incidents} label="Reported wildfires" description="US and Canadian agency reports" onChange={updateIncidents} testId="incidents-toggle" />;

  const forecastLegend = activeForecast.legendUrl
    // Content-addressed ingest PNG; next/image is not appropriate here.
    // eslint-disable-next-line @next/next/no-img-element
    ? <img className="ramp forecast" src={activeForecast.legendUrl} alt="" data-testid="forecast-legend" />
    : <div className="ramp forecast" />;
  const smokeScale = forecastLegendScale(activeForecast.paletteVersion ?? LEGACY_FORECAST_DISPLAY_PALETTE_VERSION);
  const epaLegend = <div className="legend air-legend"><span>U.S. EPA PM2.5 AQI</span><div className="aqi-ramp" aria-hidden="true" /><div className="aqi-key" aria-label="US EPA PM2.5 AQI categories">{EPA_AQI_CATEGORIES.map((category) => <span key={category.label} aria-label={`${category.label}, AQI ${category.range}`}><i className={`aqi-swatch ${category.className}`} /><b>{category.label}</b><small>{category.range}</small></span>)}</div></div>;
  const fullLegend = view === "forecast" ? <div className="legend-group single"><div className="legend forecast-legend">{smokeScale ? <span className="legend-caption">TitanSkies display scale · {smokeScale.units}</span> : null}{forecastLegend}{smokeScale ? <div className="forecast-legend-ticks" data-testid="forecast-legend-scale" aria-label={`TitanSkies smoke display scale in ${smokeScale.units}`}>{smokeScale.ticks.map((tick, index) => <small key={tick.value} data-edge={index === 0 ? "start" : index === smokeScale.ticks.length - 1 ? "end" : undefined} style={{ left: `${tick.position}%` }}>{tick.label}</small>)}</div> : <div className="legend-scale" aria-label="Smoke density scale from faint to dense"><small>Faint</small><small>Moderate</small><small>Dense</small></div>}</div>{activeLayers.incidents ? <div className="legend marker-legend"><span><i className="incident-dot" />Reported wildfire</span></div> : null}</div>
    : <div className="legend-group single">{epaLegend}</div>;
  const forecastUnavailable = Boolean(context && forecastFrames.length === 0);
  const viewUnavailable = (item: ProductView) => item === "air"
    ? Boolean(context && airViewUnavailable(context))
    : forecastUnavailable;
  const activeViewUnavailable = viewUnavailable(view);
  const compactStatus = compactFreshnessLabel({
    mode: context?.mode ?? null,
    health: publicationStatus,
    publication: contextHealth?.publication ?? null,
    source: activeSourceStatus,
    publicationAge,
    compact: mobile || shortLandscape,
  });
  const inspectLabel = inspectMarkerCopy({
    hasPoint: Boolean(inspectPoint),
    loading: Boolean(inspectPoint && (!selection || selection.kind !== "forecast")),
    inBounds: inspectPoint ? inForecastBounds(inspectPoint.lon, inspectPoint.lat) : false,
    source: selection?.kind === "forecast" ? selection.source : undefined,
    concentration: selection?.kind === "forecast" ? selection.concentration : undefined,
    concentrationMax: selection?.kind === "forecast" ? selection.concentrationMax : undefined,
  });
  const drawerTitle = contextPanel === "top-conditions" ? "Highest conditions"
    : contextPanel === "layers" ? "Map options"
      : contextPanel === "legend" ? view === "forecast" ? "Smoke legend" : "AQI legend"
        : contextPanel === "mobile-actions" ? "More"
          : contextPanel === "source-status" ? "Data status"
            : contextPanel === "install" ? "Install TitanSkies"
              : selectedCity ? selectedCity.name
                : selection ? detailsTitle(selection)
                  : "Selected data";
  const drawerEyebrow = contextPanel === "top-conditions" ? context?.mode === "demo" ? "Demo rankings" : undefined
    : contextPanel === "selection" && selectedCity ? selectedFromRanking ? "Selected location" : context?.mode === "demo" ? "Demo conditions" : undefined
      : contextPanel === "selection" && selection ? detailsEyebrow(selection)
        : undefined;
  const resetView = () => {
    pauseForInteraction();
    closeContextPanel();
    clearLocation();
    setResetSignal((value) => value + 1);
  };
  const installAction = !standalone ? <button type="button" onClick={() => { void requestInstall(); }} data-testid="install-action"><Icon name="install" /><span><strong>Install TitanSkies</strong><small>{installPrompt ? "Install on this device" : ios ? "Add from Safari’s Share menu" : "Use your browser’s install menu"}</small></span></button> : null;
  const drawerContent = contextPanel === "top-conditions" ? <TopConditions view={view} cityCount={geo?.cities.length ?? 0} smokeRows={topSmokeRows} smokeLoading={Boolean(forecastFrames.length && !imageError && (!rankingPairReady || !rankingPairDisplayed))} smokeError={!forecastFrames.length ? "Modeled smoke is unavailable." : imageError} smokeWarning={activeForecast.integratedStatus === "retained" ? "Showing the last complete smoke outlook while a newer update is unavailable." : selectedSmokeWarning} smokeValidTime={forecastPlayback.observationTime} concentrationMax={activeForecast.paletteVersion === FORECAST_DISPLAY_PALETTE_VERSION ? 1000 : 250} aqiRows={topAqiRows} aqiLoading={Boolean(!airReady && !airError)} aqiError={!airReady ? airError : null} aqiWarning={airReady ? airError : null} referenceTime={context?.mode === "demo" ? Date.parse(context.generatedAt) : topConditionsReferenceTime} onSelect={(city) => selectCity(city, true)} onMethodology={openMethodologyFromCurrent} />
    : contextPanel === "layers" ? <div className="map-options">{layerControls}</div>
      : contextPanel === "legend" ? fullLegend
        : contextPanel === "source-status" && context ? <SourceStatusPanel mode={context.mode} health={contextHealth} healthError={healthError} publicationAt={publicationTime} sourceLabel={sourceTimeLabel} sourceAt={view === "air" ? airObservationTime(context, "comparable") : sourceState?.observedAt ?? sourceState?.checkedAt ?? ""} sources={statusSources} referenceTime={context.mode === "demo" ? Date.parse(context.generatedAt) : displayNow} onRetry={retryHealth} onMethodology={openMethodologyFromCurrent} />
        : contextPanel === "mobile-actions" ? <div className="mobile-action-list">
          {installAction}
          <button type="button" onClick={resetView}><Icon name="reset" /><span><strong>Reset view</strong><small>Return to the North America overview</small></span></button>
          {view === "forecast" ? <button type="button" onClick={() => toggleLayers(contextReturnFocusRef.current)}><Icon name="layers" /><span><strong>Map options</strong><small>Reported wildfire context</small></span></button> : null}
          {!mobile && !shortLandscape ? <button type="button" onClick={(event) => openContextPanel("legend", event.currentTarget)}><Icon name="legend" /><span><strong>Legend</strong><small>{view === "forecast" ? "Smoke density scale" : "Air-quality scale"}</small></span></button> : null}
          {view === "forecast" ? reducedMotion
            ? <div className="mobile-action-status" data-testid="mobile-playback-motion-status"><Icon name="play" /><span><strong>Playback animation off</strong><small>Reduce Motion is enabled; Previous and Next still work.</small></span></div>
            : <button type="button" onClick={cyclePlaybackSpeed} data-testid="mobile-playback-speed"><Icon name="play" /><span><strong>Playback speed</strong><small>{playbackSpeedLabel(playbackSpeed)} · tap for {playbackSpeedLabel(PLAYBACK_SPEEDS[(PLAYBACK_SPEEDS.indexOf(playbackSpeed) + 1) % PLAYBACK_SPEEDS.length])}</small></span></button> : null}
          <button type="button" onClick={openMethodology}><Icon name="info" /><span><strong>About & sources</strong><small>Methods, limitations, and providers</small></span></button>
        </div>
          : contextPanel === "install" ? <div className="install-instructions" data-testid="install-instructions"><p>{ios ? "Open TitanSkies in Safari, tap Share, then choose Add to Home Screen and tap Add." : "Open your browser menu and choose Install app or Add to Home Screen."}</p><small>TitanSkies still needs a network connection for forecasts and observations.</small></div>
            : contextPanel === "selection" ? selectedCity ? <LocationCard city={selectedCity} forecast={selectedForecast} smokeLoading={Boolean(forecastFrames.length && !selectedForecast && inForecastBounds(selectedCity.lon, selectedCity.lat))} smokeWarning={selectedSmokeWarning} airReading={selectedAirReading} airLoading={!airReady && !airError} airError={airError} airWarning={selectedAirWarning} referenceTime={selectedReferenceTime} compact={mobile || shortLandscape} onRetryAir={retryAir} onMethodology={openMethodologyFromCurrent} />
              : <DetailsCard selection={selection} referenceTime={detailsReferenceTime} compact={mobile || shortLandscape} onMonitorSelect={(monitor) => setSelection({ kind: "air-monitor", monitor })} onIncidentSelect={(incident) => setSelection({ kind: "incident", incident })} onMethodology={openMethodologyFromCurrent} />
              : null;
  const globeHelp = view === "forecast"
    ? `Drag to rotate, scroll or pinch for centered zoom, and click a smoke-outlook area to pin its modeled concentration${activeLayers.incidents ? " or select a reported-wildfire marker for details" : ""}. Zoom becomes more precise near the surface. A drag does not place a pin. Focus the globe, use Plus and Minus to zoom, Arrow keys to move the inspection point, and Enter to sample the forecast. Outside the globe, use Left and Right Arrow to step through forecast time and Space to play or pause.`
    : "Drag to rotate, scroll or pinch for centered zoom, and select an air-quality monitor for details. Zoom becomes more precise near the surface. A drag does not select a monitor. Focus the globe, use Plus and Minus to zoom, Arrow keys to move the keyboard cursor, and Enter to select a nearby monitor.";

  return <main ref={explorerRef} className={`explorer view-${view}${contextPanel ? " context-open" : ""}${shortLandscape ? " short-landscape" : ""}`} aria-busy={!viewReady && !publicError} data-context-generated-at={context?.generatedAt} data-playback-phase={playbackPhase} data-detail-playback-mode={detailPlaybackMode} data-interpolation-ready={interpolationReady ? "true" : "false"} data-render-mix={contextMixRef.current.toFixed(6)} data-smoke-opacity={smokeOpacityRef.current.toFixed(3)} data-resident-frame-count={resident.length} data-raster-resident-count={decodedResidents.length} data-raster-peak-frame-window={rasterCache.metrics().peakFrameIndicesPerSurface} data-temporal-shader-samples={TEMPORAL_SHADER_SAMPLE_COUNT}>
    <h1 className="sr-only">North America smoke and air-quality explorer</h1>
    <p id="globe-help" className="sr-only">{globeHelp}</p>
    <div id="view-panel" className="scene" role="tabpanel" aria-labelledby={`view-tab-${view}`} aria-describedby="globe-help">
      <SmokeScene layers={activeLayers} geo={geo} mobile={mobile} compactLabels={shortLandscape} monitors={visibleMonitors} selectedMonitorIds={selectedAirReading ? [selectedAirReading.monitor.id] : selection?.kind === "air-monitor" ? [selection.monitor.id] : selection?.kind === "air-cluster" ? selection.monitors.map((monitor) => monitor.id) : []} incidents={incidents} selectedIncidentIds={selection?.kind === "incident" ? [selection.incident.id] : selection?.kind === "incident-cluster" ? selection.incidents.map((incident) => incident.id) : []} contextImageA={contextSurfaceA} contextImageB={contextSurfaceB} legacyForecast={legacyForecast} residentRasters={residentRasters} detailImages={detailImages} detailTileIds={detailTileIds} detailGrid={activeDetailGrid} renderedFromIndex={forecastPlayback.fromIndex} renderedToIndex={surfaceToIndex} contextMixRef={contextMixRef} smokeOpacityRef={smokeOpacityRef} renderRequestRef={sceneRenderRequestRef} renderMix={contextMixRef.current} renderOpacity={smokeOpacityRef.current} playing={animationActive} detailPlaybackMode={detailPlaybackMode} reducedMotion={reducedMotion} resetSignal={resetSignal} focusLocation={focusLocation} inspectLocation={inspectPoint} inspectLabel={inspectLabel} onReady={handleSceneReady} onLayerReady={setForecastRasterReady} onDetailsHidden={finishDetailPreparation} onPairCommitted={handlePairCommitted} onDetailTiles={handleDetailTiles} onSelect={(next) => { if (!next) { closeContextPanel(); return; } setInspectPoint(null); selectionRequestRef.current += 1; pauseForInteraction(); setSelectedCity(null); setSelectedFromRanking(false); setFocusLocation(null); if (view === "forecast") leaveLive(); setSelection(next); openContextPanel("selection", document.querySelector<HTMLElement>("[data-map-keyboard]")); }} onMapClick={selectMap} />
    </div>
    <div className="explorer-chrome">
      <header className="top-toolbar">
        <div className="toolbar-cluster toolbar-primary panel">
          <div className="brand-lockup" aria-label="TitanSkies Smoke Forecast"><div className="brand-mark" aria-hidden="true"><Image src="/brand/logo-icon.png" alt="" width={28} height={28} priority unoptimized /></div><span className="brand-copy"><strong>TitanSkies</strong><small>Smoke Forecast</small></span></div>
          <nav className="view-tabs" role="tablist" aria-label="Data view" data-active-view={view}>{VIEWS.map((item) => { const disabled = viewUnavailable(item.id); return <button key={item.id} id={`view-tab-${item.id}`} role="tab" aria-selected={view === item.id} aria-controls="view-panel" tabIndex={!disabled && (view === item.id || activeViewUnavailable) ? 0 : -1} disabled={disabled} onKeyDown={moveViewFocus} onClick={() => switchView(item.id)} data-testid={`view-${item.id}`}>{item.label}{disabled ? <small>Unavailable</small> : null}</button>; })}</nav>
        </div>
        <div className="toolbar-cluster toolbar-search panel"><div className="location-tools"><LocationSearch cities={geo?.cities ?? []} compact={shortLandscape} disabled={!geo || !context} onOpen={() => closeContextPanel()} onSelect={(city, returnFocus) => selectCity(city, false, returnFocus)} /></div></div>
        <div className="toolbar-cluster toolbar-secondary panel">
          <button className="top-conditions-trigger text-button" type="button" disabled={!geo || !context} aria-label="Worst 5 conditions" aria-expanded={topConditionsOpen} aria-controls="context-drawer" onClick={(event) => topConditionsOpen ? closeContextPanel(true) : openTopConditions(event.currentTarget)}><Icon name="ranking" /><span>Worst 5</span></button>
          <div className="toolbar-utilities" role="group" aria-label="Map controls">
            {view === "forecast" ? <button className="toolbar-action icon-button" type="button" onClick={(event) => toggleLayers(event.currentTarget)} aria-label="Map options" aria-expanded={layersOpen} aria-controls="context-drawer"><Icon name="layers" /></button> : null}
            <button className="toolbar-action icon-button" type="button" onClick={resetView} aria-label="Reset globe view" data-testid="reset-view"><Icon name="reset" /></button>
            <button className="toolbar-action icon-button" type="button" onClick={openMethodology} aria-label="Open methodology"><Icon name="info" /></button>
          </div>
          <button className="toolbar-action mobile-action icon-button" type="button" onClick={(event) => contextPanel === "mobile-actions" ? closeContextPanel(true) : openContextPanel("mobile-actions", event.currentTarget)} aria-label="More options" aria-expanded={contextPanel === "mobile-actions"} aria-controls="context-drawer"><Icon name="more" /></button>
          <button className={`status-badge ${statusClass}`} data-testid="source-badge" type="button" disabled={!context} aria-label={statusLabel} aria-live="polite" aria-expanded={Boolean(context && contextPanel === "source-status")} aria-controls="context-drawer" onClick={(event) => contextPanel === "source-status" ? closeContextPanel(true) : openContextPanel("source-status", event.currentTarget)}><span aria-hidden="true" /><b>{compactStatus}</b></button>
        </div>
      </header>
      <button className="map-legend-chip panel" type="button" onClick={(event) => openContextPanel("legend", event.currentTarget)} aria-label={`Open ${view === "forecast" ? "smoke" : "AQI"} legend`} aria-expanded={contextPanel === "legend"} aria-controls="context-drawer">{view === "forecast" ? <>{forecastLegend}<span className="legend-scale" aria-hidden="true"><small>Faint</small><small>Moderate</small><small>Dense</small></span></> : <><small>Good</small><span className="aqi-ramp" aria-hidden="true" /><small>Hazardous</small></>}</button>
      {view === "forecast" ? <section className="playback-dock panel" aria-label="forecast timeline and controls"><PlaybackControls view={view} viewReady={viewReady} canPlay={canPlay} reducedMotion={reducedMotion} buffering={buffering} playing={animationActive} speed={playbackSpeed} atStart={forecastPosition <= 0} atEnd={forecastPosition >= forecastSpan} clock={clock} referenceTime={displayNow} liveActive={liveActive} liveMode={context?.mode === "live"} liveAvailable={liveAvailable} horizonHours={horizonHours} spanMs={forecastSpan} initialPositionMs={forecastPosition} entries={forecastEntries} uiStore={playbackUiStore} timelineRef={timelineInputRef} onStep={step} onTogglePlay={togglePlay} onSpeed={setPlaybackSpeed} onGoLive={goLive} onScrubStart={() => { timelineScrubbingRef.current = true; pauseForInteraction(); dismissNonInspectPanel(); leaveLive(); }} onScrubEnd={() => { timelineScrubbingRef.current = false; }} onScrub={(position) => { if (!timelineScrubbingRef.current) { pauseForInteraction(); dismissNonInspectPanel(); leaveLive(); } commitPlaybackPosition(position); }} /></section> : null}
      {contextPanel && drawerContent ? <div id="context-drawer" className={contextPanel === "selection" ? "selection-drawer" : contextPanel === "mobile-actions" ? "mobile-actions-drawer" : undefined}><ContextDrawer title={drawerTitle} eyebrow={drawerEyebrow} onClose={closeContextPanel}>{drawerContent}</ContextDrawer></div> : null}
    </div>
    {!viewReady && !publicError ? <div className="loading-state" role="status" data-testid="loading-state"><span aria-hidden="true" />{context ? `Preparing ${view} view…` : "Loading trusted data sources…"}</div> : null}
    {displayError ? <div className="error-state panel" role="alert" data-testid="error-state"><strong>This view couldn’t load.</strong><span>{networkOnline ? userFacingLoadError(displayError) : "TitanSkies needs a network connection to load current forecasts and observations."}</span><button type="button" onClick={() => { setAssetError(null); setImageError(null); setDetailImageError(null); clearIncidentError(); setSceneError(null); setSceneReady(false); retryContext(); }}>Retry</button></div> : null}
    {warningMessages.length ? <div className="stale-banner" role={contextHealth?.status === "unhealthy" ? "alert" : "status"} data-testid="source-warning">{warningMessages.join(" · ")} {publicationWarning ? <button className="text-button" type="button" onClick={retryHealth}>Check again</button> : visibleLayerError && viewReady ? <button className="text-button" type="button" onClick={() => { setAssetError(null); setImageError(null); setDetailImageError(null); clearIncidentError(); retryContext(); }}>Retry layers</button> : null}</div> : null}
    <dialog ref={methodologyDialog} className="methodology-dialog" aria-labelledby="methodology-title" onCancel={(event) => { event.preventDefault(); methodologyDialog.current?.close(); }} onClose={() => { setMethodologyOpen(false); methodologyReturnFocus.current?.focus(); methodologyReturnFocus.current = null; }}><div className="dialog-heading"><div><span className="eyebrow">About the data</span><h2 id="methodology-title">What the forecast and air-quality views can tell you</h2></div><button className="icon-button quiet" type="button" onClick={() => methodologyDialog.current?.close()} aria-label="Close methodology"><Icon name="close" /></button></div><div className="methodology-content"><p>{PUBLIC_DATA_WARNING}</p><dl><div><dt>Forecast versus observed air quality</dt><dd>NOAA HRRR-Smoke and ECCC FireWork are model forecasts. This publication provides one continuous {horizonHours}-hour outlook. Air-quality markers are preliminary official PM2.5 observations at exact station locations. Neither product attributes pollution to a wildfire.</dd></div><div><dt>How the smoke outlook is built</dt><dd>TitanSkies starts with ECCC FireWork Canadian guidance. Where both models are valid, the numeric formula is FireWork + edgeWeight × max(HRRR − FireWork, 0). The U.S. enhancement fades in over 200 km from HRRR’s edge. Concentrations are combined before colorization. This is not an average or a calibrated ensemble.</dd></div><div><dt>How PM2.5 readings are prepared</dt><dd>AirNow provides provider-reported EPA PM2.5 AQI. TitanSkies calculates EPA NowCast AQI from official B.C. ENV and SINAICA hourly PM2.5 readings so the displayed station markers use a comparable EPA scale. AQHI remains a separate Canadian health-risk scale. Each marker remains an individual observation.</dd></div><div><dt>Map geography</dt><dd>Natural Earth supplies bundled city and boundary data. Landmark coordinates and stable IDs come from Wikidata structured data under CC0. Map generation is pinned and makes no production geocoding requests.</dd></div><div><dt>Reported wildfires</dt><dd>US WFIGS and Canadian CWFIS incidents are agency reports. An incident does not prove the origin of a forecast plume or observed pollution.</dd></div><div><dt>Privacy</dt><dd>TitanSkies is self-hosted, includes no analytics, and sends searches and selected locations nowhere.</dd></div></dl><div className="source-links"><a href="https://www.airnow.gov/" target="_blank" rel="noreferrer">EPA AirNow</a><a href="https://rapidrefresh.noaa.gov/hrrr/HRRRsmoke/" target="_blank" rel="noreferrer">NOAA HRRR-Smoke</a><a href="https://weather.gc.ca/firework/" target="_blank" rel="noreferrer">ECCC FireWork</a><a href="https://www2.gov.bc.ca/gov/content/environment/air-land-water/air/air-quality/current-air-quality-data" target="_blank" rel="noreferrer">B.C. ENV</a><a href="https://sinaica.inecc.gob.mx/" target="_blank" rel="noreferrer">INECC/SINAICA</a><a href="https://weather.gc.ca/airquality/pages/index_e.html" target="_blank" rel="noreferrer">ECCC AQHI</a><a href="https://data-nifc.opendata.arcgis.com/" target="_blank" rel="noreferrer">NIFC WFIGS</a><a href="https://cwfis.cfs.nrcan.gc.ca/" target="_blank" rel="noreferrer">CWFIS</a><a href="https://www.naturalearthdata.com/about/terms-of-use/" target="_blank" rel="noreferrer">Natural Earth terms</a><a href="https://www.wikidata.org/wiki/Wikidata:Copyright" target="_blank" rel="noreferrer">Wikidata CC0</a></div></div></dialog>
  </main>;
}
