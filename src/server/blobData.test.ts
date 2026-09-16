import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";

import { blobBaseUrl, blobManifestValid, isBlobContextPointer } from "./blobData";

const base = "https://store.public.blob.vercel-storage.com";

describe("Blob publication validation", () => {
  it.each([
    "http://store.public.blob.vercel-storage.com",
    "https://store.public.blob.vercel-storage.com:8443",
    "https://store.public.blob.vercel-storage.com/path",
    "https://blob.vercel-storage.com.attacker.example",
  ])("rejects unsafe base URL %s", (value) => expect(blobBaseUrl(value)).toBeNull());

  it("checks pointer origin, content hash, and asset origin", () => {
    const value = { version: 8, textureUrl: `${base}/context/assets/0123456789abcdef0123/best.png` };
    const bytes = new TextEncoder().encode(JSON.stringify(value));
    const digest = createHash("sha256").update(bytes).digest("hex").slice(0, 20);
    const manifestPath = `context/manifests/${digest}.json`;
    const pointer = { version: 8 as const, manifestPath, manifestUrl: `${base}/${manifestPath}`, updatedAt: "2024-07-15T20:00:00Z" };
    expect(isBlobContextPointer(pointer, base)).toBe(true);
    expect(blobManifestValid(pointer, bytes, value, base)).toBe(true);
    expect(blobManifestValid(pointer, bytes, { ...value, textureUrl: "https://attacker.example/a.png" }, base)).toBe(false);
  });
});
