import {
  allocateForecastBuffers,
  allocateForecastDecode,
  decodeForecastPixelRange,
  finalizeForecastDecodeRange,
  type ForecastRasterBuffers,
} from "./forecastRasterDecode";
import { DataTexture, HalfFloatType, LinearFilter, LinearMipmapLinearFilter, RedFormat, UnsignedByteType } from "three";

export { FORECAST_CONCENTRATIONS } from "./forecastRasterDecode";

export type ForecastRaster = {
  scalarTexture: DataTexture;
  weightTexture: DataTexture;
  width: number;
  height: number;
  concentrations: Float32Array;
  sourceCodes: Uint8Array;
  concentrationScale: number;
};

function pixels(image: ImageBitmap | HTMLImageElement): ImageData {
  const canvas = document.createElement("canvas");
  canvas.width = image.width;
  canvas.height = image.height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) throw new Error("Unable to decode the smoke raster.");
  context.drawImage(image, 0, 0);
  return context.getImageData(0, 0, canvas.width, canvas.height);
}

function byteTexture(data: Uint8Array, width: number, height: number): DataTexture {
  const texture = new DataTexture(data, width, height, RedFormat, UnsignedByteType);
  texture.flipY = false;
  texture.generateMipmaps = true;
  texture.minFilter = LinearMipmapLinearFilter;
  texture.magFilter = LinearFilter;
  texture.needsUpdate = true;
  return texture;
}

function halfTexture(data: Uint16Array, width: number, height: number): DataTexture {
  const texture = new DataTexture(data, width, height, RedFormat, HalfFloatType);
  texture.flipY = false;
  texture.generateMipmaps = true;
  texture.minFilter = LinearMipmapLinearFilter;
  texture.magFilter = LinearFilter;
  texture.needsUpdate = true;
  return texture;
}

export function decodeForecastPixels(rgba: Uint8ClampedArray, maskRgba: Uint8ClampedArray): { indexes: Uint8Array; valid: Uint8Array; sourceCodes: Uint8Array } {
  if (rgba.length !== maskRgba.length || rgba.length % 4 !== 0) throw new Error("Smoke raster pixel data is malformed.");
  const decoded = allocateForecastDecode(rgba.length / 4);
  decodeForecastPixelRange(rgba, maskRgba, decoded.indexes, decoded.valid, decoded.sourceCodes);
  return decoded;
}

const DECODE_CHUNK_PIXELS = 32_768;

function yieldToBrowser(): Promise<void> {
  const browserScheduler = (globalThis as typeof globalThis & { scheduler?: { yield?: () => Promise<void> } }).scheduler;
  if (browserScheduler?.yield) return browserScheduler.yield();
  return new Promise((resolve) => globalThis.setTimeout(resolve, 0));
}

function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) throw new DOMException("Smoke raster decoding was cancelled.", "AbortError");
}

export async function decodeForecastRasterAsync(
  image: ImageBitmap | HTMLImageElement,
  mask: ImageBitmap | HTMLImageElement,
  expected?: { width: number; height: number },
  signal?: AbortSignal,
  paletteVersion?: string,
): Promise<ForecastRaster> {
  validateForecastRasterDimensions(image, mask, expected);
  const rgba = pixels(image).data;
  const maskRgba = pixels(mask).data;
  const decoded = allocateForecastDecode(rgba.length / 4);
  for (let start = 0; start < decoded.indexes.length; start += DECODE_CHUNK_PIXELS) {
    throwIfAborted(signal);
    const end = Math.min(decoded.indexes.length, start + DECODE_CHUNK_PIXELS);
    decodeForecastPixelRange(rgba, maskRgba, decoded.indexes, decoded.valid, decoded.sourceCodes, start, end, paletteVersion);
    if (end < decoded.indexes.length) await yieldToBrowser();
  }
  const buffers = allocateForecastBuffers(image.width, image.height, decoded.valid, decoded.sourceCodes, paletteVersion);
  for (let start = 0; start < decoded.indexes.length; start += DECODE_CHUNK_PIXELS) {
    throwIfAborted(signal);
    const end = Math.min(decoded.indexes.length, start + DECODE_CHUNK_PIXELS);
    finalizeForecastDecodeRange(decoded.indexes, buffers, start, end, paletteVersion);
    if (end < decoded.indexes.length) await yieldToBrowser();
  }
  throwIfAborted(signal);
  return rasterFromBuffers(buffers);
}

export function rasterFromBuffers(buffers: ForecastRasterBuffers): ForecastRaster {
  return {
    scalarTexture: halfTexture(buffers.weighted, buffers.width, buffers.height),
    weightTexture: byteTexture(buffers.weights, buffers.width, buffers.height),
    width: buffers.width,
    height: buffers.height,
    concentrations: buffers.concentrations,
    sourceCodes: buffers.sourceCodes,
    concentrationScale: buffers.concentrationScale,
  };
}

export function validateForecastRasterDimensions(
  image: Pick<ImageBitmap | HTMLImageElement, "width" | "height">,
  mask: Pick<ImageBitmap | HTMLImageElement, "width" | "height">,
  expected?: { width: number; height: number },
): void {
  if (image.width !== mask.width || image.height !== mask.height || !image.width || !image.height) {
    throw new Error("Smoke texture and contribution mask dimensions differ.");
  }
  if (expected && (image.width !== expected.width || image.height !== expected.height)) {
    throw new Error("Smoke raster dimensions do not match the published grid.");
  }
}

export function disposeForecastRaster(raster: ForecastRaster): void {
  raster.scalarTexture.dispose();
  raster.weightTexture.dispose();
}
