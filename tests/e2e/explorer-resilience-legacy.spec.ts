import { expect, test } from "@playwright/test";
import { demoManifest, waitForReady, searchCity, useLiveContext } from "./explorer.fixtures";

test("shows healthy PM2.5 monitors while optional AQHI is stalled", async ({ page }) => {
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  const completed = new Set<string>();
  page.on("requestfinished", (request) => {
    if (/\/(airnow|bc|sinaica)-monitors\.json$/.test(request.url())) completed.add(request.url());
  });
  await page.route("**/aqhi-monitors.json", async (route) => { await held; await route.abort("failed"); });
  try {
    await page.goto("/"); await waitForReady(page);
    await page.getByTestId("view-air").click();
    await expect.poll(() => completed.size).toBe(3);
    await waitForReady(page, 5_000);
    await expect(page.getByTestId("error-state")).toHaveCount(0);
    await expect(page.getByTestId("map-hover-layer")).toHaveAttribute("data-cluster-screens", /\{.*\}/);
  } finally { release(); }
});

test("waits for complete city matching and rankings while the partial map is usable", async ({ page }) => {
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/aqhi-monitors.json", async (route) => { await held; await route.continue(); });
  try {
    await page.goto("/"); await waitForReady(page);
    await page.getByTestId("view-air").click(); await waitForReady(page, 5_000);
    await page.getByRole("button", { name: "Worst 5 conditions" }).click();
    await expect(page.getByText("Loading official monitor readings…")).toBeVisible();
    await page.getByRole("button", { name: "Close Highest conditions" }).click();
    await searchCity(page, "Montreal");
    await expect(page.getByTestId("location-air-value")).toHaveCount(0);
    await expect(page.getByTestId("location-card")).toContainText("Loading…");
    release();
    await expect(page.getByTestId("location-air-value")).toContainText("AQHI");
  } finally { release(); }
});

test("does not present a ready PM2.5 map from an AQHI-only partial result", async ({ page }) => {
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  let aqhiLoaded = false;
  page.on("requestfinished", (request) => { if (request.url().endsWith("/aqhi-monitors.json")) aqhiLoaded = true; });
  await page.route(/\/(airnow|bc|sinaica)-monitors\.json$/, async (route) => { await held; await route.continue(); });
  try {
    await page.goto("/"); await waitForReady(page);
    await page.getByTestId("view-air").click();
    await expect.poll(() => aqhiLoaded).toBe(true);
    await expect(page.getByTestId("loading-state")).toBeVisible();
    release();
    await waitForReady(page);
    await expect(page.getByTestId("error-state")).toHaveCount(0);
  } finally { release(); }
});

test("describes only the keyboard shortcuts available in each view", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  const help = page.locator("#globe-help");
  await expect(help).toContainText("Space to play or pause");
  await expect(help).not.toContainText("reported-wildfire marker");
  await page.getByRole("button", { name: "Map options" }).click();
  await page.getByTestId("incidents-toggle").check();
  await expect(help).toContainText("reported-wildfire marker");
  await page.getByRole("button", { name: "Close map options" }).click();
  await page.getByTestId("view-air").click();
  await waitForReady(page);
  await expect(help).not.toContainText("Space to play or pause");
  await expect(help).not.toContainText("reported-wildfire marker");
  await expect(help).toContainText("Enter to select a nearby monitor");
});
test("keeps Air quality available when AirNow fails", async ({ page }) => {
  await page.route("**/context/manifests/*.json", async (route) => {
    const response = await route.fetch();
    const manifest = await response.json();
    manifest.sources.airnow = { ...manifest.sources.airnow, status: "error", error: "AirNow update failed" };
    delete manifest.air.monitorsUrl;
    if (manifest.air.monitorSets) delete manifest.air.monitorSets.airnow;
    await route.fulfill({ response, json: manifest });
  });
  await page.goto("/"); await waitForReady(page);
  await page.getByTestId("view-air").click();
  await waitForReady(page);
  await expect(page.getByTestId("source-warning")).toContainText("AirNow: update failed");
  await expect(page.getByTestId("source-warning")).not.toContainText("latest source update failed");
  await expect(page.getByTestId("error-state")).toHaveCount(0);
});

test("keeps Air quality available when B.C. ENV fails", async ({ page }) => {
  await useLiveContext(page, "2024-07-15T20:37:15Z");
  await page.route("**/context/manifests/*.json", async (route) => {
    const response = await route.fetch();
    const manifest = await response.json();
    manifest.mode = "live";
    manifest.sources.bcair = { ...(manifest.sources.bcair ?? manifest.sources.airnow), status: "error", error: "B.C. update failed" };
    delete manifest.air.bcMonitorsUrl;
    if (manifest.air.monitorSets) delete manifest.air.monitorSets.bcair;
    await route.fulfill({ response, json: manifest });
  });
  await page.goto("/"); await waitForReady(page);
  await page.getByTestId("view-air").click();
  await waitForReady(page);
  await expect(page.getByTestId("source-badge")).toHaveClass(/degraded/);
  await expect(page.getByTestId("source-badge")).toContainText("Partial");
  await expect(page.getByTestId("source-warning")).toContainText("British Columbia");
  await expect(page.getByTestId("error-state")).toHaveCount(0);
  await page.getByRole("button", { name: "Open methodology" }).click();
  await expect(page.getByRole("link", { name: "EPA AirNow" })).toBeVisible();
  await expect(page.getByRole("link", { name: "B.C. ENV" })).toBeVisible();
  await expect(page.getByRole("link", { name: "INECC/SINAICA" })).toBeVisible();
});

test("shows Air quality when monitor assets validate to empty", async ({ page }) => {
  await page.route("**/*monitors.json", async (route) => {
    await route.fulfill({ json: { monitors: [{ lat: 90, lon: 0, name: "Pole" }] } });
  });
  await page.goto("/"); await waitForReady(page);
  await page.getByTestId("view-air").click();
  await waitForReady(page);
  await expect(page.getByTestId("loading-state")).toBeHidden();
  await expect(page.getByTestId("error-state")).toHaveCount(0);
  await expect(page.getByTestId("source-warning")).toContainText("No official monitors");
});

test("settles and retries when every air-monitor asset fails", async ({ page }) => {
  let fail = true;
  let requests = 0;
  await page.route("**/*-monitors.json", (route) => {
    requests += 1;
    return fail ? route.abort("failed") : route.continue();
  });

  await page.goto("/"); await waitForReady(page);
  await page.getByTestId("view-air").click();
  await expect(page.getByTestId("loading-state")).toBeHidden();
  await expect(page.getByTestId("error-state")).toContainText("Current data couldn’t be downloaded. Check your connection and try again.");
  const failedRequests = requests;
  fail = false;
  await page.getByRole("button", { name: "Retry" }).click();
  await waitForReady(page);
  expect(requests).toBeGreaterThan(failedRequests);
  await expect(page.getByTestId("error-state")).toHaveCount(0);
});

test("hides comparable Air quality when only the undisplayed AQHI source remains", async ({ page }) => {
  await page.route("**/context/manifests/*.json", async (route) => {
    const response = await route.fetch();
    const manifest = await response.json();
    for (const name of ["airnow", "bcair", "sinaica"]) {
      manifest.sources[name] = { ...(manifest.sources[name] ?? manifest.sources.airnow), status: "unavailable", error: "Air sources unavailable" };
    }
    delete manifest.air.monitorsUrl;
    delete manifest.air.bcMonitorsUrl;
    delete manifest.air.sinaicaMonitorsUrl;
    delete manifest.air.monitorSets.airnow;
    delete manifest.air.monitorSets.bcair;
    delete manifest.air.monitorSets.sinaica;
    await route.fulfill({ response, json: manifest });
  });
  await page.goto("/"); await waitForReady(page);
  await expect(page.getByTestId("view-forecast")).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("view-air")).toBeDisabled();
  await expect(page.getByTestId("view-air")).toContainText("Unavailable");
  await expect(page.getByTestId("error-state")).toHaveCount(0);
});

test("keeps a healthy alternate view keyboard-accessible when Forecast is unavailable", async ({ page }) => {
  await page.route("**/context/manifests/*.json", async (route) => {
    const response = await route.fetch();
    const manifest = await response.json();
    manifest.sources.firework = { ...manifest.sources.firework, status: "unavailable", error: "Forecast unavailable" };
    if (manifest.sources.hrrr) manifest.sources.hrrr = { ...manifest.sources.hrrr, status: "unavailable", error: "Forecast unavailable" };
    manifest.forecast.frames = [];
    manifest.forecast.integratedStatus = "unavailable";
    if (manifest.forecasts) {
      for (const name of ["best", "hrrr", "firework"]) {
        if (manifest.forecasts[name]) manifest.forecasts[name].frames = [];
      }
      manifest.forecasts.best.integratedStatus = "unavailable";
    }
    await route.fulfill({ response, json: manifest });
  });
  await page.goto("/");
  await expect(page.getByTestId("error-state")).toContainText("Forecast unavailable");
  await expect(page.getByTestId("view-air")).toHaveAttribute("tabindex", "0");
  await page.getByTestId("view-air").focus();
  await page.keyboard.press("Enter");
  await waitForReady(page);
  await expect(page.getByTestId("view-air")).toHaveAttribute("aria-selected", "true");
  await searchCity(page, "Seattle");
  await expect(page.getByTestId("location-card")).toContainText("Seattle");
  await expect(page.getByTestId("location-smoke-value")).toHaveText("Unavailable");
  await expect(page.getByTestId("location-air-value")).toBeVisible();
});

test("rejects a malformed context manifest without a runtime crash", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/context/manifests/*.json", async (route) => {
    const response = await route.fetch();
    const manifest = await response.json();
    delete manifest.fires;
    await route.fulfill({ response, json: manifest });
  });
  await page.goto("/");
  await expect(page.getByTestId("error-state")).toContainText("Invalid context manifest");
  expect(errors).toEqual([]);
});

test("rejects the retired v1 context contract", async ({ page }) => {
  await page.route("**/context/latest.json**", async (route) => {
    const response = await route.fetch();
    const pointer = await response.json();
    pointer.version = 1;
    await route.fulfill({ response, json: pointer });
  });
  await page.goto("/");
  await expect(page.getByTestId("error-state")).toContainText("Invalid context pointer");
});

test("recovers after the context pointer fails", async ({ page }) => {
  let fail = true;
  await page.route("**/demo/context/latest.json*", (route) => fail ? route.abort() : route.continue());
  await page.goto("/");
  await expect(page.getByTestId("error-state")).toContainText("This view couldn’t load");
  const status = page.getByTestId("source-badge");
  await expect(status).toBeDisabled();
  await expect(status).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByRole("dialog", { name: "Data status" })).toHaveCount(0);
  fail = false;
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(status).toBeEnabled();
  await expect(status).toHaveAccessibleName(/Demo smoke outlook/);
  await waitForReady(page);
});

test("recovers after geographic context fails", async ({ page }) => {
  let fail = true;
  let requests = 0;
  await page.route("**/geo/north-america-v3.json", (route) => {
    requests += 1;
    return fail ? route.abort() : route.continue();
  });
  await page.goto("/");
  await expect(page.getByTestId("error-state")).toContainText("political boundaries", { timeout: 20_000 });
  const failedRequests = requests;
  fail = false;
  await page.getByRole("button", { name: "Retry" }).click();
  await waitForReady(page);
  expect(requests).toBeGreaterThan(failedRequests);
});

test("retries failed forecast textures at unchanged URLs", async ({ page }) => {
  let fail = true;
  let requests = 0;
  await page.route("**/{best,firework,hrrr}.png", (route) => {
    requests += 1;
    return fail ? route.abort() : route.continue();
  });
  await page.goto("/");
  await expect(page.getByTestId("error-state")).toContainText("This view couldn’t load");
  const failedRequests = requests;
  fail = false;
  await page.getByRole("button", { name: "Retry" }).click();
  await waitForReady(page);
  expect(requests).toBeGreaterThan(failedRequests);
});

test("refetches a semantically invalid forecast texture at the same URL", async ({ page }) => {
  let invalid = true;
  let requests = 0;
  const firstFrame = demoManifest.forecast.frames[0] as { textureUrl: string; sourceMaskUrl: string };
  await page.route((url) => url.pathname === firstFrame.textureUrl, async (route) => {
    requests += 1;
    if (!invalid) return route.continue();
    const mask = await page.request.get(firstFrame.sourceMaskUrl);
    await route.fulfill({ status: 200, contentType: "image/png", body: await mask.body() });
  });
  await page.goto("/");
  await expect(page.getByTestId("error-state")).toContainText("unknown display-palette color", { timeout: 20_000 });
  invalid = false;
  await page.getByRole("button", { name: "Retry" }).click();
  await waitForReady(page);
  expect(requests).toBeGreaterThanOrEqual(2);
});
