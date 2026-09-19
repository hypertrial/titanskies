import { describe, expect, it } from "vitest";
import {
  adjacentPairFor,
  detailPlaybackModeFor,
  INITIAL_PLAYBACK_SESSION,
  liveResetTarget,
  reducePlaybackSession,
} from "./useExplorerPlaybackSession";

const entries = [
  { scanId: "forecast-0", observationStart: "2024-07-15T18:00:00Z", manifestUrl: "/a" },
  { scanId: "forecast-1", observationStart: "2024-07-15T19:00:00Z", manifestUrl: "/b" },
  { scanId: "forecast-2", observationStart: "2024-07-15T20:00:00Z", manifestUrl: "/c" },
];

describe("playback session phase transitions", () => {
  it("starts playback, buffers when frames are missing, and pauses for interaction", () => {
    const playing = reducePlaybackSession(INITIAL_PLAYBACK_SESSION, {
      type: "play",
      canPlay: true,
      ready: true,
      atEndpoint: false,
      detailVisible: false,
      readyDetailFadeKeys: [],
    });
    expect(playing.phase).toBe("playing");
    expect(playing.followingLive).toBe(false);

    const buffering = reducePlaybackSession(INITIAL_PLAYBACK_SESSION, {
      type: "play",
      canPlay: true,
      ready: false,
      atEndpoint: false,
      detailVisible: false,
      readyDetailFadeKeys: [],
    });
    expect(buffering.phase).toBe("buffering");
    expect(reducePlaybackSession(buffering, { type: "ready" }).phase).toBe("playing");

    const preparing = reducePlaybackSession(INITIAL_PLAYBACK_SESSION, {
      type: "play",
      canPlay: true,
      ready: true,
      atEndpoint: false,
      detailVisible: true,
      readyDetailFadeKeys: ["detail-a"],
    });
    expect(preparing.phase).toBe("preparing-detail");
    expect(preparing.detailFadeOutKeys).toEqual(["detail-a"]);
    expect(reducePlaybackSession(preparing, { type: "detail-hidden", ready: true }).phase).toBe("playing");
    expect(reducePlaybackSession(playing, { type: "interaction-pause" }).phase).toBe("interaction-paused");
    expect(reducePlaybackSession(playing, { type: "pause-manual" }).phase).toBe("manual-paused");
  });

  it("holds at the endpoint, then goes live and leaves live without losing pair identity", () => {
    const waiting = reducePlaybackSession(INITIAL_PLAYBACK_SESSION, {
      type: "play",
      canPlay: true,
      ready: true,
      atEndpoint: true,
      detailVisible: false,
      readyDetailFadeKeys: [],
    });
    expect(waiting.phase).toBe("waiting");
    expect(waiting.followingLive).toBe(false);

    const live = reducePlaybackSession(waiting, { type: "go-live" });
    expect(live).toMatchObject({ followingLive: true, phase: "manual-paused", detailFadeOutKeys: [] });
    expect(reducePlaybackSession(live, { type: "leave-live" }).followingLive).toBe(false);

    const committed = reducePlaybackSession(live, { type: "commit-pair", fromIndex: 2, toIndex: 3 });
    expect(committed.renderedPair).toEqual({ fromIndex: 2, toIndex: 3 });
    expect(reducePlaybackSession(committed, { type: "commit-pair", fromIndex: 2, toIndex: 3 })).toBe(committed);
    expect(reducePlaybackSession(committed, { type: "set-detail-tiles", tiles: [4, 5] }).detailTileIds).toEqual([4, 5]);
  });

  it("derives adjacent pairs, detail fade modes, and a live reset target", () => {
    expect(adjacentPairFor(entries, 0)).toEqual([0, 1]);
    expect(adjacentPairFor(entries, 3_600_000)).toEqual([1, 2]);
    expect(detailPlaybackModeFor("preparing-detail", false, false)).toBe("fading-out");
    expect(detailPlaybackModeFor("playing", true, false)).toBe("base-only");
    expect(detailPlaybackModeFor("waiting", false, true)).toBe("base-only");
    expect(detailPlaybackModeFor("manual-paused", false, false)).toBe("visible");
    expect(liveResetTarget({
      mode: "demo",
      forecastEntries: entries,
      displayNow: Date.parse("2024-07-15T18:30:00Z"),
      now: Date.parse("2024-07-15T18:30:00Z"),
      resetFrames: entries.map((entry) => ({ validTime: entry.observationStart, textureUrl: entry.manifestUrl })),
    })).toBeNull();
    expect(liveResetTarget({
      mode: "live",
      forecastEntries: entries,
      displayNow: Date.parse("2024-07-15T18:30:00Z"),
      now: Date.parse("2024-07-15T18:30:00Z"),
      resetFrames: entries.map((entry) => ({ validTime: entry.observationStart, textureUrl: entry.manifestUrl })),
    })).toEqual({
      now: Date.parse("2024-07-15T18:30:00Z"),
      position: 30 * 60_000,
      preloadPosition: 30 * 60_000,
    });
  });
});
