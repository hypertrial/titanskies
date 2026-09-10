import {
  FORECAST_DISPLAY_PALETTE_VERSION,
  forecastDisplayLut,
  forecastTextureScaleMax,
} from "@/data/forecastRaster";

export type ForecastRasterBuffers = {
  width: number;
  height: number;
  concentrations: Float32Array;
  weighted: Uint16Array;
  weights: Uint8Array;
  valid: Uint8Array;
  sourceCodes: Uint8Array;
  concentrationScale: number;
};

const packed = (r: number, g: number, b: number, a: number) => (((r * 256 + g) * 256 + b) * 256 + a) >>> 0;
// IEEE-754 float32 to float16. Kept independent of Three so the same decode
// kernel runs in the worker and in the compatibility fallback.
const halfFloatView = new Float32Array(1);
const halfIntView = new Uint32Array(halfFloatView.buffer);
const paletteData = new Map<string, ReturnType<typeof buildPalette>>();
function buildPalette(version: string) {
  const lut = forecastDisplayLut(version);
  const indexes = new Map<number, number>([
    [packed(0, 0, 0, 0), 0],
    ...lut.map((item, index) => [packed(...item.rgba as [number, number, number, number]), index + 1] as const),
  ]);
  const concentrations = Float32Array.from({ length: 256 }, (_, index) => index ? lut[index - 1]?.concentration ?? -1 : 0);
  return {
    indexes,
    entries: [...indexes].map(([color, index]) => ({ index, rgba: [(color >>> 24) & 255, (color >>> 16) & 255, (color >>> 8) & 255, color & 255] })),
    concentrations,
    weighted: Uint16Array.from(concentrations, (concentration) => toHalfFloat(Math.max(0, concentration) / forecastTextureScaleMax(version))),
    scale: forecastTextureScaleMax(version),
  };
}
function displayPalette(version = FORECAST_DISPLAY_PALETTE_VERSION) {
  const cached = paletteData.get(version);
  if (cached) return cached;
  const created = buildPalette(version);
  if (!created.entries.length) throw new Error("Unsupported smoke display palette.");
  paletteData.set(version, created);
  return created;
}
const maskEntries: Array<{ code: number; rgba: [number, number, number, number] }> = [
  { code: 0, rgba: [0, 0, 0, 0] },
  { code: 1, rgba: [32, 96, 176, 255] },
  { code: 2, rgba: [208, 88, 48, 255] },
  { code: 3, rgba: [71, 166, 139, 255] },
];
const maskCodes = new Map(maskEntries.map((entry) => [packed(...entry.rgba), entry.code]));

export const FORECAST_CONCENTRATIONS = (() => {
  return displayPalette().concentrations;
})();

export function toHalfFloat(value: number): number {
  halfFloatView[0] = Math.min(65_504, Math.max(-65_504, value));
  const bits = halfIntView[0];
  const sign = bits & 0x80000000 ? 0x8000 : 0;
  const exponent = ((bits >>> 23) & 0xff) - 127;
  const mantissa = bits & 0x7fffff;
  if (exponent < -27) return sign;
  if (exponent < -14) return sign | (0x0400 >> (-exponent - 14)) | (mantissa >> (-exponent - 1));
  if (exponent <= 15) return sign | ((exponent + 15) << 10) | (mantissa >> 13);
  return sign | 0x7c00 | (mantissa >> (exponent < 128 ? 24 : 13));
}

export function decodeForecastPixel(
  rgba: Uint8ClampedArray,
  maskRgba: Uint8ClampedArray,
  pixel: number,
  index: number,
  indexes: Uint8Array,
  valid: Uint8Array,
  sourceCodes: Uint8Array,
  paletteVersion = FORECAST_DISPLAY_PALETTE_VERSION,
): void {
  const palette = displayPalette(paletteVersion);
  decodeForecastPixelWithPalette(rgba, maskRgba, pixel, index, indexes, valid, sourceCodes, palette);
}

type DisplayPalette = ReturnType<typeof displayPalette>;

function decodeForecastPixelWithPalette(
  rgba: Uint8ClampedArray,
  maskRgba: Uint8ClampedArray,
  pixel: number,
  index: number,
  indexes: Uint8Array,
  valid: Uint8Array,
  sourceCodes: Uint8Array,
  palette: DisplayPalette,
): void {
  const textureColor = packed(rgba[pixel], rgba[pixel + 1], rgba[pixel + 2], rgba[pixel + 3]);
  const exact = palette.indexes.get(textureColor);
  const nearest = exact === undefined ? palette.entries.reduce<{ index: number; distance: number }>((best, entry) => {
    const distance = entry.rgba.reduce((sum, value, channel) => sum + Math.abs(value - rgba[pixel + channel]), 0);
    return distance < best.distance ? { index: entry.index, distance } : best;
  }, { index: -1, distance: Number.POSITIVE_INFINITY }) : null;
  const paletteIndex = exact ?? (nearest && nearest.distance <= 12 ? nearest.index : undefined);
  if (paletteIndex === undefined) throw new Error("Smoke texture uses an unknown display-palette color.");
  if (exact === undefined) palette.indexes.set(textureColor, paletteIndex);
  const maskColor = packed(maskRgba[pixel], maskRgba[pixel + 1], maskRgba[pixel + 2], maskRgba[pixel + 3]);
  const exactMaskCode = maskCodes.get(maskColor);
  const nearestMask = exactMaskCode === undefined ? maskEntries.reduce<{ code: number; distance: number }>((best, entry) => {
    const distance = entry.rgba.reduce((sum, value, channel) => sum + Math.abs(value - maskRgba[pixel + channel]), 0);
    return distance < best.distance ? { code: entry.code, distance } : best;
  }, { code: -1, distance: Number.POSITIVE_INFINITY }) : null;
  const maskCode = exactMaskCode ?? (nearestMask && nearestMask.distance <= 1 ? nearestMask.code : undefined);
  if (maskCode === undefined) throw new Error("Smoke contribution mask is malformed.");
  if (exactMaskCode === undefined) maskCodes.set(maskColor, maskCode);
  indexes[index] = paletteIndex;
  valid[index] = maskCode === 0 ? 0 : 255;
  sourceCodes[index] = maskCode;
}

export function decodeForecastPixelRange(
  rgba: Uint8ClampedArray,
  maskRgba: Uint8ClampedArray,
  indexes: Uint8Array,
  valid: Uint8Array,
  sourceCodes: Uint8Array,
  start = 0,
  end = indexes.length,
  paletteVersion = FORECAST_DISPLAY_PALETTE_VERSION,
): void {
  const palette = displayPalette(paletteVersion);
  for (let index = start, pixel = start * 4; index < end; index += 1, pixel += 4) {
    decodeForecastPixelWithPalette(rgba, maskRgba, pixel, index, indexes, valid, sourceCodes, palette);
  }
}

export function allocateForecastDecode(length: number) {
  return {
    indexes: new Uint8Array(length),
    valid: new Uint8Array(length),
    sourceCodes: new Uint8Array(length),
  };
}

export function allocateForecastBuffers(width: number, height: number, valid: Uint8Array, sourceCodes: Uint8Array, paletteVersion = FORECAST_DISPLAY_PALETTE_VERSION): ForecastRasterBuffers {
  const length = width * height;
  return {
    width,
    height,
    concentrations: new Float32Array(length),
    weighted: new Uint16Array(length),
    weights: new Uint8Array(length),
    valid,
    sourceCodes,
    concentrationScale: displayPalette(paletteVersion).scale,
  };
}

export function finalizeForecastDecodeRange(indexes: Uint8Array, buffers: ForecastRasterBuffers, start = 0, end = indexes.length, paletteVersion = FORECAST_DISPLAY_PALETTE_VERSION): void {
  const palette = displayPalette(paletteVersion);
  for (let index = start; index < end; index += 1) {
    const concentration = palette.concentrations[indexes[index]];
    buffers.concentrations[index] = concentration;
    const covered = Boolean(buffers.valid[index] && concentration >= 0);
    buffers.weights[index] = covered ? 255 : 0;
    buffers.weighted[index] = covered ? palette.weighted[indexes[index]] : 0;
  }
}

export function finalizeForecastDecode(width: number, height: number, indexes: Uint8Array, valid: Uint8Array, sourceCodes: Uint8Array, paletteVersion = FORECAST_DISPLAY_PALETTE_VERSION): ForecastRasterBuffers {
  const buffers = allocateForecastBuffers(width, height, valid, sourceCodes, paletteVersion);
  finalizeForecastDecodeRange(indexes, buffers, 0, indexes.length, paletteVersion);
  return buffers;
}

export function decodeForecastRgba(width: number, height: number, rgba: Uint8ClampedArray, maskRgba: Uint8ClampedArray, paletteVersion = FORECAST_DISPLAY_PALETTE_VERSION): ForecastRasterBuffers {
  if (rgba.length !== maskRgba.length || rgba.length !== width * height * 4) throw new Error("Smoke raster pixel data is malformed.");
  const decoded = allocateForecastDecode(width * height);
  const buffers = allocateForecastBuffers(width, height, decoded.valid, decoded.sourceCodes, paletteVersion);
  const palette = displayPalette(paletteVersion);
  for (let index = 0, pixel = 0; index < decoded.indexes.length; index += 1, pixel += 4) {
    decodeForecastPixelWithPalette(rgba, maskRgba, pixel, index, decoded.indexes, decoded.valid, decoded.sourceCodes, palette);
    const paletteIndex = decoded.indexes[index];
    const concentration = palette.concentrations[paletteIndex];
    buffers.concentrations[index] = concentration;
    const covered = Boolean(decoded.valid[index] && concentration >= 0);
    buffers.weights[index] = covered ? 255 : 0;
    buffers.weighted[index] = covered ? palette.weighted[paletteIndex] : 0;
  }
  return buffers;
}
