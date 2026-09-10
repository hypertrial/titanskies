export function GET(): Response {
  return Response.json({ ok: true, service: "titanskies" }, {
    headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" },
  });
}
