import type { MapLabelTier } from "@/data/ui";

const clampOpacity = (value: number) => Math.max(0, Math.min(1, value));

export function mapLabelLevelOpacity(tier: MapLabelTier, cameraDistance: number, compact = false): number {
  if (tier === "overview") return 1;
  if (tier === "primary") return clampOpacity((2.4 - cameraDistance) / 0.28);
  if (tier === "secondary") return clampOpacity((1.82 - cameraDistance) / 0.22);
  const delay = compact && tier !== "local" ? 0.04 : 0;
  const bands = {
    "detail-major": [1.58 - delay, 1.48 - delay],
    "detail-regional": [1.48 - delay, 1.4 - delay],
    "detail-local": [1.4 - delay, 1.34 - delay],
    local: [1.34, 1.3],
  };
  const [begin, full] = bands[tier];
  return clampOpacity((begin - cameraDistance) / (begin - full));
}

export function frontSideOpacity(normalDotCamera: number, cameraDistance: number): number {
  const horizon = 1 / cameraDistance;
  if (normalDotCamera <= horizon + 0.025) return 0;
  return clampOpacity((normalDotCamera - (horizon + 0.005)) / 0.08);
}

export function isScreenPointInSafeViewport(
  x: number,
  y: number,
  projectedZ: number,
  width: number,
  height: number,
  insets: { top: number; right: number; bottom: number; left: number },
): boolean {
  return projectedZ >= -1
    && projectedZ <= 1
    && x >= insets.left
    && x <= width - insets.right
    && y >= insets.top
    && y <= height - insets.bottom;
}

export type ScreenLabelCandidate = {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  semanticPriority: readonly number[];
  countsTowardLimit?: boolean;
};

export type ScreenLabelRect = ScreenLabelCandidate & {
  left: number;
  right: number;
  top: number;
  bottom: number;
};

export type LabelPlacementOptions = {
  padding: number;
  limit: number;
  previouslyAccepted?: ReadonlySet<string>;
};

function comparePriority(left: ScreenLabelCandidate, right: ScreenLabelCandidate, retained: ReadonlySet<string>): number {
  const length = Math.max(left.semanticPriority.length, right.semanticPriority.length);
  for (let index = 0; index < length; index += 1) {
    const difference = (left.semanticPriority[index] ?? 0) - (right.semanticPriority[index] ?? 0);
    if (difference) return difference;
  }
  const retention = Number(retained.has(right.id)) - Number(retained.has(left.id));
  return retention || left.id.localeCompare(right.id);
}

const overlaps = (left: ScreenLabelRect, right: ScreenLabelRect) => (
  left.left < right.right
  && left.right > right.left
  && left.top < right.bottom
  && left.bottom > right.top
);

export function placeMapLabels(candidates: readonly ScreenLabelCandidate[], options: LabelPlacementOptions): ScreenLabelRect[] {
  const retained = options.previouslyAccepted ?? new Set<string>();
  const accepted: ScreenLabelRect[] = [];
  let counted = 0;
  const sorted = [...candidates].sort((left, right) => comparePriority(left, right, retained));
  for (const candidate of sorted) {
    const countsTowardLimit = candidate.countsTowardLimit !== false;
    if (countsTowardLimit && counted >= options.limit) continue;
    const halfWidth = candidate.width / 2 + options.padding;
    const halfHeight = candidate.height / 2 + options.padding;
    const rect: ScreenLabelRect = {
      ...candidate,
      left: candidate.x - halfWidth,
      right: candidate.x + halfWidth,
      top: candidate.y - halfHeight,
      bottom: candidate.y + halfHeight,
    };
    if (!accepted.some((placed) => overlaps(rect, placed))) {
      accepted.push(rect);
      if (countsTowardLimit) counted += 1;
    }
  }
  return accepted;
}
