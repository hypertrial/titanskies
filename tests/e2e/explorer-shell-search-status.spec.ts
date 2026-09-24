import { expect, test } from "@playwright/test";
import { waitForReady, clickCityMarker, assertDesktopSecondarySpacing, searchCity, hoverNearbyAirMarker, useLiveContext } from "./explorer.fixtures";

test.describe("local-time live forecast", () => {
  test.use({ timezoneId: "America/Los_Angeles" });

  test("waits at the current local minute until Play and returns to sticky live following", async ({ page }) => {
    await useLiveContext(page, "2024-07-15T20:37:15Z");
    await page.goto("/");
    await waitForReady(page);
    const status = page.getByTestId("source-badge");
    await expect(status).toHaveAccessibleName(/Smoke outlook; publication healthy, published 8m ago; guidance 37m old; ECCC FireWork ok, NOAA HRRR ok/);
    await expect(status).toContainText("Updated 8m");
    await expect(page.getByTestId("play-toggle")).toHaveText("Play");
    await expect(page.getByTestId("live-control")).toHaveText("Live");
    const liveTarget = await page.getByTestId("live-control").boundingBox();
    expect(liveTarget?.width ?? 0).toBeGreaterThanOrEqual(44);
    expect(liveTarget?.height ?? 0).toBeGreaterThanOrEqual(44);
    const timeline = page.getByRole("slider", { name: "forecast timeline" });
    const start = Number(await timeline.inputValue());
    expect(start).toBeGreaterThanOrEqual(37 * 60_000 + 15_000);
    await page.getByTestId("play-toggle").click();
    await expect(page.getByTestId("play-toggle")).toHaveText("Pause");
    await expect(page.getByTestId("live-control")).toHaveText("Go live");
    await expect.poll(async () => Number(await timeline.inputValue()) - start, { timeout: 5_000 }).toBeGreaterThan(100_000);

    await page.getByTestId("live-control").click();
    await expect(page.getByTestId("live-control")).toHaveText("Live");
    await expect(page.getByTestId("play-toggle")).toHaveText("Play");
    await expect(page.getByTestId("obs-time").locator(".timeline-time-desktop")).toHaveText("Now · 1:37 PM PDT");
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByTestId("obs-time").locator(".timeline-time-mobile")).toHaveText("1:37 PM");
    await expect(page.getByTestId("obs-time")).toHaveAttribute("aria-label", /^Now, .*PDT$/);
    await page.clock.setFixedTime(new Date("2024-07-15T20:37:22Z"));
    await page.waitForTimeout(1_100);
    await expect(page.getByTestId("live-control")).toHaveText("Live");
  });

  test("warns when automatic publication health is stale", async ({ page }) => {
    await useLiveContext(page, "2024-07-15T20:37:15Z", 0, "unhealthy");
    await page.goto("/");
    await waitForReady(page);
    await expect(page.getByTestId("source-badge")).toHaveClass(/error/);
    await expect(page.getByTestId("source-badge")).toContainText("Stale · 35m");
    await expect(page.getByTestId("source-warning")).toContainText("Live data has not refreshed on schedule");
    await expect(page.getByRole("button", { name: "Check again" })).toBeVisible();
  });

  test("distinguishes active-source degradation from a failed publication", async ({ page }) => {
    await useLiveContext(page, "2024-07-15T20:37:15Z", 0, "degraded");
    await page.goto("/");
    await waitForReady(page);
    await page.getByTestId("source-badge").click();
    const status = page.getByRole("dialog", { name: "Data status" });
    await expect(status.getByText("Active data partially degraded")).toBeVisible();
    await expect(status.getByText("ECCC FireWork is stale.")).toBeVisible();
    await expect(page.getByTestId("source-warning")).toHaveCount(0);
  });

  test("keeps live freshness labels readable in compact toolbars", async ({ page }) => {
    await useLiveContext(page, "2024-07-15T20:37:15Z");
    for (const viewport of [{ width: 768, height: 1024 }, { width: 700, height: 390 }]) {
      await page.setViewportSize(viewport);
      await page.goto("/");
      await waitForReady(page);
      if (viewport.height > 500) await expect(page.getByTestId("location-search-input")).toHaveAttribute("placeholder", "Search major cities");
      await expect(page.locator(".status-badge b")).toHaveText(viewport.height > 500 ? "Updated 8m" : "8m ago");
      const lines = await page.locator(".status-badge b").evaluateAll((elements) => elements.map((element) => ({
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
      })));
      expect(lines).toHaveLength(1);
      expect(lines.every(({ clientWidth, scrollWidth }) => scrollWidth <= clientWidth)).toBe(true);
    }
  });

  test("keeps a globe inspect pin following the live minute", async ({ page }) => {
    await useLiveContext(page, "2024-07-15T20:37:15Z");
    await page.goto("/");
    await waitForReady(page);
    await page.getByTestId("interactive-globe").focus();
    for (let index = 0; index < 3; index += 1) await page.keyboard.press("ArrowLeft");
    for (let index = 0; index < 7; index += 1) await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    const details = page.getByTestId("details-card");
    await expect(page.getByTestId("live-control")).toHaveText("Live");
    await expect(page.getByTestId("inspect-marker")).toBeVisible();
    const selectedTime = await details.locator(".selection-meta time").getAttribute("datetime");
    await expect(page.getByTestId("obs-time").locator(".timeline-time-desktop")).toHaveText("Now · 1:37 PM PDT");
    await expect(page.getByRole("dialog")).not.toContainText("Loading…");
    await expect(details).toContainText("Interpolated from Higher-detail U.S. guidance to Canadian guidance + higher-detail U.S. enhancement");
    await page.clock.setFixedTime(new Date("2024-07-15T20:37:17Z"));
    await page.waitForTimeout(1_100);
    await expect.poll(async () => details.locator(".selection-meta time").getAttribute("datetime"), { timeout: 3_000 }).not.toBe(selectedTime);
    await expect(page.getByTestId("live-control")).toHaveText("Live");
  });

  test("shows the nearest valid time when the live bracket is unavailable", async ({ page }) => {
    await useLiveContext(page, "2024-07-15T20:37:15Z", 1);
    await page.goto("/");
    await waitForReady(page);
    await expect(page.getByTestId("live-control")).toHaveText("Unavailable");
    await expect(page.getByTestId("live-control")).toBeDisabled();
    await expect(page.getByTestId("play-toggle")).toHaveText("Play");
    await expect(page.getByTestId("obs-time")).toContainText(/[23]:\d{2} PM PDT/);
  });

  test("returns a future forecast inspection to Live before searching a city", async ({ page }) => {
    await useLiveContext(page, "2024-07-15T20:37:15Z");
    await page.goto("/"); await waitForReady(page);
    const timeline = page.getByRole("slider", { name: "forecast timeline" });
    await timeline.fill("7200000");
    await expect(page.getByTestId("live-control")).toHaveText("Go live");
    await searchCity(page, "Seattle");
    await expect(page.getByTestId("live-control")).toHaveText("Live");
    await expect(page.getByRole("dialog", { name: "Seattle" })).toBeVisible();
    expect(Number(await timeline.inputValue())).toBeLessThan(3_600_000);
  });

  test("preloads and resets to the new live bracket after an hour boundary", async ({ page }) => {
    await useLiveContext(page, "2024-07-15T20:37:15Z");
    await page.goto("/");
    await waitForReady(page);
    const speed = page.getByTestId("playback-speed");
    await speed.click();
    await page.clock.setFixedTime(new Date("2024-07-15T21:05:00Z"));
    const slider = page.getByRole("slider", { name: "forecast timeline" });
    const maximum = Number(await slider.getAttribute("max"));
    await slider.fill(String(maximum - 3_600_000));
    await page.evaluate(() => {
      const explorer = document.querySelector("main.explorer");
      const sliderElement = document.querySelector<HTMLInputElement>('input[aria-label="forecast timeline"]');
      (window as typeof window & { __liveResetStates?: Array<{ phase: string | null; position: number; resident: number }> }).__liveResetStates = [];
      if (!explorer || !sliderElement) return;
      const record = () => (window as typeof window & { __liveResetStates: Array<{ phase: string | null; position: number; resident: number }> }).__liveResetStates.push({
        phase: explorer.getAttribute("data-playback-phase"),
        position: Number(sliderElement.value),
        resident: Number(explorer.getAttribute("data-resident-frame-count")),
      });
      new MutationObserver(record).observe(explorer, { attributes: true, attributeFilter: ["data-playback-phase", "data-resident-frame-count"] });
      record();
    });
    await page.getByTestId("play-toggle").click();
    await expect.poll(async () => page.evaluate(() => (window as typeof window & { __liveResetStates?: Array<{ phase: string | null }> }).__liveResetStates?.some((state) => state.phase === "fading-in") ?? false), { timeout: 12_000 }).toBe(true);
    const states = await page.evaluate(() => (window as typeof window & { __liveResetStates?: Array<{ phase: string | null; position: number; resident: number }> }).__liveResetStates ?? []);
    expect(states.some((state) => state.phase === "waiting" && state.resident === 3)).toBe(true);
    expect(states.some((state) => state.phase === "fading-in" && state.position < 30 * 60_000)).toBe(true);
  });
});

test("publishes installable web app metadata and icons", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator('link[rel="manifest"]')).toHaveAttribute("href", "/manifest.webmanifest");
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute("content", "#02070c");
  await expect(page.locator('meta[name="viewport"]')).toHaveAttribute("content", /viewport-fit=cover/);

  const response = await page.request.get("/manifest.webmanifest");
  expect(response.ok()).toBe(true);
  expect(response.headers()["content-type"]).toContain("application/manifest+json");
  await expect(response.json()).resolves.toEqual(expect.objectContaining({
    name: "TitanSkies Smoke Forecast",
    short_name: "TitanSkies",
    id: "/",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "any",
    background_color: "#02070c",
    theme_color: "#02070c",
    icons: [
      { src: "/pwa-icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/pwa-icon-512.png", sizes: "512x512", type: "image/png" },
    ],
  }));

  const dimensions = await page.evaluate(async (sources) => Promise.all(sources.map((src) => new Promise<[number, number]>((resolve, reject) => {
    const icon = new Image();
    icon.onload = () => resolve([icon.naturalWidth, icon.naturalHeight]);
    icon.onerror = () => reject(new Error(`Failed to load ${src}`));
    icon.src = src;
  }))), ["/pwa-icon-192.png", "/pwa-icon-512.png"]);
  expect(dimensions).toEqual([[192, 192], [512, 512]]);
  expect(await page.evaluate(async () => "serviceWorker" in navigator ? (await navigator.serviceWorker.getRegistrations()).length : 0)).toBe(0);
});

test("opens Forecast by default with only Forecast and Air quality", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/");
  await expect(page).toHaveTitle("TitanSkies Smoke Forecast");
  await expect(page.locator(".brand-lockup")).toHaveAttribute("aria-label", "TitanSkies Smoke Forecast");
  const logo = page.locator(".brand-mark img");
  await expect.poll(() => logo.evaluate((image: HTMLImageElement) => [image.naturalWidth, image.naturalHeight])).toEqual([56, 56]);
  const logoResponse = await page.request.get("/brand/logo-icon.png");
  expect(logoResponse.ok()).toBe(true);
  expect((await logoResponse.body()).byteLength).toBeLessThanOrEqual(20_000);
  await expect(page.locator('meta[name="description"]')).toHaveAttribute("content", "Explore a continuous North America smoke outlook, official PM2.5 AQI monitor readings, and agency-reported wildfires.");
  const heading = page.getByRole("heading", { level: 1 });
  await expect(heading).toHaveText("North America smoke and air-quality explorer");
  await expect(heading).toHaveClass(/sr-only/);
  await expect(page.getByText("A continuous visual outlook combining Canadian coverage with higher-detail U.S. guidance.")).toHaveCount(0);
  await expect(page.getByRole("tab")).toHaveCount(2);
  await expect(page.getByTestId("view-forecast")).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("view-smoke")).toHaveCount(0);
  await expect(page.getByTestId("view-science")).toHaveCount(0);
  await waitForReady(page);
  await expect(page.getByTestId("forecast-toggle")).toHaveCount(0);
  await expect(page.getByTestId("incidents-toggle")).toHaveCount(0);
  await expect(page.locator(".marker-legend")).toHaveCount(0);
  await expect(page.getByText("Fire perimeters")).toHaveCount(0);
  await expect(page.getByText("Satellite hotspots")).toHaveCount(0);
  await expect(page.getByText("Faint")).toBeVisible();
  await expect(page.getByText("Moderate", { exact: true })).toBeVisible();
  await expect(page.getByText("Dense")).toBeVisible();
  await expect(page.locator(".map-legend-chip")).not.toContainText("µg/m³");
  const toolbar = await page.locator(".top-toolbar").boundingBox();
  const tabs = await page.getByRole("tablist", { name: "Data view" }).boundingBox();
  const airTab = await page.getByTestId("view-air").boundingBox();
  const searchField = await page.locator(".location-search-field").boundingBox();
  const dock = await page.getByRole("region", { name: "forecast timeline and controls" }).boundingBox();
  expect(toolbar?.height ?? 99).toBeLessThanOrEqual(56);
  expect(tabs?.x ?? 0).toBeGreaterThanOrEqual((toolbar?.x ?? 0) + 100);
  expect(airTab).toBeTruthy();
  expect(searchField).toBeTruthy();
  expect((airTab?.x ?? 0) + (airTab?.width ?? 0)).toBeLessThanOrEqual(searchField?.x ?? 0);
  expect(dock?.width ?? 999).toBeLessThanOrEqual(760);
  expect(dock?.height ?? 999).toBeLessThanOrEqual(64);
  const transportTargets = await page.locator(".transport button").evaluateAll((buttons) => buttons.map((button) => {
    const bounds = button.getBoundingClientRect();
    return { width: bounds.width, height: bounds.height };
  }));
  expect(transportTargets.every(({ width, height }) => width >= 44 && height >= 44)).toBe(true);
  await expect(page.locator(".scene")).toHaveCSS("width", "1280px");
});

test("spaces Worst 5, map controls, and status evenly on the desktop right toolbar", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/");
  await waitForReady(page);
  await assertDesktopSecondarySpacing(page);
  const iconBoxes = await page.locator(".toolbar-utilities .toolbar-action").evaluateAll((buttons) => buttons.map((button) => {
    const box = button.getBoundingClientRect();
    return { width: box.width, height: box.height };
  }));
  expect(iconBoxes.length).toBe(3);
  for (const box of iconBoxes) {
    expect(box.width).toBeGreaterThanOrEqual(43);
    expect(box.width).toBeLessThanOrEqual(45);
    expect(box.height).toBeGreaterThanOrEqual(43);
    expect(box.height).toBeLessThanOrEqual(45);
  }
  await page.getByTestId("view-air").click();
  await waitForReady(page);
  await expect(page.getByRole("button", { name: "Map options" })).toHaveCount(0);
  await assertDesktopSecondarySpacing(page);
  await expect(page.locator(".toolbar-utilities .toolbar-action")).toHaveCount(2);
  await page.setViewportSize({ width: 1024, height: 720 });
  await page.goto("/");
  await waitForReady(page);
  await assertDesktopSecondarySpacing(page);
  const toolbar = await page.locator(".top-toolbar").boundingBox();
  expect((toolbar?.x ?? 0) + (toolbar?.width ?? 0)).toBeLessThanOrEqual(1024);
  const dimensions = await page.evaluate(() => ({ body: document.body.scrollWidth, inner: window.innerWidth }));
  expect(dimensions.body).toBe(dimensions.inner);
});

test("shows only six overview anchor cities and reveals labels by zoom tier", async ({ page }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/"); await waitForReady(page, 45_000);
  const visibleLabels = async () => page.locator(".map-label.city").evaluateAll((labels) => labels
    .filter((label) => Number(getComputedStyle(label).opacity) > 0.05)
    .map((label) => ({ name: label.textContent, tier: label.getAttribute("data-label-tier") })));
  await expect.poll(async () => (await visibleLabels()).map(({ name }) => name).sort(), { timeout: 45_000 }).toEqual([
    "Anchorage", "Chicago", "Los Angeles", "Mexico City", "New York", "Vancouver",
  ]);
  const globe = page.getByTestId("interactive-globe");
  await globe.focus();
  await globe.press("+");
  await expect.poll(async () => (await visibleLabels()).filter(({ tier }) => tier === "primary").length, { timeout: 15_000 }).toBeGreaterThan(0);
  expect((await visibleLabels()).filter(({ tier }) => tier === "secondary")).toHaveLength(0);
  await globe.press("+");
  await globe.press("+");
  await expect.poll(async () => (await visibleLabels()).filter(({ tier }) => tier === "secondary").length, { timeout: 15_000 }).toBeGreaterThan(0);
  expect((await visibleLabels()).filter(({ tier }) => tier?.startsWith("detail-") || tier === "local")).toHaveLength(0);
});

test("deep zoom reveals collision-free cities and landmarks in all three countries", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/"); await waitForReady(page, 45_000);
  const globe = page.getByTestId("interactive-globe");
  for (const [city, country] of [["Toronto", "CAN"], ["New York", "USA"], ["Mexico City", "MEX"]] as const) {
    await searchCity(page, city);
    await globe.focus();
    for (let zoom = 0; zoom < 8; zoom += 1) await globe.press("+");
    const visible = page.locator(`.map-label[data-label-country="${country}"]`)
      .filter({ visible: true });
    await expect.poll(async () => visible.evaluateAll((labels) => labels.filter((label) => Number(getComputedStyle(label).opacity) > 0.05 && label.getAttribute("data-label-kind") === "landmark").length), { timeout: 15_000 }).toBeGreaterThan(0);
    await expect.poll(async () => visible.evaluateAll((labels) => labels.filter((label) => Number(getComputedStyle(label).opacity) > 0.05 && label.getAttribute("data-label-kind") === "city" && ["detail-major", "detail-regional", "detail-local", "local"].includes(label.getAttribute("data-label-tier") ?? "")).length), { timeout: 15_000 }).toBeGreaterThan(0);
  }
  await page.waitForTimeout(220);
  const landmarks = page.locator(".map-label.landmark");
  const landmark = landmarks.filter({ visible: true }).first();
  await expect(landmark).toHaveCSS("pointer-events", "none");
  const category = await landmark.getAttribute("data-label-category");
  const glyph = category === "natural" ? "△" : category === "park" ? "●" : "◆";
  await expect(landmark).toContainText(glyph);
  await globe.focus();
  await globe.press("Escape");
  const clickPoint = await landmarks.evaluateAll((labels) => labels
    .filter((label) => Number(getComputedStyle(label).opacity) > 0.05)
    .map((label) => {
      const rect = label.getBoundingClientRect();
      const x = rect.left + rect.width / 2;
      const y = rect.top + rect.height / 2;
      const target = document.elementFromPoint(x, y);
      return target?.closest('[data-testid="interactive-globe"]') ? { x, y } : null;
    })
    .find(Boolean));
  expect(clickPoint).toBeTruthy();
  await page.mouse.click(clickPoint?.x ?? 0, clickPoint?.y ?? 0);
  await expect(page.getByTestId("details-card")).toBeVisible();
  const rectangles = await page.locator(".map-label").evaluateAll((labels) => labels
    .filter((label) => Number(getComputedStyle(label).opacity) > 0.05)
    .map((label) => {
      const rect = label.getBoundingClientRect();
      return { id: label.getAttribute("data-label-id"), left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom };
    }));
  for (let left = 0; left < rectangles.length; left += 1) {
    for (let right = left + 1; right < rectangles.length; right += 1) {
      expect(rectangles[left].right <= rectangles[right].left || rectangles[right].right <= rectangles[left].left
        || rectangles[left].bottom <= rectangles[right].top || rectangles[right].bottom <= rectangles[left].top).toBe(true);
    }
  }
});

test("compact deep zoom enforces place-label caps and collision spacing", async ({ page }) => {
  test.setTimeout(120_000);
  for (const { viewport, cap } of [
    { viewport: { width: 390, height: 844 }, cap: 32 },
    { viewport: { width: 844, height: 390 }, cap: 36 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto("/"); await waitForReady(page, 45_000);
    await searchCity(page, "Toronto");
    const globe = page.getByTestId("interactive-globe");
    await globe.focus();
    for (let zoom = 0; zoom < 8; zoom += 1) await globe.press("+");
    const layer = page.getByTestId("map-label-layer");
    await expect.poll(async () => Number(await layer.getAttribute("data-label-accepted-count")), { timeout: 15_000 }).toBeGreaterThan(6);
    let previousRevision = -1;
    let stable = 0;
    await expect.poll(async () => {
      const revision = Number(await layer.getAttribute("data-label-layout-revision"));
      stable = revision === previousRevision ? stable + 1 : 0;
      previousRevision = revision;
      return stable >= 2;
    }, { intervals: [250], timeout: 10_000 }).toBe(true);
    const places = page.locator('.map-label:not(.country)');
    expect(await places.count()).toBeLessThanOrEqual(cap);
    const overlap = await page.locator(".map-label").evaluateAll((labels) => {
      const rectangles = labels.filter((label) => Number(getComputedStyle(label).opacity) > 0.05)
        .map((label) => {
          const rect = label.getBoundingClientRect();
          return { label: label.textContent, left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom };
        });
      for (let left = 0; left < rectangles.length; left += 1) {
        for (let right = left + 1; right < rectangles.length; right += 1) {
          if (rectangles[left].right > rectangles[right].left && rectangles[right].right > rectangles[left].left
            && rectangles[left].bottom > rectangles[right].top && rectangles[right].bottom > rectangles[left].top) {
            return JSON.stringify({ viewport, a: rectangles[left], b: rectangles[right] });
          }
        }
      }
      return null;
    });
    expect(overlap).toBeNull();
  }
});

test("keeps all three desktop toolbar groups separated", async ({ page }) => {
  for (const viewport of [{ width: 1024, height: 768 }, { width: 1280, height: 720 }, { width: 1440, height: 900 }]) {
    await page.setViewportSize(viewport);
    await page.goto("/"); await waitForReady(page);
    const groups = await page.locator(".toolbar-cluster").evaluateAll((elements) => elements.map((element) => {
      const bounds = element.getBoundingClientRect();
      return { left: bounds.left, right: bounds.right, width: bounds.width, height: bounds.height };
    }));
    expect(groups).toHaveLength(3);
    expect(groups[0].right).toBeLessThanOrEqual(groups[1].left);
    expect(groups[1].right).toBeLessThanOrEqual(groups[2].left);
    expect(groups.every(({ height }) => height <= 56)).toBe(true);
    if (viewport.width >= 1280) expect(groups[1].width).toBeGreaterThanOrEqual(280);

    const dock = await page.getByRole("region", { name: "forecast timeline and controls" }).boundingBox();
    const dockContent = await page.locator(".playback-main").boundingBox();
    const timeline = await page.locator(".timeline-wrap").boundingBox();
    const visibleTime = await page.locator(".observation-time time").boundingBox();
    const horizon = await page.locator(".forecast-label").boundingBox();
    const legend = await page.locator(".map-legend-chip").boundingBox();
    expect(dock).toBeTruthy();
    expect(dockContent).toBeTruthy();
    expect(timeline).toBeTruthy();
    expect(visibleTime).toBeTruthy();
    expect(horizon).toBeTruthy();
    expect(legend).toBeTruthy();
    expect(dockContent!.x).toBeGreaterThanOrEqual(dock!.x);
    expect(dockContent!.x + dockContent!.width).toBeLessThanOrEqual(dock!.x + dock!.width);
    expect(visibleTime!.x).toBeGreaterThanOrEqual(timeline!.x + timeline!.width);
    expect(horizon!.x + horizon!.width).toBeLessThanOrEqual(dock!.x + dock!.width);
    const overlapsLegend = dock!.x < legend!.x + legend!.width
      && dock!.x + dock!.width > legend!.x
      && dock!.y < legend!.y + legend!.height
      && dock!.y + dock!.height > legend!.y;
    expect(overlapsLegend).toBe(false);
  }
});

test("keeps the compact landscape timeline below its timestamp", async ({ page }) => {
  await page.setViewportSize({ width: 500, height: 390 });
  await page.goto("/"); await waitForReady(page);
  const timeline = await page.locator(".timeline-wrap").boundingBox();
  const visibleTime = await page.locator(".observation-time time").boundingBox();
  expect(timeline).toBeTruthy();
  expect(visibleTime).toBeTruthy();
  expect(visibleTime!.y + visibleTime!.height).toBeLessThanOrEqual(timeline!.y);
});

test("opens complete Data status from concise freshness and restores focus", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  const trigger = page.getByTestId("source-badge");
  await expect(trigger).toContainText("Demo");
  await page.getByTestId("play-toggle").click();
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", "playing", { timeout: 5_000 });
  await trigger.focus();
  await page.keyboard.press("Space");
  const position = await page.getByRole("slider", { name: "forecast timeline" }).inputValue();
  const drawer = page.getByRole("dialog", { name: "Data status" });
  await expect(drawer).toBeVisible();
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", /paused/);
  await expect(drawer.getByText("Demo publication")).toBeVisible();
  await expect(drawer.getByText("Publication", { exact: true })).toBeVisible();
  await expect(drawer.getByText("Guidance", { exact: true })).toBeVisible();
  await expect(drawer.getByText("ECCC FireWork")).toBeVisible();
  await expect(drawer.getByText("NOAA HRRR")).toBeVisible();
  await page.waitForTimeout(250);
  await expect(page.getByRole("slider", { name: "forecast timeline" })).toHaveValue(position);
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("explains invalid publication assets in Data status", async ({ page }) => {
  await useLiveContext(page, "2024-07-15T20:30:00Z", 0, "degraded", ["invalid-assets"]);
  await page.goto("/");
  await waitForReady(page);
  await page.getByTestId("source-badge").click();
  await expect(page.getByRole("dialog", { name: "Data status" })).toContainText("One or more published data assets are missing or invalid.");
});

test("searches major cities with keyboard access and shows current smoke plus nearby AQI", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  const search = page.getByTestId("location-search-input");
  await search.fill("san");
  await expect(page.getByRole("option")).toHaveCount(1);
  await expect(page.getByRole("option")).toContainText("San Francisco");
  await expect(page.getByRole("option")).not.toContainText("Los Angeles");
  await search.fill("seattle washington");
  await expect(search).toHaveCSS("outline-style", "none");
  await expect(search).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("option")).toHaveCount(1);
  await search.press("Enter");
  const card = page.getByTestId("location-card");
  await expect(card).toContainText("Seattle");
  await expect(card).toContainText("Washington, United States");
  await expect(card.getByTestId("location-smoke-value")).not.toHaveText("Loading…", { timeout: 10_000 });
  await expect(card.getByTestId("location-air-value")).toContainText("AQI");
  await expect(card).toContainText("1.5 km");
  await expect(card).toContainText("Forecast smoke and station air quality are separate datasets.");
  await expect(page.getByTestId("interactive-globe")).toHaveAttribute("data-focus-location", /-122\.3153,47\.6004/);
  await expect.poll(async () => (await page.getByTestId("interactive-globe").getAttribute("data-visible-detail-tiles"))?.length ?? 0, { timeout: 5_000 }).toBeGreaterThan(0);

  await page.getByRole("button", { name: "Close seattle" }).click();
  await expect(search).toBeFocused();
  await page.getByTestId("source-badge").focus();
  await search.click();
  await expect(search).toHaveAttribute("aria-expanded", "true");
  await page.reload();
  await waitForReady(page);
  const reloadedSearch = page.getByTestId("location-search-input");
  await reloadedSearch.focus();
  await expect(page.getByText("Recent searches")).toBeVisible();
  await expect(page.getByRole("option")).toContainText("Seattle");
  await reloadedSearch.fill("Seattle");
  await page.getByRole("button", { name: "Clear location search" }).click();
  await expect(page.getByText("Recent searches")).toBeVisible();
  await expect(page.getByRole("option")).toContainText("Seattle");
  await page.getByRole("button", { name: "Clear recent searches" }).click();
  await expect(page.getByText("Recent searches")).toHaveCount(0);
});

test("announces search guidance and zero results outside the empty listbox", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  const search = page.getByTestId("location-search-input");
  await search.focus();
  const results = page.getByRole("listbox", { name: "Location results" });
  const menu = results.locator("..");
  await expect(results.getByRole("option")).toHaveCount(0);
  await expect(menu.getByRole("status")).toHaveText("Search 74 curated city centers. Try Seattle, Montréal, or Mexico City.");

  await search.fill("zzzz");
  await expect(results.getByRole("option")).toHaveCount(0);
  await expect(menu.getByRole("status")).toHaveText("No match in the 74 curated major cities.");
});

test("clears a temporary city marker when its selection surface is dismissed or replaced", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  const globe = page.getByTestId("interactive-globe");
  const search = page.getByTestId("location-search-input");

  await searchCity(page, "Seattle");
  await expect(globe).not.toHaveAttribute("data-focus-location", "");
  await search.focus();
  await expect(page.getByTestId("location-card")).toHaveCount(0);
  await expect(globe).toHaveAttribute("data-focus-location", "");

  await search.press("Enter");
  await expect(page.getByTestId("location-card")).toBeVisible();
  await page.getByRole("button", { name: "Open smoke legend" }).click();
  const legend = page.getByRole("dialog", { name: "Smoke legend" });
  await expect(legend).toBeVisible();
  await expect(legend).toContainText("TitanSkies display scale · µg/m³");
  await expect(legend.getByTestId("forecast-legend-scale").locator("small")).toHaveText(["1", "100", "250", "500", "1000+"]);
  await expect(page.getByTestId("location-card")).toHaveCount(0);
  await expect(globe).toHaveAttribute("data-focus-location", "");
});

test("shows separate accessible top-five smoke and comparable AQI rankings", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  const trigger = page.getByRole("button", { name: "Worst 5 conditions" });
  await expect(trigger).toContainText("Worst 5");
  await expect(trigger).toHaveCSS("color", "rgb(255, 155, 156)");
  await expect(trigger).toHaveCSS("background-color", "rgba(18, 35, 45, 0.46)");
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
  await trigger.click();
  const panel = page.getByRole("dialog", { name: "Highest conditions" });
  await expect(panel).toBeVisible();
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  await expect(trigger).toHaveCSS("background-color", "rgba(255, 111, 112, 0.17)");
  const smokeTab = panel.getByRole("tab", { name: "Modeled smoke" });
  const aqiTab = panel.getByRole("tab", { name: "PM2.5 AQI" });
  await expect(panel.getByRole("heading", { name: "Highest conditions" })).toBeFocused();
  await expect(smokeTab).toHaveAttribute("aria-selected", "true");
  await expect(panel.getByRole("tabpanel", { name: "Modeled smoke" })).toContainText("74 city centers · Valid");
  await expect(panel.locator(".top-conditions-list li")).toHaveCount(5);
  await expect(panel.locator(".top-conditions-list button").first()).toHaveAccessibleName(/, .+, (Canada|United States|Mexico), ranked 1/);
  await smokeTab.press("ArrowRight");
  await expect(aqiTab).toBeFocused();
  await expect(aqiTab).toHaveAttribute("aria-selected", "true");
  await expect(panel.getByRole("tabpanel", { name: "PM2.5 AQI" })).toContainText("74 city centers · Stations within 50 km");
  await expect(panel.locator(".top-conditions-list li")).toHaveCount(5);
  await expect(panel.locator(".top-conditions-list button", { hasText: "AQHI" })).toHaveCount(0);
  const longCategory = panel.getByText("Unhealthy for sensitive groups").first();
  await expect(longCategory).toBeVisible();
  expect((await longCategory.boundingBox())?.height ?? 0).toBeGreaterThan(18);
  await expect(panel.getByRole("button", { name: "How rankings work" })).toBeVisible();
  await aqiTab.press("Escape");
  await expect(panel).toBeHidden();
  await expect(trigger).toBeFocused();
  await trigger.click();
  await page.locator(".brand-lockup").click();
  await expect(panel).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("keeps open AQI rankings current across a publication refresh", async ({ page }) => {
  await useLiveContext(page, "2024-07-15T20:30:00Z");
  await page.goto("/");
  await waitForReady(page);
  await page.getByRole("button", { name: "Worst 5 conditions" }).click();
  const panel = page.getByRole("dialog", { name: "Highest conditions" });
  await panel.getByRole("tab", { name: "PM2.5 AQI" }).click();
  const rows = panel.getByRole("tabpanel", { name: "PM2.5 AQI" }).locator(".top-conditions-list li");
  await expect(rows).toHaveCount(5);

  let refreshes = 0;
  let monitorRefreshes = 0;
  await page.route("**/*-monitors.json?publication=next", async (route) => {
    const response = await route.fetch();
    const payload = await response.json();
    payload.monitors = payload.monitors.map((monitor: Record<string, unknown>) => ({
      ...monitor,
      observedAt: "2024-07-15T20:31:00Z",
    }));
    await route.fulfill({ response, json: payload });
    monitorRefreshes += 1;
  });
  await page.route("**/context/manifests/*.json", async (route) => {
    refreshes += 1;
    const response = await route.fetch();
    const manifest = await response.json();
    manifest.mode = "live";
    manifest.generatedAt = "2024-07-15T20:30:00Z";
    manifest.air.observedAt = "2024-07-15T20:31:00Z";
    for (const set of Object.values(manifest.air.monitorSets) as Array<Record<string, unknown>>) {
      set.observedAt = "2024-07-15T20:31:00Z";
      set.url = `${set.url}?publication=next`;
    }
    for (const name of ["airnow", "bcair", "sinaica", "aqhi"]) {
      manifest.sources[name].checkedAt = "2024-07-15T20:31:00Z";
      manifest.sources[name].observedAt = "2024-07-15T20:31:00Z";
    }
    await route.fulfill({ response, json: manifest });
  });

  await page.clock.setFixedTime(new Date("2024-07-15T20:32:00Z"));
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect.poll(() => refreshes).toBeGreaterThan(0);
  await expect(rows).toHaveCount(5);
  await expect(panel.getByText("No recent comparable city monitor readings are available.")).toHaveCount(0);
  await expect.poll(() => monitorRefreshes).toBe(4);
  await page.unrouteAll({ behavior: "wait" });
});

test("preserves the ranked future forecast time when focusing a top smoke city", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  const timeline = page.getByRole("slider", { name: "forecast timeline" });
  await timeline.fill("7200000");
  await expect.poll(async () => page.locator(".explorer").getAttribute("data-playback-position")).toBe("7200000");
  await page.getByRole("button", { name: "Worst 5 conditions" }).click();
  const smokePanel = page.getByRole("tabpanel", { name: "Modeled smoke" });
  await expect(smokePanel.locator(".top-conditions-list li")).toHaveCount(5);
  await expect(timeline).toHaveValue("7200000");
  await smokePanel.locator(".top-conditions-list button").first().click();
  const card = page.getByTestId("location-card");
  await expect(page.getByRole("dialog", { name: /.+/ })).toContainText("Selected location");
  await expect(card.getByTestId("location-smoke-value")).not.toHaveText("Loading…", { timeout: 10_000 });
  await expect(timeline).toHaveValue("7200000");
  await expect(page.getByTestId("interactive-globe")).toHaveAttribute("data-focus-location", /.+,.+/);
});

test("defaults top conditions to AQI in Air quality and keeps the active view", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  await page.getByTestId("view-air").click(); await waitForReady(page);
  await page.getByRole("button", { name: "Worst 5 conditions" }).click();
  const panel = page.getByRole("dialog", { name: "Highest conditions" });
  await expect(panel.getByRole("tab", { name: "PM2.5 AQI" })).toHaveAttribute("aria-selected", "true");
  await expect(panel.getByRole("tabpanel", { name: "PM2.5 AQI" }).locator(".top-conditions-list li")).toHaveCount(5);
  await panel.getByRole("tab", { name: "Modeled smoke" }).click();
  await expect(panel.getByRole("tabpanel", { name: "Modeled smoke" }).locator(".top-conditions-list li")).toHaveCount(5, { timeout: 10_000 });
  await expect(page.getByTestId("view-air")).toHaveAttribute("aria-selected", "true");
});

test("keeps top conditions bounded on mobile and in short landscape", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await page.goto("/"); await waitForReady(page);
  await page.getByRole("button", { name: "Worst 5 conditions" }).click();
  const panel = page.getByRole("dialog", { name: "Highest conditions" });
  const portraitPanel = await panel.boundingBox();
  const portraitTabs = await page.getByRole("tablist", { name: "Data view" }).boundingBox();
  const portraitDock = page.getByRole("region", { name: "forecast timeline and controls" });
  expect(portraitPanel).toBeTruthy();
  expect(portraitTabs).toBeTruthy();
  await expect(portraitDock).toBeHidden();
  expect(portraitPanel?.x).toBeGreaterThanOrEqual(8);
  expect((portraitPanel?.x ?? 0) + (portraitPanel?.width ?? 0)).toBeLessThanOrEqual(352);
  expect((portraitPanel?.y ?? 0) + (portraitPanel?.height ?? 0)).toBeLessThanOrEqual((portraitTabs?.y ?? 740) - 8);

  await page.setViewportSize({ width: 667, height: 375 });
  const landscapePanel = await panel.boundingBox();
  expect(landscapePanel?.x).toBeGreaterThanOrEqual(290);
  expect(landscapePanel?.width).toBeLessThanOrEqual(360);
  await expect(page.getByRole("region", { name: "forecast timeline and controls" })).toBeHidden();
  await expect(panel.locator(".top-conditions-list")).toBeVisible();
});

test("preserves the air-quality view and uses an explicitly labeled Canadian AQHI fallback", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  await page.getByTestId("view-air").click(); await waitForReady(page);
  await searchCity(page, "Montreal");
  await expect(page.getByTestId("view-air")).toHaveAttribute("aria-selected", "true");
  const card = page.getByTestId("location-card");
  await expect(card).toContainText("Québec, Canada");
  await expect(card).toContainText("AQHI");
  await expect(card.getByTestId("location-air-value")).toContainText("AQHI");
});

test("keeps Alaska searchable while clearly reporting unavailable modeled smoke", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  await searchCity(page, "Anchorage");
  const card = page.getByTestId("location-card");
  await expect(card.getByTestId("location-smoke-value")).toHaveText("Unavailable");
  await expect(card).toContainText("Modeled smoke unavailable outside forecast coverage.");
  await expect(card.getByTestId("location-air-value")).toContainText("AQI");
});

test("associates data tabs with the visible globe view instead of hidden mobile layers", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/"); await waitForReady(page);
  const forecastTab = page.getByRole("tab", { name: "Forecast" });
  await expect(forecastTab).toHaveAttribute("aria-controls", "view-panel");
  await expect(page.getByRole("tabpanel", { name: "Forecast" })).toBeVisible();
  await expect(page.getByTestId("forecast-toggle")).toHaveCount(0);

  await page.getByRole("tab", { name: "Air quality" }).click();
  await waitForReady(page);
  await expect(page.getByRole("tabpanel", { name: "Air quality" })).toBeVisible();
  await expect(page.getByTestId("air-toggle")).toHaveCount(0);
});

test("offers a branded recovery path for unknown routes", async ({ page }) => {
  await page.goto("/not-a-page");
  await expect(page.getByRole("heading", { name: "This smoke-map page isn’t here." })).toBeVisible();
  const recoveryLink = page.getByRole("link", { name: "Return to the smoke outlook" });
  await expect(recoveryLink).toHaveAttribute("href", "/");
  await recoveryLink.click();
  await expect(page.getByRole("heading", { name: "North America smoke and air-quality explorer" })).toBeAttached();
});

test("keeps the intrinsic forecast smoke layer visible", async ({ page }) => {
  await page.goto("/");
  await waitForReady(page);
  const canvas = page.locator("canvas");
  expect((await canvas.screenshot()).byteLength).toBeGreaterThan(10_000);
  await expect(page.getByTestId("forecast-toggle")).toHaveCount(0);
  await expect(page.getByTestId("interactive-globe")).toHaveAttribute("data-air-representation", "none");
});

test("provides a useful fallback when WebGL2 is unavailable", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 640 });
  await page.addInitScript(() => {
    HTMLCanvasElement.prototype.getContext = new Proxy(HTMLCanvasElement.prototype.getContext, {
      apply(target, thisArg, args) { return args[0] === "webgl2" ? null : Reflect.apply(target, thisArg, args); },
    });
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "WebGL2 is required for the interactive globe." })).toBeVisible();
  await expect(page.getByTestId("fallback-sources")).toContainText("NOAA HRRR");
  await expect(page.getByTestId("fallback-sources")).toContainText("AirNow");
  await expect(page.getByTestId("fallback-sources")).toContainText("Demo · ok");
  const currentData = page.getByRole("link", { name: "Open current data" });
  await expect(currentData).toHaveAttribute("href", /context\/latest\.json/);
  await currentData.scrollIntoViewIfNeeded();
  await expect(currentData).toBeVisible();
  const dimensions = await page.evaluate(() => ({ body: document.body.scrollWidth, document: document.documentElement.scrollWidth, inner: window.innerWidth }));
  expect(dimensions.body).toBe(dimensions.inner);
  expect(dimensions.document).toBe(dimensions.inner);
});

test("renders official PM2.5 station dots without local-index controls", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  await page.getByTestId("view-air").click();
  await waitForReady(page);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("North America smoke and air-quality explorer");
  await expect(page.getByTestId("source-badge")).toHaveAccessibleName(/Demo air quality/);
  await expect(page.getByTestId("air-toggle")).toHaveCount(0);
  await expect(page.getByTestId("interactive-globe")).toHaveAttribute("data-air-representation", "points");
  await expect(page.locator('[data-testid^="air-mode-"]')).toHaveCount(0);
  await expect(page.getByText("Local indices", { exact: false })).toHaveCount(0);
  await expect(page.locator(".map-legend-chip")).not.toContainText("0–50");
  await page.getByRole("button", { name: "Open AQI legend" }).click();
  const aqiCategories = page.getByLabel("US EPA PM2.5 AQI categories");
  await expect(aqiCategories).toContainText("Good");
  await expect(aqiCategories).toContainText("Sensitive groups");
  await expect(aqiCategories).toContainText("Hazardous");
  for (const name of ["Good, AQI 0–50", "Moderate, AQI 51–100", "Sensitive groups, AQI 101–150", "Unhealthy, AQI 151–200", "Very unhealthy, AQI 201–300", "Hazardous, AQI 301+"]) {
    await expect(aqiCategories.getByLabel(name)).toBeVisible();
  }
  await page.getByRole("button", { name: "Close AQI legend" }).click();
  await page.getByRole("button", { name: "Open methodology" }).click();
  await expect(page.getByRole("link", { name: "EPA AirNow" })).toBeVisible();
  await expect(page.getByRole("link", { name: "B.C. ENV" })).toBeVisible();
  await expect(page.getByRole("link", { name: "INECC/SINAICA" })).toBeVisible();
  await expect(page.getByTestId("play-toggle")).toHaveCount(0);
  await expect(page.getByTestId("forecast-toggle")).toHaveCount(0);
  expect((await page.locator("canvas").screenshot()).byteLength).toBeGreaterThan(10_000);
});

test("keeps official station selection intrinsic to the Air quality view", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  await page.getByTestId("view-air").click();
  await waitForReady(page);
  await clickCityMarker(page, "Edmonton");
  await expect(page.getByTestId("details-card")).toBeVisible();
});

test("opens exact official station details", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  await page.getByTestId("view-air").click();
  await waitForReady(page);
  await clickCityMarker(page, "Edmonton");
  const details = page.getByTestId("details-card");
  await expect(details).toBeVisible();
  const nearby = details.locator(".nearby-observations button").first();
  const index = details.locator(".selection-value");
  await expect(nearby.or(index)).toBeVisible();
  if (await nearby.isVisible()) await nearby.click();
  await expect(details).toContainText(/AQI|AQHI/);
  await expect(details.locator(".selection-meta")).toContainText(/Observed (?:\d+ min old|[0-2] hr \d+ min old)/);
  await expect(details).toContainText("Agency");
});

test("keyboard Enter selects a nearby official monitor", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  await page.getByTestId("view-air").click();
  await waitForReady(page);
  const globe = page.getByTestId("interactive-globe");
  await globe.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("details-card")).toContainText(/AQI|AQHI/);
});

test("keeps air-quality controls usable without mobile overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/"); await waitForReady(page);
  await page.getByTestId("view-air").click();
  await waitForReady(page);
  const globe = page.getByTestId("interactive-globe");
  await globe.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("details-card")).toBeVisible();
  await page.getByRole("button", { name: "More options" }).click();
  await expect(page.getByTestId("details-card")).toHaveCount(0);
  const more = page.getByRole("dialog", { name: "More" });
  await expect(more.getByRole("button", { name: /Map options/ })).toHaveCount(0);
  await expect(more.getByRole("button", { name: /Legend/ })).toHaveCount(0);
  await expect(page.getByTestId("air-toggle")).toHaveCount(0);
  await expect(page.locator('[data-testid^="air-mode-"]')).toHaveCount(0);
  const sheet = await more.boundingBox();
  const tabs = await page.getByRole("tablist", { name: "Data view" }).boundingBox();
  expect(sheet).toBeTruthy();
  expect(tabs).toBeTruthy();
  expect((sheet?.y ?? 0) + (sheet?.height ?? 0)).toBeLessThanOrEqual((tabs?.y ?? 0) - 8);
  const widths = await page.evaluate(() => ({ body: document.body.scrollWidth, viewport: window.innerWidth }));
  expect(widths.body).toBe(widths.viewport);
});

test("keeps compact mobile details clear of navigation", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 650 });
  await useLiveContext(page, "2024-07-15T20:37:15Z");
  await page.goto("/"); await waitForReady(page);
  const live = await page.getByTestId("live-control").boundingBox();
  const forecastTabs = await page.getByRole("tablist", { name: "Data view" }).boundingBox();
  const forecastDock = await page.locator(".playback-dock").boundingBox();
  expect(live?.height ?? 0).toBeGreaterThanOrEqual(24);
  expect(forecastTabs).toBeTruthy();
  expect(forecastDock).toBeTruthy();
  expect((forecastDock?.y ?? 0) + (forecastDock?.height ?? 0)).toBeLessThanOrEqual((forecastTabs?.y ?? 0) - 8);
  await page.getByTestId("view-air").click();
  await waitForReady(page);
  const globe = page.getByTestId("interactive-globe");
  await globe.focus();
  await page.keyboard.press("Enter");
  const details = await page.getByTestId("details-card").boundingBox();
  const tabs = await page.getByRole("tablist", { name: "Data view" }).boundingBox();
  expect(details).toBeTruthy();
  expect(tabs).toBeTruthy();
  expect((details?.y ?? 0) + (details?.height ?? 0)).toBeLessThanOrEqual((tabs?.y ?? 0) - 8);
});

test("keeps desktop AQI details in the shared drawer clear of the legend", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/"); await waitForReady(page);
  await page.getByTestId("view-air").click();
  await waitForReady(page);
  await clickCityMarker(page, "Edmonton");
  const details = await page.getByTestId("details-card").boundingBox();
  const drawer = await page.getByRole("dialog").boundingBox();
  const legend = await page.getByRole("button", { name: "Open AQI legend" }).boundingBox();
  expect(details).toBeTruthy();
  expect(drawer).toBeTruthy();
  expect(legend).toBeTruthy();
  expect(drawer?.x ?? 0).toBeGreaterThan((legend?.x ?? 0) + (legend?.width ?? 0));
});

test("shows a block AQI hover label on desktop station hover", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/"); await waitForReady(page);
  await page.getByTestId("view-air").click();
  await waitForReady(page);
  const hover = await hoverNearbyAirMarker(page, "Edmonton");
  await expect(hover).toBeVisible();
  await expect(hover).toContainText(/stations|AQI|AQHI/);
  await expect(hover).toHaveCSS("pointer-events", "none");
  await expect(hover).toHaveCSS("display", "block");
  const metrics = await hover.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const lines = [...element.children].map((child) => child.getBoundingClientRect());
    return {
      width: rect.width,
      height: rect.height,
      textWidth: Math.max(...lines.map((line) => line.width)),
      contained: lines.every((line) => line.left >= rect.left - 1 && line.right <= rect.right + 1 && line.top >= rect.top - 1 && line.bottom <= rect.bottom + 1),
    };
  });
  expect(metrics.width).toBeGreaterThanOrEqual(metrics.textWidth);
  expect(metrics.width).toBeGreaterThan(metrics.height);
  expect(metrics.contained).toBe(true);
});

test("hides AQI hover labels on narrow viewports", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/"); await waitForReady(page);
  await page.getByTestId("view-air").click();
  await waitForReady(page);
  const canvas = page.getByTestId("interactive-globe");
  const box = await canvas.boundingBox();
  expect(box).toBeTruthy();
  await page.mouse.move((box?.x ?? 0) + (box?.width ?? 0) / 2, (box?.y ?? 0) + (box?.height ?? 0) / 2);
  await page.mouse.move((box?.x ?? 0) + (box?.width ?? 0) / 2 + 32, (box?.y ?? 0) + (box?.height ?? 0) / 2);
  await expect(page.getByTestId("map-hover-label")).toHaveCount(0);
});

test("opens the mobile legend through the contextual map chip", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/"); await waitForReady(page);
  const trigger = page.getByRole("button", { name: "Open smoke legend" });
  await trigger.click();
  await expect(page.getByRole("dialog", { name: "Smoke legend" })).toBeVisible();
  await expect(page.getByRole("dialog", { name: "Smoke legend" }).getByTestId("forecast-legend")).toBeVisible();
  const legend = await page.getByRole("dialog", { name: "Smoke legend" }).boundingBox();
  expect(legend).toBeTruthy();
  const tabs = await page.getByRole("tablist", { name: "Data view" }).boundingBox();
  expect(tabs).toBeTruthy();
  expect((legend?.y ?? 0) + (legend?.height ?? 0)).toBeLessThanOrEqual((tabs?.y ?? 0) - 8);
  await expect(page.locator(".playback-dock")).toBeHidden();
  await page.getByRole("button", { name: "Close smoke legend" }).click();
  await expect(trigger).toBeFocused();
});

test("exposes the keyboard-driven globe as a named interactive region", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  await expect(page.getByRole("region", { name: "Interactive globe" })).toHaveAttribute("aria-keyshortcuts", "ArrowUp ArrowDown ArrowLeft ArrowRight Enter Escape + -");
});

test("shows exactly one reset control at desktop and mobile sizes", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/"); await waitForReady(page);
  await expect(page.getByTestId("reset-view")).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByTestId("reset-view")).toBeHidden();
  await page.getByRole("button", { name: "More options" }).click();
  await expect(page.getByRole("dialog", { name: "More" }).getByRole("button", { name: /Reset view/ })).toBeVisible();
});

for (const width of [480, 500, 700, 844]) test(`keeps the ${width}px short-landscape map full-bleed beneath compact controls`, async ({ page }) => {
  await page.setViewportSize({ width, height: 390 });
  await page.goto("/"); await waitForReady(page);
  const scene = await page.locator(".scene").boundingBox();
  const dock = await page.getByRole("region", { name: "forecast timeline and controls" }).boundingBox();
  expect(scene).toBeTruthy();
  expect(dock).toBeTruthy();
  expect(scene?.x).toBe(0);
  expect(scene?.width).toBe(width);
  expect(dock?.width ?? 999).toBeLessThanOrEqual(660);
  expect(dock?.height ?? 999).toBeLessThanOrEqual(72);
  const tabIndicator = await page.getByRole("tablist", { name: "Data view" }).evaluate((element) => {
    const tablist = element.getBoundingClientRect();
    const indicator = getComputedStyle(element, "::before");
    return { tablistWidth: tablist.width, indicatorWidth: Number.parseFloat(indicator.width) };
  });
  expect(tabIndicator.indicatorWidth).toBeGreaterThan(0);
  expect(tabIndicator.indicatorWidth).toBeLessThanOrEqual(tabIndicator.tablistWidth / 2);
  await expect.poll(() => page.locator(".map-label.city").count()).toBeGreaterThan(0);
  expect(await page.locator(".map-label.city").count()).toBeLessThanOrEqual(36);
  await expect(page.locator(".brand-lockup small")).toBeVisible();
  await expect(page.locator(".brand-lockup small")).toHaveText("Smoke Forecast");
  const searchTrigger = await page.getByRole("button", { name: "Search cities" }).boundingBox();
  const worstTrigger = await page.getByRole("button", { name: "Worst 5 conditions" }).boundingBox();
  const compactStatus = await page.getByTestId("source-badge").boundingBox();
  const moreTrigger = await page.getByRole("button", { name: "More options" }).boundingBox();
  expect(searchTrigger).toBeTruthy();
  expect(worstTrigger).toBeTruthy();
  expect(compactStatus).toBeTruthy();
  expect(moreTrigger).toBeTruthy();
  expect((searchTrigger?.x ?? 0) + (searchTrigger?.width ?? 0)).toBeLessThanOrEqual(worstTrigger?.x ?? 0);
  expect(worstTrigger?.x ?? width).toBeLessThan(compactStatus?.x ?? 0);
  expect(compactStatus?.x ?? width).toBeLessThan(moreTrigger?.x ?? 0);
  await expect(page.getByRole("button", { name: "Search cities" })).toBeVisible();
  await expect(page.getByTestId("location-search-input")).toHaveCount(0);
  await page.getByRole("button", { name: "Search cities" }).click();
  const search = await page.getByTestId("location-search-input").boundingBox();
  const searchField = await page.locator(".location-search-field").boundingBox();
  const brand = await page.locator(".brand-lockup").boundingBox();
  expect(search?.width ?? 999).toBeLessThanOrEqual(320);
  expect(searchField).toBeTruthy();
  expect(brand).toBeTruthy();
  expect((searchField?.x ?? 0)).toBeGreaterThanOrEqual((brand?.x ?? 0) + (brand?.width ?? 0));
  expect((searchField?.x ?? 0) + (searchField?.width ?? 0)).toBeLessThanOrEqual(width - 6);
  await expect(page.getByRole("button", { name: "Worst 5 conditions" })).toBeHidden();
  await expect(page.getByRole("button", { name: "More options" })).toBeHidden();
  await expect(page.getByTestId("source-badge")).toBeHidden();
  await page.getByTestId("location-search-input").press("Escape");
  await expect(page.getByRole("button", { name: "Search cities" })).toBeFocused();
  await page.getByRole("button", { name: "Search cities" }).click();
  await page.locator(".brand-lockup").click();
  await expect(page.getByRole("button", { name: "Search cities" })).toBeFocused();
  await page.getByRole("button", { name: "More options" }).click();
  await page.getByRole("dialog", { name: "More" }).getByRole("button", { name: /Map options/ }).click();
  const drawer = await page.getByRole("dialog", { name: "Map options" }).boundingBox();
  expect(drawer?.width ?? 999).toBeLessThanOrEqual(320);
  const sceneAfter = await page.locator(".scene").boundingBox();
  expect(sceneAfter?.width).toBe(width);
});

for (const width of [700, 844]) test(`keeps searched-location details reachable at ${width}px short landscape`, async ({ page }) => {
  await page.setViewportSize({ width, height: 390 });
  await page.goto("/"); await waitForReady(page);
  await searchCity(page, "Portland Oregon");
  const card = page.getByTestId("location-card");
  const cardBox = await card.boundingBox();
  const sceneBox = await page.locator(".scene").boundingBox();
  expect(cardBox).toBeTruthy();
  expect(sceneBox).toBeTruthy();
  expect(cardBox?.y ?? 0).toBeGreaterThanOrEqual(56);
  expect((cardBox?.x ?? 0) + (cardBox?.width ?? 0)).toBeLessThanOrEqual((sceneBox?.x ?? 0) + (sceneBox?.width ?? 0));
  await card.getByText("More details").click();
  await card.getByRole("link", { name: "Read official AQI guidance" }).scrollIntoViewIfNeeded();
  await expect(card.getByRole("link", { name: "Read official AQI guidance" })).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: /^Close / }).click();
  await expect(page.getByTestId("interactive-globe")).toBeFocused();
});

test("uses mobile-sized search actions and keeps location details clear of navigation", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/"); await waitForReady(page);
  const search = page.getByTestId("location-search-input");
  await search.fill("Portland Oregon");
  const clear = await page.getByRole("button", { name: "Clear location search" }).boundingBox();
  expect(clear?.width ?? 0).toBeGreaterThanOrEqual(44);
  expect(clear?.height ?? 0).toBeGreaterThanOrEqual(44);
  await search.press("Enter");
  const card = page.getByTestId("location-card");
  await expect(card.getByTestId("location-air-value")).toBeVisible();
  const cardBox = await card.boundingBox();
  const searchBox = await page.locator(".location-search").boundingBox();
  const tabsBox = await page.getByRole("tablist", { name: "Data view" }).boundingBox();
  expect(cardBox).toBeTruthy();
  expect(searchBox).toBeTruthy();
  expect(tabsBox).toBeTruthy();
  expect(cardBox?.y ?? 0).toBeGreaterThanOrEqual((searchBox?.y ?? 0) + (searchBox?.height ?? 0) + 8);
  expect((cardBox?.y ?? 0) + (cardBox?.height ?? 0)).toBeLessThanOrEqual((tabsBox?.y ?? 0) - 8);
  await card.getByText("More details").click();
  await card.getByRole("link", { name: "Read official AQI guidance" }).scrollIntoViewIfNeeded();
  await expect(card.getByRole("link", { name: "Read official AQI guidance" })).toBeVisible();
});

test("moves the mobile air-quality timestamp into Data status", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/"); await waitForReady(page);
  await page.getByTestId("view-air").click();
  await waitForReady(page);
  await expect(page.getByTestId("obs-time")).toHaveCount(0);
  await page.getByTestId("source-badge").click();
  const status = page.getByRole("dialog", { name: "Data status" });
  await expect(status).toBeVisible();
  await expect(status.getByText("Observed", { exact: true })).toBeVisible();
});

test("keeps the mobile Air quality sheet above persistent view navigation", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto("/"); await waitForReady(page);
  await page.getByTestId("view-air").click();
  await waitForReady(page);
  await page.getByRole("button", { name: "More options" }).click();
  const tabs = await page.getByRole("tablist", { name: "Data view" }).boundingBox();
  const sheet = await page.getByRole("dialog", { name: "More" }).boundingBox();
  expect(tabs).toBeTruthy();
  expect(sheet).toBeTruthy();
  expect((sheet?.y ?? 0) + (sheet?.height ?? 0)).toBeLessThanOrEqual((tabs?.y ?? 0) - 8);
  await expect(page.locator(".observation-chip")).toBeHidden();
});

test("supports arrow-key navigation between data views", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  await page.getByTestId("view-forecast").focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByTestId("view-air")).toBeFocused();
  await expect(page.getByTestId("view-air")).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByTestId("view-forecast")).toBeFocused();
});


test("search works when the localStorage getter is denied", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    Object.defineProperty(window, "localStorage", { get() { throw new DOMException("Storage denied", "SecurityError"); } });
  });
  await page.goto("/");
  await waitForReady(page);
  await searchCity(page, "Seattle");
  await expect(page.getByTestId("location-card")).toContainText("Seattle");
  await page.reload();
  await waitForReady(page);
  await page.getByTestId("location-search-input").focus();
  await expect(page.getByText("Recent searches")).toHaveCount(0);
  await searchCity(page, "Toronto");
  await expect(page.getByTestId("location-card")).toContainText("Toronto");
  expect(errors).toEqual([]);
});
