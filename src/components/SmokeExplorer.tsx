"use client";

import {
  CONTEXT_BOUNDS,
  contextSourceLabel,
  inForecastBounds,
  type ContextSource,
  type ProductView,
  type SourceState,
} from "@/data/contextSchema";
import { contextPointerUrl, fetchJson } from "@/data/contextClient";
import {
  EPA_AQI_CATEGORIES,
  airObservationTime,
  airSourceStates,
  airViewUnavailable,
  emptyAirMonitorWarning,
  fallbackSourceEntries,
  filterAirMonitors,
  forecastSourceStates,
  newestTime,
  preferredAirSourceState,
  publicationFreshness,
  sourceFreshness,
} from "@/data/airQuality";
import {
  FORECAST_DISPLAY_PALETTE_VERSION,
  LEGACY_FORECAST_DISPLAY_PALETTE_VERSION,
  forecastLegendScale,
  interpolateForecastConcentration,
  sampleDecodedForecastConcentration,
  sampleDecodedForecastMask,
  sampleForecastConcentration,
  sampleForecastMask,
} from "@/data/forecastRaster";
import { inspectMarkerCopy, shouldKeepInspectPanel, type InspectPoint } from "@/data/inspectPoint";
import { locationAirReading } from "@/data/locationSearch";
import { rankAqiCities, rankSmokeCities } from "@/data/topConditions";
import { contextSurfaceReducer, visibleMapLayers, type ContextSurface } from "@/data/explorerUi";
import { PUBLIC_DATA_WARNING, compactFreshnessLabel, userFacingLoadError } from "@/data/interfacePresentation";
import { livePosition } from "@/data/timeline";
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
import { DetailsCard, detailsEyebrow, detailsTitle } from "./explorer/DetailsCard";
import { LayerToggle } from "./explorer/LayerToggle";
import { LocationCard } from "./explorer/LocationCard";
import { PLAYBACK_SPEEDS, playbackSpeedLabel, useExplorerPlaybackSession } from "./explorer/useExplorerPlaybackSession";
import { useExplorerEnvironment } from "./explorer/useExplorerEnvironment";
import { useContextPolling } from "./explorer/useContextPolling";
import { useExplorerAssets } from "./explorer/useExplorerAssets";
import { useImages } from "./explorer/useImages";
import { INITIAL_MAP_SELECTION, useMapSelection } from "./explorer/useMapSelection";

const SmokeScene = dynamic(() => import("./scene/SmokeScene").then((mod) => mod.SmokeScene), { ssr: false });
const EARTH_TEXTURE_URL = "/geo/earth-dark-v2.webp";
const VIEWS: Array<{ id: ProductView; label: string }> = [
  { id: "forecast", label: "Forecast" },
  { id: "air", label: "Air quality" },
];

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
  const [geo, setGeo] = useState<import("@/data/ui").GeoContext | null>(null);
  const [incidentsVisible, setIncidentsVisible] = useState(false);
  const activeLayers = useMemo(() => visibleMapLayers(view, incidentsVisible), [incidentsVisible, view]);
  const [assetError, setAssetError] = useState<string | null>(null);
  const [imageError, setImageError] = useState<string | null>(null);
  const [detailImageError, setDetailImageError] = useState<string | null>(null);
  const [methodologyOpen, setMethodologyOpen] = useState(false);
  const [topConditionsReferenceTime, setTopConditionsReferenceTime] = useState(() => Date.now());
  const [contextPanel, dispatchContextPanel] = useReducer(contextSurfaceReducer, null);
  const contextReturnFocusRef = useRef<HTMLElement | null>(null);
  const [resetSignal, setResetSignal] = useState(0);
  const [sceneReady, setSceneReady] = useState(false);
  const [forecastRasterReady, setForecastRasterReady] = useState(false);
  const [sceneError, setSceneError] = useState<string | null>(null);
  const [geoError, setGeoError] = useState<string | null>(null);
  const [earthError, setEarthError] = useState<string | null>(null);
  const handleSceneReady = useCallback(() => setSceneReady(true), []);
  const topConditionsAirOnlyRef = useRef(false);
  const wasOfflineRef = useRef(false);
  const explorerRef = useRef<HTMLElement>(null);
  const timelineInputRef = useRef<HTMLInputElement>(null);
  const motionReducedRef = useRef(() => {});
  const handleMotionReduced = useCallback(() => motionReducedRef.current(), []);
  const mapStateRef = useRef(INITIAL_MAP_SELECTION);
  const dismissNonInspectPanelRef = useRef(() => {});
  const clearSelectionRef = useRef(() => {});
  const inspectPointRef = useRef<InspectPoint | null>(null);
  const selectedCityPresenceRef = useRef(false);
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
  const topConditionsOpen = contextPanel === "top-conditions";
  const layersOpen = contextPanel === "layers";
  const healthExpectedSources = useMemo<ContextSource[]>(() => view === "air"
    ? ["airnow", "bcair", "sinaica"]
    : ["firework", "hrrr", ...(activeLayers.incidents ? ["wfigs", "cwfis"] as const : [])], [activeLayers.incidents, view]);
  const publicationHandlersRef = useRef({
    same: (_args: { manifest: import("@/data/contextSchema").ContextManifest; previous: import("@/data/contextSchema").ContextManifest | null; now: number; previousNow: number }) => {},
    changed: (_args: { manifest: import("@/data/contextSchema").ContextManifest; previous: import("@/data/contextSchema").ContextManifest | null; reason: "load" | "poll"; now: number; previousNow: number }) => {},
  });
  const handleSamePublication = useCallback((args: Parameters<typeof publicationHandlersRef.current.same>[0]) => {
    publicationHandlersRef.current.same(args);
  }, []);
  const handleChangedPublication = useCallback((args: Parameters<typeof publicationHandlersRef.current.changed>[0]) => {
    publicationHandlersRef.current.changed(args);
  }, []);
  const {
    context,
    contextError,
    contextHealth,
    contextRetry,
    displayNow,
    displayNowRef,
    healthError,
    retryContext,
    retryHealth,
    setDisplayNow,
  } = useContextPolling({
    expectedSources: healthExpectedSources,
    followingLiveRef: { current: true },
    onChangedPublication: handleChangedPublication,
    onSamePublication: handleSamePublication,
  });
  const earthImages = useImages([EARTH_TEXTURE_URL], (error) => setEarthError(error ? "Unable to load the globe texture." : null), contextRetry, true);
  const earthImage = earthImages[EARTH_TEXTURE_URL] ?? null;

  useEffect(() => {
    let cancelled = false;
    setGeo(null);
    fetchJson<import("@/data/ui").GeoContext>("/geo/north-america-v3.json")
      .then((payload) => {
        if (cancelled) return;
        if (payload.version !== 3 || !Array.isArray(payload.cities) || !Array.isArray(payload.mapLabels)) throw new Error("Unsupported geographic context");
        setGeo(payload);
        setGeoError(null);
      })
      .catch(() => { if (!cancelled) setGeoError("Unable to load North American political boundaries."); });
    return () => { cancelled = true; };
  }, [contextRetry]);

  const airDataNeeded = view === "air" || selectedCityPresenceRef.current || topConditionsOpen;
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
  const playback = useExplorerPlaybackSession({
    view,
    mobile,
    reducedMotion,
    documentVisible,
    methodologyOpen,
    context,
    contextRetry,
    displayNow,
    displayNowRef,
    setDisplayNow,
    selectedCity: mapStateRef.current.selectedCity,
    selection: mapStateRef.current.selection,
    inspectPoint: mapStateRef.current.inspectPoint,
    contextPanel,
    topConditionsOpen,
    forecastLayerOn: activeLayers.forecast,
    airLayerOn: activeLayers.air,
    sceneReady,
    forecastRasterReady,
    airMapReady,
    imageError,
    explorerRef,
    timelineInputRef,
    dismissNonInspectPanelRef,
    clearSelectionRef,
    inspectPointRef,
    setImageError,
    setDetailImageError,
  });
  motionReducedRef.current = () => {
    playback.changeSmokeOpacity(1);
    playback.changePlaybackPhase("manual-paused");
  };
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
    liveActive,
    liveAvailable,
    surfaceFrameB,
    surfaceToIndex,
  } = playback;

  const clearLocationRef = useRef(() => {});
  const openContextPanel = useCallback((panel: Exclude<ContextSurface, null>, returnFocus?: HTMLElement | null, options: { pause?: boolean } = {}) => {
    if (options.pause !== false) playback.pauseForInteraction();
    if (panel !== "selection" && contextPanel === "selection") clearLocationRef.current();
    contextReturnFocusRef.current = returnFocus ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null);
    dispatchContextPanel({ type: "open", surface: panel });
  }, [contextPanel, playback]);
  const map = useMapSelection({
    view,
    context,
    liveAvailable,
    forecastEntries,
    forecastFrames,
    forecastPositionRef: playback.forecastPositionRef,
    activeForecast,
    activeDetailGrid,
    legacyForecast: playback.legacyForecast,
    contextSurfaceA: playback.contextSurfaceA,
    contextSurfaceB: playback.contextSurfaceB,
    contextMaskA: playback.contextMaskA,
    contextMaskB: playback.contextMaskB,
    decodedResidents: playback.decodedResidents,
    residentRasters: playback.residentRasters,
    contextPanel,
    contextReturnFocusRef,
    forecastPosition: playback.forecastPosition,
    forecastFromIndex: forecastPlayback.fromIndex,
    forecastToIndex: forecastPlayback.toIndex,
    displayNowRef,
    setDisplayNow,
    commitPlaybackPosition: playback.commitPlaybackPosition,
    pauseForInteraction: playback.pauseForInteraction,
    leaveLive: playback.leaveLive,
    followLive: playback.followLive,
    changeSmokeOpacity: playback.changeSmokeOpacity,
    changePlaybackPhase: playback.changePlaybackPhase,
    openContextPanel,
    playbackUiStore: playback.playbackUiStore,
    selectedCityPresenceRef,
  });
  mapStateRef.current = {
    selection: map.selection,
    selectedCity: map.selectedCity,
    selectedFromRanking: map.selectedFromRanking,
    focusLocation: map.focusLocation,
    inspectPoint: map.inspectPoint,
  };
  inspectPointRef.current = map.inspectPoint;
  clearSelectionRef.current = map.clearSelection;
  clearLocationRef.current = map.clearLocation;
  const { selection, selectedCity, selectedFromRanking, focusLocation, inspectPoint } = map;
  const closeContextPanel = useCallback((restoreFocus = false, origin: ContextDrawerCloseOrigin = "keyboard") => {
    const selectionWasOpen = contextPanel === "selection";
    if (selectionWasOpen) map.clearLocation();
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
  }, [contextPanel, map, mobile, shortLandscape]);
  dismissNonInspectPanelRef.current = () => {
    if (shouldKeepInspectPanel(inspectPointRef.current, contextPanel)) return;
    closeContextPanel();
    if (!inspectPointRef.current) map.clearLocation();
  };
  publicationHandlersRef.current = {
    same: playback.applySamePublication,
    changed: ({ manifest, previous, reason, now, previousNow }) => {
      if (reason === "load") {
        playback.resetPublicationLoad();
        setAssetError(null);
        setImageError(null);
        setDetailImageError(null);
      }
      map.clearLocation();
      dispatchContextPanel({ type: "close-selection" });
      setTopConditionsReferenceTime(manifest.mode === "demo" ? Date.parse(manifest.generatedAt) : now);
      playback.applyChangedPublication({ manifest, previous, reason, now, previousNow });
    },
  };

  useEffect(() => { setAssetError(null); setImageError(null); setDetailImageError(null); clearIncidentError(); }, [clearIncidentError, view]);

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
  const sourceStatusSummary = statusSources.map(([label, state]) => `${label} ${state.status}`).join(", ");
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
  const rankingFromRaster = playback.residentRasters.find((source) => source.frameIndex === forecastPlayback.fromIndex)?.raster ?? null;
  const rankingToRaster = playback.residentRasters.find((source) => source.frameIndex === forecastPlayback.toIndex)?.raster ?? rankingFromRaster;
  const rankingPairReady = playback.legacyForecast
    ? Boolean(playback.contextSurfaceA && (!forecastPlayback.interpolating || (forecastB?.textureUrl && playback.legacyContextImages[forecastB.textureUrl])))
    : Boolean(rankingFromRaster && rankingToRaster);
  const rankingPairDisplayed = view !== "forecast" || !activeLayers.forecast
    || (playback.renderedPair.fromIndex === forecastPlayback.fromIndex && (!forecastPlayback.interpolating || playback.renderedPair.toIndex === forecastPlayback.toIndex));
  const topSmokeRows = topConditionsOpen && geo && rankingPairReady && rankingPairDisplayed ? rankSmokeCities(geo.cities, (city) => {
    if (!inForecastBounds(city.lon, city.lat)) return null;
    if (playback.legacyForecast && playback.contextSurfaceA && playback.contextSurfaceB) {
      const fromSource = playback.contextMaskA ? sampleForecastMask(playback.contextMaskA, city.lon, city.lat) : undefined;
      const toSource = playback.contextMaskB ? sampleForecastMask(playback.contextMaskB, city.lon, city.lat) : undefined;
      const fromConcentration = fromSource !== "none" ? sampleForecastConcentration(playback.contextSurfaceA, city.lon, city.lat, CONTEXT_BOUNDS, activeForecast.paletteVersion) : null;
      const toConcentration = toSource !== "none" ? sampleForecastConcentration(playback.contextSurfaceB, city.lon, city.lat, CONTEXT_BOUNDS, activeForecast.paletteVersion) : null;
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
  const detailImages = playback.detailPlaybackMode === "base-only" ? [] : playback.readyDetailTileIds.flatMap((tileId) => {
    const tileA = forecastA?.detailTiles?.[tileId];
    const tileB = surfaceFrameB?.detailTiles?.[tileId] ?? tileA;
    if (!tileA || !tileB) return [];
    const tileResident = playback.decodedResidents.filter((source) => source.tileId === tileId);
    return [{ tileId, column: tileA.column, row: tileA.row, fromIndex: forecastPlayback.fromIndex, toIndex: surfaceToIndex, resident: tileResident }];
  });
  const viewReady = playback.viewReady;
  const interpolationReady = playback.interpolationReady;
  const unavailable = context && sourceState && (view === "forecast"
    ? (sourceState.status === "error" || sourceState.status === "unavailable") && forecastFrames.length === 0
    : airViewUnavailable(context) && visibleMonitors.length === 0)
    ? (view === "air" ? airSources.find(([, state]) => state.error)?.[1].error : sourceState.error) ?? `${view} data is currently unavailable.` : null;
  const layerError = assetError ?? imageError ?? detailImageError;
  const visibleLayerError = layerError ?? (view === "air" ? airError : null) ?? (activeLayers.incidents ? incidentError : null);
  const publicError = contextError ?? unavailable ?? (!viewReady ? (view === "air" ? airError : layerError) : null);
  const criticalSceneError = geoError ?? earthError ?? sceneError;
  const displayError = publicError ?? criticalSceneError;
  useEffect(() => {
    if (!networkOnline) {
      wasOfflineRef.current = true;
      return;
    }
    if (!wasOfflineRef.current || !(publicError || criticalSceneError)) return;
    wasOfflineRef.current = false;
    retryContext();
  }, [criticalSceneError, networkOnline, publicError, retryContext]);
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

  const updateIncidents = (value: boolean) => {
    playback.pauseForInteraction();
    setIncidentsVisible(value);
  };
  const toggleLayers = (trigger?: HTMLElement | null) => {
    playback.pauseForInteraction();
    if (layersOpen) closeContextPanel(true);
    else {
      map.clearLocation();
      openContextPanel("layers", trigger);
    }
  };
  const openTopConditions = (trigger?: HTMLElement | null) => {
    playback.pauseForInteraction();
    map.clearLocation();
    const referenceTime = context?.mode === "demo" ? Date.parse(context.generatedAt) : Date.now();
    topConditionsAirOnlyRef.current = view === "air";
    setTopConditionsReferenceTime(referenceTime);
    if (view === "air") {
      displayNowRef.current = referenceTime;
      setDisplayNow(referenceTime);
      playback.commitPlaybackPosition(context?.mode === "demo" ? 0 : livePosition(forecastEntries, referenceTime).positionMs);
    }
    openContextPanel("top-conditions", trigger);
  };
  const switchView = (next: ProductView) => { playback.pauseForInteraction(); closeContextPanel(); map.clearLocation(); setView(next); };
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
    playback.pauseForInteraction();
    closeContextPanel();
    setMethodologyOpen(true);
    methodologyDialog.current?.showModal();
  };
  const openMethodology = (event: React.MouseEvent<HTMLButtonElement>) => showMethodology(event.currentTarget);
  const openMethodologyFromCurrent = () => showMethodology();
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
    playback.pauseForInteraction();
    closeContextPanel();
    map.clearLocation();
    setResetSignal((value) => value + 1);
  };
  const installAction = !standalone ? <button type="button" onClick={() => { void requestInstall(); }} data-testid="install-action"><Icon name="install" /><span><strong>Install TitanSkies</strong><small>{installPrompt ? "Install on this device" : ios ? "Add from Safari’s Share menu" : "Use your browser’s install menu"}</small></span></button> : null;
  const drawerContent = contextPanel === "top-conditions" ? <TopConditions view={view} cityCount={geo?.cities.length ?? 0} smokeRows={topSmokeRows} smokeLoading={Boolean(forecastFrames.length && !imageError && (!rankingPairReady || !rankingPairDisplayed))} smokeError={!forecastFrames.length ? "Modeled smoke is unavailable." : imageError} smokeWarning={activeForecast.integratedStatus === "retained" ? "Showing the last complete smoke outlook while a newer update is unavailable." : selectedSmokeWarning} smokeValidTime={forecastPlayback.observationTime} concentrationMax={activeForecast.paletteVersion === FORECAST_DISPLAY_PALETTE_VERSION ? 1000 : 250} aqiRows={topAqiRows} aqiLoading={Boolean(!airReady && !airError)} aqiError={!airReady ? airError : null} aqiWarning={airReady ? airError : null} referenceTime={context?.mode === "demo" ? Date.parse(context.generatedAt) : topConditionsReferenceTime} onSelect={(city) => map.selectCity(city, true)} onMethodology={openMethodologyFromCurrent} />
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
            : <button type="button" onClick={playback.cyclePlaybackSpeed} data-testid="mobile-playback-speed"><Icon name="play" /><span><strong>Playback speed</strong><small>{playbackSpeedLabel(playback.playbackSpeed)} · tap for {playbackSpeedLabel(PLAYBACK_SPEEDS[(PLAYBACK_SPEEDS.indexOf(playback.playbackSpeed) + 1) % PLAYBACK_SPEEDS.length])}</small></span></button> : null}
          <button type="button" onClick={openMethodology}><Icon name="info" /><span><strong>About & sources</strong><small>Methods, limitations, and providers</small></span></button>
        </div>
          : contextPanel === "install" ? <div className="install-instructions" data-testid="install-instructions"><p>{ios ? "Open TitanSkies in Safari, tap Share, then choose Add to Home Screen and tap Add." : "Open your browser menu and choose Install app or Add to Home Screen."}</p><small>TitanSkies still needs a network connection for forecasts and observations.</small></div>
            : contextPanel === "selection" ? selectedCity ? <LocationCard city={selectedCity} forecast={selectedForecast} smokeLoading={Boolean(forecastFrames.length && !selectedForecast && inForecastBounds(selectedCity.lon, selectedCity.lat))} smokeWarning={selectedSmokeWarning} airReading={selectedAirReading} airLoading={!airReady && !airError} airError={airError} airWarning={selectedAirWarning} referenceTime={selectedReferenceTime} compact={mobile || shortLandscape} onRetryAir={retryAir} onMethodology={openMethodologyFromCurrent} />
              : <DetailsCard selection={selection} referenceTime={detailsReferenceTime} compact={mobile || shortLandscape} onMonitorSelect={(monitor) => map.setSelection({ kind: "air-monitor", monitor })} onIncidentSelect={(incident) => map.setSelection({ kind: "incident", incident })} onMethodology={openMethodologyFromCurrent} />
              : null;
  const globeHelp = view === "forecast"
    ? `Drag to rotate, scroll or pinch for centered zoom, and click a smoke-outlook area to pin its modeled concentration${activeLayers.incidents ? " or select a reported-wildfire marker for details" : ""}. Zoom becomes more precise near the surface. A drag does not place a pin. Focus the globe, use Plus and Minus to zoom, Arrow keys to move the inspection point, and Enter to sample the forecast. Outside the globe, use Left and Right Arrow to step through forecast time and Space to play or pause.`
    : "Drag to rotate, scroll or pinch for centered zoom, and select an air-quality monitor for details. Zoom becomes more precise near the surface. A drag does not select a monitor. Focus the globe, use Plus and Minus to zoom, Arrow keys to move the keyboard cursor, and Enter to select a nearby monitor.";

  return <main ref={explorerRef} className={`explorer view-${view}${contextPanel ? " context-open" : ""}${shortLandscape ? " short-landscape" : ""}`} aria-busy={!viewReady && !publicError} data-context-generated-at={context?.generatedAt} data-playback-phase={playback.playbackPhase} data-detail-playback-mode={playback.detailPlaybackMode} data-interpolation-ready={interpolationReady ? "true" : "false"} data-render-mix={playback.contextMixRef.current.toFixed(6)} data-smoke-opacity={playback.smokeOpacityRef.current.toFixed(3)} data-resident-frame-count={playback.resident.length} data-raster-resident-count={playback.decodedResidents.length} data-raster-peak-frame-window={playback.rasterCache.metrics().peakFrameIndicesPerSurface} data-temporal-shader-samples={TEMPORAL_SHADER_SAMPLE_COUNT}>
    <h1 className="sr-only">North America smoke and air-quality explorer</h1>
    <p id="globe-help" className="sr-only">{globeHelp}</p>
    <div id="view-panel" className="scene" role="tabpanel" aria-labelledby={`view-tab-${view}`} aria-describedby="globe-help">
      <SmokeScene layers={activeLayers} geo={geo} earthImage={earthImage} mobile={mobile} compactLabels={shortLandscape} monitors={visibleMonitors} selectedMonitorIds={selectedAirReading ? [selectedAirReading.monitor.id] : selection?.kind === "air-monitor" ? [selection.monitor.id] : selection?.kind === "air-cluster" ? selection.monitors.map((monitor) => monitor.id) : []} incidents={incidents} selectedIncidentIds={selection?.kind === "incident" ? [selection.incident.id] : selection?.kind === "incident-cluster" ? selection.incidents.map((incident) => incident.id) : []} contextImageA={playback.contextSurfaceA} contextImageB={playback.contextSurfaceB} legacyForecast={playback.legacyForecast} residentRasters={playback.residentRasters} detailImages={detailImages} detailTileIds={playback.detailTileIds} detailGrid={activeDetailGrid} renderedFromIndex={forecastPlayback.fromIndex} renderedToIndex={surfaceToIndex} contextMixRef={playback.contextMixRef} smokeOpacityRef={playback.smokeOpacityRef} renderRequestRef={playback.sceneRenderRequestRef} renderMix={playback.contextMixRef.current} renderOpacity={playback.smokeOpacityRef.current} playing={playback.animationActive} detailPlaybackMode={playback.detailPlaybackMode} reducedMotion={reducedMotion} resetSignal={resetSignal} focusLocation={focusLocation} inspectLocation={inspectPoint} inspectLabel={inspectLabel} onReady={handleSceneReady} onLayerReady={setForecastRasterReady} onDetailsHidden={playback.finishDetailPreparation} onPairCommitted={playback.handlePairCommitted} onDetailTiles={playback.handleDetailTiles} sceneRetryKey={contextRetry} onSceneError={() => setSceneError("Unable to load the globe texture.")} onSelect={(next) => { if (!next) { closeContextPanel(); return; } map.selectMarker(next); playback.pauseForInteraction(); if (view === "forecast") playback.leaveLive(); openContextPanel("selection", document.querySelector<HTMLElement>("[data-map-keyboard]")); }} onMapClick={map.selectMap} />
    </div>
    <div className="explorer-chrome">
      <header className="top-toolbar">
        <div className="toolbar-cluster toolbar-primary panel">
          <div className="brand-lockup" aria-label="TitanSkies Smoke Forecast"><div className="brand-mark" aria-hidden="true"><Image src="/brand/logo-icon.png" alt="" width={28} height={28} priority unoptimized /></div><span className="brand-copy"><strong>TitanSkies</strong><small>Smoke Forecast</small></span></div>
          <nav className="view-tabs" role="tablist" aria-label="Data view" data-active-view={view}>{VIEWS.map((item) => { const disabled = viewUnavailable(item.id); return <button key={item.id} id={`view-tab-${item.id}`} role="tab" aria-selected={view === item.id} aria-controls="view-panel" tabIndex={!disabled && (view === item.id || activeViewUnavailable) ? 0 : -1} disabled={disabled} onKeyDown={moveViewFocus} onClick={() => switchView(item.id)} data-testid={`view-${item.id}`}>{item.label}{disabled ? <small>Unavailable</small> : null}</button>; })}</nav>
        </div>
        <div className="toolbar-cluster toolbar-search panel"><div className="location-tools"><LocationSearch cities={geo?.cities ?? []} compact={shortLandscape} disabled={!geo || !context} onOpen={() => closeContextPanel()} onSelect={(city, returnFocus) => map.selectCity(city, false, returnFocus)} /></div></div>
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
      {view === "forecast" ? <section className="playback-dock panel" aria-label="forecast timeline and controls"><PlaybackControls view={view} viewReady={viewReady} canPlay={playback.canPlay} reducedMotion={reducedMotion} buffering={playback.buffering} playing={playback.animationActive} speed={playback.playbackSpeed} atStart={playback.forecastPosition <= 0} atEnd={playback.forecastPosition >= forecastSpan} clock={clock} referenceTime={displayNow} liveActive={liveActive} liveMode={context?.mode === "live"} liveAvailable={liveAvailable} horizonHours={horizonHours} spanMs={forecastSpan} initialPositionMs={playback.forecastPosition} entries={forecastEntries} uiStore={playback.playbackUiStore} timelineRef={timelineInputRef} onStep={playback.step} onTogglePlay={playback.togglePlay} onSpeed={playback.setPlaybackSpeed} onGoLive={playback.goLive} onScrubStart={() => { playback.timelineScrubbingRef.current = true; playback.pauseForInteraction(); dismissNonInspectPanelRef.current(); playback.leaveLive(); }} onScrubEnd={() => { playback.timelineScrubbingRef.current = false; }} onScrub={(position) => { if (!playback.timelineScrubbingRef.current) { playback.pauseForInteraction(); dismissNonInspectPanelRef.current(); playback.leaveLive(); } playback.commitPlaybackPosition(position); }} /></section> : null}
      {contextPanel && drawerContent ? <div id="context-drawer" className={contextPanel === "selection" ? "selection-drawer" : contextPanel === "mobile-actions" ? "mobile-actions-drawer" : undefined}><ContextDrawer title={drawerTitle} eyebrow={drawerEyebrow} onClose={closeContextPanel}>{drawerContent}</ContextDrawer></div> : null}
    </div>
    {!viewReady && !publicError ? <div className="loading-state" role="status" data-testid="loading-state"><span aria-hidden="true" />{context ? `Preparing ${view} view…` : "Loading trusted data sources…"}</div> : null}
    {displayError ? <div className="error-state panel" role="alert" data-testid="error-state"><strong>This view couldn’t load.</strong><span>{networkOnline ? userFacingLoadError(displayError) : "TitanSkies needs a network connection to load current forecasts and observations."}</span><button type="button" onClick={() => { setAssetError(null); setImageError(null); setDetailImageError(null); clearIncidentError(); setSceneError(null); setGeoError(null); setEarthError(null); setSceneReady(false); retryContext(); }}>Retry</button></div> : null}
    {warningMessages.length ? <div className="stale-banner" role={contextHealth?.status === "unhealthy" ? "alert" : "status"} data-testid="source-warning">{warningMessages.join(" · ")} {publicationWarning ? <button className="text-button" type="button" onClick={retryHealth}>Check again</button> : visibleLayerError && viewReady ? <button className="text-button" type="button" onClick={() => { setAssetError(null); setImageError(null); setDetailImageError(null); clearIncidentError(); retryContext(); }}>Retry layers</button> : null}</div> : null}
    <dialog ref={methodologyDialog} className="methodology-dialog" aria-labelledby="methodology-title" onCancel={(event) => { event.preventDefault(); methodologyDialog.current?.close(); }} onClose={() => { setMethodologyOpen(false); methodologyReturnFocus.current?.focus(); methodologyReturnFocus.current = null; }}><div className="dialog-heading"><div><span className="eyebrow">About the data</span><h2 id="methodology-title">What the forecast and air-quality views can tell you</h2></div><button className="icon-button quiet" type="button" onClick={() => methodologyDialog.current?.close()} aria-label="Close methodology"><Icon name="close" /></button></div><div className="methodology-content"><p>{PUBLIC_DATA_WARNING}</p><dl><div><dt>Forecast versus observed air quality</dt><dd>NOAA HRRR-Smoke and ECCC FireWork are model forecasts. This publication provides one continuous {horizonHours}-hour outlook. Air-quality markers are preliminary official PM2.5 observations at exact station locations. Neither product attributes pollution to a wildfire.</dd></div><div><dt>How the smoke outlook is built</dt><dd>TitanSkies starts with ECCC FireWork Canadian guidance. Where both models are valid, the numeric formula is FireWork + edgeWeight × max(HRRR − FireWork, 0). The U.S. enhancement fades in over 200 km from HRRR’s edge. Concentrations are combined before colorization. This is not an average or a calibrated ensemble.</dd></div><div><dt>How PM2.5 readings are prepared</dt><dd>AirNow provides provider-reported EPA PM2.5 AQI. TitanSkies calculates EPA NowCast AQI from official B.C. ENV and SINAICA hourly PM2.5 readings so the displayed station markers use a comparable EPA scale. AQHI remains a separate Canadian health-risk scale. Each marker remains an individual observation.</dd></div><div><dt>Map geography</dt><dd>Natural Earth supplies bundled city and boundary data. Landmark coordinates and stable IDs come from Wikidata structured data under CC0. Map generation is pinned and makes no production geocoding requests.</dd></div><div><dt>Reported wildfires</dt><dd>US WFIGS and Canadian CWFIS incidents are agency reports. An incident does not prove the origin of a forecast plume or observed pollution.</dd></div><div><dt>Privacy</dt><dd>TitanSkies is self-hosted, includes no analytics, and sends searches and selected locations nowhere.</dd></div></dl><div className="source-links"><a href="https://www.airnow.gov/" target="_blank" rel="noreferrer">EPA AirNow</a><a href="https://rapidrefresh.noaa.gov/hrrr/HRRRsmoke/" target="_blank" rel="noreferrer">NOAA HRRR-Smoke</a><a href="https://weather.gc.ca/firework/" target="_blank" rel="noreferrer">ECCC FireWork</a><a href="https://www2.gov.bc.ca/gov/content/environment/air-land-water/air/air-quality/current-air-quality-data" target="_blank" rel="noreferrer">B.C. ENV</a><a href="https://sinaica.inecc.gob.mx/" target="_blank" rel="noreferrer">INECC/SINAICA</a><a href="https://weather.gc.ca/airquality/pages/index_e.html" target="_blank" rel="noreferrer">ECCC AQHI</a><a href="https://data-nifc.opendata.arcgis.com/" target="_blank" rel="noreferrer">NIFC WFIGS</a><a href="https://cwfis.cfs.nrcan.gc.ca/" target="_blank" rel="noreferrer">CWFIS</a><a href="https://www.naturalearthdata.com/about/terms-of-use/" target="_blank" rel="noreferrer">Natural Earth terms</a><a href="https://www.wikidata.org/wiki/Wikidata:Copyright" target="_blank" rel="noreferrer">Wikidata CC0</a></div></div></dialog>
  </main>;
}
