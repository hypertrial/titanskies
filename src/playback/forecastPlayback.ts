import { advancePlaybackPosition, playbackAt, type PlaybackSpeed, type RenderedPair } from "@/data/timeline";
import type { TimelineEntry } from "@/data/contracts";
import { useEffect } from "react";

export type PlaybackPhase = "waiting" | "preparing-detail" | "buffering" | "playing" | "interaction-paused" | "manual-paused" | "fading-out" | "resetting" | "fading-in";

export type PlaybackEvent =
  | { type: "play"; canPlay: boolean; ready: boolean; atEndpoint: boolean; detailVisible: boolean }
  | { type: "pause" }
  | { type: "interaction" }
  | { type: "ready" }
  | { type: "detail-hidden"; ready: boolean }
  | { type: "endpoint" }
  | { type: "fade-complete" }
  | { type: "reset-complete" }
  | { type: "asset-failure" }
  | { type: "set"; phase: PlaybackPhase };

export const playbackIsActive = (phase: PlaybackPhase): boolean =>
  phase === "preparing-detail" || phase === "buffering" || phase === "playing" || phase === "fading-out" || phase === "resetting" || phase === "fading-in";

export function playbackPhaseReducer(phase: PlaybackPhase, event: PlaybackEvent): PlaybackPhase {
  switch (event.type) {
    case "play":
      if (!event.canPlay) return phase;
      if (event.atEndpoint) return "waiting";
      if (event.detailVisible) return "preparing-detail";
      return event.ready ? "playing" : "buffering";
    case "pause":
      return playbackIsActive(phase) ? "manual-paused" : phase;
    case "interaction":
      return playbackIsActive(phase) ? "interaction-paused" : phase;
    case "ready":
      return phase === "buffering" ? "playing" : phase;
    case "detail-hidden":
      return phase === "preparing-detail" ? event.ready ? "playing" : "buffering" : phase;
    case "endpoint":
      return phase === "playing" ? "waiting" : phase;
    case "fade-complete":
      return phase === "fading-out" ? "resetting" : phase === "fading-in" ? "playing" : phase;
    case "reset-complete":
      return phase === "resetting" ? "fading-in" : phase;
    case "asset-failure":
      return playbackIsActive(phase) ? "interaction-paused" : phase;
    case "set":
      return event.phase;
  }
}

export type PlaybackUiSnapshot = { positionMs: number; revision: number };

export class PlaybackUiStore {
  private listeners = new Set<() => void>();
  private snapshot: PlaybackUiSnapshot;
  private lastPublish = Number.NEGATIVE_INFINITY;

  constructor(positionMs = 0, private readonly intervalMs = 250) {
    this.snapshot = { positionMs, revision: 0 };
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): PlaybackUiSnapshot => this.snapshot;

  update(positionMs: number, nowMs: number, force = false): void {
    if (!force && nowMs - this.lastPublish < this.intervalMs) return;
    if (this.snapshot.positionMs === positionMs && !force) return;
    this.lastPublish = nowMs;
    this.snapshot = { positionMs, revision: this.snapshot.revision + 1 };
    this.listeners.forEach((listener) => listener());
  }
}

export type PlaybackClockOptions = {
  enabled: boolean;
  mobile: boolean;
  reducedMotion: boolean;
  entries: TimelineEntry[];
  spanMs: number;
  speed: PlaybackSpeed;
  positionRef: React.MutableRefObject<number>;
  mixRef: React.MutableRefObject<number>;
  renderedPairRef: React.MutableRefObject<RenderedPair>;
  timelineRef: React.RefObject<HTMLInputElement | null>;
  explorerRef: React.RefObject<HTMLElement | null>;
  uiStore: PlaybackUiStore;
  pairReady: (indexes: number[]) => boolean;
  onPairBoundary: (positionMs: number, pair: RenderedPair) => void;
  requestRender: () => void;
  onBuffering: (positionMs: number) => void;
  onEndpoint: () => void;
  scrubbingRef?: React.RefObject<boolean>;
};

export function playbackPair(entries: TimelineEntry[], positionMs: number): RenderedPair {
  const state = playbackAt(entries, positionMs);
  return {
    fromIndex: state.fromIndex,
    toIndex: state.interpolating ? state.toIndex : Math.min(entries.length - 1, state.fromIndex + 1),
  };
}

export function playbackMixForRenderedPair(
  state: ReturnType<typeof playbackAt>,
  desiredPair: RenderedPair,
  renderedPair: RenderedPair,
): number {
  if (desiredPair.fromIndex === renderedPair.fromIndex && desiredPair.toIndex === renderedPair.toIndex) {
    return state.interpolating ? state.t : 0;
  }
  // React Three Fiber commits its scene in a separate renderer. While that
  // commit is pending, keep the outgoing pair on its shared hourly endpoint;
  // applying the incoming pair's near-zero mix to the old textures would draw
  // the previous hour again for one frame.
  if (desiredPair.fromIndex >= renderedPair.toIndex) return 1;
  return 0;
}

export function playbackTargetFrameMs(mobile: boolean): number {
  return mobile ? 1_000 / 30 : 1_000 / 60;
}

export function playbackFrameDue(nowMs: number, lastUpdateMs: number, mobile: boolean): boolean {
  return nowMs - lastUpdateMs >= playbackTargetFrameMs(mobile) - 1;
}

export function syncTimelineElement(
  input: HTMLInputElement | null,
  positionMs: number,
  options: { writeValue?: boolean; spanMs?: number } = {},
): void {
  if (!input) return;
  if (options.writeValue !== false) input.value = String(positionMs);
  const maximum = options.spanMs ?? Number(input.max);
  const progress = maximum > 0 ? Math.max(0, Math.min(100, positionMs / maximum * 100)) : 0;
  input.style.setProperty("--timeline-progress", `${progress}%`);
}

export type TimelinePointerInput = {
  min: string;
  max: string;
  step: string;
  getBoundingClientRect(): { left: number; width: number };
};

export function timelinePositionFromPointer(
  input: TimelinePointerInput,
  clientX: number,
): number {
  const min = Number(input.min) || 0;
  const max = Number(input.max);
  const step = Number(input.step) || 1;
  const rect = input.getBoundingClientRect();
  if (!(rect.width > 0) || !(max > min)) return min;
  const raw = min + Math.max(0, Math.min(1, (clientX - rect.left) / rect.width)) * (max - min);
  return Math.min(max, Math.max(min, Math.round(raw / step) * step));
}

export function useForecastPlaybackClock(options: PlaybackClockOptions): void {
  const {
    enabled, mobile, reducedMotion, entries, spanMs, speed, positionRef, mixRef, renderedPairRef,
    timelineRef, explorerRef, uiStore, pairReady, onPairBoundary, requestRender, onBuffering, onEndpoint,
    scrubbingRef,
  } = options;

  useEffect(() => {
    if (!enabled || reducedMotion || spanMs <= 0 || entries.length < 2) return;
    let prior = performance.now();
    let lastUpdate = prior;
    let animationFrame = 0;
    const tick = (now: number) => {
      animationFrame = window.requestAnimationFrame(tick);
      if (document.visibilityState === "hidden") { prior = now; lastUpdate = now; return; }
      if (!playbackFrameDue(now, lastUpdate, mobile)) return;
      const elapsed = now - prior;
      prior = now;
      lastUpdate = now;
      const next = advancePlaybackPosition(positionRef.current, elapsed, spanMs, speed);
      const state = playbackAt(entries, next);
      const pair = playbackPair(entries, next);
      if (!pairReady([pair.fromIndex, pair.toIndex])) {
        mixRef.current = pair.fromIndex >= renderedPairRef.current.toIndex ? 1 : 0;
        onBuffering(positionRef.current);
        return;
      }
      if (pair.fromIndex !== renderedPairRef.current.fromIndex || pair.toIndex !== renderedPairRef.current.toIndex) {
        onPairBoundary(next, pair);
      }
      mixRef.current = playbackMixForRenderedPair(state, pair, renderedPairRef.current);
      positionRef.current = next;
      syncTimelineElement(timelineRef.current, next, { writeValue: !scrubbingRef?.current, spanMs });
      if (explorerRef.current) {
        explorerRef.current.dataset.renderMix = mixRef.current.toFixed(6);
        explorerRef.current.dataset.playbackPosition = String(Math.round(next));
      }
      uiStore.update(next, now);
      if (process.env.NEXT_PUBLIC_PERF_DIAGNOSTICS === "1") {
        const target = globalThis as typeof globalThis & { __TITANSKIES_PERF__?: Record<string, unknown> };
        const current = Number(target.__TITANSKIES_PERF__?.playbackTicks ?? 0);
        target.__TITANSKIES_PERF__ = { ...target.__TITANSKIES_PERF__, playbackTicks: current + 1 };
      }
      requestRender();
      if (next >= spanMs) onEndpoint();
    };
    animationFrame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(animationFrame);
  }, [enabled, entries, explorerRef, mixRef, mobile, onBuffering, onEndpoint, onPairBoundary, pairReady, positionRef, reducedMotion, renderedPairRef, requestRender, scrubbingRef, spanMs, speed, timelineRef, uiStore]);
}
