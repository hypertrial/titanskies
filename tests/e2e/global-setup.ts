export default async function globalSetup() {
  const port = Number(process.env.TITANSKIES_E2E_PORT ?? "3010");
  const response = await fetch(`http://127.0.0.1:${port}/`, { redirect: "follow" });
  if (!response.ok) throw new Error(`e2e homepage warmup failed: ${response.status}`);
  await response.arrayBuffer();
}
