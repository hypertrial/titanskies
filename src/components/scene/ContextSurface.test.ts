import { describe, expect, it } from "vitest";
import { advanceDetailMix, DETAIL_FADE_SECONDS } from "./ContextSurface";

describe("regional detail fades", () => {
  it("reaches both endpoints in the documented duration", () => {
    expect(advanceDetailMix(0, 1, DETAIL_FADE_SECONDS / 2)).toBe(0.5);
    expect(advanceDetailMix(0.5, 1, DETAIL_FADE_SECONDS / 2)).toBe(1);
    expect(advanceDetailMix(1, 0, DETAIL_FADE_SECONDS / 2)).toBe(0.5);
    expect(advanceDetailMix(0.5, 0, DETAIL_FADE_SECONDS / 2)).toBe(0);
  });

  it("clamps stalled frames and reduced motion to the target", () => {
    expect(advanceDetailMix(0.2, 1, 10)).toBe(1);
    expect(advanceDetailMix(0.8, 0, 10)).toBe(0);
    expect(advanceDetailMix(0.2, 1, 0.001, true)).toBe(1);
  });
});
