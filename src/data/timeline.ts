import { MAX_INTERPOLATION_GAP_MINUTES, type TimelineEntry } from "./contracts";

export type PlaybackSpeed = 0.25 | 1 | 3;
export const PLAYBACK_RATE = 3_600;
export const MAX_PLAYBACK_TICK_MS = 250;

export type PlaybackState = {
  t: number;
  fromIndex: number;
  toIndex: number;
  interpolating: boolean;
  observationTime: string;
};

export type RenderedPair = { fromIndex: number; toIndex: number };

export function timelineSpanMs(entries: TimelineEntry[]): number {
  if (entries.length < 2) return 0;
  return Math.max(0, Date.parse(entries[entries.length - 1].observationStart) - Date.parse(entries[0].observationStart));
}

export function positionForIndex(entries: TimelineEntry[], index: number): number {
  if (entries.length === 0) return 0;
  const safeIndex = Math.min(entries.length - 1, Math.max(0, index));
  return Math.max(0, Date.parse(entries[safeIndex].observationStart) - Date.parse(entries[0].observationStart));
}

export function positionForTime(entries: TimelineEntry[], timeMs: number): number {
  if (entries.length === 0) return 0;
  const start = Date.parse(entries[0].observationStart);
  return Math.min(timelineSpanMs(entries), Math.max(0, timeMs - start));
}

export function livePosition(entries: TimelineEntry[], timeMs: number): { positionMs: number; available: boolean } {
  const positionMs = positionForTime(entries, timeMs);
  const playback = playbackAt(entries, positionMs);
  return {
    positionMs,
    available: Boolean(playback.observationTime) && Date.parse(playback.observationTime) === timeMs,
  };
}

export function steppedPosition(entries: TimelineEntry[], positionMs: number, delta: -1 | 1): number {
  const state = playbackAt(entries, positionMs);
  const target = state.interpolating
    ? delta < 0 ? state.fromIndex : state.toIndex
    : state.fromIndex + delta;
  return positionForIndex(entries, target);
}

export function nearestTimelineIndex(entries: TimelineEntry[], positionMs: number): number {
  if (entries.length === 0) return 0;
  const absolute = Date.parse(entries[0].observationStart) + Math.min(timelineSpanMs(entries), Math.max(0, positionMs));
  let nearest = 0;
  let distance = Number.POSITIVE_INFINITY;
  entries.forEach((entry, index) => {
    const nextDistance = Math.abs(Date.parse(entry.observationStart) - absolute);
    if (nextDistance < distance) {
      nearest = index;
      distance = nextDistance;
    }
  });
  return nearest;
}

export function advancePlaybackPosition(positionMs: number, elapsedMs: number, spanMs: number, speed: PlaybackSpeed = 1): number {
  if (spanMs <= 0) return 0;
  if (positionMs >= spanMs) return spanMs;
  const tickMs = Math.min(MAX_PLAYBACK_TICK_MS, Math.max(0, elapsedMs));
  return Math.min(spanMs, Math.max(0, positionMs) + tickMs * PLAYBACK_RATE * speed);
}

export function playbackAt(entries: TimelineEntry[], clockMs: number, maxGapMinutes = MAX_INTERPOLATION_GAP_MINUTES): PlaybackState {
  if (entries.length === 0) {
    return { t: 0, fromIndex: 0, toIndex: 0, interpolating: false, observationTime: "" };
  }
  if (entries.length === 1) {
    return { t: 0, fromIndex: 0, toIndex: 0, interpolating: false, observationTime: entries[0].observationStart };
  }
  const start = Date.parse(entries[0].observationStart);
  const end = Date.parse(entries[entries.length - 1].observationStart);
  const span = Math.max(0, end - start);
  const elapsed = Math.min(span, Math.max(0, clockMs));
  const abs = start + elapsed;
  let toIndex = entries.findIndex((entry) => Date.parse(entry.observationStart) >= abs);
  if (toIndex < 0) toIndex = entries.length - 1;
  if (Date.parse(entries[toIndex].observationStart) === abs) {
    return { t: 0, fromIndex: toIndex, toIndex, interpolating: false, observationTime: entries[toIndex].observationStart };
  }
  const fromIndex = Math.max(0, toIndex - 1);
  const fromMs = Date.parse(entries[fromIndex].observationStart);
  const toMs = Date.parse(entries[toIndex].observationStart);
  const gap = (toMs - fromMs) / 60000;
  if (gap <= 0) {
    return { t: 0, fromIndex, toIndex, interpolating: false, observationTime: entries[fromIndex].observationStart };
  }
  if (gap > maxGapMinutes) {
    const useTo = abs - fromMs > toMs - abs;
    const index = useTo ? toIndex : fromIndex;
    return {
      t: 0,
      fromIndex: index,
      toIndex: index,
      interpolating: false,
      observationTime: entries[index].observationStart,
    };
  }
  const t = Math.min(1, Math.max(0, (abs - fromMs) / (toMs - fromMs)));
  return {
    t,
    fromIndex,
    toIndex,
    interpolating: t > 0 && t < 1,
    observationTime: new Date(abs).toISOString(),
  };
}

export function residentIndices(state: PlaybackState, count: number): number[] {
  if (count <= 0) return [];
  const atEnd = state.fromIndex === count - 1 && state.toIndex === count - 1;
  const nextIndex = state.interpolating ? state.toIndex : state.fromIndex + 1;
  const preferred = atEnd && count > 2
    ? [count - 1, 0, 1]
    : [state.fromIndex, nextIndex, nextIndex + 1, state.fromIndex - 1];
  const unique: number[] = [];
  for (const index of preferred) {
    if (index >= 0 && index < count && !unique.includes(index)) {
      unique.push(index);
    }
    if (unique.length === 3) break;
  }
  return unique;
}
