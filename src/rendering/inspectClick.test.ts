import { describe, expect, it } from "vitest";
import { INSPECT_CLICK_MOUSE_PX, INSPECT_CLICK_TOUCH_PX, inspectClickThreshold, inspectPointerFromEvent, isInspectClick } from "./inspectClick";

describe("inspect click versus drag", () => {
  it("accepts a stationary mouse click and rejects movement past the threshold", () => {
    expect(isInspectClick({ x: 100, y: 80 }, { x: 100, y: 80 }, "mouse")).toBe(true);
    expect(isInspectClick({ x: 100, y: 80 }, { x: 104, y: 83 }, "mouse")).toBe(true);
    expect(isInspectClick({ x: 100, y: 80 }, { x: 100 + INSPECT_CLICK_MOUSE_PX + 1, y: 80 }, "mouse")).toBe(false);
    expect(isInspectClick(null, { x: 100, y: 80 }, "mouse")).toBe(true);
    expect(inspectPointerFromEvent({ nativeEvent: { clientX: 12, clientY: 40, pointerType: "touch" } })).toEqual({
      point: { x: 12, y: 40 },
      pointerType: "touch",
    });
    expect(inspectPointerFromEvent({ clientX: 8, clientY: 9, nativeEvent: { pointerType: "pen" } })).toEqual({
      point: { x: 8, y: 9 },
      pointerType: "pen",
    });
    expect(inspectPointerFromEvent({ clientX: 3, clientY: 4 })).toEqual({
      point: { x: 3, y: 4 },
      pointerType: "mouse",
    });
    expect(inspectPointerFromEvent({})).toBeNull();
  });

  it("uses a larger touch threshold and treats pen like touch", () => {
    expect(inspectClickThreshold("mouse")).toBe(INSPECT_CLICK_MOUSE_PX);
    expect(inspectClickThreshold("touch")).toBe(INSPECT_CLICK_TOUCH_PX);
    expect(inspectClickThreshold("pen")).toBe(INSPECT_CLICK_TOUCH_PX);
    expect(isInspectClick({ x: 40, y: 40 }, { x: 40 + INSPECT_CLICK_MOUSE_PX + 1, y: 40 }, "touch")).toBe(true);
    expect(isInspectClick({ x: 40, y: 40 }, { x: 40 + INSPECT_CLICK_TOUCH_PX + 1, y: 40 }, "touch")).toBe(false);
  });
});
