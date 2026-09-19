import {
  ASSET_PATH,
  JSON_MAX_BYTES,
  MANIFEST_PATH,
  isContextPointer,
  shortSha256,
  visitAssetUrls,
  type PublishedContextPointer,
} from "./publicationContract";

const BLOB_HOST_SUFFIX = ".blob.vercel-storage.com";

export type BlobContextPointer = PublishedContextPointer;

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
  return isContextPointer(value, (manifestPath) => `${base}/${manifestPath}`);
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
      if (size > JSON_MAX_BYTES) throw new Error("oversized response");
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
  if (!match || shortSha256(bytes) !== match[1]) return false;
  let foundAsset = false;
  const prefix = `${base}/`;
  const valid = visitAssetUrls(value, (url) => {
    if (!url.startsWith(prefix)) return false;
    foundAsset = true;
    return ASSET_PATH.test(url.slice(prefix.length));
  });
  return valid && foundAsset;
}
