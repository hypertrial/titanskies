import { readLocalJson } from "@/server/localData";

const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };

export async function GET(): Promise<Response> {
  try {
    const pointer = await readLocalJson("context/latest.json");
    if (
      !pointer || typeof pointer !== "object"
      || (pointer as { version?: unknown }).version !== 8
      || typeof (pointer as { manifestPath?: unknown }).manifestPath !== "string"
      || !/^context\/manifests\/[0-9a-f]{20}\.json$/.test((pointer as { manifestPath: string }).manifestPath)
      || (pointer as { manifestUrl?: unknown }).manifestUrl !== `/data/${(pointer as { manifestPath: string }).manifestPath}`
      || !Number.isFinite(Date.parse(String((pointer as { updatedAt?: unknown }).updatedAt)))
    ) {
      throw new Error("invalid pointer");
    }
    return Response.json(pointer, { headers });
  } catch {
    return Response.json({ ok: false, error: "TitanSkies data is initializing." }, { status: 503, headers });
  }
}
