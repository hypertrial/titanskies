import { lstat, readFile, realpath } from "node:fs/promises";
import path from "node:path";

import {
  ASSET_PATH,
  JSON_MAX_BYTES,
  MANIFEST_PATH,
  PNG_MAX_BYTES,
  isContextPointer,
  shortSha256,
  visitAssetUrls,
  type PublishedContextPointer,
} from "./publicationContract";

const ALLOWED_PATHS = [
  /^context\/(?:latest|status)\.json$/,
  MANIFEST_PATH,
  ASSET_PATH,
];

export type LocalDataFile = {
  bytes: Buffer;
  immutable: boolean;
} & (
  | { contentType: "application/json"; value: unknown }
  | { contentType: "image/png" }
);

export type LocalContextPointer = PublishedContextPointer;

const ASSET_VERIFICATION_CACHE_MS = 60_000;
let assetVerification: { key: string; expiresAt: number; result: Promise<boolean> } | null = null;

export function isLocalContextPointer(value: unknown): value is LocalContextPointer {
  return isContextPointer(value, (manifestPath) => `/data/${manifestPath}`);
}

function dataRoot(): string {
  const configured = process.env.TITANSKIES_DATA_DIR?.trim();
  return path.resolve(/* turbopackIgnore: true */ configured || path.join(process.cwd(), ".local", "data"));
}

function inside(root: string, candidate: string): boolean {
  const relative = path.relative(root, candidate);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

export async function readLocalDataFile(parts: string[]): Promise<LocalDataFile | null> {
  const pathname = parts.join("/");
  if (!ALLOWED_PATHS.some((pattern) => pattern.test(pathname))) return null;

  const root = dataRoot();
  let canonicalRoot: string;
  try {
    canonicalRoot = await realpath(/* turbopackIgnore: true */ root);
  } catch {
    return null;
  }
  const candidate = path.resolve(root, ...parts);
  if (!inside(root, candidate)) return null;

  try {
    const direct = await lstat(/* turbopackIgnore: true */ candidate);
    if (!direct.isFile() || direct.isSymbolicLink()) return null;
    const canonical = await realpath(/* turbopackIgnore: true */ candidate);
    if (!inside(canonicalRoot, canonical)) return null;
    const contentType = pathname.endsWith(".json") ? "application/json" : "image/png";
    const maxBytes = contentType === "application/json" ? JSON_MAX_BYTES : PNG_MAX_BYTES;
    if (direct.size > maxBytes) return null;
    const bytes = await readFile(/* turbopackIgnore: true */ canonical);
    if (bytes.byteLength > maxBytes) return null;
    let value: unknown;
    if (contentType === "application/json") value = JSON.parse(bytes.toString("utf8"));
    else if (!bytes.subarray(0, 8).equals(Buffer.from("89504e470d0a1a0a", "hex"))) return null;
    const expectedHash = pathname.startsWith("context/manifests/")
      ? parts[2]?.replace(/\.json$/, "")
      : pathname.startsWith("context/assets/") ? parts[2] : null;
    if (expectedHash && shortSha256(bytes) !== expectedHash) return null;
    const immutable = pathname.startsWith("context/manifests/") || pathname.startsWith("context/assets/");
    return contentType === "application/json"
      ? { bytes, contentType, immutable, value }
      : { bytes, contentType, immutable };
  } catch {
    return null;
  }
}

export async function readLocalJson(pathname: string): Promise<unknown> {
  const file = await readLocalDataFile(pathname.split("/"));
  if (!file || file.contentType !== "application/json") throw new Error("data unavailable");
  return file.value;
}

async function verifyLocalManifestAssets(value: unknown): Promise<boolean> {
  const paths = new Set<string>();
  const valid = visitAssetUrls(value, (url) => {
    if (!url.startsWith("/data/")) return false;
    const relative = url.slice("/data/".length);
    if (!ASSET_PATH.test(relative)) return false;
    paths.add(relative);
    return true;
  });
  if (!valid || paths.size === 0) return false;
  for (const pathname of paths) {
    if (!await readLocalDataFile(pathname.split("/"))) return false;
  }
  return true;
}

export function localManifestAssetsAvailable(manifestPath: string, value: unknown): Promise<boolean> {
  const key = `${dataRoot()}\0${manifestPath}`;
  if (assetVerification?.key === key && assetVerification.expiresAt > Date.now()) return assetVerification.result;
  const result = verifyLocalManifestAssets(value);
  assetVerification = { key, expiresAt: Date.now() + ASSET_VERIFICATION_CACHE_MS, result };
  void result.catch(() => {
    if (assetVerification?.result === result) assetVerification = null;
  });
  return result;
}
