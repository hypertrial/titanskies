export const HOVER_PAD = 12;
export const HOVER_OFFSET_X = 14;
export const HOVER_OFFSET_Y = 10;

export type LabelSafeInsets = {
  top?: number;
  right?: number;
  bottom?: number;
  left?: number;
};

export function inspectLabelInsets(width: number, mobile: boolean, shortLandscape: boolean): Required<LabelSafeInsets> {
  if (shortLandscape) return { top: 56, right: 332, bottom: 72, left: 12 };
  if (mobile || width < 768) return { top: 118, right: 12, bottom: 148, left: 12 };
  const drawerWidth = Math.min(380, Math.max(320, width * 0.28));
  return { top: 80, right: 16 + drawerWidth + 12, bottom: 92, left: 16 };
}

export function placeHoverLabel(
  x: number,
  y: number,
  width: number,
  height: number,
  viewportWidth: number,
  viewportHeight: number,
  insets: LabelSafeInsets = {},
) {
  const padLeft = HOVER_PAD + (insets.left ?? 0);
  const padRight = HOVER_PAD + (insets.right ?? 0);
  const padTop = HOVER_PAD + (insets.top ?? 0);
  const padBottom = HOVER_PAD + (insets.bottom ?? 0);
  const maxX = Math.max(padLeft, viewportWidth - width - padRight);
  const maxY = Math.max(padTop, viewportHeight - height - padBottom);
  let left = x + HOVER_OFFSET_X;
  let top = y - height - HOVER_OFFSET_Y;
  if (left > maxX) left = x - width - HOVER_OFFSET_X;
  if (top < padTop) top = y + HOVER_OFFSET_Y;
  return {
    left: Math.min(maxX, Math.max(padLeft, left)),
    top: Math.min(maxY, Math.max(padTop, top)),
  };
}
