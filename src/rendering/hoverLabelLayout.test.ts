import { describe, expect, it } from "vitest";
import { HOVER_OFFSET_X, HOVER_OFFSET_Y, HOVER_PAD, inspectLabelInsets, placeHoverLabel } from "./hoverLabelLayout";

describe("hover label placement", () => {
  it("offsets above and to the right when the panel fits", () => {
    expect(placeHoverLabel(200, 180, 120, 40, 1280, 720)).toEqual({
      left: 200 + HOVER_OFFSET_X,
      top: 180 - 40 - HOVER_OFFSET_Y,
    });
  });

  it("flips left near the right edge and below near the top", () => {
    expect(placeHoverLabel(1240, 180, 120, 40, 1280, 720).left).toBe(1240 - 120 - HOVER_OFFSET_X);
    expect(placeHoverLabel(200, 20, 120, 40, 1280, 720).top).toBe(20 + HOVER_OFFSET_Y);
  });

  it("clamps a corner placement inside the padded viewport", () => {
    const placed = placeHoverLabel(1270, 8, 200, 60, 1280, 720);
    expect(placed.left).toBeGreaterThanOrEqual(HOVER_PAD);
    expect(placed.top).toBeGreaterThanOrEqual(HOVER_PAD);
    expect(placed.left + 200).toBeLessThanOrEqual(1280 - HOVER_PAD);
    expect(placed.top + 60).toBeLessThanOrEqual(720 - HOVER_PAD);
  });

  it("keeps inspect chips out of the playback dock, details drawer, and short-landscape drawer", () => {
    const desktop = inspectLabelInsets(1280, false, false);
    const placed = placeHoverLabel(640, 680, 112, 36, 1280, 720, desktop);
    expect(placed.top + 36).toBeLessThanOrEqual(720 - HOVER_PAD - desktop.bottom);
    const besideDrawer = placeHoverLabel(1200, 200, 112, 36, 1280, 720, desktop);
    expect(besideDrawer.left + 112).toBeLessThanOrEqual(1280 - HOVER_PAD - desktop.right);
    expect(desktop.right).toBeGreaterThanOrEqual(348);
    const landscape = inspectLabelInsets(700, false, true);
    const besideLandscapeDrawer = placeHoverLabel(520, 180, 112, 36, 700, 390, landscape);
    expect(besideLandscapeDrawer.left + 112).toBeLessThanOrEqual(700 - HOVER_PAD - landscape.right);
  });
});
