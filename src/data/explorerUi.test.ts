import { describe, expect, it } from "vitest";
import { contextSurfaceReducer, isContextDrawerOutsideIgnored, shouldIgnoreExplorerPlaybackKeys, visibleMapLayers, type ContextSurface } from "./explorerUi";

describe("map-first explorer UI state", () => {
  it("replaces the current contextual surface instead of stacking overlays", () => {
    let state: ContextSurface = null;
    state = contextSurfaceReducer(state, { type: "open", surface: "top-conditions" });
    expect(state).toBe("top-conditions");
    state = contextSurfaceReducer(state, { type: "open", surface: "selection" });
    expect(state).toBe("selection");
    state = contextSurfaceReducer(state, { type: "open", surface: "legend" });
    expect(state).toBe("legend");
    state = contextSurfaceReducer(state, { type: "open", surface: "source-status" });
    expect(state).toBe("source-status");
    expect(contextSurfaceReducer(state, { type: "close-selection" })).toBe("source-status");
    expect(contextSurfaceReducer("selection", { type: "close-selection" })).toBeNull();
    expect(contextSurfaceReducer(state, { type: "close" })).toBeNull();
  });

  it("keeps each product's primary data intrinsic and wildfires optional", () => {
    expect(visibleMapLayers("forecast", false)).toEqual({ forecast: true, air: false, incidents: false });
    expect(visibleMapLayers("forecast", true)).toEqual({ forecast: true, air: false, incidents: true });
    expect(visibleMapLayers("air", true)).toEqual({ forecast: false, air: true, incidents: false });
  });

  it("keeps inspect and other drawers open when playback controls receive the pointer", () => {
    const hit = (token: string) => ({
      closest: (selector: string) => selector.includes(token) ? {} : null,
    }) as unknown as EventTarget;
    expect(isContextDrawerOutsideIgnored(hit(".playback-dock"))).toBe(true);
    expect(isContextDrawerOutsideIgnored(hit("[data-map-keyboard]"))).toBe(true);
    expect(isContextDrawerOutsideIgnored(hit(".toolbar-action"))).toBe(false);
    expect(isContextDrawerOutsideIgnored(null)).toBe(false);
  });

  it("ignores Space and Arrow playback keys inside a role=dialog drawer", () => {
    const hit = (token: string) => ({
      closest: (selector: string) => selector.split(",").map((part) => part.trim()).includes(token) ? {} : null,
    }) as unknown as EventTarget;
    expect(shouldIgnoreExplorerPlaybackKeys(hit("[role='dialog']"))).toBe(true);
    expect(shouldIgnoreExplorerPlaybackKeys(hit("dialog"))).toBe(true);
    expect(shouldIgnoreExplorerPlaybackKeys(hit("button"))).toBe(true);
    expect(shouldIgnoreExplorerPlaybackKeys(hit("input"))).toBe(true);
    expect(shouldIgnoreExplorerPlaybackKeys(hit("[contenteditable='true']"))).toBe(true);
    expect(shouldIgnoreExplorerPlaybackKeys(hit("[data-map-keyboard]"))).toBe(true);
    expect(shouldIgnoreExplorerPlaybackKeys(hit(".toolbar-action"))).toBe(false);
    expect(shouldIgnoreExplorerPlaybackKeys(null)).toBe(true);
    expect(shouldIgnoreExplorerPlaybackKeys({} as EventTarget)).toBe(true);
  });
});
