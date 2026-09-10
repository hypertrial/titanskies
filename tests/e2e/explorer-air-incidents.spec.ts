import { expect, test } from "@playwright/test";
import { HOVER_OFFSET_X, HOVER_OFFSET_Y } from "../../src/rendering/hoverLabelLayout";
import { INSPECT_CLICK_MOUSE_PX } from "../../src/rendering/inspectClick";
import { waitForReady, searchCity, useLiveContext } from "./explorer.fixtures";

test("loads and reports wildfire incidents only while their layer is active", async ({ page }) => {
  let requests = 0;
  await page.route("**/*-incidents.json", (route) => { requests += 1; return route.abort("failed"); });
  await page.goto("/");
  await waitForReady(page);
  expect(requests).toBe(0);
  await expect(page.getByTestId("source-warning")).toHaveCount(0);
  await page.getByRole("button", { name: "Map options" }).click();
  await page.getByTestId("incidents-toggle").check();
  await expect.poll(() => requests).toBeGreaterThan(0);
  await expect(page.getByTestId("source-warning")).toBeVisible();
  await page.getByTestId("source-badge").click();
  const status = page.getByRole("dialog", { name: "Data status" });
  await expect(status.getByText("US wildfire reports")).toBeVisible();
  await expect(status.getByText("Canadian wildfire reports")).toBeVisible();
});

test("groups nearby wildfire reports without hiding their agency details or selection", async ({ page }) => {
  const report = (id: string, name: string, country: "US" | "CA", status: string, areaHectares: number) => ({
    id,
    name,
    country,
    lat: 45,
    lon: -110,
    status,
    areaHectares,
    sourceArea: areaHectares,
    sourceAreaUnit: "hectares",
    updatedAt: "2024-07-15T18:00:00Z",
    sourceUrl: country === "US" ? "https://data-nifc.opendata.arcgis.com/" : "https://cwfis.cfs.nrcan.gc.ca/",
  });
  await page.route("**/wfigs-incidents.json", (route) => route.fulfill({ json: { incidents: [
    report("US:alpha", "Alpha Fire", "US", "Active", 125),
    report("US:bravo", "Bravo Fire", "US", "40% contained", 80),
  ] } }));
  await page.route("**/cwfis-incidents.json", (route) => route.fulfill({ json: { incidents: [
    report("CA:charlie", "Charlie Fire", "CA", "Out of control", 210),
  ] } }));
  await page.goto("/");
  await waitForReady(page);
  await page.getByRole("button", { name: "Map options" }).click();
  await page.getByTestId("incidents-toggle").check();
  await page.getByRole("button", { name: "Close map options" }).click();

  const layer = page.getByTestId("map-hover-layer");
  let screen = { x: 0, y: 0 };
  await expect.poll(async () => {
    const raw = await layer.getAttribute("data-cluster-screens");
    const screens = raw ? JSON.parse(raw) as Array<{ x: number; y: number }> : [];
    screen = screens[0] ?? screen;
    return screens.length;
  }).toBe(1);
  const globe = page.getByTestId("interactive-globe");
  await globe.click({ position: screen });
  const details = page.getByRole("dialog", { name: "Nearby reported wildfires (3)" });
  await expect(details).toBeVisible();
  await expect(details.getByText("NIFC WFIGS · Active")).toBeVisible();
  await expect(details.getByText("Canadian CWFIS · Out of control")).toBeVisible();
  await expect(details.getByText("125 ha · Updated", { exact: false })).toBeVisible();
  await expect(details).toContainText("grouped only to keep the map legible");
  await expect(globe).toHaveAttribute("data-selected-incident-count", "3");

  await globe.press("+");
  await expect(details).toBeVisible();
  await expect(globe).toHaveAttribute("data-selected-incident-count", "3");
  await details.getByRole("button", { name: "Open Alpha Fire reported wildfire details" }).click();
  const incidentDetails = page.getByRole("dialog", { name: "Alpha Fire" });
  await expect(incidentDetails.getByText("NIFC WFIGS", { exact: true })).toBeVisible();
  await expect(incidentDetails.getByText("Active", { exact: true })).toBeVisible();
  await expect(incidentDetails.getByText("125 ha", { exact: true })).toBeVisible();
  await expect(incidentDetails.getByRole("link", { name: "Open the NIFC WFIGS source" })).toHaveAttribute("href", "https://data-nifc.opendata.arcgis.com/");
  await expect(globe).toHaveAttribute("data-selected-incident-count", "1");
});

test("uses the compact brand asset on the responsive not-found page", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/missing-smoke-map");
  await expect(page.getByRole("heading", { name: "This smoke-map page isn’t here." })).toBeVisible();
  const mark = page.locator(".fallback-brand img");
  await expect(mark).toHaveAttribute("src", "/brand/logo-icon.png");
  await expect(mark).toHaveJSProperty("naturalWidth", 56);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test("loads all air systems once while keeping AQHI off the comparable marker map", async ({ page }) => {
  let aqhiRequests = 0;
  await page.route("**/aqhi-monitors.json", (route) => {
    aqhiRequests += 1;
    return route.abort("failed");
  });
  await page.goto("/");
  await waitForReady(page);
  await page.getByTestId("view-air").click();
  await waitForReady(page);
  expect(aqhiRequests).toBe(1);
  await expect(page.getByTestId("source-warning")).toContainText("Some official air-quality sources could not load");
  await page.getByTestId("view-forecast").click();
  await searchCity(page, "Seattle");
  await expect(page.getByTestId("location-air-value")).toContainText("AQI");
  expect(aqhiRequests).toBe(1);
});

test("supports keyboard forecast stepping without hijacking the slider", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  await page.keyboard.press("ArrowRight");
  await expect(page.getByTestId("previous-frame")).toBeEnabled();
  await expect(page.locator("main.explorer")).toHaveAttribute("data-interpolation-ready", "true", { timeout: 15_000 });
  await page.keyboard.press("Space");
  await expect(page.getByTestId("play-toggle")).toHaveText("Pause");
  await page.getByRole("slider", { name: "forecast timeline" }).focus();
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByTestId("play-toggle")).toHaveText("Play");
});

test("enables stepping throughout the first and final interpolated hours", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  const slider = page.getByRole("slider", { name: "forecast timeline" });
  const maximum = Number(await slider.getAttribute("max"));
  await expect(page.getByTestId("previous-frame")).toBeDisabled();
  await slider.fill(String(30 * 60_000));
  await expect(page.getByTestId("previous-frame")).toBeEnabled();
  await expect(page.getByTestId("next-frame")).toBeEnabled();
  await slider.fill(String(maximum - 30 * 60_000));
  await expect(page.getByTestId("previous-frame")).toBeEnabled();
  await expect(page.getByTestId("next-frame")).toBeEnabled();
  await slider.fill(String(maximum));
  await expect(page.getByTestId("next-frame")).toBeDisabled();
});

test("loads regional detail only after keyboard zoom and resets cleanly", async ({ page }) => {
  let detailRequests = 0;
  page.on("request", (request) => {
    if (/\/best-(?:mask-)?[0-3]-[0-3]\.png(?:\?|$)/.test(request.url())) detailRequests += 1;
  });
  await page.goto("/"); await waitForReady(page);
  await page.waitForTimeout(300);
  expect(detailRequests).toBe(0);
  const globe = page.getByTestId("interactive-globe");
  await globe.focus();
  for (let index = 0; index < 10; index += 1) await page.keyboard.press("+");
  await expect.poll(() => detailRequests).toBeGreaterThan(0);
  await page.getByTestId("reset-view").click();
  await expect(globe).toBeVisible();
});

test("keeps playback moving without scheduling zoom-selected detail tiles", async ({ page }) => {
  let detailRequests = 0;
  await page.route(/\/best-(?:mask-)?[0-3]-[0-3]\.png(?:\?|$)/, async (route) => {
    detailRequests += 1;
    await new Promise((resolve) => setTimeout(resolve, 2_500));
    await route.continue();
  });
  await page.goto("/"); await waitForReady(page);
  const speed = page.getByTestId("playback-speed");
  await speed.click();
  await speed.click();
  const timeline = page.getByRole("slider", { name: "forecast timeline" });
  await timeline.fill("300000");
  await page.getByTestId("play-toggle").click();
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", "playing", { timeout: 5_000 });
  const beforeZoom = Number(await timeline.inputValue());
  const globe = page.getByTestId("interactive-globe");
  await globe.focus();
  for (let index = 0; index < 10; index += 1) await page.keyboard.press("+");
  await expect.poll(async () => Number(await timeline.inputValue()), { timeout: 5_000 }).toBeGreaterThan(beforeZoom + 100_000);
  await page.waitForTimeout(500);
  expect(detailRequests).toBe(0);
  await expect(page.locator("main.explorer")).toHaveAttribute("data-detail-playback-mode", "base-only");
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", "playing");
  await expect(page.getByTestId("play-toggle")).toHaveText("Pause");
});

test("fades warm regional detail to a stable base-only playback surface", async ({ page }) => {
  test.setTimeout(90_000);
  let detailRequests = 0;
  page.on("request", (request) => {
    if (/\/best-(?:mask-)?[0-3]-[0-3]\.png(?:\?|$)/.test(request.url())) detailRequests += 1;
  });
  await page.goto("/"); await waitForReady(page);
  const globe = page.getByTestId("interactive-globe");
  const canvas = globe.locator("canvas");
  const timeline = page.getByRole("slider", { name: "forecast timeline" });
  await globe.focus();
  for (let index = 0; index < 10; index += 1) await page.keyboard.press("+");
  await expect.poll(async () => Number(await canvas.getAttribute("data-ready-detail-tiles")), { timeout: 20_000 }).toBeGreaterThan(0);
  await expect.poll(async () => Number(await canvas.getAttribute("data-detail-max-opacity")), { timeout: 5_000 }).toBe(1);

  const preparation = await page.evaluate(async () => {
    const timelineInput = document.querySelector<HTMLInputElement>('input[aria-label="forecast timeline"]');
    const play = document.querySelector<HTMLButtonElement>('[data-testid="play-toggle"]');
    const explorer = document.querySelector("main.explorer");
    if (!timelineInput || !play || !explorer) throw new Error("Playback controls are unavailable");
    const held = timelineInput.value;
    play.click();
    await new Promise((resolve) => window.setTimeout(resolve, 60));
    return { held, current: timelineInput.value, phase: explorer.getAttribute("data-playback-phase") };
  });
  expect(["preparing-detail", "playing"]).toContain(preparation.phase);
  if (preparation.phase === "preparing-detail") expect(preparation.current).toBe(preparation.held);
  await expect(page.locator("main.explorer")).toHaveAttribute("data-detail-playback-mode", "base-only", { timeout: 5_000 });
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", "playing");
  await expect(canvas).toHaveAttribute("data-detail-max-opacity", "0.000");

  const steadyRequests = detailRequests;
  const moving = Number(await timeline.inputValue());
  await page.waitForTimeout(2_200);
  expect(detailRequests).toBe(steadyRequests);
  expect(Number(await timeline.inputValue())).toBeGreaterThan(moving + 1_000_000);

  await page.getByTestId("play-toggle").click();
  await expect(page.locator("main.explorer")).toHaveAttribute("data-detail-playback-mode", "visible");
  await expect.poll(() => detailRequests, { timeout: 20_000 }).toBeGreaterThan(steadyRequests);
  await expect.poll(async () => Number(await canvas.getAttribute("data-detail-max-opacity")), { timeout: 20_000 }).toBe(1);

  const cancelled = await page.evaluate(async () => {
    const timelineInput = document.querySelector<HTMLInputElement>('input[aria-label="forecast timeline"]');
    const play = document.querySelector<HTMLButtonElement>('[data-testid="play-toggle"]');
    const canvas = document.querySelector<HTMLCanvasElement>('[data-testid="interactive-globe"] canvas');
    const explorer = document.querySelector("main.explorer");
    if (!timelineInput || !play || !canvas || !explorer) throw new Error("Playback controls are unavailable");
    const held = timelineInput.value;
    play.click();
    canvas.dispatchEvent(new WheelEvent("wheel", { bubbles: true, deltaY: -1 }));
    await new Promise((resolve) => window.setTimeout(resolve, 60));
    return { held, current: timelineInput.value, phase: explorer.getAttribute("data-playback-phase") };
  });
  expect(cancelled.phase).toBe("interaction-paused");
  expect(cancelled.current).toBe(cancelled.held);
});

test("keeps playback and base smoke visible when a regional tile fails", async ({ page }) => {
  await page.route(/\/best-[0-3]-[0-3]\.png(?:\?|$)/, (route) => route.abort("failed"));
  await page.goto("/"); await waitForReady(page);
  const globe = page.getByTestId("interactive-globe");
  await globe.focus();
  for (let index = 0; index < 10; index += 1) await page.keyboard.press("+");
  await expect(page.getByTestId("source-warning")).toBeVisible();
  await expect(page.getByTestId("error-state")).toHaveCount(0);
  const speed = page.getByTestId("playback-speed");
  await speed.click();
  await speed.click();
  const timeline = page.getByRole("slider", { name: "forecast timeline" });
  await timeline.fill("300000");
  await page.getByTestId("play-toggle").click();
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", "playing", { timeout: 5_000 });
  const beforeZoom = Number(await timeline.inputValue());
  await expect(globe).toBeVisible();
  await expect.poll(async () => Number(await timeline.inputValue()), { timeout: 5_000 }).toBeGreaterThan(beforeZoom + 100_000);
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", "playing");
  await expect(page.getByTestId("play-toggle")).toHaveText("Pause");
});

test("offers quarter-, one-, and three-times forecast playback", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  const speed = page.getByTestId("playback-speed");
  await expect(speed).toHaveText("1×");
  await expect(speed).toHaveAccessibleName(/activate to use 3×/);
  await speed.click();
  await expect(speed).toHaveText("3×");
  await expect(speed).toHaveAccessibleName(/activate to use ¼×/);
  await speed.click();
  await expect(speed).toHaveText("¼×");
  await expect(speed).toHaveAccessibleName(/activate to use 1×/);
});

test("supports keyboard inspection of forecast and monitor data", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  const globe = page.getByTestId("interactive-globe");
  await globe.focus();
  await expect(globe).toHaveAttribute("data-keyboard-cursor-visible", "false");
  await page.keyboard.press("ArrowLeft");
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", "manual-paused");
  await page.keyboard.press("ArrowUp");
  await expect(globe).toHaveAttribute("data-keyboard-cursor-visible", "true");
  expect((await page.screenshot()).byteLength).toBeGreaterThan(100_000);
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("details-card")).toContainText("Guidance");
  await expect(page.getByRole("dialog").getByRole("heading")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("details-card")).toHaveCount(0);
  await expect(globe).toBeFocused();

  await page.getByTestId("view-air").click();
  await waitForReady(page);
  await globe.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("details-card")).toContainText(/AQI|AQHI/);
  await expect(page.getByRole("dialog").getByRole("heading")).toBeFocused();
  await page.getByRole("dialog").getByRole("button", { name: /^Close / }).click();
  await expect(globe).toBeFocused();
});

test("samples the forecast with Enter while reported wildfires are visible", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  await page.getByRole("button", { name: "Map options" }).click();
  await page.getByTestId("incidents-toggle").check();
  await page.getByRole("dialog").getByRole("button", { name: /^Close / }).click();
  const globe = page.getByTestId("interactive-globe");
  await globe.focus();
  await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("details-card")).toContainText("Guidance");
  await expect(page.getByRole("dialog").getByRole("heading")).toContainText("µg/m³");
});

test("does not start playback from a focused context drawer", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  await page.getByRole("button", { name: /Open smoke legend/ }).click();
  const heading = page.getByRole("dialog").getByRole("heading");
  await expect(heading).toBeFocused();
  await page.keyboard.press("Space");
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(heading).toBeFocused();
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", "manual-paused");
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", "manual-paused");
});

test("does not show the keyboard cursor for pointer inspection", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  const globe = page.getByTestId("interactive-globe");
  await globe.click({ position: { x: 700, y: 360 } });
  await expect(page.getByRole("dialog").getByRole("heading")).toBeFocused();
  await expect(globe).toHaveAttribute("data-keyboard-cursor-visible", "false");
  await expect(page.getByTestId("details-card")).toBeVisible({ timeout: 15_000 });
  await page.getByRole("dialog").getByRole("button", { name: /^Close / }).click();
  await expect(globe).toBeFocused();
});

test("holds the next playback pair until its selection data is ready", async ({ page }) => {
  let maskRequest = 0;
  let releaseMask!: () => void;
  const maskReady = new Promise<void>((resolve) => { releaseMask = resolve; });
  await page.route("**/*best-mask.png", async (route) => {
    maskRequest += 1;
    if (maskRequest > 2) await maskReady;
    await route.continue();
  });
  await page.goto("/"); await waitForReady(page);
  const globe = page.getByTestId("interactive-globe");
  await globe.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("details-card")).toBeVisible();
  const initialSelectionTime = await page.getByTestId("details-card").locator(".selection-meta time").getAttribute("datetime");

  await page.getByTestId("next-frame").click();
  await expect(page.getByTestId("details-card")).toBeVisible();
  await expect.poll(async () => page.getByTestId("details-card").locator(".selection-meta time").getAttribute("datetime")).not.toBe(initialSelectionTime);
  await expect(page.getByTestId("details-card").locator(".selection-meta time")).toHaveAttribute("datetime", await page.getByTestId("obs-time").getAttribute("datetime") ?? "");
  await expect(page.getByTestId("loading-state")).toHaveCount(0);
  await expect(page.locator("main.explorer")).toHaveAttribute("data-interpolation-ready", "false");
  await page.getByRole("slider", { name: "forecast timeline" }).fill(String(3_600_000 + 10 * 60_000));
  await globe.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("details-card")).toHaveCount(0);
  releaseMask();
  await expect(page.locator("main.explorer")).toHaveAttribute("data-interpolation-ready", "true", { timeout: 15_000 });
  await expect(page.getByTestId("details-card")).toBeVisible({ timeout: 15_000 });
});

test("keeps the inspect marker when Play is pressed after a globe click", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/"); await waitForReady(page);
  await page.getByTestId("interactive-globe").click({ position: { x: 640, y: 360 } });
  await expect(page.getByTestId("inspect-marker")).toBeVisible();
  await expect(page.getByTestId("details-card")).toBeVisible();
  await page.getByTestId("play-toggle").click();
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", "playing", { timeout: 5_000 });
  await expect(page.getByTestId("inspect-marker")).toBeVisible();
  await expect(page.getByTestId("details-card")).toBeVisible();
});

test("keeps playback running and updates the clicked inspect marker", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/"); await waitForReady(page);
  await expect(page.getByTestId("play-toggle")).toHaveText("Play");
  await page.getByTestId("play-toggle").click();
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", "playing", { timeout: 5_000 });
  const globe = page.getByTestId("interactive-globe");
  await globe.click({ position: { x: 640, y: 360 } });
  await expect(page.getByTestId("inspect-marker")).toBeVisible();
  await expect(page.getByTestId("inspect-marker-value")).toContainText(/µg\/m³|Unavailable|Loading/);
  await expect(page.getByTestId("details-card")).toBeVisible();
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", "playing");
  const validTime = page.getByTestId("details-card").locator(".selection-meta time");
  const selectedTime = await validTime.getAttribute("datetime");
  await expect.poll(async () => validTime.getAttribute("datetime"), { timeout: 8_000 }).not.toBe(selectedTime);
  await page.getByTestId("next-frame").click();
  await expect(page.getByTestId("details-card")).toBeVisible();
  await expect(page.getByTestId("inspect-marker")).toBeVisible();
});

test("places one inspect marker on click and ignores globe drags", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/"); await waitForReady(page);
  const globe = page.getByTestId("interactive-globe");
  const box = await globe.boundingBox();
  expect(box).toBeTruthy();
  const clickGlobe = (x: number, y: number) => page.mouse.click((box?.x ?? 0) + x, (box?.y ?? 0) + y);
  await page.mouse.move((box?.x ?? 0) + 640, (box?.y ?? 0) + 360);
  await page.mouse.down();
  await page.mouse.move((box?.x ?? 0) + 760, (box?.y ?? 0) + 420, { steps: 8 });
  await page.mouse.up();
  await expect(page.getByTestId("inspect-marker")).toHaveCount(0);

  await clickGlobe(640, 360);
  await expect(page.getByTestId("inspect-marker")).toBeVisible();
  const first = await globe.getAttribute("data-inspect-location");
  expect(first).toBeTruthy();
  await page.mouse.move((box?.x ?? 0) + 640, (box?.y ?? 0) + 360);
  await page.mouse.down();
  await page.mouse.move((box?.x ?? 0) + 720, (box?.y ?? 0) + 400, { steps: 8 });
  await page.mouse.up();
  await expect(globe).toHaveAttribute("data-inspect-location", first ?? "");

  // The globe center remains inside the bounded North America display after orbiting.
  await clickGlobe(640, 360);
  await expect.poll(async () => globe.getAttribute("data-inspect-location")).not.toBe(first);
  await page.getByRole("dialog").getByRole("button", { name: /^Close / }).click();
  await expect(page.getByTestId("inspect-marker")).toHaveCount(0);
  await clickGlobe(640, 360);
  await expect(page.getByTestId("inspect-marker")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("inspect-marker")).toHaveCount(0);
  await clickGlobe(640, 360);
  await clickGlobe(16, 16);
  await expect(page.getByTestId("inspect-marker")).toHaveCount(0);
});

test("does not pin modeled smoke when clicking the globe in Air quality", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/"); await waitForReady(page);
  await page.getByTestId("view-air").click();
  await waitForReady(page);
  const globe = page.getByTestId("interactive-globe");
  await globe.click({ position: { x: 640, y: 360 } });
  await expect(page.getByTestId("inspect-marker")).toHaveCount(0);
  await expect(globe).toHaveAttribute("data-inspect-location", "");
});

test("keeps the inspect pin on an off-center click and the value chip north-east of it", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/"); await waitForReady(page);
  const globe = page.getByTestId("interactive-globe");
  const box = await globe.boundingBox();
  expect(box).toBeTruthy();
  const click = { x: 540, y: 280 };
  await globe.click({ position: click });
  const pin = page.getByTestId("inspect-pin");
  const chip = page.getByTestId("inspect-marker");
  await expect(pin).toBeVisible();
  await expect(chip).toBeVisible();
  const pinBox = await pin.boundingBox();
  const chipBox = await chip.boundingBox();
  expect(pinBox).toBeTruthy();
  expect(chipBox).toBeTruthy();
  const pinCenter = {
    x: (pinBox?.x ?? 0) + (pinBox?.width ?? 0) / 2,
    y: (pinBox?.y ?? 0) + (pinBox?.height ?? 0) / 2,
  };
  const clickViewport = { x: (box?.x ?? 0) + click.x, y: (box?.y ?? 0) + click.y };
  expect(Math.hypot(pinCenter.x - clickViewport.x, pinCenter.y - clickViewport.y)).toBeLessThanOrEqual(INSPECT_CLICK_MOUSE_PX);
  expect(Math.abs((chipBox?.x ?? 0) - (pinCenter.x + HOVER_OFFSET_X))).toBeLessThanOrEqual(3);
  expect(Math.abs((chipBox?.y ?? 0) + (chipBox?.height ?? 0) - (pinCenter.y - HOVER_OFFSET_Y))).toBeLessThanOrEqual(3);
});

test("ignores a late source mask from an older selection", async ({ page }) => {
  let request = 0;
  await page.route("**/*best-mask.png", async (route) => {
    request += 1;
    if (request === 1) await new Promise((resolve) => setTimeout(resolve, 1_000));
    await route.continue();
  });
  await page.goto("/"); await waitForReady(page);
  const canvas = page.locator("canvas");
  await canvas.click({ position: { x: 540, y: 360 } });
  await page.getByTestId("next-frame").click();
  await canvas.click({ position: { x: 700, y: 360 } });
  await expect(page.getByTestId("details-card")).toContainText("Jul 15, 2024 · 9:00 PM UTC");
  await page.waitForTimeout(1_200);
  await expect(page.getByTestId("details-card")).toContainText("Jul 15, 2024 · 9:00 PM UTC");
});

for (const viewport of [{ width: 320, height: 568 }, { width: 360, height: 800 }, { width: 390, height: 844 }, { width: 700, height: 390 }, { width: 768, height: 1024 }]) {
  test(`has no clipping or horizontal overflow at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto("/"); await waitForReady(page);
    await expect(page.getByRole("tablist", { name: "Data view" })).toBeVisible();
    const toolbarBox = await page.locator(".location-tools").boundingBox();
    const searchBox = await page.locator(".location-search").boundingBox();
    expect(toolbarBox).toBeTruthy();
    expect(searchBox).toBeTruthy();
    expect(toolbarBox?.x ?? -1).toBeGreaterThanOrEqual(0);
    expect((toolbarBox?.x ?? 0) + (toolbarBox?.width ?? 0)).toBeLessThanOrEqual(viewport.width);
    if (viewport.width < 640) expect(toolbarBox?.width ?? 0).toBeGreaterThanOrEqual(viewport.width - 60);
    else expect(searchBox?.width ?? 999).toBeLessThanOrEqual(440);
    if (viewport.width === 768) expect((await page.locator(".top-toolbar").boundingBox())?.width ?? 999).toBeLessThanOrEqual(736);
    if (viewport.width < 360) {
      const dock = await page.getByRole("region", { name: "forecast timeline and controls" }).boundingBox();
      expect(dock?.x ?? -1).toBeGreaterThanOrEqual(0);
      expect((dock?.x ?? 0) + (dock?.width ?? 0)).toBeLessThanOrEqual(viewport.width);
      await expect(page.getByTestId("obs-time")).toBeVisible();
    }
    if (viewport.width < 768) {
      const brandMetadataSize = await page.locator(".brand-copy small").evaluate((element) => Number.parseFloat(getComputedStyle(element).fontSize));
      expect(brandMetadataSize).toBeGreaterThanOrEqual(8);
      const more = page.getByRole("button", { name: "More options" });
      const moreBox = await more.boundingBox();
      expect(moreBox?.width ?? 0).toBeGreaterThanOrEqual(44);
      expect(moreBox?.height ?? 0).toBeGreaterThanOrEqual(44);
      await more.click();
      await expect(page.getByRole("dialog", { name: "More" }).getByRole("button", { name: /Map options/ })).toBeVisible();
    }
    if (viewport.width < 768 && viewport.height > 500) {
      const statusBox = await page.getByTestId("source-badge").boundingBox();
      const worstBox = await page.getByRole("button", { name: "Worst 5 conditions" }).boundingBox();
      const moreBox = await page.getByRole("button", { name: "More options" }).boundingBox();
      expect(statusBox?.x ?? viewport.width).toBeLessThan(worstBox?.x ?? 0);
      expect(worstBox?.x ?? viewport.width).toBeLessThan(moreBox?.x ?? 0);
    }
    if (viewport.width === 360 && viewport.height === 800) {
      const sources = await page.getByRole("dialog", { name: "More" }).getByRole("button", { name: /About & sources/ }).boundingBox();
      expect(sources?.height ?? 0).toBeGreaterThanOrEqual(44);
    }
    const dimensions = await page.evaluate(() => ({ body: document.body.scrollWidth, document: document.documentElement.scrollWidth, inner: window.innerWidth }));
    expect(dimensions.body).toBe(dimensions.inner);
    expect(dimensions.document).toBe(dimensions.inner);
  });
}

test("keeps only optional map options in the desktop drawer", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/"); await waitForReady(page);
  await expect(page.getByRole("button", { name: "Map options" })).toBeVisible();
  await expect(page.getByTestId("forecast-toggle")).toHaveCount(0);
  await expect(page.getByTestId("forecast-model-best")).toHaveCount(0);
  await page.getByRole("button", { name: "Worst 5 conditions" }).click();
  await page.getByRole("button", { name: "Map options" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(1);
  await expect(page.getByRole("dialog", { name: "Highest conditions" })).toHaveCount(0);
  await expect(page.getByRole("dialog", { name: "Map options" })).toBeVisible();
  const drawerHeading = page.getByRole("dialog", { name: "Map options" }).getByRole("heading");
  await expect(drawerHeading).toBeFocused();
  expect(await drawerHeading.evaluate((element) => getComputedStyle(element).outlineStyle)).toBe("none");
  await expect(page.getByTestId("incidents-toggle")).toBeVisible();
});

test("shows one coherent focus treatment for search and globe navigation", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  const search = page.getByTestId("location-search-input");
  const searchField = page.locator(".location-search-field");
  const idleDecoration = await searchField.evaluate((element) => ({
    borderColor: getComputedStyle(element).borderColor,
    boxShadow: getComputedStyle(element).boxShadow,
  }));
  await search.focus();
  await expect(search).toBeFocused();
  expect(await search.evaluate((element) => getComputedStyle(element).outlineStyle)).toBe("none");
  const focusedDecoration = await searchField.evaluate((element) => ({
    borderColor: getComputedStyle(element).borderColor,
    boxShadow: getComputedStyle(element).boxShadow,
  }));
  expect(focusedDecoration.borderColor).not.toBe(idleDecoration.borderColor);
  expect(focusedDecoration.boxShadow).not.toBe(idleDecoration.boxShadow);

  const globe = page.getByTestId("interactive-globe");
  await globe.focus();
  await expect(globe).toBeFocused();
  expect(await globe.evaluate((element) => getComputedStyle(element).outlineStyle)).toBe("solid");
  expect(await globe.evaluate((element) => getComputedStyle(element).outlineOffset)).toBe("-4px");
});

test("keeps optional map options reachable at 1023x768", async ({ page }) => {
  await page.setViewportSize({ width: 1023, height: 768 });
  await page.goto("/"); await waitForReady(page);
  await page.getByRole("button", { name: "More options" }).click();
  await page.getByRole("dialog", { name: "More" }).getByRole("button", { name: /Map options/ }).click();
  await expect(page.getByTestId("forecast-toggle")).toHaveCount(0);
  const toggle = await page.getByTestId("incidents-toggle").boundingBox();
  expect(toggle).toBeTruthy();
  expect(toggle?.x ?? -1).toBeGreaterThanOrEqual(0);
  expect(toggle?.y ?? -1).toBeGreaterThanOrEqual(0);
  expect((toggle?.x ?? 0) + (toggle?.width ?? 0)).toBeLessThanOrEqual(1023);
  expect((toggle?.y ?? 0) + (toggle?.height ?? 0)).toBeLessThanOrEqual(768);
});

test("shows only the smoke outlook without a model switcher", async ({ page }) => {
  const requested: string[] = [];
  page.on("request", (request) => requested.push(request.url()));
  await page.goto("/"); await waitForReady(page);
  await expect(page.getByTestId("forecast-model-best")).toHaveCount(0);
  await expect(page.getByTestId("forecast-model-hrrr")).toHaveCount(0);
  await expect(page.getByTestId("forecast-model-firework")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "North America smoke and air-quality explorer" })).toBeAttached();
  await expect(page.getByTestId("forecast-toggle")).toHaveCount(0);
  await expect(page.getByTestId("forecast-legend")).toBeVisible();
  await expect(page.getByTestId("forecast-horizon")).toContainText("+36h");
  await expect(page.getByRole("link", { name: "NOAA HRRR-Smoke" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "ECCC FireWork" })).toHaveCount(0);
  expect(requested.some((url) => /\/hrrr\.png(?:\?|$)/.test(url) || /\/firework\.png(?:\?|$)/.test(url))).toBe(false);
});

test("keeps the smoke outlook when HRRR is unavailable", async ({ page }) => {
  await useLiveContext(page, "2024-07-15T20:37:15Z");
  await page.route("**/context/manifests/*.json", async (route) => {
    const response = await route.fetch();
    const manifest = await response.json();
    manifest.mode = "live";
    if (manifest.sources.hrrr) manifest.sources.hrrr = { ...manifest.sources.hrrr, status: "unavailable", error: "HRRR unavailable" };
    if (manifest.forecasts?.hrrr) manifest.forecasts.hrrr = { frames: [] };
    await route.fulfill({ response, json: manifest });
  });
  await page.goto("/"); await waitForReady(page);
  await expect(page.getByTestId("forecast-toggle")).toHaveCount(0);
  await expect(page.locator("canvas")).toBeVisible();
  await expect(page.getByTestId("source-badge")).toHaveClass(/degraded/);
  await expect(page.getByTestId("source-badge")).toContainText("Partial");
  await expect(page.getByTestId("source-warning")).toContainText("NOAA HRRR: unavailable");
  await expect(page.getByTestId("error-state")).toHaveCount(0);
});

test("keeps the smoke outlook when FireWork is unavailable", async ({ page }) => {
  await page.route("**/context/manifests/*.json", async (route) => {
    const response = await route.fetch();
    const manifest = await response.json();
    manifest.mode = "live";
    manifest.sources.firework = { ...manifest.sources.firework, status: "unavailable", error: "FireWork unavailable" };
    if (manifest.forecasts?.firework) manifest.forecasts.firework = { frames: [] };
    await route.fulfill({ response, json: manifest });
  });
  await page.goto("/"); await waitForReady(page);
  await expect(page.getByTestId("forecast-toggle")).toHaveCount(0);
  await expect(page.locator("canvas")).toBeVisible();
  await expect(page.getByTestId("source-warning")).toContainText("ECCC FireWork: unavailable");
  await expect(page.getByTestId("error-state")).toHaveCount(0);
});

test("uses forecast smoke concentration as the compact clicked-location headline", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/"); await waitForReady(page);
  await page.locator("canvas").click({ position: { x: 640, y: 360 } });
  await expect(page.getByTestId("inspect-marker-value")).toBeVisible();
  const details = page.getByTestId("details-card");
  await expect(details).toBeVisible();
  await details.getByText("Source details").click();
  await expect(details.getByText("Guidance", { exact: true })).toBeVisible();
  await expect(details.getByText("Updated", { exact: true })).toBeVisible();
  await expect(page.getByRole("dialog").getByRole("heading")).toHaveText(/(?:<1|\d+(?:\.\d+)?\+?) µg\/m³/);
  await expect(details).toContainText("Modeled wildfire-smoke PM2.5");
  const box = await page.getByRole("dialog").boundingBox();
  expect(box?.width).toBeLessThanOrEqual(380);
  expect(box?.height).toBeLessThanOrEqual(548);
});

test("keeps the globe centered during off-center zoom", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/"); await waitForReady(page);
  const globe = page.getByTestId("interactive-globe");
  await globe.click({ position: { x: 640, y: 360 } });
  const centerLocation = await page.locator(".forecast-location").innerText();
  await page.getByRole("dialog").getByRole("button", { name: /^Close / }).click();
  await page.mouse.move(1_040, 300);
  await page.mouse.wheel(0, -1_400);
  await globe.click({ position: { x: 640, y: 360 } });
  await expect(page.locator(".forecast-location")).toHaveText(centerLocation);
});

test("uses the published outlook legend asset", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  const legend = page.getByTestId("forecast-legend");
  await expect(legend).toBeVisible();
  await expect(legend).toHaveAttribute("src", /legend|forecast-legend|best-legend/i);
});

test("warns when a retained outlook is shown", async ({ page }) => {
  await page.route("**/context/manifests/*.json", async (route) => {
    const response = await route.fetch();
    const manifest = await response.json();
    manifest.forecast.integratedStatus = "retained";
    if (manifest.forecasts?.best) manifest.forecasts.best.integratedStatus = "retained";
    await route.fulfill({ response, json: manifest });
  });
  await page.goto("/"); await waitForReady(page);
  await expect(page.getByTestId("source-warning")).toContainText("last complete smoke outlook");
  await expect(page.getByTestId("view-forecast")).toBeEnabled();

  await page.setViewportSize({ width: 390, height: 844 });
  const portraitWarning = await page.getByTestId("source-warning").boundingBox();
  const toolbar = await page.locator(".top-toolbar").boundingBox();
  expect(portraitWarning).toBeTruthy();
  expect(toolbar).toBeTruthy();
  expect((toolbar?.y ?? 0) + (toolbar?.height ?? 0)).toBeLessThanOrEqual(portraitWarning?.y ?? 0);

  await page.setViewportSize({ width: 844, height: 390 });
  const landscapeWarning = await page.getByTestId("source-warning").boundingBox();
  const scene = await page.locator(".scene").boundingBox();
  const dock = await page.locator(".playback-dock").boundingBox();
  expect(landscapeWarning).toBeTruthy();
  expect(scene).toBeTruthy();
  expect(dock).toBeTruthy();
  expect((landscapeWarning?.x ?? 0) + (landscapeWarning?.width ?? 0)).toBeLessThanOrEqual(scene?.width ?? 0);
  expect((landscapeWarning?.y ?? 0) + (landscapeWarning?.height ?? 0)).toBeLessThanOrEqual(dock?.y ?? 0);
});

test("explains the feathered numeric smoke outlook", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  await page.getByRole("button", { name: "Open methodology" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("How the smoke outlook is built")).toBeVisible();
  await expect(dialog.getByRole("link", { name: "NOAA HRRR-Smoke" })).toBeVisible();
  await expect(dialog.getByRole("link", { name: "ECCC FireWork" })).toBeVisible();
  await expect(dialog.getByText(/FireWork \+ edgeWeight × max\(HRRR − FireWork, 0\)/)).toBeVisible();
  await expect(dialog.getByText(/200 km/)).toBeVisible();
  await expect(dialog.getByText(/combined before colorization/)).toBeVisible();
  await expect(dialog.getByText(/not an average or a calibrated ensemble/)).toBeVisible();
});

test("honors reduced motion while keeping forecast stepping", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/"); await waitForReady(page);
  await expect(page.getByTestId("play-toggle")).toBeDisabled();
  await expect(page.getByTestId("next-frame")).toBeEnabled();
  await searchCity(page, "Seattle");
  const globe = page.getByTestId("interactive-globe");
  await expect(globe).toHaveAttribute("data-focus-location", /-122\.3153,47\.6004/);
  await expect.poll(async () => await globe.getAttribute("data-visible-detail-tiles"), { timeout: 2_000 }).not.toBe("");
});

test("preserves the camera when reduced-motion preference changes", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.goto("/"); await waitForReady(page);
  const globe = page.getByTestId("interactive-globe");
  await globe.focus();
  for (let index = 0; index < 10; index += 1) await page.keyboard.press("+");
  await expect(globe).not.toHaveAttribute("data-visible-detail-tiles", "");
  const before = await globe.getAttribute("data-visible-detail-tiles");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.waitForTimeout(100);
  await expect(globe).toHaveAttribute("data-visible-detail-tiles", before ?? "");
});
