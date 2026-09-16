import { chromium } from "@playwright/test";

export default async function globalSetup() {
  const port = Number(process.env.TITANSKIES_E2E_PORT ?? "3010");
  const url = `http://127.0.0.1:${port}/`;
  const response = await fetch(url, { redirect: "follow" });
  if (!response.ok) throw new Error(`e2e homepage warmup failed: ${response.status}`);
  await response.arrayBuffer();
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 120_000 });
    await page.getByTestId("loading-state").waitFor({ state: "hidden", timeout: 120_000 });
    await page.locator("canvas").waitFor({ state: "visible", timeout: 120_000 });
  } finally {
    await browser.close();
  }
}
