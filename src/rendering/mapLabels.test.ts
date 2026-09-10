import { describe, expect, it } from "vitest";
import {
  frontSideOpacity,
  isScreenPointInSafeViewport,
  mapLabelLevelOpacity,
  placeMapLabels,
  type ScreenLabelCandidate,
} from "./mapLabels";

describe("map label tiers", () => {
  it("reveals primary labels before secondary labels as the camera approaches", () => {
    expect(mapLabelLevelOpacity("overview", 3)).toBe(1);
    expect(mapLabelLevelOpacity("primary", 2.4)).toBe(0);
    expect(mapLabelLevelOpacity("primary", 2.0)).toBe(1);
    expect(mapLabelLevelOpacity("secondary", 2.0)).toBe(0);
    expect(mapLabelLevelOpacity("secondary", 1.55)).toBe(1);
  });

  it("uses the specified close-zoom bands and delays compact detail labels", () => {
    expect(mapLabelLevelOpacity("detail-major", 1.58)).toBe(0);
    expect(mapLabelLevelOpacity("detail-major", 1.48)).toBe(1);
    expect(mapLabelLevelOpacity("detail-regional", 1.48)).toBe(0);
    expect(mapLabelLevelOpacity("detail-regional", 1.4)).toBe(1);
    expect(mapLabelLevelOpacity("detail-local", 1.4)).toBe(0);
    expect(mapLabelLevelOpacity("detail-local", 1.34)).toBe(1);
    expect(mapLabelLevelOpacity("local", 1.34)).toBe(0);
    expect(mapLabelLevelOpacity("local", 1.3)).toBe(1);
    expect(mapLabelLevelOpacity("detail-major", 1.54, true)).toBe(0);
    expect(mapLabelLevelOpacity("detail-major", 1.44, true)).toBe(1);
    expect(mapLabelLevelOpacity("local", 1.3, true)).toBe(1);
  });

  it("rejects far-side, offscreen, and safe-inset points", () => {
    expect(frontSideOpacity(0.9, 2)).toBeGreaterThan(0);
    expect(frontSideOpacity(0.5, 2)).toBe(0);
    const insets = { top: 20, right: 20, bottom: 20, left: 20 };
    expect(isScreenPointInSafeViewport(50, 50, 0, 100, 100, insets)).toBe(true);
    expect(isScreenPointInSafeViewport(19, 50, 0, 100, 100, insets)).toBe(false);
    expect(isScreenPointInSafeViewport(50, 81, 0, 100, 100, insets)).toBe(false);
    expect(isScreenPointInSafeViewport(50, 50, 1.01, 100, 100, insets)).toBe(false);
  });
});

const candidate = (id: string, x: number, semanticPriority: readonly number[] = [0]): ScreenLabelCandidate => ({
  id,
  x,
  y: 20,
  width: 10,
  height: 10,
  semanticPriority,
});

describe("map label collision placement", () => {
  it("lets higher semantic priority win and never accepts overlaps", () => {
    const accepted = placeMapLabels([
      candidate("low", 20, [2]),
      candidate("high", 20, [1]),
      candidate("separate", 60, [3]),
    ], { padding: 4, limit: 80 });
    expect(accepted.map(({ id }) => id)).toEqual(["high", "separate"]);
    for (let left = 0; left < accepted.length; left += 1) {
      for (let right = left + 1; right < accepted.length; right += 1) {
        expect(accepted[left].right <= accepted[right].left || accepted[right].right <= accepted[left].left
          || accepted[left].bottom <= accepted[right].top || accepted[right].bottom <= accepted[left].top).toBe(true);
      }
    }
  });

  it("retains a prior label only when semantic priority ties", () => {
    const tied = [candidate("alpha", 20, [1, 1]), candidate("beta", 20, [1, 1])];
    expect(placeMapLabels(tied, { padding: 4, limit: 80, previouslyAccepted: new Set(["beta"]) }).map(({ id }) => id)).toEqual(["beta"]);
    const unequal = [candidate("retained-low", 20, [2]), candidate("new-high", 20, [1])];
    expect(placeMapLabels(unequal, { padding: 4, limit: 80, previouslyAccepted: new Set(["retained-low"]) }).map(({ id }) => id)).toEqual(["new-high"]);
  });

  it("is deterministic, honors caps, padding, and stable duplicate-name IDs", () => {
    const many = Array.from({ length: 90 }, (_, index) => candidate(`ne:${index}`, index * 30, [0, index]));
    const first = placeMapLabels(many, { padding: 4, limit: 80 });
    const second = placeMapLabels([...many].reverse(), { padding: 4, limit: 80 });
    expect(first.map(({ id }) => id)).toEqual(second.map(({ id }) => id));
    expect(first).toHaveLength(80);
    expect(placeMapLabels([candidate("ne:portland-1", 10), candidate("ne:portland-2", 28)], { padding: 4, limit: 80 })).toHaveLength(2);
    expect(placeMapLabels([candidate("ne:portland-1", 10), candidate("ne:portland-2", 28)], { padding: 7, limit: 32 })).toHaveLength(1);
  });
});
