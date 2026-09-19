import { createHash } from "node:crypto";

import contract from "../../shared/context-contract-v8.json";

export const ASSET_HASH = "[0-9a-f]{20}";
export const ASSET_NAME = "[a-z0-9][a-z0-9-]*";
export const MANIFEST_PATH = new RegExp(`^context/manifests/(${ASSET_HASH})\\.json$`);
export const ASSET_PATH = new RegExp(`^context/assets/${ASSET_HASH}/${ASSET_NAME}\\.(?:json|png)$`);

export const JSON_MAX_BYTES = Math.max(contract.assetBudgets.jsonBytes, contract.assetBudgets.monitorJsonBytes);
export const PNG_MAX_BYTES = contract.assetBudgets.rasterBytes;

export type PublishedContextPointer = {
  version: 8;
  manifestPath: string;
  manifestUrl: string;
  updatedAt: string;
};

export function shortSha256(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex").slice(0, 20);
}

export function isContextPointer(
  value: unknown,
  manifestUrlFor: (manifestPath: string) => string,
): value is PublishedContextPointer {
  if (!value || typeof value !== "object") return false;
  const pointer = value as Partial<PublishedContextPointer>;
  return pointer.version === 8
    && typeof pointer.manifestPath === "string"
    && MANIFEST_PATH.test(pointer.manifestPath)
    && pointer.manifestUrl === manifestUrlFor(pointer.manifestPath)
    && typeof pointer.updatedAt === "string"
    && Number.isFinite(Date.parse(pointer.updatedAt));
}

export function visitAssetUrls(value: unknown, visit: (url: string) => boolean): boolean {
  if (Array.isArray(value)) return value.every((item) => visitAssetUrls(item, visit));
  if (!value || typeof value !== "object") return true;
  return Object.entries(value).every(([key, child]) => {
    if (key === "url" || key.endsWith("Url")) return typeof child === "string" && visit(child);
    return visitAssetUrls(child, visit);
  });
}
