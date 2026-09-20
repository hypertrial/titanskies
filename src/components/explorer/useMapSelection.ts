"use client";

import {
  CONTEXT_BOUNDS,
  detailTileBounds,
  detailTileId,
  forecastFrameRun,
  inForecastBounds,
  type ContextManifest,
  type DetailGridShape,
  type ForecastFrame,
  type ForecastRun,
  type ProductView,
} from "@/data/contextSchema";
import { newestTime } from "@/data/airQuality";
import {
  FORECAST_DISPLAY_PALETTE_VERSION,
  LEGACY_FORECAST_DISPLAY_PALETTE_VERSION,
  interpolateForecastConcentration,
  sampleDecodedForecastConcentration,
  sampleDecodedForecastMask,
  sampleForecastConcentration,
  sampleForecastMask,
} from "@/data/forecastRaster";
import { reduceInspectPoint, type InspectPoint } from "@/data/inspectPoint";
import { cityIdentity } from "@/data/locationSearch";
import { livePosition, playbackAt } from "@/data/timeline";
import type { CityLabel, MapSelection } from "@/data/ui";
import type { ContextSurface } from "@/data/explorerUi";
import type { ResidentForecastRaster } from "@/rendering/forecastRasterCache";
import { useCallback, useEffect, useReducer, useRef, type Dispatch, type MutableRefObject, type SetStateAction } from "react";
import type { ImageSource } from "./useImages";

export type MapSelectionState = {
  selection: MapSelection | null;
  selectedCity: CityLabel | null;
  selectedFromRanking: boolean;
  focusLocation: { lon: number; lat: number; requestId: number } | null;
  inspectPoint: InspectPoint | null;
};

export const INITIAL_MAP_SELECTION: MapSelectionState = {
  selection: null,
  selectedCity: null,
  selectedFromRanking: false,
  focusLocation: null,
  inspectPoint: null,
};

export type MapSelectionEvent =
  | { type: "clear-selection" }
  | { type: "clear-location" }
  | { type: "set-selection"; selection: MapSelection | null }
  | { type: "select-map"; lon: number; lat: number }
  | { type: "select-city"; city: CityLabel; preserveForecastTime: boolean; requestId: number }
  | { type: "select-marker"; selection: MapSelection | null };

export function reduceMapSelection(state: MapSelectionState, event: MapSelectionEvent): MapSelectionState {
  switch (event.type) {
    case "clear-selection":
      return state.selection === null ? state : { ...state, selection: null };
    case "clear-location":
      return state.selection === null && state.selectedCity === null && !state.selectedFromRanking && state.focusLocation === null && state.inspectPoint === null
        ? state
        : INITIAL_MAP_SELECTION;
    case "set-selection":
      return state.selection === event.selection ? state : { ...state, selection: event.selection };
    case "select-map":
      return {
        ...state,
        selectedCity: null,
        selectedFromRanking: false,
        focusLocation: null,
        inspectPoint: reduceInspectPoint({ type: "set", lon: event.lon, lat: event.lat }),
      };
    case "select-city":
      return {
        selection: null,
        selectedCity: event.city,
        selectedFromRanking: event.preserveForecastTime,
        focusLocation: { lon: event.city.lon, lat: event.city.lat, requestId: event.requestId },
        inspectPoint: null,
      };
    case "select-marker":
      return {
        selection: event.selection,
        selectedCity: null,
        selectedFromRanking: false,
        focusLocation: null,
        inspectPoint: null,
      };
  }
}

export function useMapSelection({
  view,
  context,
  liveAvailable,
  forecastEntries,
  forecastFrames,
  forecastPositionRef,
  activeForecast,
  activeDetailGrid,
  legacyForecast,
  contextSurfaceA,
  contextSurfaceB,
  contextMaskA,
  contextMaskB,
  decodedResidents,
  residentRasters,
  contextPanel,
  contextReturnFocusRef,
  forecastPosition,
  forecastFromIndex,
  forecastToIndex,
  displayNowRef,
  setDisplayNow,
  commitPlaybackPosition,
  pauseForInteraction,
  leaveLive,
  followLive,
  changeSmokeOpacity,
  changePlaybackPhase,
  openContextPanel,
  playbackUiStore,
  selectedCityPresenceRef,
}: {
  view: ProductView;
  context: ContextManifest | null;
  liveAvailable: boolean;
  forecastEntries: Array<{ scanId: string; observationStart: string; manifestUrl: string }>;
  forecastFrames: ForecastFrame[];
  forecastPositionRef: MutableRefObject<number>;
  activeForecast: ForecastRun;
  activeDetailGrid: DetailGridShape;
  legacyForecast: boolean;
  contextSurfaceA: ImageSource | null;
  contextSurfaceB: ImageSource | null;
  contextMaskA: ImageSource | null;
  contextMaskB: ImageSource | null;
  decodedResidents: ResidentForecastRaster[];
  residentRasters: ResidentForecastRaster[];
  contextPanel: ContextSurface;
  contextReturnFocusRef: MutableRefObject<HTMLElement | null>;
  forecastPosition: number;
  forecastFromIndex: number;
  forecastToIndex: number;
  displayNowRef: MutableRefObject<number>;
  setDisplayNow: Dispatch<SetStateAction<number>>;
  commitPlaybackPosition: (next: number) => void;
  pauseForInteraction: () => void;
  leaveLive: () => void;
  followLive: () => void;
  changeSmokeOpacity: (next: number) => void;
  changePlaybackPhase: (phase: "manual-paused" | "interaction-paused" | "playing" | "waiting" | "buffering" | "preparing-detail" | "fading-out" | "resetting" | "fading-in") => void;
  openContextPanel: (panel: Exclude<ContextSurface, null>, returnFocus?: HTMLElement | null, options?: { pause?: boolean }) => void;
  playbackUiStore: { subscribe: (listener: () => void) => () => void };
  selectedCityPresenceRef: MutableRefObject<boolean>;
}) {
  const [state, dispatch] = useReducer(reduceMapSelection, INITIAL_MAP_SELECTION);
  const { selection, selectedCity, selectedFromRanking, focusLocation, inspectPoint } = state;
  const selectionRequestRef = useRef(0);
  const pendingSelectionRef = useRef<{ lon: number; lat: number } | null>(null);
  const inspectPointRef = useRef<InspectPoint | null>(null);
  const sampleMapRef = useRef<(point: { lon: number; lat: number }, options?: { preserveLive?: boolean; allowInAir?: boolean; returnFocus?: HTMLElement | null; pause?: boolean; preserveSelection?: boolean }) => void>(() => {});
  inspectPointRef.current = inspectPoint;
  selectedCityPresenceRef.current = selectedCity !== null;

  const clearSelection = useCallback(() => {
    selectionRequestRef.current += 1;
    pendingSelectionRef.current = null;
    dispatch({ type: "clear-selection" });
  }, []);
  const clearLocation = useCallback(() => {
    selectedCityPresenceRef.current = false;
    clearSelection();
    dispatch({ type: "clear-location" });
  }, [clearSelection, selectedCityPresenceRef]);
  const setSelection = useCallback((next: MapSelection | null) => {
    dispatch({ type: "set-selection", selection: next });
  }, []);

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
    if (!options.preserveSelection) dispatch({ type: "set-selection", selection: null });
    const fromFrame = forecastFrames[currentPlayback.fromIndex] ?? null;
    const toFrame = forecastFrames[currentPlayback.toIndex] ?? fromFrame;
    const modelRun = newestTime([fromFrame?.modelRun, toFrame?.modelRun, activeForecast.modelRun]);
    const payload = { kind: "forecast" as const, lon, lat, modelRun, validTime: currentPlayback.observationTime, horizonHours: activeForecast.horizonHours, concentrationMax: activeForecast.paletteVersion === FORECAST_DISPLAY_PALETTE_VERSION ? 1000 : 250 };
    if (!inForecastBounds(lon, lat)) {
      pendingSelectionRef.current = null;
      dispatch({ type: "set-selection", selection: { ...payload, source: "none" } });
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
      dispatch({ type: "set-selection", selection: { ...payload, source, concentration } });
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
        if (requestId === selectionRequestRef.current) dispatch({ type: "set-selection", selection: { ...payload, source, concentration, modelRun: selectedRun, interpolation } });
        pendingSelectionRef.current = null;
      } else {
        pendingSelectionRef.current = { lon, lat };
        if (requestId === selectionRequestRef.current) dispatch({ type: "set-selection", selection: null });
      }
    }
  };

  const selectMap = (payload: { lon: number; lat: number }) => {
    if (view === "air") return;
    dispatch({ type: "select-map", lon: payload.lon, lat: payload.lat });
    sampleMap(payload, { pause: false, preserveLive: true });
  };
  const selectCity = (city: CityLabel, preserveForecastTime = false, returnFocus?: HTMLElement | null) => {
    pauseForInteraction();
    selectionRequestRef.current += 1;
    pendingSelectionRef.current = null;
    selectedCityPresenceRef.current = true;
    const requestId = selectionRequestRef.current;
    dispatch({ type: "select-city", city, preserveForecastTime, requestId });
    if (!preserveForecastTime && context?.mode === "live" && liveAvailable) {
      const now = Date.now();
      followLive();
      displayNowRef.current = now;
      setDisplayNow(now);
      commitPlaybackPosition(livePosition(forecastEntries, now).positionMs);
    } else if (!preserveForecastTime && context?.mode === "demo") {
      leaveLive();
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
    const required = new Set([forecastFromIndex, forecastToIndex]);
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

  const selectMarker = (next: MapSelection | null) => {
    selectionRequestRef.current += 1;
    dispatch({ type: "select-marker", selection: next });
  };

  return {
    selection,
    selectedCity,
    selectedFromRanking,
    focusLocation,
    inspectPoint,
    inspectPointRef,
    selectionRequestRef,
    clearSelection,
    clearLocation,
    setSelection,
    sampleMap,
    selectMap,
    selectCity,
    selectMarker,
  };
}
