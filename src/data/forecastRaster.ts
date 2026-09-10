import paletteV1 from "../../shared/smoke-display-palette-v1.json";
import paletteV2 from "../../shared/smoke-display-palette-v2.json";
import { CONTEXT_BOUNDS } from "./contextSchema";
import type { ForecastSourceLabel } from "./contextSchema";

type DisplayPalette = typeof paletteV2 | typeof paletteV1;
const DISPLAY_PALETTES = new Map<string, DisplayPalette>([
  [paletteV1.processingVersion, paletteV1],
  [paletteV2.processingVersion, paletteV2],
]);
const MASK_COLORS = paletteV2.mask as Record<ForecastSourceLabel, number[]>;
export const FORECAST_DISPLAY_PALETTE_VERSION = paletteV2.processingVersion;
export const LEGACY_FORECAST_DISPLAY_PALETTE_VERSION = paletteV1.processingVersion;
export const forecastDisplayPalette = (version = FORECAST_DISPLAY_PALETTE_VERSION) => DISPLAY_PALETTES.get(version) ?? null;
export const forecastDisplayLut = (version = FORECAST_DISPLAY_PALETTE_VERSION) => {
  const palette = forecastDisplayPalette(version);
  if (!palette) return [];
  return (palette.thresholds as Array<{ min: number; max: number; rgba: number[]; rgbaEnd?: number[] }>).flatMap((stop) => {
    const end = stop.rgbaEnd ?? stop.rgba;
    if (stop.rgba[3] === 0) return [];
    const steps = stop.rgba.every((value, index) => value === end[index]) ? 1 : palette.lutStepsPerInterval;
    return Array.from({ length: steps }, (_, index) => {
      const t = steps === 1 ? 0 : index / (steps - 1);
      return {
        rgba: stop.rgba.map((value, channel) => Math.round(value + (end[channel] - value) * t)),
        concentration: stop.min + (stop.max - stop.min) * t,
      };
    });
  });
};
export const FORECAST_DISPLAY_LUT = forecastDisplayLut();
export const forecastTextureScaleMax = (version = FORECAST_DISPLAY_PALETTE_VERSION): number => {
  const palette = forecastDisplayPalette(version);
  return palette && "textureScaleMax" in palette ? Number(palette.textureScaleMax) : 250;
};

const FORECAST_LEGEND_TICKS: Record<string, readonly number[]> = {
  [LEGACY_FORECAST_DISPLAY_PALETTE_VERSION]: [1, 60, 100, 150, 250],
  [FORECAST_DISPLAY_PALETTE_VERSION]: [1, 100, 250, 500, 1000],
};

export function forecastLegendScale(version = FORECAST_DISPLAY_PALETTE_VERSION) {
  const palette = forecastDisplayPalette(version);
  const values = FORECAST_LEGEND_TICKS[version];
  if (!palette || !values) return null;
  const maximum = forecastTextureScaleMax(version);
  return {
    units: palette.units,
    maximum,
    ticks: values.map((value) => ({
      value,
      label: `${value}${value === maximum ? "+" : ""}`,
      position: (value - 1) / (maximum - 1) * 100,
    })),
  };
}

export function integratedSourceLabel(source?: ForecastSourceLabel): string {
  if (source === "hrrr") return "Higher-detail U.S. guidance";
  if (source === "firework") return "Canadian guidance";
  if (source === "combined") return "Canadian guidance + higher-detail U.S. enhancement";
  if (source === "none") return "No forecast value";
  return "Canadian guidance with higher-detail U.S. enhancement";
}

export function forecastMaskLabel(r: number, g: number, b: number, a: number): ForecastSourceLabel {
  if (a < 8) return "none";
  let match: ForecastSourceLabel = "none";
  let best = 36;
  for (const label of ["firework", "hrrr", "combined"] as const) {
    const [cr, cg, cb] = MASK_COLORS[label];
    const distance = Math.abs(r - cr) + Math.abs(g - cg) + Math.abs(b - cb);
    if (distance < best) {
      best = distance;
      match = label;
    }
  }
  return match;
}

export function forecastConcentrationForColor(r: number, g: number, b: number, a: number, paletteVersion = FORECAST_DISPLAY_PALETTE_VERSION): number | null {
  if (a < 8) return null;
  const pixel = [r, g, b, a];
  const lut = forecastDisplayLut(paletteVersion);
  let nearest = lut[0];
  let distance = Number.POSITIVE_INFINITY;
  for (const candidate of lut) {
    const next = candidate.rgba.reduce((sum, value, index) => sum + Math.abs(value - pixel[index]), 0);
    if (next < distance) {
      nearest = candidate;
      distance = next;
    }
  }
  return nearest?.concentration ?? null;
}

type ForecastBounds = { west: number; south: number; east: number; north: number };
export type DecodedForecastRasterData = { width: number; height: number; concentrations: Float32Array; sourceCodes: Uint8Array };

function decodedCoordinates(data: DecodedForecastRasterData, lon: number, lat: number, bounds: ForecastBounds) {
  const x = (lon - bounds.west) / (bounds.east - bounds.west) * (data.width - 1);
  const y = (bounds.north - lat) / (bounds.north - bounds.south) * (data.height - 1);
  return x < 0 || y < 0 || x > data.width - 1 || y > data.height - 1 ? null : { x, y };
}

function decodedSource(data: DecodedForecastRasterData, lon: number, lat: number, bounds: ForecastBounds): ForecastSourceLabel {
  const point = decodedCoordinates(data, lon, lat, bounds);
  if (!point) return "none";
  const x0 = Math.floor(point.x); const y0 = Math.floor(point.y);
  const tx = point.x - x0; const ty = point.y - y0;
  let code = 0; let bestWeight = 0;
  for (let dy = 0; dy <= 1; dy += 1) for (let dx = 0; dx <= 1; dx += 1) {
    const x = Math.min(data.width - 1, x0 + dx); const y = Math.min(data.height - 1, y0 + dy);
    const nextCode = data.sourceCodes[y * data.width + x];
    const nextWeight = (dx ? tx : 1 - tx) * (dy ? ty : 1 - ty);
    if (nextCode && nextWeight > bestWeight) { code = nextCode; bestWeight = nextWeight; }
  }
  return code === 1 ? "firework" : code === 2 ? "hrrr" : code === 3 ? "combined" : "none";
}

export function sampleDecodedForecastMask(data: DecodedForecastRasterData, lon: number, lat: number, bounds: ForecastBounds = CONTEXT_BOUNDS): ForecastSourceLabel {
  return decodedSource(data, lon, lat, bounds);
}

function decodedConcentration(data: DecodedForecastRasterData, lon: number, lat: number, bounds: ForecastBounds): number | null {
  const point = decodedCoordinates(data, lon, lat, bounds);
  if (!point || decodedSource(data, lon, lat, bounds) === "none") return null;
  const x0 = Math.floor(point.x); const y0 = Math.floor(point.y);
  const tx = point.x - x0; const ty = point.y - y0;
  let total = 0; let weight = 0;
  for (let dy = 0; dy <= 1; dy += 1) for (let dx = 0; dx <= 1; dx += 1) {
    const x = Math.min(data.width - 1, x0 + dx); const y = Math.min(data.height - 1, y0 + dy);
    const index = y * data.width + x;
    const nextWeight = (dx ? tx : 1 - tx) * (dy ? ty : 1 - ty);
    if (data.sourceCodes[index]) {
      total += Math.max(0, data.concentrations[index]) * nextWeight;
      weight += nextWeight;
    }
  }
  return weight > 0 ? total / weight : null;
}

export function sampleDecodedForecastConcentration(data: DecodedForecastRasterData, lon: number, lat: number, bounds: ForecastBounds = CONTEXT_BOUNDS): number | null {
  return decodedConcentration(data, lon, lat, bounds);
}

function sampleForecastPixel(
  image: ImageBitmap | HTMLImageElement,
  lon: number,
  lat: number,
  bounds: ForecastBounds = CONTEXT_BOUNDS,
): number[] | null {
  const width = "width" in image ? image.width : 0;
  const height = "height" in image ? image.height : 0;
  if (!width || !height) return null;
  const x = Math.round((lon - bounds.west) / (bounds.east - bounds.west) * (width - 1));
  const y = Math.round((bounds.north - lat) / (bounds.north - bounds.south) * (height - 1));
  if (x < 0 || y < 0 || x >= width || y >= height) return null;
  const canvas = document.createElement("canvas");
  canvas.width = 1;
  canvas.height = 1;
  const context = canvas.getContext("2d");
  if (!context) return null;
  context.drawImage(image, x, y, 1, 1, 0, 0, 1, 1);
  return [...context.getImageData(0, 0, 1, 1).data];
}

export function sampleForecastMask(
  image: ImageBitmap | HTMLImageElement,
  lon: number,
  lat: number,
  bounds: ForecastBounds = CONTEXT_BOUNDS,
): ForecastSourceLabel {
  const [r, g, b, a] = sampleForecastPixel(image, lon, lat, bounds) ?? [0, 0, 0, 0];
  return forecastMaskLabel(r, g, b, a);
}

export function sampleForecastConcentration(
  image: ImageBitmap | HTMLImageElement,
  lon: number,
  lat: number,
  bounds: ForecastBounds = CONTEXT_BOUNDS,
  paletteVersion = FORECAST_DISPLAY_PALETTE_VERSION,
): number | null {
  const pixel = sampleForecastPixel(image, lon, lat, bounds);
  if (!pixel) return null;
  return forecastConcentrationForColor(pixel[0], pixel[1], pixel[2], pixel[3], paletteVersion);
}

export function interpolateForecastConcentration(
  from: number | null,
  to: number | null,
  t: number,
  fromCovered = from != null,
  toCovered = to != null,
): number | null {
  if (!fromCovered && !toCovered) return null;
  if (!fromCovered) return to ?? 0;
  if (!toCovered) return from ?? 0;
  const mix = Math.min(1, Math.max(0, t));
  return (from ?? 0) + ((to ?? 0) - (from ?? 0)) * mix;
}
