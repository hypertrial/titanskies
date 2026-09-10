export const INSPECT_CLICK_MOUSE_PX = 6;
export const INSPECT_CLICK_TOUCH_PX = 12;

export type InspectPointerPoint = {
  x: number;
  y: number;
};

export type InspectPointerEvent = {
  clientX?: number;
  clientY?: number;
  pointerType?: string;
  nativeEvent?: { clientX?: number; clientY?: number; pointerType?: string };
};

export type InspectPointer = {
  point: InspectPointerPoint;
  pointerType: string;
};

export function inspectClickThreshold(pointerType: string): number {
  return pointerType === "touch" || pointerType === "pen" ? INSPECT_CLICK_TOUCH_PX : INSPECT_CLICK_MOUSE_PX;
}

export function inspectPointerFromEvent(event: InspectPointerEvent): InspectPointer | null {
  const source = event.nativeEvent ?? event;
  const x = source.clientX ?? event.clientX;
  const y = source.clientY ?? event.clientY;
  if (typeof x !== "number" || typeof y !== "number" || !Number.isFinite(x) || !Number.isFinite(y)) return null;
  return { point: { x, y }, pointerType: source.pointerType || event.pointerType || "mouse" };
}

export function isInspectClick(
  down: InspectPointerPoint | null | undefined,
  up: InspectPointerPoint,
  pointerType = "mouse",
): boolean {
  if (!down) return true;
  const dx = up.x - down.x;
  const dy = up.y - down.y;
  const limit = inspectClickThreshold(pointerType);
  return dx * dx + dy * dy <= limit * limit;
}
