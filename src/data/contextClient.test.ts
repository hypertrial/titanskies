import { afterEach, expect, it, vi } from "vitest";
import { fetchJson, loadContextHealth } from "./contextClient";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

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
