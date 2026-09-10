import { lstat, readFile, realpath } from "node:fs/promises";
import { createHash } from "node:crypto";
import path from "node:path";

const JSON_MAX_BYTES = 2 * 1024 * 1024;
const PNG_MAX_BYTES = 750_000;
const HASH = "[0-9a-f]{20}";
const NAME = "[a-z0-9][a-z0-9-]*";
const ALLOWED_PATHS = [
  /^context\/(?:latest|status)\.json$/,
  new RegExp(`^context/manifests/${HASH}\\.json$`),
  new RegExp(`^context/assets/${HASH}/${NAME}\\.(?:json|png)$`),
];

export type LocalDataFile = {
  bytes: Buffer;
  contentType: "application/json" | "image/png";
  immutable: boolean;
};

export function dataRoot(): string {
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
    if (contentType === "application/json") JSON.parse(bytes.toString("utf8"));
    else if (!bytes.subarray(0, 8).equals(Buffer.from("89504e470d0a1a0a", "hex"))) return null;
    const expectedHash = pathname.startsWith("context/manifests/")
      ? parts[2]?.replace(/\.json$/, "")
      : pathname.startsWith("context/assets/") ? parts[2] : null;
    if (expectedHash && createHash("sha256").update(bytes).digest("hex").slice(0, 20) !== expectedHash) return null;
    return {
      bytes,
      contentType,
      immutable: pathname.startsWith("context/manifests/") || pathname.startsWith("context/assets/"),
    };
  } catch {
    return null;
  }
}

export async function readLocalJson(pathname: string): Promise<unknown> {
  const file = await readLocalDataFile(pathname.split("/"));
  if (!file || file.contentType !== "application/json") throw new Error("data unavailable");
  return JSON.parse(file.bytes.toString("utf8"));
}
