import { afterEach, expect, it, vi } from "vitest";
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { GET } from "./route";

const previousRoot = process.env.TITANSKIES_DATA_DIR;

afterEach(() => {
  vi.unstubAllEnvs();
  if (previousRoot === undefined) delete process.env.TITANSKIES_DATA_DIR;
  else process.env.TITANSKIES_DATA_DIR = previousRoot;
});

it("redirects Blob mode only to an approved Vercel Blob origin", async () => {
  vi.stubEnv("STORAGE_BACKEND", "blob");
  vi.stubEnv("PUBLIC_BLOB_BASE_URL", "https://store.public.blob.vercel-storage.com/");
  const response = await GET();
  expect(response.status).toBe(307);
  expect(response.headers.get("location")).toBe("https://store.public.blob.vercel-storage.com/context/latest.json");
  expect(response.headers.get("vercel-cdn-cache-control")).toContain("s-maxage=60");
});

it.each([
  "http://store.public.blob.vercel-storage.com",
  "https://blob.vercel-storage.com.attacker.example",
  "https://user:secret@store.public.blob.vercel-storage.com",
  "https://store.public.blob.vercel-storage.com/other",
])("fails closed for invalid Blob origin %s", async (origin) => {
  vi.stubEnv("STORAGE_BACKEND", "blob");
  vi.stubEnv("PUBLIC_BLOB_BASE_URL", origin);
  expect((await GET()).status).toBe(503);
});

it("retains the filesystem pointer behavior when Blob mode is not configured", async () => {
  vi.stubEnv("STORAGE_BACKEND", "local");
  vi.stubEnv("PUBLIC_BLOB_BASE_URL", "");
  const root = await mkdtemp(path.join(tmpdir(), "titanskies-context-data-"));
  await mkdir(path.join(root, "context"));
  const pointer = {
    version: 8,
    manifestPath: "context/manifests/0123456789abcdef0123.json",
    manifestUrl: "/data/context/manifests/0123456789abcdef0123.json",
    updatedAt: "2024-07-15T20:00:00Z",
  };
  await writeFile(path.join(root, "context/latest.json"), JSON.stringify(pointer));
  process.env.TITANSKIES_DATA_DIR = root;
  const response = await GET();
  expect(response.status).toBe(200);
  await expect(response.json()).resolves.toEqual(pointer);
});

it("does not fetch inherited production storage from a demo preview", async () => {
  vi.stubEnv("VERCEL_ENV", "preview");
  vi.stubEnv("STORAGE_BACKEND", "local");
  vi.stubEnv("PUBLIC_BLOB_BASE_URL", "https://production.public.blob.vercel-storage.com");
  vi.stubEnv("NEXT_PUBLIC_CONTEXT_URL", "/demo/context/latest.json");
  process.env.TITANSKIES_DATA_DIR = await mkdtemp(path.join(tmpdir(), "titanskies-preview-"));
  const fetchMock = vi.spyOn(globalThis, "fetch");
  expect((await GET()).status).toBe(503);
  expect(fetchMock).not.toHaveBeenCalled();
  fetchMock.mockRestore();
});

it("fails closed when a preview is misconfigured for Blob storage", async () => {
  vi.stubEnv("VERCEL_ENV", "preview");
  vi.stubEnv("STORAGE_BACKEND", "blob");
  vi.stubEnv("PUBLIC_BLOB_BASE_URL", "https://production.public.blob.vercel-storage.com");
  const fetchMock = vi.spyOn(globalThis, "fetch");
  expect((await GET()).status).toBe(503);
  expect(fetchMock).not.toHaveBeenCalled();
  fetchMock.mockRestore();
});
