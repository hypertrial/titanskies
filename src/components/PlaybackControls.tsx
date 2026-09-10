"use client";

import type { ProductView } from "@/data/contextSchema";
import { formatCompactTimelineTime, formatExactInterfaceTime, formatMobileTimelineTime } from "@/data/interfacePresentation";
import { playbackAt, type PlaybackSpeed } from "@/data/timeline";
import { PlaybackUiStore, timelinePositionFromPointer } from "@/playback/forecastPlayback";
import { useEffect, useRef, useSyncExternalStore } from "react";
import { Icon } from "./Icon";

const PLAYBACK_SPEEDS: PlaybackSpeed[] = [0.25, 1, 3];
const playbackSpeedLabel = (speed: PlaybackSpeed) => speed === 0.25 ? "¼×" : `${speed}×`;

function ForecastValidTime({ store, entries, timelineRef, liveActive, referenceTime }: {
  store: PlaybackUiStore;
  entries: Array<{ scanId: string; observationStart: string; manifestUrl: string }>;
  timelineRef: React.RefObject<HTMLInputElement | null>;
  liveActive: boolean;
  referenceTime: number;
}) {
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  const observationTime = playbackAt(entries, snapshot.positionMs).observationTime;
  const label = formatCompactTimelineTime(observationTime, referenceTime, liveActive);
  const mobileLabel = formatMobileTimelineTime(observationTime, referenceTime);
  const exact = formatExactInterfaceTime(observationTime);
  useEffect(() => timelineRef.current?.setAttribute("aria-valuetext", exact), [exact, timelineRef]);
  return <time dateTime={observationTime} aria-label={liveActive ? `Now, ${exact}` : exact} data-testid="obs-time" data-ui-revision={snapshot.revision}><span className="timeline-time-desktop">{label}</span><span className="timeline-time-mobile">{mobileLabel}</span></time>;
}

type PlaybackControlsProps = {
  view: ProductView;
  viewReady: boolean;
  canPlay: boolean;
  reducedMotion: boolean;
  buffering: boolean;
  playing: boolean;
  speed: PlaybackSpeed;
  atStart: boolean;
  atEnd: boolean;
  clock: string;
  referenceTime: number;
  liveActive: boolean;
  liveMode: boolean;
  liveAvailable: boolean;
  horizonHours: number;
  spanMs: number;
  initialPositionMs: number;
  entries: Array<{ scanId: string; observationStart: string; manifestUrl: string }>;
  uiStore: PlaybackUiStore;
  timelineRef: React.RefObject<HTMLInputElement | null>;
  onStep: (delta: -1 | 1) => void;
  onTogglePlay: () => void;
  onSpeed: (speed: PlaybackSpeed) => void;
  onGoLive: () => void;
  onScrub: (positionMs: number) => void;
  onScrubStart?: () => void;
  onScrubEnd?: () => void;
};

export function PlaybackControls(props: PlaybackControlsProps) {
  const nextSpeed = PLAYBACK_SPEEDS[(PLAYBACK_SPEEDS.indexOf(props.speed) + 1) % PLAYBACK_SPEEDS.length];
  const motionUnavailable = "Playback animation unavailable because Reduce Motion is enabled";
  const onScrubRef = useRef(props.onScrub);
  const onScrubStartRef = useRef(props.onScrubStart);
  const onScrubEndRef = useRef(props.onScrubEnd);
  const draggingRef = useRef(false);
  onScrubRef.current = props.onScrub;
  onScrubStartRef.current = props.onScrubStart;
  onScrubEndRef.current = props.onScrubEnd;
  useEffect(() => {
    const progress = props.spanMs > 0 ? Math.max(0, Math.min(100, props.initialPositionMs / props.spanMs * 100)) : 0;
    props.timelineRef.current?.style.setProperty("--timeline-progress", `${progress}%`);
  }, [props.initialPositionMs, props.spanMs, props.timelineRef]);
  useEffect(() => {
    const apply = (clientX: number) => {
      const input = props.timelineRef.current;
      if (!input) return;
      const next = timelinePositionFromPointer(input, clientX);
      input.value = String(next);
      onScrubRef.current(next);
    };
    const onUp = () => {
      if (!draggingRef.current) return;
      draggingRef.current = false;
      onScrubEndRef.current?.();
    };
    const onMove = (event: PointerEvent) => {
      if (!draggingRef.current) return;
      if (event.pointerType !== "touch" && !(event.buttons & 1)) {
        onUp();
        return;
      }
      apply(event.clientX);
    };
    window.addEventListener("pointermove", onMove, true);
    window.addEventListener("pointerup", onUp, true);
    window.addEventListener("pointercancel", onUp, true);
    return () => {
      window.removeEventListener("pointermove", onMove, true);
      window.removeEventListener("pointerup", onUp, true);
      window.removeEventListener("pointercancel", onUp, true);
      if (draggingRef.current) {
        draggingRef.current = false;
        onScrubEndRef.current?.();
      }
    };
  }, [props.timelineRef]);
  return <div className="playback-main">
      {props.view === "forecast" ? <div className="transport">
        <button className="icon-button" type="button" onClick={() => props.onStep(-1)} disabled={!props.viewReady || props.atStart} aria-label="Previous time" data-testid="previous-frame"><Icon name="previous" /></button>
        <button className="play-button" type="button" onClick={props.onTogglePlay} disabled={!props.viewReady || !props.canPlay} aria-busy={props.buffering} aria-label={props.reducedMotion ? motionUnavailable : props.buffering ? "Cancel loading forecast playback" : undefined} title={props.reducedMotion ? motionUnavailable : undefined} data-testid="play-toggle"><Icon name={props.playing ? "pause" : "play"} />{props.buffering ? "Loading…" : props.playing ? "Pause" : "Play"}</button>
        <button className="icon-button" type="button" onClick={() => props.onStep(1)} disabled={!props.viewReady || props.atEnd} aria-label="Next time" data-testid="next-frame"><Icon name="next" /></button>
        <button className="speed-button" type="button" onClick={() => props.onSpeed(nextSpeed)} disabled={props.reducedMotion} aria-label={props.reducedMotion ? "Playback speed unavailable because Reduce Motion is enabled" : `Playback speed ${playbackSpeedLabel(props.speed)}; activate to use ${playbackSpeedLabel(nextSpeed)}`} data-testid="playback-speed">{playbackSpeedLabel(props.speed)}</button>
      </div> : null}
      {props.view === "forecast" ? <div className="timeline-wrap" onPointerDown={(event) => {
        if (event.button !== 0 || !props.viewReady) return;
        const input = props.timelineRef.current;
        if (!input) return;
        event.preventDefault();
        draggingRef.current = true;
        input.focus();
        onScrubStartRef.current?.();
        const next = timelinePositionFromPointer(input, event.clientX);
        input.value = String(next);
        onScrubRef.current(next);
      }}><input ref={props.timelineRef} className="timeline" type="range" min={0} max={Math.max(1, props.spanMs)} step={1_000} defaultValue={props.initialPositionMs} onInput={(event) => props.onScrub(Number(event.currentTarget.value))} disabled={!props.viewReady} aria-label="forecast timeline" /></div> : null}
      <div className="observation-time">
        {props.view === "forecast" ? <ForecastValidTime store={props.uiStore} entries={props.entries} timelineRef={props.timelineRef} liveActive={props.liveActive} referenceTime={props.referenceTime} /> : <time dateTime={props.clock} data-testid="obs-time">{formatExactInterfaceTime(props.clock)}</time>}
        {props.view === "forecast" ? props.liveMode
          ? <button className={`live-control${props.liveActive ? " active" : ""}`} type="button" onClick={props.onGoLive} disabled={!props.liveAvailable} aria-pressed={props.liveActive} data-testid="live-control">{props.liveActive ? "Live" : props.liveAvailable ? "Go live" : "Unavailable"}</button>
          : <span className="forecast-label" aria-label={`Forecast horizon ${props.horizonHours} hours`} data-testid="forecast-horizon"><span className="forecast-label-prefix">Forecast · </span>+{props.horizonHours}h</span>
          : null}
      </div>
  </div>;
}
