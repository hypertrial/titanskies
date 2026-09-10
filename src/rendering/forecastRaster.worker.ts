/// <reference lib="webworker" />

import { decodeForecastRgba, type ForecastRasterBuffers } from "./forecastRasterDecode";

export type ForecastRasterWorkerRequest = {
  type: "decode";
  id: number;
  textureUrl: string;
  maskUrl: string;
  expected: { width: number; height: number };
  paletteVersion: string;
};
export type ForecastRasterWorkerCancel = { type: "cancel"; id: number };
export type ForecastRasterWorkerSuccess = { type: "success"; id: number; buffers: ForecastRasterBuffers; durationMs: number };
export type ForecastRasterWorkerFailure = { type: "failure"; id: number; message: string; fallback?: boolean };
export type ForecastRasterWorkerMessage = ForecastRasterWorkerRequest | ForecastRasterWorkerCancel;
export type ForecastRasterWorkerReply = ForecastRasterWorkerSuccess | ForecastRasterWorkerFailure;

const controllers = new Map<number, AbortController>();
const IMAGE_TIMEOUT_MS = 15_000;

async function decodeBitmap(blob: Blob, signal: AbortSignal): Promise<ImageBitmap> {
  const pending = createImageBitmap(blob, { premultiplyAlpha: "none" });
  pending.then((image) => { if (signal.aborted) image.close(); }, () => undefined);
  if (signal.aborted) throw signal.reason;
  let onAbort: (() => void) | undefined;
  try {
    const image = await Promise.race([
      pending,
      new Promise<never>((_, reject) => {
        onAbort = () => reject(signal.reason);
        signal.addEventListener("abort", onAbort, { once: true });
      }),
    ]);
    if (signal.aborted) {
      image.close();
      throw signal.reason;
    }
    return image;
  } finally {
    if (onAbort) signal.removeEventListener("abort", onAbort);
  }
}

async function bitmap(url: string, signal: AbortSignal): Promise<ImageBitmap> {
  const controller = new AbortController();
  const abort = () => controller.abort(signal.reason);
  if (signal.aborted) abort();
  else signal.addEventListener("abort", abort, { once: true });
  const deadline = setTimeout(() => controller.abort(new DOMException("Timed out", "TimeoutError")), IMAGE_TIMEOUT_MS);
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) throw new Error(`Failed to fetch texture ${url}`);
    const blob = await response.blob();
    if (controller.signal.aborted) throw controller.signal.reason;
    return await decodeBitmap(blob, controller.signal);
  } finally {
    clearTimeout(deadline);
    signal.removeEventListener("abort", abort);
  }
}

function rgba(image: ImageBitmap): Uint8ClampedArray {
  const canvas = new OffscreenCanvas(image.width, image.height);
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) throw new Error("Unable to decode the smoke raster.");
  context.drawImage(image, 0, 0);
  return context.getImageData(0, 0, image.width, image.height).data;
}

self.onmessage = async (event: MessageEvent<ForecastRasterWorkerMessage>) => {
  if (event.data.type === "cancel") {
    controllers.get(event.data.id)?.abort();
    controllers.delete(event.data.id);
    return;
  }
  const { id, textureUrl, maskUrl, expected, paletteVersion } = event.data;
  if (typeof OffscreenCanvas === "undefined" || typeof createImageBitmap === "undefined") {
    const reply: ForecastRasterWorkerFailure = { type: "failure", id, message: "Worker image decoding is unavailable.", fallback: true };
    self.postMessage(reply);
    return;
  }
  const controller = new AbortController();
  controllers.set(id, controller);
  const start = performance.now();
  let image: ImageBitmap | null = null;
  let mask: ImageBitmap | null = null;
  try {
    const loaded = await Promise.allSettled([bitmap(textureUrl, controller.signal), bitmap(maskUrl, controller.signal)]);
    image = loaded[0].status === "fulfilled" ? loaded[0].value : null;
    mask = loaded[1].status === "fulfilled" ? loaded[1].value : null;
    const failure = loaded.find((result): result is PromiseRejectedResult => result.status === "rejected");
    if (failure) throw failure.reason;
    if (!image || !mask) throw new Error("Unable to decode the smoke raster.");
    if (image.width !== mask.width || image.height !== mask.height || image.width !== expected.width || image.height !== expected.height) {
      throw new Error("Smoke raster dimensions do not match the published grid.");
    }
    const buffers = decodeForecastRgba(image.width, image.height, rgba(image), rgba(mask), paletteVersion);
    const reply: ForecastRasterWorkerSuccess = { type: "success", id, buffers, durationMs: performance.now() - start };
    self.postMessage(reply, { transfer: [buffers.concentrations.buffer, buffers.weighted.buffer, buffers.weights.buffer, buffers.valid.buffer, buffers.sourceCodes.buffer] });
  } catch (error) {
    if (!controller.signal.aborted) {
      const reply: ForecastRasterWorkerFailure = { type: "failure", id, message: error instanceof Error ? error.message : "Unable to decode the smoke raster." };
      self.postMessage(reply);
    }
  } finally {
    image?.close();
    mask?.close();
    controllers.delete(id);
  }
};
