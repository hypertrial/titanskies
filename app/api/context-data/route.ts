import { isLocalContextPointer, readLocalJson } from "@/server/localData";

const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };

export async function GET(): Promise<Response> {
  try {
    const pointer = await readLocalJson("context/latest.json");
    if (!isLocalContextPointer(pointer)) throw new Error("invalid pointer");
    return Response.json(pointer, { headers });
  } catch {
    return Response.json({ ok: false, error: "TitanSkies data is initializing." }, { status: 503, headers });
  }
}
