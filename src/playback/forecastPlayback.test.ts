import { describe, expect, it, vi } from "vitest";
import { PlaybackUiStore, playbackFrameDue, playbackMixForRenderedPair, playbackPair, playbackPhaseReducer, playbackTargetFrameMs, syncTimelineElement, timelinePositionFromPointer } from "./forecastPlayback";
import { playbackAt } from "@/data/timeline";

const entries = [
  { scanId: "a", observationStart: "2026-08-21T10:00:00Z", manifestUrl: "a" },
  { scanId: "b", observationStart: "2026-08-21T11:00:00Z", manifestUrl: "b" },
  { scanId: "c", observationStart: "2026-08-21T12:00:00Z", manifestUrl: "c" },
];

describe("forecast playback state machine", () => {
  it("keeps explicit pause sticky and starts only after Play", () => {
    expect(playbackPhaseReducer("manual-paused", { type: "ready" })).toBe("manual-paused");
    expect(playbackPhaseReducer("manual-paused", { type: "play", canPlay: true, ready: true, atEndpoint: false, detailVisible: false })).toBe("playing");
    expect(playbackPhaseReducer("playing", { type: "pause" })).toBe("manual-paused");
    expect(playbackPhaseReducer("manual-paused", { type: "ready" })).toBe("manual-paused");
  });

  it("buffers, resumes, pauses for interaction, and sequences the endpoint reset", () => {
    expect(playbackPhaseReducer("manual-paused", { type: "play", canPlay: true, ready: false, atEndpoint: false, detailVisible: false })).toBe("buffering");
    expect(playbackPhaseReducer("buffering", { type: "ready" })).toBe("playing");
    expect(playbackPhaseReducer("playing", { type: "interaction" })).toBe("interaction-paused");
    expect(playbackPhaseReducer("playing", { type: "endpoint" })).toBe("waiting");
    expect(playbackPhaseReducer("fading-out", { type: "fade-complete" })).toBe("resetting");
    expect(playbackPhaseReducer("resetting", { type: "reset-complete" })).toBe("fading-in");
    expect(playbackPhaseReducer("fading-in", { type: "fade-complete" })).toBe("playing");
  });

  it("holds playback while visible detail fades and then enters the base-only path", () => {
    expect(playbackPhaseReducer("manual-paused", { type: "play", canPlay: true, ready: true, atEndpoint: false, detailVisible: true })).toBe("preparing-detail");
    expect(playbackPhaseReducer("preparing-detail", { type: "detail-hidden", ready: true })).toBe("playing");
    expect(playbackPhaseReducer("preparing-detail", { type: "detail-hidden", ready: false })).toBe("buffering");
    expect(playbackPhaseReducer("preparing-detail", { type: "pause" })).toBe("manual-paused");
    expect(playbackPhaseReducer("preparing-detail", { type: "interaction" })).toBe("interaction-paused");
    expect(playbackPhaseReducer("manual-paused", { type: "detail-hidden", ready: true })).toBe("manual-paused");
  });

  it("selects the next two-sample pair without an extended mix range", () => {
    expect(playbackPair(entries, 3_599_000)).toEqual({ fromIndex: 0, toIndex: 1 });
    expect(playbackPair(entries, 3_600_000)).toEqual({ fromIndex: 1, toIndex: 2 });
    expect(playbackPair(entries, 3_601_000)).toEqual({ fromIndex: 1, toIndex: 2 });
  });

  it("holds the outgoing endpoint until the scene commits the incoming pair", () => {
    const position = 3_600_000 + 120_000;
    const state = playbackAt(entries, position);
    const desired = playbackPair(entries, position);

    expect(playbackMixForRenderedPair(state, desired, { fromIndex: 0, toIndex: 1 })).toBe(1);
    expect(playbackMixForRenderedPair(state, desired, desired)).toBeCloseTo(120_000 / 3_600_000);
  });
});

describe("playback UI publication", () => {
  it("publishes at four hertz without throttling hot position updates", () => {
    const store = new PlaybackUiStore(0, 250);
    const listener = vi.fn();
    store.subscribe(listener);
    for (let now = 0; now <= 1_000; now += 16) store.update(now * 3_600, now);
    expect(listener.mock.calls.length).toBeGreaterThanOrEqual(4);
    expect(listener.mock.calls.length).toBeLessThanOrEqual(5);
    expect(store.getSnapshot().positionMs).toBeGreaterThan(2_700_000);
  });

  it("can force the final interaction position through the UI throttle", () => {
    const store = new PlaybackUiStore(0, 250);
    const listener = vi.fn();
    store.subscribe(listener);
    store.update(100, 0);
    store.update(200, 10);
    expect(store.getSnapshot().positionMs).toBe(100);
    store.update(200, 10, true);
    expect(store.getSnapshot().positionMs).toBe(200);
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("schedules deterministic desktop and mobile cadences", () => {
    expect(playbackTargetFrameMs(false)).toBeCloseTo(16.667, 2);
    expect(playbackTargetFrameMs(true)).toBeCloseTo(33.333, 2);
    expect(playbackFrameDue(16.667, 0, false)).toBe(true);
    expect(playbackFrameDue(16.667, 0, true)).toBe(false);
    expect(playbackFrameDue(33.334, 0, true)).toBe(true);
  });

  it("updates timeline progress without overwriting the value while scrubbing", () => {
    const input = {
      value: "1200000",
      max: "3600000",
      style: { progress: "", setProperty(name: string, value: string) { if (name === "--timeline-progress") this.progress = value; } },
    };
    syncTimelineElement(input as unknown as HTMLInputElement, 2_400_000, { writeValue: false, spanMs: 3_600_000 });
    expect(input.value).toBe("1200000");
    expect(Number.parseFloat(input.style.progress)).toBeCloseTo(66.67, 1);
    syncTimelineElement(input as unknown as HTMLInputElement, 1_800_000, { spanMs: 3_600_000 });
    expect(input.value).toBe("1800000");
    expect(input.style.progress).toBe("50%");
  });

  it("maps pointer x to a stepped timeline position", () => {
    const input = { min: "0", max: "3600000", step: "1000", getBoundingClientRect: () => ({ left: 100, width: 200 }) };
    expect(timelinePositionFromPointer(input, 100)).toBe(0);
    expect(timelinePositionFromPointer(input, 200)).toBe(1_800_000);
    expect(timelinePositionFromPointer(input, 300)).toBe(3_600_000);
    expect(timelinePositionFromPointer(input, 40)).toBe(0);
    expect(timelinePositionFromPointer(input, 360)).toBe(3_600_000);
    expect(timelinePositionFromPointer(input, 150)).toBe(900_000);
    expect(timelinePositionFromPointer({ ...input, getBoundingClientRect: () => ({ left: 100, width: 0 }) }, 150)).toBe(0);
    expect(timelinePositionFromPointer({ ...input, max: "0" }, 200)).toBe(0);
  });
});
