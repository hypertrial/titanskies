import { expect } from "@playwright/test";
import { readFileSync } from "node:fs";

export const demoPointer = JSON.parse(readFileSync("public/demo/context/latest.json", "utf8"));
export const demoManifest = JSON.parse(readFileSync(`public/demo/${demoPointer.manifestPath}`, "utf8"));

export async function waitForReady(page: import("@playwright/test").Page, timeout = 20_000) {
  await expect(page.getByTestId("loading-state")).toBeHidden({ timeout });
  await expect(page.locator("canvas")).toHaveCount(1);
}

export async function openMore(page: import("@playwright/test").Page, options: { tap?: boolean } = {}) {
  const trigger = page.getByRole("button", { name: "More options" });
  if (options.tap) await trigger.tap();
  else await trigger.click();
  return page.getByRole("dialog", { name: "More" });
}

export async function searchAndSelectMapLabel(page: import("@playwright/test").Page, name: string) {
  const trigger = page.getByRole("button", { name: "Search cities" });
  if (await trigger.isVisible()) await trigger.click();
  const search = page.getByTestId("location-search-input");
  await search.fill(name);
  await search.press("Enter");
  const label = page.locator(".map-label.city").filter({ hasText: name }).first();
  await expect(label).toBeVisible();
  const [labelBox, canvasBox] = await Promise.all([label.boundingBox(), page.getByTestId("interactive-globe").boundingBox()]);
  expect(labelBox).toBeTruthy();
  expect(canvasBox).toBeTruthy();
  await page.getByTestId("interactive-globe").click({ position: {
    x: (labelBox?.x ?? 0) + (labelBox?.width ?? 0) / 2 - (canvasBox?.x ?? 0),
    y: (labelBox?.y ?? 0) + (labelBox?.height ?? 0) / 2 - (canvasBox?.y ?? 0),
  } });
}

async function settledAirMarkerScreens(page: import("@playwright/test").Page) {
  const layer = page.getByTestId("map-hover-layer");
  let previous = "";
  let stable = 0;
  let points: Array<{ x: number; y: number }> = [];
  await expect.poll(async () => {
    const raw = await layer.getAttribute("data-cluster-screens") ?? "";
    stable = raw && raw === previous ? stable + 1 : 0;
    previous = raw;
    points = raw ? JSON.parse(raw) : [];
    return points.length > 0 && stable >= 2;
  }, { intervals: [250], timeout: 10_000 }).toBe(true);
  return points;
}

export async function clickCityMarker(page: import("@playwright/test").Page, name: string) {
  const matchingLabels = page.locator(".map-label.city").filter({ hasText: name });
  if (!await matchingLabels.count()) await searchCity(page, name);
  await expect.poll(() => matchingLabels.count()).toBeGreaterThan(0);
  const canvas = page.getByTestId("interactive-globe");
  const canvasBox = await canvas.boundingBox();
  expect(canvasBox).toBeTruthy();
  const labelBox = await matchingLabels.first().boundingBox();
  expect(labelBox).toBeTruthy();
  const points = await settledAirMarkerScreens(page);
  const centerX = (labelBox?.x ?? 0) + (labelBox?.width ?? 0) / 2;
  const centerY = (labelBox?.y ?? 0) + (labelBox?.height ?? 0) / 2;
  points.sort((a, b) => Math.hypot((canvasBox?.x ?? 0) + a.x - centerX, (canvasBox?.y ?? 0) + a.y - centerY)
    - Math.hypot((canvasBox?.x ?? 0) + b.x - centerX, (canvasBox?.y ?? 0) + b.y - centerY));
  for (const point of points) {
    const x = (canvasBox?.x ?? 0) + point.x;
    const y = (canvasBox?.y ?? 0) + point.y;
    if (Math.hypot(x - centerX, y - centerY) > 48) break;
    if (!await page.evaluate(({ x, y }) => document.querySelector('[data-testid="interactive-globe"]')?.contains(document.elementFromPoint(x, y)), { x, y })) continue;
    await page.mouse.click(x, y);
    if (await page.getByTestId("details-card").isVisible()) {
      await expect(page.getByRole("dialog").getByRole("heading")).toContainText(name);
      return;
    }
  }
  throw new Error(`No selectable ${name} monitor marker near its map label`);
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
  const hover = page.getByTestId("map-hover-label");
  const canvas = page.getByTestId("interactive-globe");
  const canvasBox = await canvas.boundingBox();
  expect(canvasBox).toBeTruthy();
  const screens = (await settledAirMarkerScreens(page)).filter((point) => point.x > 24 && point.y > 24 && point.x < (canvasBox?.width ?? 0) - 24 && point.y < (canvasBox?.height ?? 0) - 24);
  expect(screens.length).toBeGreaterThan(0);
  for (const point of screens) {
    const x = (canvasBox?.x ?? 0) + point.x;
    const y = (canvasBox?.y ?? 0) + point.y;
    if (!await page.evaluate(({ x, y }) => document.querySelector('[data-testid="interactive-globe"]')?.contains(document.elementFromPoint(x, y)), { x, y })) continue;
    await page.mouse.move(x, y);
    try {
      await expect(hover).toBeVisible({ timeout: 800 });
      await expect(hover).toHaveAttribute("data-ready", "true", { timeout: 800 });
      await page.waitForTimeout(150);
      if (await hover.isVisible()) return hover;
    } catch { /* try the next visible marker */ }
  }
  await expect(hover).toBeVisible();
  return hover;
}

export async function useLiveContext(page: import("@playwright/test").Page, now: string, shiftHours = 0, healthStatus: "healthy" | "degraded" | "unhealthy" = "healthy", healthIssues?: string[]) {
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
      issues: healthIssues ?? (healthStatus === "healthy" ? [] : healthStatus === "degraded" ? ["source-firework-stale"] : ["stale-heartbeat", "stale-forecast"]),
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
