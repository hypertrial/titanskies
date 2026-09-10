import type { ProductView } from "./contextSchema";
import type { LayerVisibility } from "./ui";

export const CONTEXT_DRAWER_OUTSIDE_IGNORE = "[data-map-keyboard], .playback-dock";
export const EXPLORER_PLAYBACK_KEY_IGNORE = "input, button, a, dialog, [role='dialog'], [contenteditable='true'], [data-map-keyboard]";

export function isContextDrawerOutsideIgnored(target: EventTarget | null): boolean {
  if (!target || typeof (target as Element).closest !== "function") return false;
  return Boolean((target as Element).closest(CONTEXT_DRAWER_OUTSIDE_IGNORE));
}

export function shouldIgnoreExplorerPlaybackKeys(target: EventTarget | null): boolean {
  if (!target || typeof (target as Element).closest !== "function") return true;
  return Boolean((target as Element).closest(EXPLORER_PLAYBACK_KEY_IGNORE));
}

export type ContextSurface = "top-conditions" | "layers" | "legend" | "selection" | "mobile-actions" | "source-status" | "install" | null;

export type ContextSurfaceAction =
  | { type: "open"; surface: Exclude<ContextSurface, null> }
  | { type: "close" }
  | { type: "close-selection" };

export function contextSurfaceReducer(state: ContextSurface, action: ContextSurfaceAction): ContextSurface {
  if (action.type === "open") return action.surface;
  if (action.type === "close-selection") return state === "selection" ? null : state;
  return null;
}

export function visibleMapLayers(view: ProductView, incidentsVisible: boolean): LayerVisibility {
  return {
    forecast: view === "forecast",
    air: view === "air",
    incidents: view === "forecast" && incidentsVisible,
  };
}
