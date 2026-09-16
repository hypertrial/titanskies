import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

let GET: typeof import("./route").GET;
const base = "https://store.public.blob.vercel-storage.com";

beforeEach(async () => {
  vi.resetModules();
  ({ GET } = await import("./route"));
  vi.useFakeTimers();
  vi.setSystemTime("2024-07-15T20:10:00Z");
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function publication() {
  const sourcePointer = JSON.parse(readFileSync("public/demo/context/latest.json", "utf8"));
  const source = readFileSync(`public/demo/${sourcePointer.manifestPath}`, "utf8");
  const manifest = JSON.parse(source);
  const rewrite = (item: unknown): unknown => {
    if (Array.isArray(item)) return item.map(rewrite);
    if (item && typeof item === "object") {
      return Object.fromEntries(Object.entries(item).map(([key, value]) => [key, rewrite(value)]));
    }
    return typeof item === "string" && item.startsWith("/demo/context/assets/") ? `${base}${item.slice(5)}` : item;
  };
  const value = rewrite(manifest);
  const bytes = JSON.stringify(value);
  const digest = createHash("sha256").update(bytes).digest("hex").slice(0, 20);
  const manifestPath = `context/manifests/${digest}.json`;
  const pointer = { version: 8, manifestPath, manifestUrl: `${base}/${manifestPath}`, updatedAt: "2024-07-15T20:00:00Z" };
  const status = JSON.parse(readFileSync("public/demo/context/status.json", "utf8"));
  return { bytes, pointer, status };
}

it("validates the same v8 health contract from Blob storage", async () => {
  const data = publication();
  vi.stubEnv("STORAGE_BACKEND", "blob");
  vi.stubEnv("PUBLIC_BLOB_BASE_URL", base);
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("status.json")) return Response.json(data.status);
    if (url.endsWith("latest.json")) return Response.json(data.pointer);
    if (url === data.pointer.manifestUrl) return new Response(data.bytes, { headers: { "content-type": "application/json" } });
    return new Response(null, { status: 404 });
  }));
  const response = await GET(new Request("https://www.titanskies.com/api/context-health?expectedVersion=8"));
  expect(response.status).toBe(200);
  await expect(response.json()).resolves.toMatchObject({ schemaVersion: 1, ok: true, contextVersion: 8 });
});

it("rejects a pointer that crosses Blob origins before fetching its manifest", async () => {
  const data = publication();
  data.pointer.manifestUrl = "https://attacker.example/context/manifests/0123456789abcdef0123.json";
  vi.stubEnv("STORAGE_BACKEND", "blob");
  vi.stubEnv("PUBLIC_BLOB_BASE_URL", base);
  const fetchMock = vi.fn(async (url: string) => {
    if (url.endsWith("status.json")) return Response.json(data.status);
    return Response.json(data.pointer);
  });
  vi.stubGlobal("fetch", fetchMock);
  const response = await GET(new Request("https://www.titanskies.com/api/context-health"));
  expect(response.status).toBe(503);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  await expect(response.json()).resolves.toMatchObject({ issues: expect.arrayContaining(["invalid-pointer"]) });
});
