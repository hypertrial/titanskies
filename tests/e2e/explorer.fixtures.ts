import { expect } from "@playwright/test";
import { readFileSync } from "node:fs";

export const demoPointer = JSON.parse(readFileSync("public/demo/context/latest.json", "utf8"));
export const demoManifest = JSON.parse(readFileSync(`public/demo/${demoPointer.manifestPath}`, "utf8"));

export async function waitForReady(page: import("@playwright/test").Page, timeout = 20_000) {
  await expect(page.getByTestId("loading-state")).toBeHidden({ timeout });
  await expect(page.locator("canvas")).toHaveCount(1);
}

export async function clickCityMarker(page: import("@playwright/test").Page, name: string) {
  const matchingLabels = page.locator(".map-label.city").filter({ hasText: name });
  if (!await matchingLabels.count()) await searchCity(page, name);
  await expect.poll(() => matchingLabels.count()).toBeGreaterThan(0);
  const label = matchingLabels.first();
  const labelBox = await label.boundingBox();
  const canvas = page.getByTestId("interactive-globe");
  const canvasBox = await canvas.boundingBox();
  expect(labelBox).toBeTruthy();
  expect(canvasBox).toBeTruthy();
  await canvas.click({ position: {
    x: (labelBox?.x ?? 0) + (labelBox?.width ?? 0) / 2 - (canvasBox?.x ?? 0),
    y: (labelBox?.y ?? 0) + (labelBox?.height ?? 0) / 2 - (canvasBox?.y ?? 0),
  } });
}

export async function assertDesktopSecondarySpacing(page: import("@playwright/test").Page) {
  const cluster = page.locator(".toolbar-secondary");
  const worst = page.getByRole("button", { name: "Worst 5 conditions" });
  const utilities = page.locator(".toolbar-utilities");
  const status = page.getByTestId("source-badge");
  await expect(utilities).toBeVisible();
  const [clusterBox, worstBox, utilitiesBox, statusBox] = await Promise.all([
    cluster.boundingBox(),
    worst.boundingBox(),
    utilities.boundingBox(),
    status.boundingBox(),
  ]);
  expect(clusterBox).toBeTruthy();
  expect(worstBox).toBeTruthy();
  expect(utilitiesBox).toBeTruthy();
  expect(statusBox).toBeTruthy();
  const leftGap = (utilitiesBox?.x ?? 0) - ((worstBox?.x ?? 0) + (worstBox?.width ?? 0));
  const rightGap = (statusBox?.x ?? 0) - ((utilitiesBox?.x ?? 0) + (utilitiesBox?.width ?? 0));
  expect(leftGap).toBeGreaterThanOrEqual(8);
  expect(rightGap).toBeGreaterThanOrEqual(8);
  expect(Math.abs(leftGap - rightGap)).toBeLessThanOrEqual(2);
  const leftInset = (worstBox?.x ?? 0) - (clusterBox?.x ?? 0);
  const rightInset = ((clusterBox?.x ?? 0) + (clusterBox?.width ?? 0)) - ((statusBox?.x ?? 0) + (statusBox?.width ?? 0));
  expect(Math.abs(leftInset - rightInset)).toBeLessThanOrEqual(2);
  const midline = (box: { y: number; height: number }) => box.y + box.height / 2;
  expect(Math.abs(midline(worstBox!) - midline(utilitiesBox!))).toBeLessThanOrEqual(2);
  expect(Math.abs(midline(utilitiesBox!) - midline(statusBox!))).toBeLessThanOrEqual(2);
  expect(worstBox?.height ?? 0).toBeGreaterThanOrEqual(43);
  expect(worstBox?.height ?? 0).toBeLessThanOrEqual(45);
  expect(utilitiesBox?.height ?? 0).toBeGreaterThanOrEqual(43);
  expect(utilitiesBox?.height ?? 0).toBeLessThanOrEqual(45);
  expect(statusBox?.height ?? 0).toBeGreaterThanOrEqual(43);
  expect(statusBox?.height ?? 0).toBeLessThanOrEqual(45);
}

export async function searchCity(page: import("@playwright/test").Page, name: string) {
  if (!await page.getByTestId("location-search-input").count()) await page.getByRole("button", { name: "Search cities" }).click();
  const search = page.getByTestId("location-search-input");
  await search.fill(name);
  await expect(page.getByRole("listbox", { name: "Location results" })).toBeVisible();
  await search.press("Enter");
  await expect(page.getByTestId("location-card")).toBeVisible();
}

export async function hoverNearbyAirMarker(page: import("@playwright/test").Page, city: string) {
  const matchingLabels = page.locator(".map-label.city").filter({ hasText: city });
  if (!await matchingLabels.count()) await searchCity(page, city);
  await expect.poll(() => matchingLabels.count()).toBeGreaterThan(0);
  const layer = page.getByTestId("map-hover-layer");
  const hover = page.getByTestId("map-hover-label");
  const canvas = page.getByTestId("interactive-globe");
  const canvasBox = await canvas.boundingBox();
  expect(canvasBox).toBeTruthy();
  let screens: Array<{ x: number; y: number }> = [];
  await expect.poll(async () => {
    const raw = await layer.getAttribute("data-cluster-screens");
    const points = raw ? JSON.parse(raw) as Array<{ x: number; y: number }> : [];
    screens = points.filter((point) => point.x > 24 && point.y > 24 && point.x < (canvasBox?.width ?? 0) - 24 && point.y < (canvasBox?.height ?? 0) - 24);
    return screens.length;
  }).toBeGreaterThan(0);
  for (const point of screens) {
    await canvas.hover({ position: { x: point.x, y: point.y } });
    if (await hover.count()) return hover;
  }
  await expect(hover).toBeVisible();
  return hover;
}

export async function useLiveContext(page: import("@playwright/test").Page, now: string, shiftHours = 0, healthStatus: "healthy" | "degraded" | "unhealthy" = "healthy") {
  await page.clock.setFixedTime(new Date(now));
  const pointerAgeMinutes = healthStatus === "unhealthy" ? 35 : 8;
  await page.route("**/api/context-health?**", async (route) => {
    const pointerUpdatedAt = new Date(Date.parse(now) - pointerAgeMinutes * 60_000).toISOString();
    const sources = Object.fromEntries(Object.entries(demoManifest.sources).map(([name, source]) => {
      const state = source as { status: string; checkedAt: string; observedAt: string | null; provenance: string };
      return [name, {
        status: state.status,
        checkedAt: state.checkedAt,
        observedAt: state.observedAt,
        provenance: state.provenance,
      }];
    }));
    await route.fulfill({ status: healthStatus === "unhealthy" ? 503 : 200, json: {
      schemaVersion: 1,
      ok: healthStatus !== "unhealthy",
      status: healthStatus,
      checkedAt: now,
      contextVersion: 8,
      publication: healthStatus === "unhealthy" ? "failed" : "fresh",
      lastAttemptAt: pointerUpdatedAt,
      lastCompleteForecastAt: pointerUpdatedAt,
      pointerUpdatedAt,
      forecastFrameCount: 37,
      forecastLastValidTime: new Date(Date.parse(now) + 36 * 3_600_000).toISOString(),
      remainingCoverageHours: 36,
      sources,
      issues: healthStatus === "healthy" ? [] : healthStatus === "degraded" ? ["source-firework-stale"] : ["stale-heartbeat", "stale-forecast"],
    } });
  });
  await page.route("**/context/manifests/*.json", async (route) => {
    const response = await route.fetch();
    const manifest = await response.json();
    const shiftRun = (run: { frames: Array<{ validTime: string }> }) => ({
      ...run,
      frames: run.frames.map((frame) => ({
        ...frame,
        validTime: new Date(Date.parse(frame.validTime) + shiftHours * 3_600_000).toISOString(),
      })),
    });
    manifest.mode = "live";
    manifest.generatedAt = new Date(Date.parse(now) + shiftHours * 3_600_000).toISOString();
    if (shiftHours) {
      manifest.forecast = shiftRun(manifest.forecast);
      manifest.forecasts.best = shiftRun(manifest.forecasts.best);
    }
    await route.fulfill({ response, json: manifest });
  });
}
