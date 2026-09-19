"use client";

import { CONTEXT_DETAIL_GRID, UI_FORECAST_HORIZON_HOURS, uiForecastHorizonHours, visibleForecastRun } from "@/data/contextSchema";
import type { ContextManifest } from "@/data/contextSchema";
import { livePosition, playbackAt, residentIndices, timelineEntry, timelineSpanMs } from "@/data/timeline";
import { useMemo } from "react";

export function useForecastPlaybackModel(
  context: ContextManifest | null,
  displayNow: number,
  forecastPosition: number,
  followingLive: boolean,
) {
  const activeForecast = useMemo(
    () => context ? visibleForecastRun(context, uiForecastHorizonHours(context), displayNow) : { frames: [] },
    [context, displayNow],
  );
  const forecastFrames = activeForecast.frames;
  const forecastTimelineKey = forecastFrames.map((frame) => frame.validTime).join("|");
  // Keep the playback timeline stable across the once-per-second live-clock render.
  // Its entries change only when the visible publication-hour window changes.
  const forecastEntries = useMemo(
    () => forecastFrames.map(timelineEntry),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [forecastTimelineKey],
  );
  const forecastPlayback = playbackAt(forecastEntries, forecastPosition);
  const live = livePosition(forecastEntries, displayNow);
  const liveAvailable = context?.mode === "live" && live.available;
  const forecastA = forecastFrames[forecastPlayback.fromIndex] ?? null;
  const forecastB = forecastFrames[forecastPlayback.toIndex] ?? forecastA;
  const surfaceFrameB = !forecastPlayback.interpolating && forecastPlayback.fromIndex < forecastFrames.length - 1
    ? forecastFrames[forecastPlayback.fromIndex + 1]
    : forecastB;
  const surfaceToIndex = forecastPlayback.interpolating
    ? forecastPlayback.toIndex
    : Math.min(forecastFrames.length - 1, forecastPlayback.fromIndex + 1);

  return {
    activeDetailGrid: activeForecast.detailGrid ?? CONTEXT_DETAIL_GRID,
    activeForecast,
    forecastA,
    forecastB,
    forecastEntries,
    forecastFrames,
    forecastPlayback,
    forecastSpan: timelineSpanMs(forecastEntries),
    horizonHours: activeForecast.horizonHours ?? UI_FORECAST_HORIZON_HOURS,
    live,
    liveActive: Boolean(followingLive && liveAvailable),
    liveAvailable,
    resident: residentIndices(forecastPlayback, forecastFrames.length),
    surfaceFrameB,
    surfaceToIndex,
  };
}
