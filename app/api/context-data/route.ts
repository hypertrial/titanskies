import { isLocalContextPointer, readLocalJson } from "@/server/localData";
import { blobBaseUrl } from "@/server/blobData";

const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };
const blobHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Cache-Control": "public, max-age=0, must-revalidate",
  "Vercel-CDN-Cache-Control": "public, s-maxage=60, stale-while-revalidate=60",
  "X-Content-Type-Options": "nosniff",
};

export async function GET(): Promise<Response> {
  const storageBackend = process.env.STORAGE_BACKEND?.trim() || "local";
  const configured = process.env.PUBLIC_BLOB_BASE_URL?.trim();
  if (storageBackend === "blob") {
    if (process.env.VERCEL_ENV === "preview" || !configured) {
      return Response.json({ ok: false, error: "TitanSkies data is unavailable." }, { status: 503, headers });
    }
    const base = blobBaseUrl(configured);
    if (!base) return Response.json({ ok: false, error: "TitanSkies data is unavailable." }, { status: 503, headers });
    return new Response(null, { status: 307, headers: { ...blobHeaders, Location: `${base}/context/latest.json` } });
  }
  if (storageBackend !== "local") {
    return Response.json({ ok: false, error: "TitanSkies data is unavailable." }, { status: 503, headers });
  }
  try {
    const pointer = await readLocalJson("context/latest.json");
    if (!isLocalContextPointer(pointer)) throw new Error("invalid pointer");
    return Response.json(pointer, { headers });
  } catch {
    return Response.json({ ok: false, error: "TitanSkies data is initializing." }, { status: 503, headers });
  }
}
