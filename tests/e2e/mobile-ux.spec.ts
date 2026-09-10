import { devices, expect, test, type Page } from "@playwright/test";

async function waitForReady(page: Page) {
  await expect(page.getByTestId("loading-state")).toBeHidden({ timeout: 20_000 });
  await expect(page.locator("canvas")).toHaveCount(1);
}

async function openMore(page: Page) {
  await page.getByRole("button", { name: "More options" }).click();
  return page.getByRole("dialog", { name: "More" });
}

async function searchAndSelectMapLabel(page: Page, name: string) {
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

test.use({ ...devices["Pixel 5"], deviceScaleFactor: 1 });

test.describe("mobile UX", () => {
  for (const viewport of [
    { width: 320, height: 568 },
    { width: 360, height: 800 },
    { width: 390, height: 844 },
    { width: 402, height: 670 },
    { width: 568, height: 320 },
    { width: 844, height: 390 },
  ]) {
    test(`centers playback and keeps search usable at ${viewport.width}x${viewport.height}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await page.goto("/");
      await waitForReady(page);

      const mapLegend = page.getByRole("button", { name: "Open smoke legend" });
      await expect(mapLegend).toBeVisible();
      expect((await mapLegend.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);

      const searchTrigger = page.getByRole("button", { name: "Search cities" });
      if (await searchTrigger.isVisible()) await searchTrigger.click();
      const search = page.getByTestId("location-search-input");
      await expect(search).toHaveAttribute("placeholder", "Search major cities");
      expect(await search.evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize))).toBeGreaterThanOrEqual(16);
      expect((await search.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
      await search.fill("a");
      await expect(page.getByRole("listbox", { name: "Location results" })).toBeVisible();
      await expect(mapLegend).toBeHidden();
      expect(await page.getByRole("option").count()).toBeLessThanOrEqual(8);
      await expect(page.locator(".playback-dock")).toBeHidden();
      await expect(page.getByRole("tablist", { name: "Data view" })).toBeHidden();
      await page.getByRole("option").last().click();
      const selection = page.getByRole("dialog");
      await expect(selection).toBeVisible();
      await selection.getByRole("button", { name: /^Close / }).click();
      await expect(page.getByTestId("interactive-globe")).toBeFocused();
      await expect(mapLegend).toBeVisible();

      const dock = page.locator(".playback-dock");
      await expect(dock).toBeVisible();
      await expect(page.getByRole("tablist", { name: "Data view" })).toBeVisible();
      const [dockBox, playBox, playIconBox, previousBox, nextBox, transportBox, timelineBox, timeBox, statusBox, legendBox] = await Promise.all([
        dock.boundingBox(),
        page.getByTestId("play-toggle").boundingBox(),
        page.getByTestId("play-toggle").locator("svg").boundingBox(),
        page.getByTestId("previous-frame").boundingBox(),
        page.getByTestId("next-frame").boundingBox(),
        page.locator(".transport").boundingBox(),
        page.locator(".timeline-wrap").boundingBox(),
        page.getByTestId("obs-time").boundingBox(),
        page.locator(".live-control, .forecast-label").boundingBox(),
        mapLegend.boundingBox(),
      ]);
      expect(dockBox).toBeTruthy();
      expect(playBox).toBeTruthy();
      expect(playIconBox).toBeTruthy();
      expect(previousBox).toBeTruthy();
      expect(nextBox).toBeTruthy();
      expect(transportBox).toBeTruthy();
      expect(timelineBox).toBeTruthy();
      expect(timeBox).toBeTruthy();
      expect(statusBox).toBeTruthy();
      expect(dockBox?.height ?? 999).toBeLessThanOrEqual(72);
      expect(playBox?.height ?? 0).toBeGreaterThanOrEqual(44);
      expect(previousBox?.height ?? 0).toBeGreaterThanOrEqual(44);
      expect(nextBox?.height ?? 0).toBeGreaterThanOrEqual(44);
      expect(timelineBox?.height ?? 0).toBeGreaterThanOrEqual(24);
      expect(Math.abs((playBox?.x ?? 0) + (playBox?.width ?? 0) / 2 - viewport.width / 2)).toBeLessThanOrEqual(2);
      expect(Math.abs((playIconBox?.x ?? 0) + (playIconBox?.width ?? 0) / 2 - ((playBox?.x ?? 0) + (playBox?.width ?? 0) / 2))).toBeLessThanOrEqual(2);
      expect((timeBox?.x ?? 999) + (timeBox?.width ?? 999)).toBeLessThanOrEqual((transportBox?.x ?? 0) - 2);
      expect(statusBox?.x ?? 0).toBeGreaterThanOrEqual((transportBox?.x ?? 999) + (transportBox?.width ?? 999) + 2);
      expect((legendBox?.y ?? 999) + (legendBox?.height ?? 999)).toBeLessThanOrEqual((dockBox?.y ?? 0) - 8);
      await expect(page.getByRole("slider", { name: "forecast timeline" })).toBeVisible();
      await expect(page.locator(".live-control, .forecast-label")).toBeVisible();
      await expect(page.getByTestId("playback-speed")).toBeHidden();
      const visibleTime = await page.getByTestId("obs-time").innerText();
      expect(await page.getByTestId("obs-time").evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize))).toBeGreaterThanOrEqual(10);
      expect(await page.locator(".live-control, .forecast-label").evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize))).toBeGreaterThanOrEqual(10);
      expect(visibleTime).not.toMatch(/\b(?:UTC|GMT|PST|PDT|MST|MDT|CST|CDT|EST|EDT)\b/);
      expect(visibleTime).not.toContain("Now");
      await expect(page.getByTestId("obs-time")).toHaveAttribute("aria-label", /\b(?:UTC|GMT|PST|PDT|MST|MDT|CST|CDT|EST|EDT)\b/);
      const visiblePlaceLabels = page.locator(".map-label.city, .map-label.landmark").filter({ visible: true });
      expect(await visiblePlaceLabels.count()).toBeLessThanOrEqual(viewport.width > viewport.height && viewport.height <= 500 ? 20 : 24);
      const visibleCity = page.locator(".map-label.city").filter({ visible: true }).first();
      await expect(visibleCity).toBeVisible();
      expect(await visibleCity.evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize))).toBeGreaterThanOrEqual(10);

      const more = await openMore(page);
      await expect(mapLegend).toBeHidden();
      await expect(more.getByRole("button", { name: /Legend/ })).toHaveCount(0);
      const [installBox, moreBox] = await Promise.all([more.getByTestId("install-action").boundingBox(), more.boundingBox()]);
      expect(installBox).toBeTruthy();
      expect(moreBox).toBeTruthy();
      expect(installBox?.y ?? 0).toBeGreaterThanOrEqual(moreBox?.y ?? 999);
      expect((installBox?.y ?? 999) + (installBox?.height ?? 999)).toBeLessThanOrEqual((moreBox?.y ?? 0) + (moreBox?.height ?? 0));
      const speed = more.getByTestId("mobile-playback-speed");
      await expect(speed).toContainText("1×");
      await speed.click();
      await expect(speed).toContainText("3×");
    });
  }

  test("slows close zoom and keyboard steps without changing the globe target", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await waitForReady(page);
    const search = page.getByTestId("location-search-input");
    await search.fill("Denver");
    await search.press("Enter");
    await page.getByRole("dialog", { name: "Denver" }).getByRole("button", { name: "Close denver" }).click();

    const globe = page.getByTestId("interactive-globe");
    const cameraDistance = () => globe.getAttribute("data-camera-distance").then(Number);
    await expect.poll(async () => Math.abs(await cameraDistance() - 1.45)).toBeLessThan(0.01);
    await page.setViewportSize({ width: 568, height: 320 });
    await expect.poll(async () => Math.abs(await cameraDistance() - 1.45)).toBeLessThan(0.01);
    await page.setViewportSize({ width: 390, height: 844 });
    await expect.poll(async () => Math.abs(await cameraDistance() - 1.45)).toBeLessThan(0.01);
    expect(Number(await globe.getAttribute("data-camera-zoom-speed"))).toBeLessThan(0.5);
    expect(Number(await globe.getAttribute("data-camera-rotate-speed"))).toBeLessThan(0.3);
    expect(Number(await globe.getAttribute("data-camera-damping-factor"))).toBeGreaterThan(0.12);

    const before = await cameraDistance();
    const box = await globe.boundingBox();
    expect(box).toBeTruthy();
    await page.mouse.move((box?.x ?? 0) + (box?.width ?? 0) / 2, (box?.y ?? 0) + (box?.height ?? 0) / 2);
    await page.mouse.wheel(0, -100);
    await expect.poll(cameraDistance).toBeLessThan(before - 0.005);
    const after = await cameraDistance();
    const closeAltitudeChange = (before - after) / (before - 1);
    expect(closeAltitudeChange).toBeGreaterThan(0.02);
    expect(closeAltitudeChange).toBeLessThanOrEqual(0.1);

    await page.mouse.wheel(0, 100);
    await expect.poll(cameraDistance).toBeGreaterThan(after + 0.005);
    await expect.poll(async () => Math.abs(await cameraDistance() - before)).toBeLessThan(0.03);

    await globe.focus();
    await page.keyboard.press("+");
    const afterOneKey = await cameraDistance();
    expect(afterOneKey).toBeGreaterThan(1.31);
    for (let press = 0; press < 4; press += 1) await page.keyboard.press("+");
    await expect.poll(cameraDistance).toBeLessThanOrEqual(1.301);
    expect(Number(await globe.getAttribute("data-camera-zoom-speed"))).toBeCloseTo(0.22, 2);
    expect(Number(await globe.getAttribute("data-camera-rotate-speed"))).toBeCloseTo(0.24, 2);
    expect(Number(await globe.getAttribute("data-camera-damping-factor"))).toBeCloseTo(0.14, 2);
    await page.mouse.wheel(0, -100);
    await page.waitForTimeout(300);
    expect(await cameraDistance()).toBeCloseTo(1.3, 2);

    const more = await openMore(page);
    await more.getByRole("button", { name: /Reset view/ }).click();
    await expect.poll(cameraDistance).toBeGreaterThan(1.8);
    await expect.poll(async () => Number(await globe.getAttribute("data-camera-zoom-speed"))).toBeCloseTo(0.72, 2);
    expect(Number(await globe.getAttribute("data-camera-rotate-speed"))).toBeCloseTo(0.45, 2);
    expect(Number(await globe.getAttribute("data-camera-damping-factor"))).toBeCloseTo(0.08, 2);
    const overviewBefore = await cameraDistance();
    await page.mouse.wheel(0, -100);
    await expect.poll(cameraDistance).toBeLessThan(overviewBefore - 0.005);
    const overviewAfter = await cameraDistance();
    expect(overviewBefore - overviewAfter).toBeGreaterThan((before - after) * 2);
  });

  test("starts a 568px landscape with a useful globe and still allows zooming out", async ({ page }) => {
    await page.setViewportSize({ width: 568, height: 320 });
    await page.goto("/");
    await waitForReady(page);
    const globe = page.getByTestId("interactive-globe");
    const cameraDistance = () => globe.getAttribute("data-camera-distance").then(Number);
    await expect.poll(cameraDistance).toBeLessThanOrEqual(4.451);
    const resetDistance = await cameraDistance();
    const projectedDiameterRatio = 1 / (Math.sqrt(resetDistance ** 2 - 1) * Math.tan(42 * Math.PI / 360));
    expect(projectedDiameterRatio).toBeGreaterThanOrEqual(0.6);
    const box = await globe.boundingBox();
    expect(box).toBeTruthy();
    await page.mouse.move((box?.x ?? 0) + (box?.width ?? 0) / 2, (box?.y ?? 0) + (box?.height ?? 0) / 2);
    await page.mouse.wheel(0, 100);
    await expect.poll(cameraDistance).toBeGreaterThan(resetDistance + 0.02);
  });

  test("opens both mobile legends from the contextual map chip", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await waitForReady(page);
    const smokeChip = page.getByRole("button", { name: "Open smoke legend" });
    await smokeChip.click();
    await expect(page.getByRole("dialog", { name: "Smoke legend" })).toBeVisible();
    await expect(smokeChip).toBeHidden();
    await page.keyboard.press("Escape");
    await expect(smokeChip).toBeFocused();

    await page.getByTestId("view-air").click();
    await waitForReady(page);
    const airChip = page.getByRole("button", { name: "Open AQI legend" });
    await expect(airChip).toBeVisible();
    await airChip.click();
    await expect(page.getByRole("dialog", { name: "AQI legend" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(airChip).toBeFocused();
  });

  for (const viewport of [{ width: 320, height: 568 }, { width: 844, height: 390 }]) {
    test(`shows and clears the More drawer overflow cue at ${viewport.width}x${viewport.height}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await page.goto("/");
      await waitForReady(page);
      const more = await openMore(page);
      await expect(more).toHaveAttribute("data-scroll-more", "true");
      const body = more.locator(".context-drawer-body");
      await body.evaluate((element) => { element.scrollTop = element.scrollHeight; element.dispatchEvent(new Event("scroll")); });
      await expect(more).toHaveAttribute("data-scroll-more", "false");
      const about = more.getByRole("button", { name: /About & sources/ });
      await expect(about).toBeVisible();
      await about.click();
      await expect(page.locator("dialog[open]")).toBeVisible();
    });
  }

  test("restores search focus for keyboard close but not touch close", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await waitForReady(page);
    const search = page.getByTestId("location-search-input");
    await search.fill("Portland Oregon");
    await search.press("Enter");
    const close = page.getByRole("dialog", { name: "Portland" }).getByRole("button", { name: "Close portland" });
    await close.focus();
    await close.press("Enter");
    await expect(search).toBeFocused();

    await search.fill("Portland Oregon");
    await search.press("Enter");
    await page.getByRole("dialog", { name: "Portland" }).getByRole("button", { name: "Close portland" }).click();
    await expect(page.getByTestId("interactive-globe")).toBeFocused();
  });

  test("explains disabled animation when Reduce Motion is enabled", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await waitForReady(page);
    await expect(page.getByTestId("play-toggle")).toBeDisabled();
    await expect(page.getByTestId("play-toggle")).toHaveAccessibleName("Playback animation unavailable because Reduce Motion is enabled");
    await expect(page.getByTestId("play-toggle")).toHaveAttribute("title", "Playback animation unavailable because Reduce Motion is enabled");
    await expect(page.getByTestId("next-frame")).toBeEnabled();
    const more = await openMore(page);
    await expect(more.getByTestId("mobile-playback-speed")).toHaveCount(0);
    await expect(more.getByTestId("mobile-playback-motion-status")).toContainText("Previous and Next still work");
  });

  test("explains an offline data failure and retries once connectivity returns", async ({ page }) => {
    let online = false;
    let contextRequests = 0;
    await page.addInitScript(() => {
      (window as typeof window & { __testOnline?: boolean }).__testOnline = false;
      Object.defineProperty(navigator, "onLine", { configurable: true, get: () => (window as typeof window & { __testOnline?: boolean }).__testOnline });
    });
    await page.route(/\/(?:demo\/context\/latest\.json|api\/context-data)(?:\?|$)/, async (route) => {
      contextRequests += 1;
      if (!online) await route.abort("internetdisconnected");
      else await route.continue();
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    const error = page.getByTestId("error-state");
    await expect(error).toContainText("TitanSkies needs a network connection to load current forecasts and observations.");
    await expect(error.getByRole("button", { name: "Retry" })).toBeEnabled();
    const requestsBeforeRecovery = contextRequests;

    online = true;
    await page.evaluate(() => {
      (window as typeof window & { __testOnline?: boolean }).__testOnline = true;
      window.dispatchEvent(new Event("online"));
    });
    await waitForReady(page);
    await expect.poll(() => contextRequests).toBe(requestsBeforeRecovery + 1);
    await expect(error).toBeHidden();
  });

  test("sizes search results to a keyboard-reduced visual viewport", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript(() => {
      const viewport = Object.assign(new EventTarget(), {
        height: 360,
        width: 390,
        offsetLeft: 0,
        offsetTop: 0,
        pageLeft: 0,
        pageTop: 0,
        scale: 1,
        onresize: null,
        onscroll: null,
      });
      Object.defineProperty(window, "visualViewport", { configurable: true, value: viewport });
    });
    await page.goto("/");
    await waitForReady(page);
    const search = page.getByTestId("location-search-input");
    await search.fill("a");
    const menu = page.getByRole("listbox", { name: "Location results" }).locator("..");
    await expect(menu).toBeVisible();
    const menuBox = await menu.boundingBox();
    expect(menuBox).toBeTruthy();
    expect((menuBox?.y ?? 0) + (menuBox?.height ?? 0)).toBeLessThanOrEqual(360);
    await page.getByRole("option").last().scrollIntoViewIfNeeded();
    await page.getByRole("option").last().click();
    await expect(page.getByTestId("location-card")).toBeVisible();
  });

  test("keeps city and forecast selections compact until details are requested", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await waitForReady(page);
    const search = page.getByTestId("location-search-input");
    await search.fill("Portland Oregon");
    await search.press("Enter");
    let sheet = page.getByRole("dialog", { name: "Portland" });
    expect((await sheet.boundingBox())?.height ?? 999).toBeLessThanOrEqual(Math.min(240, 844 * 0.38) + 1);
    const metrics = sheet.locator(".location-metrics section");
    const metricBoxes = await metrics.evaluateAll((elements) => elements.map((element) => element.getBoundingClientRect().top));
    expect(Math.abs(metricBoxes[0] - metricBoxes[1])).toBeLessThanOrEqual(2);
    expect(await metrics.locator("small").first().evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize))).toBeGreaterThanOrEqual(11);
    expect((await sheet.getByText("More details").boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
    await sheet.getByText("More details").click();
    expect((await sheet.boundingBox())?.height ?? 999).toBeLessThanOrEqual(844 * 0.6 + 1);
    await expect(sheet.getByRole("link", { name: /Read official/ })).toBeAttached();
    await sheet.getByRole("button", { name: "Close portland" }).click();

    const globe = page.getByTestId("interactive-globe");
    await globe.focus();
    await page.keyboard.press("ArrowLeft");
    await page.keyboard.press("Enter");
    sheet = page.getByRole("dialog");
    await expect(page.getByTestId("details-card")).toBeVisible();
    expect((await sheet.boundingBox())?.height ?? 999).toBeLessThanOrEqual(Math.min(240, 844 * 0.38) + 1);
    await sheet.getByText("More details").click();
    await expect(sheet.getByText("Guidance", { exact: true })).toBeVisible();
    expect((await sheet.boundingBox())?.height ?? 999).toBeLessThanOrEqual(844 * 0.6 + 1);
    await page.keyboard.press("Escape");
    await expect(globe).toBeFocused();
  });

  test("wraps city summaries only at the narrowest phone width", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 568 });
    await page.goto("/");
    await waitForReady(page);
    const search = page.getByTestId("location-search-input");
    await search.fill("Portland Oregon");
    await search.press("Enter");
    const sections = page.getByTestId("location-card").locator(".location-metrics section");
    const tops = await sections.evaluateAll((elements) => elements.map((element) => element.getBoundingClientRect().top));
    expect(tops[1]).toBeGreaterThan(tops[0]);
    await expect(page.getByText("More details", { exact: true })).toBeVisible();
  });

  test("keeps secondary drawer actions touch-sized", async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 568 });
    await page.goto("/");
    await waitForReady(page);

    await page.getByTestId("source-badge").click();
    const sourceLink = page.getByRole("dialog", { name: "Data status" }).getByRole("button", { name: "About & sources" });
    await sourceLink.scrollIntoViewIfNeeded();
    expect((await sourceLink.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
    await page.getByRole("button", { name: "Close data status" }).click();

    await page.getByRole("button", { name: "Worst 5 conditions" }).click();
    const rankingLink = page.getByRole("dialog", { name: "Highest conditions" }).getByRole("button", { name: "How rankings work" });
    await rankingLink.scrollIntoViewIfNeeded();
    expect((await rankingLink.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
  });

  test("keeps air-monitor selections compact and expandable", async ({ page }) => {
    await page.setViewportSize({ width: 844, height: 390 });
    await page.goto("/");
    await waitForReady(page);
    await page.getByTestId("view-air").click();
    await waitForReady(page);
    await searchAndSelectMapLabel(page, "Edmonton");
    let sheet = page.getByRole("dialog");
    await expect(sheet).toBeVisible();
    expect((await sheet.boundingBox())?.height ?? 999).toBeLessThanOrEqual(Math.min(240, 390 * 0.38) + 1);
    const more = sheet.getByText("More details");
    await more.click();
    const nearby = sheet.locator(".nearby-observations button").first();
    if (await nearby.count()) {
      await nearby.scrollIntoViewIfNeeded();
      await nearby.click();
      sheet = page.getByRole("dialog");
      await expect(sheet.locator(".selection-value")).toBeVisible();
      await expect(sheet.locator(".selection-more")).not.toHaveAttribute("open", "");
      await sheet.getByText("More details").click();
    }
    await expect(sheet.getByText("Observed", { exact: true })).toBeVisible();
    expect((await sheet.boundingBox())?.height ?? 999).toBeLessThanOrEqual(390 * 0.6 + 1);
  });

  test("keeps wildfire selections compact and expandable", async ({ page }) => {
    const report = (id: string, name: string, country: "US" | "CA", status: string, areaHectares: number) => ({
      id, name, country, lat: 45.7883, lon: -108.54, status, areaHectares, sourceArea: areaHectares, sourceAreaUnit: "hectares",
      updatedAt: "2024-07-15T18:00:00Z",
      sourceUrl: country === "US" ? "https://data-nifc.opendata.arcgis.com/" : "https://cwfis.cfs.nrcan.gc.ca/",
    });
    await page.route("**/wfigs-incidents.json", (route) => route.fulfill({ json: { incidents: [report("US:alpha", "Alpha Fire", "US", "Active", 125), report("US:bravo", "Bravo Fire", "US", "40% contained", 80)] } }));
    await page.route("**/cwfis-incidents.json", (route) => route.fulfill({ json: { incidents: [report("CA:charlie", "Charlie Fire", "CA", "Out of control", 210)] } }));
    await page.setViewportSize({ width: 844, height: 390 });
    await page.goto("/");
    await waitForReady(page);
    const more = await openMore(page);
    await more.getByRole("button", { name: /Map options/ }).click();
    await page.getByTestId("incidents-toggle").check();
    await page.getByRole("button", { name: "Close map options" }).click();
    await searchAndSelectMapLabel(page, "Billings");
    let sheet = page.getByRole("dialog", { name: "Nearby reported wildfires (3)" });
    await expect(sheet).toBeVisible();
    expect((await sheet.boundingBox())?.height ?? 999).toBeLessThanOrEqual(Math.min(240, 390 * 0.38) + 1);
    await sheet.getByText("More details").click();
    const alpha = sheet.getByRole("button", { name: "Open Alpha Fire reported wildfire details" });
    await alpha.scrollIntoViewIfNeeded();
    await alpha.click();
    sheet = page.getByRole("dialog", { name: "Alpha Fire" });
    await expect(sheet.getByText("Active", { exact: true })).toBeVisible();
    await expect(sheet.locator(".selection-more")).not.toHaveAttribute("open", "");
    await sheet.getByText("More details").click();
    await expect(sheet.getByText("NIFC WFIGS", { exact: true })).toBeVisible();
    expect((await sheet.boundingBox())?.height ?? 999).toBeLessThanOrEqual(390 * 0.6 + 1);
  });

  test("shows browser-menu instructions before a native install prompt is available", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await waitForReady(page);
    const more = await openMore(page);
    await expect(more.getByTestId("install-action")).toContainText("Use your browser’s install menu");
    await more.getByTestId("install-action").click();
    const instructions = page.getByTestId("install-instructions");
    await expect(instructions).toContainText("Install app or Add to Home Screen");
    await expect(instructions).toContainText("needs a network connection");
  });

  test("uses Chromium install prompts and keeps the action after dismissal", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await waitForReady(page);
    await page.evaluate(() => {
      const event = new Event("beforeinstallprompt") as Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: string; platform: string }> };
      event.prompt = async () => { (window as typeof window & { installPromptCalls?: number }).installPromptCalls = ((window as typeof window & { installPromptCalls?: number }).installPromptCalls ?? 0) + 1; };
      event.userChoice = Promise.resolve({ outcome: "dismissed", platform: "web" });
      window.dispatchEvent(event);
    });
    let more = await openMore(page);
    await more.getByTestId("install-action").click();
    await expect.poll(() => page.evaluate(() => (window as typeof window & { installPromptCalls?: number }).installPromptCalls ?? 0)).toBe(1);
    await expect(more.getByTestId("install-action")).toBeVisible();

    await page.evaluate(() => {
      const event = new Event("beforeinstallprompt") as Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: string; platform: string }> };
      event.prompt = async () => { (window as typeof window & { installPromptCalls?: number }).installPromptCalls = ((window as typeof window & { installPromptCalls?: number }).installPromptCalls ?? 0) + 1; };
      event.userChoice = Promise.resolve({ outcome: "accepted", platform: "web" });
      window.dispatchEvent(event);
    });
    await expect(more.getByTestId("install-action")).toContainText("Install on this device");
    await more.getByTestId("install-action").click();
    await expect(more).toBeHidden();
    more = await openMore(page);
    await expect(more.getByTestId("install-action")).toHaveCount(0);
  });

  test("shows iOS Safari instructions and hides install in standalone mode", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "userAgent", { configurable: true, value: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1" });
      Object.defineProperty(navigator, "maxTouchPoints", { configurable: true, value: 5 });
    });
    await page.goto("/");
    await waitForReady(page);
    const more = await openMore(page);
    await expect(more.getByTestId("install-action")).toContainText("Safari’s Share menu");
    await more.getByTestId("install-action").click();
    await expect(page.getByTestId("install-instructions")).toContainText("Open TitanSkies in Safari");
    await expect(page.getByTestId("install-instructions")).toContainText("Share");
    await expect(page.getByTestId("install-instructions")).toContainText("Add to Home Screen");

    await page.evaluate(() => window.dispatchEvent(new Event("appinstalled")));
    await page.getByRole("button", { name: "Close install titanskies" }).click();
    const reopened = await openMore(page);
    await expect(reopened.getByTestId("install-action")).toHaveCount(0);
  });

  test("does not offer installation when already running standalone", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript(() => Object.defineProperty(navigator, "standalone", { configurable: true, value: true }));
    await page.goto("/");
    await waitForReady(page);
    const more = await openMore(page);
    await expect(more.getByTestId("install-action")).toHaveCount(0);
  });
});
