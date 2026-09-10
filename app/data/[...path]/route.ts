import { readLocalDataFile } from "@/server/localData";

type Context = { params: Promise<{ path: string[] }> };

async function response(context: Context, head = false): Promise<Response> {
  const file = await readLocalDataFile((await context.params).path);
  if (!file) {
    return Response.json({ ok: false, error: "Not found." }, {
      status: 404,
      headers: { "Cache-Control": "no-store" },
    });
  }
  return new Response(head ? null : new Uint8Array(file.bytes), {
    headers: {
      "Cache-Control": file.immutable ? "public, max-age=31536000, immutable" : "no-cache",
      "Content-Length": String(file.bytes.byteLength),
      "Content-Type": file.contentType,
      "X-Content-Type-Options": "nosniff",
    },
  });
}

export function GET(_request: Request, context: Context): Promise<Response> {
  return response(context);
}

export function HEAD(_request: Request, context: Context): Promise<Response> {
  return response(context, true);
}
