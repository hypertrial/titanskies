import { describe, expect, it } from "vitest";
import { advancePlaybackPosition, livePosition, MAX_PLAYBACK_TICK_MS, nearestTimelineIndex, playbackAt, PLAYBACK_RATE, positionForIndex, positionForTime, residentIndices, steppedPosition, timelineEntry, timelineSpanMs } from "./timeline";

const entry = (scanId: string, hour: string) => ({
  scanId,
  observationStart: `2024-07-15T${hour}:00:00Z`,
  manifestUrl: `/demo/manifests/${scanId}.json`,
});

describe("timeline", () => {
  it("builds a forecast timeline entry from a frame and index", () => {
    expect(timelineEntry({ validTime: "2024-07-15T18:00:00Z", textureUrl: "/frames/a.webp" }, 3)).toEqual({
      scanId: "forecast-3",
      observationStart: "2024-07-15T18:00:00Z",
      manifestUrl: "/frames/a.webp",
    });
    expect(timelineEntry({ validTime: "2024-07-15T19:00:00Z" }, 0).manifestUrl).toBe("");
  });

  it("interpolates across short gaps and holds overnight", () => {
    const shortGap = [entry("a", "18"), entry("b", "19")];
    const play = playbackAt(shortGap, 30 * 60 * 1000);
    expect(play.interpolating).toBe(true);
    expect(play.fromIndex).toBe(0);
    expect(play.toIndex).toBe(1);

    const overnight = [entry("a", "01"), { ...entry("b", "14"), observationStart: "2024-07-15T14:00:00Z" }];
    const held = playbackAt(overnight, 4 * 3600 * 1000);
    expect(held.interpolating).toBe(false);
    expect(playbackAt(overnight, 4 * 3600 * 1000, Number.POSITIVE_INFINITY).interpolating).toBe(true);
  });

  it("positions live time exactly and rejects extrapolation or long gaps", () => {
    const entries = [entry("a", "18"), entry("b", "19")];
    const now = Date.parse("2024-07-15T18:37:15Z");
    expect(positionForTime(entries, now)).toBe(37 * 60_000 + 15_000);
    expect(livePosition(entries, now)).toEqual({ positionMs: 37 * 60_000 + 15_000, available: true });
    expect(livePosition(entries, Date.parse("2024-07-15T19:00:00Z"))).toEqual({ positionMs: 3_600_000, available: true });
    expect(livePosition(entries, Date.parse("2024-07-15T17:00:00Z"))).toEqual({ positionMs: 0, available: false });
    expect(livePosition(entries, Date.parse("2024-07-15T20:00:00Z"))).toEqual({ positionMs: 3_600_000, available: false });
    const gap = [entry("a", "18"), { ...entry("b", "21"), observationStart: "2024-07-15T21:00:00Z" }];
    expect(livePosition(gap, Date.parse("2024-07-15T19:00:00Z")).available).toBe(false);
  });

  it("steps to the surrounding hours from an interpolated minute", () => {
    const entries = [entry("a", "18"), entry("b", "19"), entry("c", "20")];
    const position = 37 * 60_000;
    expect(steppedPosition(entries, position, -1)).toBe(0);
    expect(steppedPosition(entries, position, 1)).toBe(3_600_000);
    expect(steppedPosition(entries, 3_600_000, 1)).toBe(7_200_000);
  });

  it("keeps at most adjacent resident frames", () => {
    const state = { t: 0.4, fromIndex: 2, toIndex: 3, interpolating: true, observationTime: "" };
    const ids = residentIndices(state, 8);
    expect(ids.length).toBeLessThanOrEqual(3);
    expect(ids).toContain(2);
    expect(ids).toContain(3);
  });

  it("keeps two forward frames resident at an exact hourly boundary", () => {
    const state = { t: 0, fromIndex: 2, toIndex: 2, interpolating: false, observationTime: "" };
    expect(residentIndices(state, 8)).toEqual([2, 3, 4]);
  });

  it("preloads the first interpolation pair while parked at the latest frame", () => {
    const state = { t: 0, fromIndex: 4, toIndex: 4, interpolating: false, observationTime: "" };
    expect(residentIndices(state, 5)).toEqual([4, 0, 1]);
  });

  it("advances smoothly at all speeds, caps stalls, and clamps at the endpoint", () => {
    const span = 4 * 3_600_000;
    expect(PLAYBACK_RATE).toBe(3_600);
    let position = 0;
    for (let elapsed = 0; elapsed < 1_000; elapsed += MAX_PLAYBACK_TICK_MS) {
      position = advancePlaybackPosition(position, MAX_PLAYBACK_TICK_MS, span);
    }
    expect(position).toBe(3_600_000);
    expect(advancePlaybackPosition(0, 1_000, span, 0.25)).toBe(MAX_PLAYBACK_TICK_MS * PLAYBACK_RATE * 0.25);
    expect(advancePlaybackPosition(0, 1_000, span, 3)).toBe(MAX_PLAYBACK_TICK_MS * PLAYBACK_RATE * 3);
    expect(advancePlaybackPosition(0, 500, span)).toBe(MAX_PLAYBACK_TICK_MS * PLAYBACK_RATE);
    expect(advancePlaybackPosition(0, 30_000, span)).toBe(MAX_PLAYBACK_TICK_MS * PLAYBACK_RATE);
    expect(advancePlaybackPosition(span, 0, span)).toBe(span);
    expect(advancePlaybackPosition(span, 1, span)).toBe(span);
    expect(advancePlaybackPosition(span - 10, 1, span)).toBe(span);
  });

  it("represents an hourly handoff as the same endpoint in adjacent pairs", () => {
    const entries = [entry("a", "18"), entry("b", "19"), entry("c", "20")];
    const before = playbackAt(entries, 3_599_000);
    const endpoint = playbackAt(entries, 3_600_000);
    const after = playbackAt(entries, 3_601_000);
    expect(before).toMatchObject({ fromIndex: 0, toIndex: 1, t: expect.closeTo(0.999722, 5) });
    expect(endpoint).toMatchObject({ fromIndex: 1, toIndex: 1, t: 0 });
    expect(after).toMatchObject({ fromIndex: 1, toIndex: 2, t: expect.closeTo(0.000278, 5) });
  });

  it("selects the latest observation exactly without wrapping", () => {
    const entries = [entry("a", "17"), entry("b", "18"), entry("c", "20")];
    const span = timelineSpanMs(entries);
    const latest = playbackAt(entries, span);
    expect(latest.fromIndex).toBe(2);
    expect(latest.toIndex).toBe(2);
    expect(latest.observationTime).toBe(entries[2].observationStart);
    expect(nearestTimelineIndex(entries, span)).toBe(2);
  });

  it("moves between real observations across a long gap", () => {
    const entries = [entry("a", "01"), entry("b", "14")];
    expect(positionForIndex(entries, 1)).toBe(13 * 3_600_000);
    expect(playbackAt(entries, positionForIndex(entries, 1)).fromIndex).toBe(1);
  });

  it("selects an exact observation instead of the preceding interpolation pair", () => {
    const entries = [entry("a", "17"), entry("b", "18"), entry("c", "19")];
    expect(playbackAt(entries, positionForIndex(entries, 1))).toMatchObject({
      t: 0,
      fromIndex: 1,
      toIndex: 1,
      interpolating: false,
      observationTime: entries[1].observationStart,
    });
  });
});
