import { afterEach, expect, it, vi } from "vitest";
import { CONTEXT_IMAGE_TIMEOUT_MS, fetchJson, loadContextHealth, loadContextImage } from "./contextClient";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

for (const phase of ["headers", "body"] as const) {
  it(`bounds stalled JSON ${phase} and allows a fresh retry`, async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
      const pending = new Promise((_, reject) => init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true }));
      return phase === "headers" ? pending : Promise.resolve({ ok: true, json: () => pending });
    });
    vi.stubGlobal("fetch", fetchMock);
    const failure = vi.fn();
    const request = fetchJson("/source.json").catch(failure);
    await vi.advanceTimersByTimeAsync(14_999);
    expect(failure).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(failure).toHaveBeenCalledWith(expect.objectContaining({ message: expect.stringMatching(/timed out/i) }));
    await request;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ recovered: true })));
    await expect(fetchJson("/source.json")).resolves.toEqual({ recovered: true });
    expect(vi.getTimerCount()).toBe(0);
  });
}

it("cleans up JSON deadlines on success, HTTP error and invalid JSON", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce(Response.json({ ok: true }))
    .mockResolvedValueOnce(new Response("unavailable", { status: 503 }))
    .mockResolvedValueOnce(new Response("invalid", { headers: { "content-type": "application/json" } })));
  await expect(fetchJson("/source.json")).resolves.toEqual({ ok: true });
  expect(vi.getTimerCount()).toBe(0);
  await expect(fetchJson("/source.json")).rejects.toThrow("Failed to fetch");
  expect(vi.getTimerCount()).toBe(0);
  await expect(fetchJson("/source.json")).rejects.toThrow();
  expect(vi.getTimerCount()).toBe(0);
});

it("bounds a stalled health response body", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) => Promise.resolve({
    ok: false, status: 503,
    json: () => new Promise((_, reject) => init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true })),
  })));
  const failure = vi.fn();
  const request = loadContextHealth(8, "/health").catch(failure);
  await vi.advanceTimersByTimeAsync(15_000);
  expect(failure).toHaveBeenCalledWith(expect.objectContaining({ message: expect.stringMatching(/timed out/i) }));
  await request;
  expect(vi.getTimerCount()).toBe(0);
});

it("bounds a stalled forecast image request and preserves caller cancellation", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("AbortSignal", {});
  vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) => {
    if (init?.signal?.aborted) return Promise.reject(init.signal.reason);
    return new Promise((_, reject) => {
      init?.signal?.addEventListener("abort", () => reject(init.signal?.reason), { once: true });
    });
  }));
  const timedOut = loadContextImage("/forecast.png");
  const failure = vi.fn();
  void timedOut.catch(failure);
  await vi.advanceTimersByTimeAsync(CONTEXT_IMAGE_TIMEOUT_MS);
  expect(failure).toHaveBeenCalledWith(expect.objectContaining({ message: expect.stringMatching(/timed out/i) }));
  await timedOut.catch(() => undefined);

  const caller = new AbortController();
  const cancelled = loadContextImage("/forecast.png", caller.signal);
  caller.abort(new DOMException("Cancelled", "AbortError"));
  await expect(cancelled).rejects.toMatchObject({ name: "AbortError" });

  const alreadyCancelled = new AbortController();
  alreadyCancelled.abort(new DOMException("Cancelled", "AbortError"));
  await expect(loadContextImage("/forecast.png", alreadyCancelled.signal)).rejects.toMatchObject({ name: "AbortError" });
  expect(vi.getTimerCount()).toBe(0);
});

it("bounds a stalled forecast image decode and closes a late bitmap", async () => {
  let fireDeadline!: () => void;
  const realSetTimeout = globalThis.setTimeout;
  const deadlineToken = {} as ReturnType<typeof setTimeout>;
  vi.spyOn(globalThis, "setTimeout").mockImplementation(((callback: TimerHandler, delay?: number, ...args: unknown[]) => {
    if (delay === CONTEXT_IMAGE_TIMEOUT_MS) {
      fireDeadline = () => { if (typeof callback === "function") callback(...args); };
      return deadlineToken;
    }
    return realSetTimeout(callback, delay, ...args);
  }) as typeof setTimeout);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: () => Promise.resolve(new Blob(["png"])) }));
  let finishDecode!: (image: ImageBitmap) => void;
  const close = vi.fn();
  vi.stubGlobal("window", { createImageBitmap: true });
  vi.stubGlobal("createImageBitmap", vi.fn(() => new Promise<ImageBitmap>((resolve) => { finishDecode = resolve; })));

  const pending = loadContextImage("/forecast.png");
  const rejection = pending.catch((error: unknown) => error);
  for (let attempt = 0; attempt < 10 && !finishDecode; attempt += 1) await Promise.resolve();
  expect(finishDecode).toBeTypeOf("function");
  fireDeadline();
  await expect(rejection).resolves.toEqual(expect.objectContaining({ message: expect.stringMatching(/timed out/i) }));
  finishDecode({ close } as unknown as ImageBitmap);
  await Promise.resolve();
  expect(close).toHaveBeenCalledOnce();
});

it("rejects a bitmap completed in the same turn as caller cancellation", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, blob: () => Promise.resolve(new Blob(["png"])) }));
  let finishDecode!: (image: ImageBitmap) => void;
  const close = vi.fn();
  vi.stubGlobal("window", { createImageBitmap: true });
  vi.stubGlobal("createImageBitmap", vi.fn(() => new Promise<ImageBitmap>((resolve) => { finishDecode = resolve; })));
  const caller = new AbortController();

  const pending = loadContextImage("/forecast.png", caller.signal);
  const rejection = pending.catch((error: unknown) => error);
  for (let attempt = 0; attempt < 10 && !finishDecode; attempt += 1) await Promise.resolve();
  finishDecode({ close } as unknown as ImageBitmap);
  caller.abort(new DOMException("Cancelled", "AbortError"));

  await expect(rejection).resolves.toEqual(expect.objectContaining({ name: "AbortError" }));
  expect(close).toHaveBeenCalled();
});

it("honors cancellation that arrives while the image body is resolving", async () => {
  let finishBody!: (blob: Blob) => void;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    blob: () => new Promise<Blob>((resolve) => { finishBody = resolve; }),
  }));
  const caller = new AbortController();
  const pending = loadContextImage("/forecast.png", caller.signal);
  await Promise.resolve();
  caller.abort(new DOMException("Cancelled", "AbortError"));
  finishBody(new Blob(["png"]));
  await expect(pending).rejects.toMatchObject({ name: "AbortError" });
});
