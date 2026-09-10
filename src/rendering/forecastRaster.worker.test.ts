import { afterEach, expect, it, vi } from "vitest";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.resetModules();
});

it("aborts stalled worker image requests at the fixed deadline", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("AbortSignal", {});
  const posted = vi.fn();
  const worker = { postMessage: posted, onmessage: null as ((event: MessageEvent) => Promise<void>) | null };
  vi.stubGlobal("self", worker);
  vi.stubGlobal("OffscreenCanvas", class {});
  vi.stubGlobal("createImageBitmap", vi.fn());

  vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) => new Promise((_resolve, reject) => {
    init?.signal?.addEventListener("abort", () => reject(init.signal?.reason), { once: true });
  })));

  await import("./forecastRaster.worker");
  const pending = worker.onmessage!({
    data: {
      type: "decode",
      id: 7,
      textureUrl: "/texture.png",
      maskUrl: "/mask.png",
      expected: { width: 2, height: 2 },
      paletteVersion: "v1",
    },
  } as MessageEvent);
  await Promise.resolve();
  await vi.advanceTimersByTimeAsync(15_000);
  await pending;

  expect(posted).toHaveBeenCalledWith(expect.objectContaining({ type: "failure", id: 7 }));
});

it("aborts stalled worker image decodes and closes late bitmaps", async () => {
  const deadlines: Array<() => void> = [];
  const realSetTimeout = globalThis.setTimeout;
  const deadlineToken = {} as ReturnType<typeof setTimeout>;
  vi.spyOn(globalThis, "setTimeout").mockImplementation(((callback: TimerHandler, delay?: number, ...args: unknown[]) => {
    if (delay === 15_000) {
      deadlines.push(() => { if (typeof callback === "function") callback(...args); });
      return deadlineToken;
    }
    return realSetTimeout(callback, delay, ...args);
  }) as typeof setTimeout);
  const posted = vi.fn();
  const worker = { postMessage: posted, onmessage: null as ((event: MessageEvent) => Promise<void>) | null };
  vi.stubGlobal("self", worker);
  vi.stubGlobal("OffscreenCanvas", class {});
  const decodes: Array<(image: ImageBitmap) => void> = [];
  vi.stubGlobal("createImageBitmap", vi.fn(() => new Promise<ImageBitmap>((resolve) => decodes.push(resolve))));
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(["png"]) }));

  await import("./forecastRaster.worker");
  const pending = worker.onmessage!({
    data: {
      type: "decode",
      id: 8,
      textureUrl: "/texture.png",
      maskUrl: "/mask.png",
      expected: { width: 2, height: 2 },
      paletteVersion: "v1",
    },
  } as MessageEvent);
  for (let attempt = 0; attempt < 10 && decodes.length < 2; attempt += 1) await Promise.resolve();
  expect(decodes).toHaveLength(2);
  expect(deadlines).toHaveLength(2);
  deadlines.forEach((expire) => expire());
  await pending;
  expect(posted).toHaveBeenCalledWith(expect.objectContaining({ type: "failure", id: 8 }));

  const closes = decodes.map(() => vi.fn());
  decodes.forEach((finish, index) => finish({ close: closes[index] } as unknown as ImageBitmap));
  await Promise.resolve();
  closes.forEach((close) => expect(close).toHaveBeenCalledOnce());
});

it("does not commit bitmaps completed in the same turn as cancellation", async () => {
  const posted = vi.fn();
  const worker = { postMessage: posted, onmessage: null as ((event: MessageEvent) => Promise<void>) | null };
  vi.stubGlobal("self", worker);
  vi.stubGlobal("OffscreenCanvas", class {});
  const decodes: Array<(image: ImageBitmap) => void> = [];
  vi.stubGlobal("createImageBitmap", vi.fn(() => new Promise<ImageBitmap>((resolve) => decodes.push(resolve))));
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: () => Promise.resolve(new Blob(["png"])) }));

  await import("./forecastRaster.worker");
  const pending = worker.onmessage!({
    data: {
      type: "decode",
      id: 9,
      textureUrl: "/texture.png",
      maskUrl: "/mask.png",
      expected: { width: 2, height: 2 },
      paletteVersion: "v1",
    },
  } as MessageEvent);
  for (let attempt = 0; attempt < 10 && decodes.length < 2; attempt += 1) await Promise.resolve();
  expect(decodes).toHaveLength(2);
  const closes = decodes.map(() => vi.fn());
  decodes.forEach((finish, index) => finish({ width: 2, height: 2, close: closes[index] } as unknown as ImageBitmap));
  await worker.onmessage!({ data: { type: "cancel", id: 9 } } as MessageEvent);
  await pending;

  expect(posted).not.toHaveBeenCalled();
  closes.forEach((close) => expect(close).toHaveBeenCalled());
});
