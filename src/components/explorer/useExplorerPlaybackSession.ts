"use client";

import {
  CONTEXT_DETAIL_RASTER,
  CONTEXT_RASTER,
  uiForecastHorizonHours,
  visibleForecastRun,
  type ContextManifest,
  type ForecastFrame,
  type ProductView,
} from "@/data/contextSchema";
import { playbackPositionAfterContextRefresh } from "@/data/contextClient";
import { LEGACY_FORECAST_DISPLAY_PALETTE_VERSION } from "@/data/forecastRaster";
import { airViewReady } from "@/data/airQuality";
import type { InspectPoint } from "@/data/inspectPoint";
import { shouldIgnoreExplorerPlaybackKeys, type ContextSurface } from "@/data/explorerUi";
import { livePosition, playbackAt, steppedPosition, timelineEntry, type PlaybackSpeed } from "@/data/timeline";
import type { TimelineEntry } from "@/data/contracts";
import type { CityLabel, MapSelection } from "@/data/ui";
import {
  PlaybackUiStore,
  playbackIsActive,
  playbackMixForRenderedPair,
  playbackPair,
  playbackPhaseReducer,
  syncTimelineElement,
  useForecastPlaybackClock,
  type PlaybackPhase,
} from "@/playback/forecastPlayback";
import { forecastRasterCacheKey, useForecastRasterCache, type ForecastRasterSource } from "@/rendering/forecastRasterCache";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState, type Dispatch, type MutableRefObject, type RefObject, type SetStateAction } from "react";
import { useForecastPlaybackModel } from "./useForecastPlaybackModel";
import { useImages } from "./useImages";

const LOOP_FADE_MS = 350;
export const PLAYBACK_SPEEDS: PlaybackSpeed[] = [0.25, 1, 3];
export const playbackSpeedLabel = (speed: PlaybackSpeed) => speed === 0.25 ? "¼×" : `${speed}×`;

export function adjacentPairFor(entries: TimelineEntry[], position: number): [number, number] {
  const state = playbackAt(entries, position);
  return [state.fromIndex, state.interpolating ? state.toIndex : Math.min(entries.length - 1, state.fromIndex + 1)];
}

export function detailPlaybackModeFor(
  phase: PlaybackPhase,
  animationActive: boolean,
  atEndpoint: boolean,
): "fading-out" | "base-only" | "visible" {
  return phase === "preparing-detail" ? "fading-out"
    : animationActive || (phase === "waiting" && atEndpoint) ? "base-only"
      : "visible";
}

export type PlaybackSessionState = {
  position: number;
  phase: PlaybackPhase;
  speed: PlaybackSpeed;
  followingLive: boolean;
  renderedPair: { fromIndex: number; toIndex: number };
  detailTileIds: number[];
  detailFadeOutKeys: string[];
};

export const INITIAL_PLAYBACK_SESSION: PlaybackSessionState = {
  position: 0,
  phase: "manual-paused",
  speed: 1,
  followingLive: true,
  renderedPair: { fromIndex: 0, toIndex: 0 },
  detailTileIds: [],
  detailFadeOutKeys: [],
};

export type PlaybackSessionEvent =
  | { type: "set-position"; position: number }
  | { type: "set-phase"; phase: PlaybackPhase }
  | { type: "set-speed"; speed: PlaybackSpeed }
  | { type: "leave-live" }
  | { type: "go-live" }
  | { type: "play"; canPlay: boolean; ready: boolean; atEndpoint: boolean; detailVisible: boolean; readyDetailFadeKeys: string[] }
  | { type: "pause-manual" }
  | { type: "interaction-pause" }
  | { type: "detail-hidden"; ready: boolean }
  | { type: "ready" }
  | { type: "commit-pair"; fromIndex: number; toIndex: number }
  | { type: "set-detail-tiles"; tiles: number[] }
  | { type: "set-detail-fade-keys"; keys: string[] }
  | { type: "cycle-speed" };

export function reducePlaybackSession(state: PlaybackSessionState, event: PlaybackSessionEvent): PlaybackSessionState {
  switch (event.type) {
    case "set-position":
      return state.position === event.position ? state : { ...state, position: event.position };
    case "set-phase":
      return state.phase === event.phase ? state : { ...state, phase: event.phase };
    case "set-speed":
      return state.speed === event.speed ? state : { ...state, speed: event.speed };
    case "leave-live":
      return state.followingLive ? { ...state, followingLive: false } : state;
    case "go-live":
      return { ...state, followingLive: true, detailFadeOutKeys: [], phase: "manual-paused" };
    case "play": {
      if (!event.canPlay) return state;
      if (playbackIsActive(state.phase)) {
        return { ...state, detailFadeOutKeys: [], phase: "manual-paused" };
      }
      if (event.atEndpoint) return { ...state, followingLive: false, phase: "waiting" };
      const phase = playbackPhaseReducer(state.phase, {
        type: "play",
        canPlay: event.canPlay,
        ready: event.ready,
        atEndpoint: false,
        detailVisible: event.detailVisible,
      });
      return {
        ...state,
        followingLive: false,
        detailFadeOutKeys: event.detailVisible ? event.readyDetailFadeKeys : [],
        phase,
      };
    }
    case "pause-manual":
      return { ...state, detailFadeOutKeys: [], phase: playbackPhaseReducer(state.phase, { type: "pause" }) };
    case "interaction-pause":
      if (!playbackIsActive(state.phase)) return state;
      return { ...state, detailFadeOutKeys: [], phase: "interaction-paused" };
    case "detail-hidden":
      return {
        ...state,
        detailFadeOutKeys: [],
        phase: playbackPhaseReducer(state.phase, { type: "detail-hidden", ready: event.ready }),
      };
    case "ready":
      return { ...state, phase: playbackPhaseReducer(state.phase, { type: "ready" }) };
    case "commit-pair":
      if (state.renderedPair.fromIndex === event.fromIndex && state.renderedPair.toIndex === event.toIndex) return state;
      return { ...state, renderedPair: { fromIndex: event.fromIndex, toIndex: event.toIndex } };
    case "set-detail-tiles":
      if (state.detailTileIds.length === event.tiles.length && state.detailTileIds.every((value, index) => value === event.tiles[index])) return state;
      return { ...state, detailTileIds: event.tiles };
    case "set-detail-fade-keys":
      return { ...state, detailFadeOutKeys: event.keys };
    case "cycle-speed":
      return { ...state, speed: PLAYBACK_SPEEDS[(PLAYBACK_SPEEDS.indexOf(state.speed) + 1) % PLAYBACK_SPEEDS.length] };
  }
}

export function liveResetTarget(input: {
  mode: ContextManifest["mode"] | undefined;
  forecastEntries: TimelineEntry[];
  displayNow: number;
  now: number;
  resetFrames: ForecastFrame[];
}): { now: number; position: number; preloadPosition: number } | null {
  if (input.mode !== "live") return null;
  const preloadTarget = livePosition(input.forecastEntries, input.now);
  const resetEntries = input.resetFrames.map(timelineEntry);
  const target = livePosition(resetEntries, input.now);
  const targetState = playbackAt(resetEntries, target.positionMs);
  const targetHasPair = targetState.interpolating || targetState.fromIndex < resetEntries.length - 1;
  if (preloadTarget.available && target.available && targetHasPair) {
    return { now: input.now, position: target.positionMs, preloadPosition: target.positionMs };
  }
  return null;
}

export function useExplorerPlaybackSession({
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
  selectedCity,
  selection,
  inspectPoint,
  contextPanel,
  topConditionsOpen,
  forecastLayerOn,
  airLayerOn,
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
}: {
  view: ProductView;
  mobile: boolean;
  reducedMotion: boolean;
  documentVisible: boolean;
  methodologyOpen: boolean;
  context: ContextManifest | null;
  contextRetry: number;
  displayNow: number;
  displayNowRef: MutableRefObject<number>;
  setDisplayNow: Dispatch<SetStateAction<number>>;
  selectedCity: CityLabel | null;
  selection: MapSelection | null;
  inspectPoint: InspectPoint | null;
  contextPanel: ContextSurface;
  topConditionsOpen: boolean;
  forecastLayerOn: boolean;
  airLayerOn: boolean;
  sceneReady: boolean;
  forecastRasterReady: boolean;
  airMapReady: boolean;
  imageError: string | null;
  explorerRef: RefObject<HTMLElement | null>;
  timelineInputRef: RefObject<HTMLInputElement | null>;
  dismissNonInspectPanelRef: MutableRefObject<() => void>;
  clearSelectionRef: MutableRefObject<() => void>;
  inspectPointRef: MutableRefObject<InspectPoint | null>;
  setImageError: Dispatch<SetStateAction<string | null>>;
  setDetailImageError: Dispatch<SetStateAction<string | null>>;
}) {
  const [forecastPosition, setForecastPosition] = useState(0);
  const [playbackPhase, dispatchPlayback] = useReducer(playbackPhaseReducer, "manual-paused");
  const [playbackSpeed, setPlaybackSpeed] = useState<PlaybackSpeed>(1);
  const [detailTileIds, setDetailTileIds] = useState<number[]>([]);
  const [detailFadeOutKeys, setDetailFadeOutKeys] = useState<string[]>([]);
  const [renderedPair, setRenderedPair] = useState({ fromIndex: 0, toIndex: 0 });
  const [followingLive, setFollowingLive] = useState(true);
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
    resident: baseResident,
    surfaceFrameB,
    surfaceToIndex,
  } = useForecastPlaybackModel(context, displayNow, forecastPosition, followingLive);
  const playingRef = useRef(false);
  const playbackPhaseRef = useRef<PlaybackPhase>("manual-paused");
  const followingLiveRef = useRef(true);
  const forecastPositionRef = useRef(0);
  const forecastEntriesRef = useRef<TimelineEntry[]>([]);
  const timelineScrubbingRef = useRef(false);
  const contextMixRef = useRef(0);
  const smokeOpacityRef = useRef(1);
  const playbackUiStore = useMemo(() => new PlaybackUiStore(0), []);
  const sceneRenderRequestRef = useRef<() => void>(() => {});
  const renderedPairRef = useRef({ fromIndex: 0, toIndex: 0 });
  const requestedPairRef = useRef<{ fromIndex: number; toIndex: number } | null>(null);
  const forecastInitializedRef = useRef(false);
  const forecastRasterInitializedRef = useRef(false);
  const resetTargetRef = useRef<{ now: number; position: number } | null>(null);
  const playing = playbackPhase === "playing";
  const buffering = playbackPhase === "buffering";
  const animationActive = playbackIsActive(playbackPhase);
  playingRef.current = playing;
  playbackPhaseRef.current = playbackPhase;
  followingLiveRef.current = followingLive;
  const changePlaybackPhase = useCallback((next: PlaybackPhase) => {
    playbackPhaseRef.current = next;
    playingRef.current = next === "playing";
    dispatchPlayback({ type: "set", phase: next });
  }, []);
  const changeSmokeOpacity = useCallback((next: number) => {
    smokeOpacityRef.current = next;
    if (explorerRef.current) explorerRef.current.dataset.smokeOpacity = next.toFixed(3);
    sceneRenderRequestRef.current();
  }, [explorerRef]);
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
  }, [explorerRef, playbackUiStore, timelineInputRef]);
  const pauseForInteraction = useCallback(() => {
    if (!playbackIsActive(playbackPhaseRef.current)) return;
    if (playbackPhaseRef.current !== "playing") changeSmokeOpacity(1);
    setDetailFadeOutKeys([]);
    if (!timelineScrubbingRef.current) commitPlaybackPosition(forecastPositionRef.current);
    changePlaybackPhase("interaction-paused");
  }, [changePlaybackPhase, changeSmokeOpacity, commitPlaybackPosition]);
  const handleDetailTiles = useCallback((tiles: number[]) => {
    setDetailTileIds((current) => current.length === tiles.length && current.every((value, index) => value === tiles[index]) ? current : tiles);
  }, []);
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
  }, [explorerRef]);
  const currentResetTarget = () => {
    const now = Date.now();
    const live = liveResetTarget({
      mode: context?.mode,
      forecastEntries,
      displayNow: displayNowRef.current,
      now,
      resetFrames: context ? visibleForecastRun(context, uiForecastHorizonHours(context), now).frames : [],
    });
    if (live) return live;
    return { now: displayNowRef.current, position: 0, preloadPosition: 0 };
  };
  const atEndpoint = forecastSpan > 0 && forecastPosition >= forecastSpan;
  const detailPlaybackMode = detailPlaybackModeFor(playbackPhase, animationActive, atEndpoint);
  const resetTarget = atEndpoint ? currentResetTarget() : { now: displayNowRef.current, position: 0, preloadPosition: 0 };
  const resetForecastFrames = atEndpoint && context?.mode === "live"
    ? visibleForecastRun(context, uiForecastHorizonHours(context), resetTarget.now).frames
    : forecastFrames;
  const resetForecastEntries = resetForecastFrames.map(timelineEntry);
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
  const forecastDataNeeded = forecastLayerOn || selectedCity !== null || topConditionsOpen;
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
    const details = detailPlaybackMode !== "base-only" && (forecastLayerOn || selectedCity !== null) ? detailTileIds.flatMap((tileId): ForecastRasterSource[] => {
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
  }, [setDetailImageError, setImageError]);
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

  useEffect(() => {
    if (!forecastEntries.length) return;
    commitPlaybackPosition(Math.min(forecastPositionRef.current, forecastSpan));
    if (!inspectPointRef.current) clearSelectionRef.current();
  }, [clearSelectionRef, commitPlaybackPosition, forecastSpan, forecastEntries.length, inspectPointRef]);

  const leaveLive = useCallback(() => { followingLiveRef.current = false; setFollowingLive(false); }, []);
  const followLive = useCallback(() => { followingLiveRef.current = true; setFollowingLive(true); }, []);
  const step = useCallback((delta: -1 | 1) => {
    pauseForInteraction();
    leaveLive();
    dismissNonInspectPanelRef.current();
    if (view === "forecast") {
      const next = steppedPosition(forecastEntries, forecastPositionRef.current, delta);
      commitPlaybackPosition(next);
    }
  }, [commitPlaybackPosition, dismissNonInspectPanelRef, forecastEntries, leaveLive, pauseForInteraction, view]);
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
    dismissNonInspectPanelRef.current();
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
  }, [canPlay, changePlaybackPhase, changeSmokeOpacity, commitPlaybackPosition, dismissNonInspectPanelRef, forecastSpan, forwardResident, frameRenderReady, leaveLive, readyDetailFadeKeys]);
  const goLive = useCallback(() => {
    if (!context || context.mode !== "live" || !liveAvailable) return;
    const now = Date.now();
    followingLiveRef.current = true;
    setDetailFadeOutKeys([]);
    changeSmokeOpacity(1);
    changePlaybackPhase("manual-paused");
    setFollowingLive(true);
    dismissNonInspectPanelRef.current();
    displayNowRef.current = now;
    setDisplayNow(now);
    commitPlaybackPosition(livePosition(forecastEntries, now).positionMs);
  }, [changePlaybackPhase, changeSmokeOpacity, commitPlaybackPosition, context, dismissNonInspectPanelRef, displayNowRef, forecastEntries, liveAvailable, setDisplayNow]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (shouldIgnoreExplorerPlaybackKeys(event.target)) return;
      if (event.key === " ") { event.preventDefault(); togglePlay(); }
      if (event.key === "ArrowLeft") step(-1);
      if (event.key === "ArrowRight") step(1);
    };
    window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey);
  }, [step, togglePlay]);

  const inspectPlaybackAllowed = Boolean(inspectPoint) && contextPanel === "selection" && !selectedCity
    && (selection == null || selection.kind === "forecast");
  const contextSurfaceA = legacyForecast && forecastA?.textureUrl ? legacyContextImages[forecastA.textureUrl] ?? null : null;
  const contextMaskA = legacyForecast && forecastA?.sourceMaskUrl ? legacyContextImages[forecastA.sourceMaskUrl] ?? null : null;
  const loadedSurfaceB = legacyForecast && surfaceFrameB?.textureUrl ? legacyContextImages[surfaceFrameB.textureUrl] ?? null : contextSurfaceA;
  const contextSurfaceB = loadedSurfaceB ?? contextSurfaceA;
  const loadedMaskB = legacyForecast && surfaceFrameB?.sourceMaskUrl ? legacyContextImages[surfaceFrameB.sourceMaskUrl] ?? null : contextMaskA;
  const contextMaskB = loadedMaskB ?? contextMaskA;
  const interpolationReady = legacyForecast ? Boolean(contextSurfaceA && contextSurfaceB) : frameRenderReady([forecastPlayback.fromIndex, surfaceToIndex]);
  forecastInitializedRef.current ||= interpolationReady;
  forecastRasterInitializedRef.current ||= forecastRasterReady;
  const forecastReady = !forecastLayerOn || (legacyForecast
    ? Boolean(contextSurfaceA && (interpolationReady || forecastInitializedRef.current))
    : Boolean(frameRenderReady([forecastPlayback.fromIndex]) && (interpolationReady || forecastInitializedRef.current) && (forecastRasterReady || forecastRasterInitializedRef.current)));
  const viewReady = sceneReady && (view === "forecast" ? Boolean(context && forecastReady) : Boolean(context && airViewReady(airLayerOn, airMapReady)));
  const interactionBlocked = reducedMotion || !documentVisible || methodologyOpen
    || (Boolean(contextPanel) && !inspectPlaybackAllowed)
    || Boolean(selectedCity)
    || (Boolean(selection) && !inspectPlaybackAllowed)
    || view !== "forecast" || !forecastLayerOn || !viewReady || Boolean(imageError);

  const resetPairReady = atEndpoint && resetForecastEntries.length > 1 && resetAssetsReady;

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
    if (!forecastLayerOn) forecastRasterInitializedRef.current = false;
  }, [forecastLayerOn]);

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

  const applySamePublication = useCallback((args: {
    manifest: ContextManifest; previous: ContextManifest | null; now: number; previousNow: number;
  }) => {
    if (args.manifest.mode !== "live" || !followingLiveRef.current) return;
    commitPlaybackPosition(playbackPositionAfterContextRefresh({
      previous: args.previous,
      next: args.manifest,
      previousPositionMs: forecastPositionRef.current,
      previousNowMs: args.previousNow,
      nowMs: args.now,
      followingLive: true,
    }));
  }, [commitPlaybackPosition]);
  const resetPublicationLoad = useCallback(() => {
    forecastRasterInitializedRef.current = false;
  }, []);
  const applyChangedPublication = useCallback((args: {
    manifest: ContextManifest; previous: ContextManifest | null; reason: "load" | "poll"; now: number; previousNow: number;
  }) => {
    commitPlaybackPosition(playbackPositionAfterContextRefresh({
      previous: args.reason === "load" ? null : args.previous,
      next: args.manifest,
      previousPositionMs: forecastPositionRef.current,
      previousNowMs: args.previousNow,
      nowMs: args.now,
      followingLive: args.manifest.mode === "live" && followingLiveRef.current,
    }));
  }, [commitPlaybackPosition]);
  const cyclePlaybackSpeed = () => {
    const next = PLAYBACK_SPEEDS[(PLAYBACK_SPEEDS.indexOf(playbackSpeed) + 1) % PLAYBACK_SPEEDS.length];
    setPlaybackSpeed(next);
  };

  return {
    forecastPosition,
    playbackPhase,
    playbackSpeed,
    followingLive,
    followingLiveRef,
    renderedPair,
    detailTileIds,
    detailFadeOutKeys,
    detailPlaybackMode,
    animationActive,
    playing,
    buffering,
    canPlay,
    atEndpoint,
    resident,
    forwardResident,
    legacyForecast,
    legacyContextImages,
    rasterCache,
    decodedResidents,
    residentRasters,
    readyDetailTileIds,
    readyDetailFadeKeys,
    forecastPositionRef,
    forecastEntriesRef,
    contextMixRef,
    smokeOpacityRef,
    sceneRenderRequestRef,
    timelineScrubbingRef,
    playbackUiStore,
    forecastInitializedRef,
    forecastRasterInitializedRef,
    frameRenderReady,
    interpolationReady,
    forecastReady,
    viewReady,
    contextSurfaceA,
    contextSurfaceB,
    contextMaskA,
    contextMaskB,
    resetAssetsReady,
    resetPairReady,
    changePlaybackPhase,
    changeSmokeOpacity,
    commitPlaybackPosition,
    pauseForInteraction,
    handleDetailTiles,
    handlePairCommitted,
    finishDetailPreparation,
    leaveLive,
    followLive,
    step,
    togglePlay,
    goLive,
    cyclePlaybackSpeed,
    setPlaybackSpeed,
    applySamePublication,
    applyChangedPublication,
    resetPublicationLoad,
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
  };
}
