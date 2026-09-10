export type MarkerVisualState = { selected: boolean; hovered: boolean };

export function markerEmphasis({ selected, hovered }: MarkerVisualState): number {
  return selected ? 1 : hovered ? 0.72 : 0;
}

export function airMarkerPointSize(relativeSeverity: number, clusterCount: number, state: MarkerVisualState): number {
  const boundedSeverity = Math.max(0, Math.min(1, relativeSeverity));
  const clusterBoost = Math.min(2, Math.max(0, clusterCount - 1));
  return 8 + boundedSeverity * 4 + clusterBoost + markerEmphasis(state) * 2;
}
