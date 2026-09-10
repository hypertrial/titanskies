export const TEMPORAL_SHADER_SAMPLE_COUNT = 2;

export type SurfaceSample = { concentration: number; covered: boolean; spatialCoverage: number };

export function interpolateSurfaceSample(from: SurfaceSample, to: SurfaceSample, mix: number): SurfaceSample | null {
  if (!from.covered && !to.covered) return null;
  if (!from.covered) return to;
  if (!to.covered) return from;
  const t = Math.min(1, Math.max(0, mix));
  return {
    concentration: from.concentration + (to.concentration - from.concentration) * t,
    covered: true,
    spatialCoverage: from.spatialCoverage + (to.spatialCoverage - from.spatialCoverage) * t,
  };
}

export function subThresholdAlpha(concentration: number): number {
  return 40 / 255 * Math.min(1, Math.max(0, concentration));
}
