import { createHash } from "node:crypto";

const MAX_JSON_BYTES = 2 * 1024 * 1024;
const BLOB_HOST_SUFFIX = ".blob.vercel-storage.com";
const MANIFEST_PATH = /^context\/manifests\/([0-9a-f]{20})\.json$/;
const ASSET_URL = /^context\/assets\/[0-9a-f]{20}\/[a-z0-9][a-z0-9-]*\.(?:json|png)$/;

export type BlobContextPointer = {
  version: 8;
  manifestPath: string;
  manifestUrl: string;
  updatedAt: string;
};

export function blobBaseUrl(value = process.env.PUBLIC_BLOB_BASE_URL): string | null {
  if (!value?.trim()) return null;
  try {
    const url = new URL(value.trim());
    if (url.protocol !== "https:" || url.username || url.password || url.port || url.search || url.hash) return null;
    if (!url.hostname.toLowerCase().endsWith(BLOB_HOST_SUFFIX) || (url.pathname !== "/" && url.pathname !== "")) return null;
    return url.origin;
  } catch {
    return null;
  }
}

export function isBlobContextPointer(value: unknown, base: string): value is BlobContextPointer {
  if (!value || typeof value !== "object") return false;
  const pointer = value as Partial<BlobContextPointer>;
  return pointer.version === 8
    && typeof pointer.manifestPath === "string"
    && MANIFEST_PATH.test(pointer.manifestPath)
    && pointer.manifestUrl === `${base}/${pointer.manifestPath}`
    && typeof pointer.updatedAt === "string"
    && Number.isFinite(Date.parse(pointer.updatedAt));
}

export async function readBlobJson(url: string, signal: AbortSignal): Promise<{ bytes: Uint8Array; value: unknown }> {
  const response = await fetch(url, { cache: "no-store", redirect: "error", signal });
  if (!response.ok) throw new Error("publication unavailable");
  const reader = response.body?.getReader();
  if (!reader) throw new Error("missing response body");
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      signal.throwIfAborted();
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_JSON_BYTES) throw new Error("oversized response");
      chunks.push(value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    signal.throwIfAborted();
    return { bytes, value: JSON.parse(new TextDecoder().decode(bytes)) };
  } finally {
    void reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export function blobManifestValid(pointer: BlobContextPointer, bytes: Uint8Array, value: unknown, base: string): boolean {
  const match = MANIFEST_PATH.exec(pointer.manifestPath);
  if (!match || createHash("sha256").update(bytes).digest("hex").slice(0, 20) !== match[1]) return false;
  let foundAsset = false;
  const visit = (item: unknown): boolean => {
    if (Array.isArray(item)) return item.every(visit);
    if (!item || typeof item !== "object") return true;
    return Object.entries(item).every(([key, child]) => {
      if (key === "url" || key.endsWith("Url")) {
        if (typeof child !== "string" || !child.startsWith(`${base}/`)) return false;
        const relative = child.slice(base.length + 1);
        foundAsset = true;
        return ASSET_URL.test(relative);
      }
      return visit(child);
    });
  };
  return visit(value) && foundAsset;
}
