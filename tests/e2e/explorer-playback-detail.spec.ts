import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { demoManifest, waitForReady, searchCity } from "./explorer.fixtures";

test("smoothly animates adjacent FireWork hours", async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto("/"); await waitForReady(page);
  const canvas = page.locator("canvas");
  const timeline = page.getByRole("slider", { name: "forecast timeline" });
  const start = Number(await timeline.inputValue());
  const startProgress = await timeline.evaluate((element) => Number.parseFloat(element.style.getPropertyValue("--timeline-progress")));
  const labelLayer = page.getByTestId("map-label-layer");
  await expect(labelLayer).toHaveAttribute("data-label-catalog-count", "1271", { timeout: 45_000 });
  const labelRevision = Number(await labelLayer.getAttribute("data-label-layout-revision"));
  const labelCount = await page.locator(".map-label").count();
  const canvasSize = await canvas.evaluate((element) => ({ width: (element as HTMLCanvasElement).width, height: (element as HTMLCanvasElement).height }));
  const canvasDigest = async () => createHash("sha256").update(await canvas.evaluate(async (element) => {
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    return (element as HTMLCanvasElement).toDataURL("image/png");
  })).digest("hex");
  const idleA = await canvasDigest();
  await page.waitForTimeout(300);
  const idleB = await canvasDigest();
  expect(idleB).toBe(idleA);
  await expect(page.getByTestId("play-toggle")).toHaveText("Play");
  await expect(page.getByTestId("playback-speed")).toHaveText("1×");
  await page.getByTestId("play-toggle").click();
  await expect(page.getByTestId("play-toggle")).toHaveText("Pause");
  const renderedFrames = new Set<string>();
  for (let sample = 0; sample < 5; sample += 1) {
    await page.waitForTimeout(250);
    renderedFrames.add(await canvasDigest());
  }
  expect(renderedFrames.size).toBeGreaterThan(1);
  await expect.poll(async () => Number(await timeline.inputValue()) - start, { timeout: 5_000 }).toBeGreaterThan(2_000_000);
  await expect.poll(async () => timeline.evaluate((element) => Number.parseFloat(element.style.getPropertyValue("--timeline-progress"))), { timeout: 15_000 }).toBeGreaterThan(startProgress);
  expect(await page.locator(".map-label").count()).toBe(labelCount);
  expect(Number(await labelLayer.getAttribute("data-label-layout-revision"))).toBe(labelRevision);
  expect(await canvas.evaluate((element) => ({ width: (element as HTMLCanvasElement).width, height: (element as HTMLCanvasElement).height }))).toEqual(canvasSize);
  await expect(page.getByTestId("forecast-horizon")).toContainText("Forecast");
});

test("scrubs the forecast timeline by dragging the thumb", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/"); await waitForReady(page);
  const timeline = page.getByRole("slider", { name: "forecast timeline" });
  const globe = page.getByTestId("interactive-globe");
  const start = Number(await timeline.inputValue());
  const startInspect = await globe.getAttribute("data-inspect-location");
  const box = await timeline.boundingBox();
  expect(box).toBeTruthy();
  const y = (box?.y ?? 0) + (box?.height ?? 0) / 2;
  await page.mouse.move((box?.x ?? 0) + 16, y);
  await page.mouse.down();
  await page.mouse.move((box?.x ?? 0) + (box?.width ?? 0) * 0.65, y, { steps: 12 });
  await page.mouse.up();
  await expect.poll(async () => Number(await timeline.inputValue())).toBeGreaterThan(start + 60_000);
  await expect.poll(async () => Number.parseFloat(await timeline.evaluate((element) => element.style.getPropertyValue("--timeline-progress")))).toBeGreaterThan(1);
  expect(await globe.getAttribute("data-inspect-location")).toBe(startInspect);

  await page.getByTestId("play-toggle").click();
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", "playing", { timeout: 5_000 });
  const playingStart = Number(await timeline.inputValue());
  const playingBox = await timeline.boundingBox();
  expect(playingBox).toBeTruthy();
  const playingY = (playingBox?.y ?? 0) + (playingBox?.height ?? 0) / 2;
  const playingX = (playingBox?.x ?? 0) + (playingBox?.width ?? 0) * (playingStart > 0 ? 0.25 : 0.7);
  await page.mouse.move((playingBox?.x ?? 0) + 16, playingY);
  await page.mouse.down();
  await page.mouse.move(playingX, playingY, { steps: 10 });
  await page.mouse.up();
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", "interaction-paused");
  await expect.poll(async () => Number(await timeline.inputValue())).not.toBe(playingStart);
  expect(await globe.getAttribute("data-inspect-location")).toBe(startInspect);
});

test("starts demo animation only after Play, repaints smoke, and keeps zoom active", async ({ page }) => {
  await page.setViewportSize({ width: 512, height: 512 });
  await page.goto("/"); await waitForReady(page);
  const explorer = page.locator("main.explorer");
  const canvas = page.getByTestId("interactive-globe");
  const timeline = page.getByRole("slider", { name: "forecast timeline" });
  await expect(page.getByTestId("play-toggle")).toHaveText("Play");
  await expect(page.getByTestId("playback-speed")).toBeHidden();
  await page.getByRole("button", { name: "More options" }).click();
  await expect(page.getByTestId("mobile-playback-speed")).toContainText("1×");
  await page.getByRole("button", { name: "Close more" }).click();
  const idlePosition = await timeline.inputValue();
  await page.waitForTimeout(500);
  expect(await timeline.inputValue()).toBe(idlePosition);
  await timeline.fill("300000");
  await page.getByTestId("play-toggle").click();
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", "playing", { timeout: 5_000 });
  await expect(page.getByTestId("play-toggle")).toHaveText("Pause");
  const samples = await page.evaluate(async () => {
    const slider = document.querySelector<HTMLInputElement>('input[aria-label="forecast timeline"]');
    const values = new Set<string>();
    const end = performance.now() + 1_500;
    await new Promise<void>((resolve) => {
      const sample = (now: number) => {
        if (slider) values.add(slider.value);
        if (now >= end) resolve();
        else requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
    });
    return values.size;
  });
  expect(samples).toBeGreaterThan(1);
  await canvas.hover();
  await page.mouse.wheel(0, -400);
  await expect(explorer).toHaveAttribute("data-playback-phase", "playing");
  await canvas.dispatchEvent("pointerdown", { pointerType: "touch", pointerId: 1 });
  await expect(explorer).toHaveAttribute("data-playback-phase", "playing");
  await canvas.focus();
  await page.keyboard.press("+");
  await expect(explorer).toHaveAttribute("data-playback-phase", "playing");
  await expect(explorer).toHaveAttribute("data-resident-frame-count", /[123]/);
});

test("freezes the rendered smoke at the hot timeline position when playback pauses", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  const explorer = page.locator("main.explorer");
  const canvas = page.getByTestId("interactive-globe").locator("canvas");
  const timeline = page.getByRole("slider", { name: "forecast timeline" });
  const snapshot = async () => ({
    position: Number(await timeline.inputValue()),
    hotMix: Number(await explorer.getAttribute("data-render-mix")),
    canvasMix: Number(await canvas.getAttribute("data-rendered-forecast-mix")),
    renderedFrom: Number(await explorer.getAttribute("data-rendered-from-index")),
  });
  const expectFrozen = async () => {
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    const first = await snapshot();
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    const second = await snapshot();
    expect(second.position).toBe(first.position);
    expect(second.hotMix).toBeCloseTo(first.hotMix, 5);
    expect(second.canvasMix).toBeCloseTo(first.canvasMix, 5);
    expect(second.canvasMix, JSON.stringify({ first, second })).toBeCloseTo(second.hotMix, 5);
  };

  await timeline.fill("300000");
  await page.getByTestId("play-toggle").click();
  await expect.poll(async () => Number(await timeline.inputValue()), { timeout: 5_000 }).toBeGreaterThan(1_000_000);
  await page.getByTestId("play-toggle").click();
  await expect(explorer).toHaveAttribute("data-playback-phase", "manual-paused");
  const pausedPosition = Number(await timeline.inputValue());
  await expectFrozen();

  await page.getByTestId("play-toggle").click();
  await expect.poll(async () => Number(await timeline.inputValue()), { timeout: 5_000 }).toBeGreaterThan(pausedPosition + 500_000);
  await page.getByRole("button", { name: "Open methodology" }).click();
  await expect(explorer).toHaveAttribute("data-playback-phase", "interaction-paused");
  await expectFrozen();
});

test("crosses an hourly boundary at a uniform cadence without holding the endpoint", async ({ page }) => {
  const rendererErrors: string[] = [];
  page.on("pageerror", (error) => {
    if (error.message.includes("Should not already be working") || error.message.includes("Data cannot be cloned")) {
      rendererErrors.push(error.message);
    }
  });
  await page.goto("/"); await waitForReady(page);
  const slider = page.getByRole("slider", { name: "forecast timeline" });
  const boundary = 3_600_000;
  await slider.fill(String(boundary - 1_800_000));
  await page.evaluate(() => {
    const sliderElement = document.querySelector<HTMLInputElement>('input[aria-label="forecast timeline"]');
    const explorer = document.querySelector("main.explorer");
    const samples: Array<{ at: number; position: number; mix: number; phase: string | null; renderedFrom: number }> = [];
    (window as typeof window & { __hourlyCadenceSamples?: typeof samples }).__hourlyCadenceSamples = samples;
    const sample = (now: number) => {
      samples.push({
        at: now,
        position: Number(sliderElement?.value ?? 0),
        mix: Number(explorer?.getAttribute("data-render-mix") ?? 0),
        phase: explorer?.getAttribute("data-playback-phase") ?? null,
        renderedFrom: Number(explorer?.getAttribute("data-rendered-from-index") ?? 0),
      });
      requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });
  await page.getByTestId("play-toggle").click();
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", "playing", { timeout: 5_000 });
  await expect.poll(async () => Number(await slider.inputValue()), { timeout: 5_000 }).toBeGreaterThan(boundary);
  const samples = await page.evaluate(() => (window as typeof window & { __hourlyCadenceSamples?: Array<{ at: number; position: number; mix: number; phase: string | null; renderedFrom: number }> }).__hourlyCadenceSamples ?? []);
  const playingSamples = samples.filter((sample) => sample.phase === "playing");
  const changed = playingSamples.filter((sample, index) => index === 0 || sample.position !== playingSamples[index - 1].position);
  const crossing = changed.findIndex((sample) => sample.position >= boundary);
  expect(crossing).toBeGreaterThan(0);
  expect(changed.length).toBeGreaterThanOrEqual(4);
  expect(changed.every((sample) => sample.phase === "playing")).toBe(true);
  expect(changed.slice(1).every((sample, index) => sample.position > changed[index].position)).toBe(true);
  const updateGaps = changed.slice(1).map((sample, index) => sample.at - changed[index].at);
  const crossingGap = changed[crossing].at - changed[crossing - 1].at;
  const neighboringGaps = updateGaps.filter((_, index) => index !== crossing - 1);
  expect(crossingGap).toBeLessThanOrEqual(Math.max(...neighboringGaps) + 40);
  const before = changed[crossing - 1];
  expect(before.mix).toBeGreaterThan(0.5);
  expect(changed.every((sample) => {
    const desiredFrom = Math.min(1, Math.floor(sample.position / boundary));
    if (sample.renderedFrom < desiredFrom) return sample.mix >= 0.999;
    if (sample.renderedFrom === desiredFrom) {
      const expectedMix = (sample.position - desiredFrom * boundary) / boundary;
      return Math.abs(sample.mix - expectedMix) < 0.08;
    }
    return false;
  })).toBe(true);
  expect(changed.at(-1)?.position).toBeGreaterThan(boundary);
  expect(rendererErrors).toEqual([]);
});

test("starts three-times playback without waiting for browser idle decode time", async ({ page }) => {
  await page.addInitScript(() => {
    window.requestIdleCallback = (callback, options) => window.setTimeout(() => callback({
      didTimeout: true,
      timeRemaining: () => 0,
    }), options?.timeout ?? 1);
    window.cancelIdleCallback = (handle) => window.clearTimeout(handle);
  });
  await page.goto("/"); await waitForReady(page);
  const speed = page.getByTestId("playback-speed");
  await speed.click();
  await expect(speed).toHaveText("3×");
  const timeline = page.getByRole("slider", { name: "forecast timeline" });
  await timeline.fill("0");
  await page.getByTestId("play-toggle").click();
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", "playing", { timeout: 400 });
  const samples = await page.evaluate(async () => {
    const explorer = document.querySelector("main.explorer");
    const slider = document.querySelector<HTMLInputElement>('input[aria-label="forecast timeline"]');
    const values: Array<{ phase: string | null; position: number }> = [];
    const end = performance.now() + 450;
    await new Promise<void>((resolve) => {
      const sample = (now: number) => {
        values.push({
          phase: explorer?.getAttribute("data-playback-phase") ?? null,
          position: Number(slider?.value ?? 0),
        });
        if (now >= end) resolve();
        else requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
    });
    return values;
  });
  expect(samples.every((sample) => sample.phase === "playing")).toBe(true);
  expect(samples.at(-1)?.position).toBeGreaterThan(3_600_000);
});

test("shows loading and starts automatically after a requested raster becomes resident", async ({ page }) => {
  const delayed = new Set([demoManifest.forecast.frames[2].textureUrl, demoManifest.forecast.frames[2].sourceMaskUrl]);
  let releaseFrames = () => {};
  const frameGate = new Promise<void>((resolve) => { releaseFrames = resolve; });
  await page.route("**/demo/context/assets/**/*.png", async (route) => {
    if (delayed.has(new URL(route.request().url()).pathname)) await frameGate;
    await route.continue();
  });
  await page.goto("/"); await waitForReady(page);
  const speed = page.getByTestId("playback-speed");
  await speed.click();
  const slider = page.getByRole("slider", { name: "forecast timeline" });
  await slider.fill(String(3_600_000 - 300_000));
  await page.getByTestId("play-toggle").click();
  await expect(page.getByTestId("play-toggle")).toHaveText("Loading…");
  await expect(page.getByTestId("play-toggle")).toHaveAccessibleName("Cancel loading forecast playback");
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", "buffering");
  const held = await slider.inputValue();
  await page.waitForTimeout(300);
  expect(await slider.inputValue()).toBe(held);
  releaseFrames();
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", "playing", { timeout: 5_000 });
  await expect.poll(async () => Number(await slider.inputValue()), { timeout: 5_000 }).toBeGreaterThan(3_600_000 - 300_000);
  await expect(page.getByTestId("play-toggle")).toHaveText("Pause");
});

test("does not start playback from passive pointer movement", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  const phase = page.locator("main.explorer");
  const slider = page.getByRole("slider", { name: "forecast timeline" });
  const start = Number(await slider.inputValue());
  for (let index = 0; index < 8; index += 1) {
    await page.getByTestId("interactive-globe").dispatchEvent("pointermove", { clientX: 100 + index, clientY: 100 });
    await page.waitForTimeout(50);
  }
  await expect(phase).toHaveAttribute("data-playback-phase", "manual-paused");
  await expect(page.getByTestId("play-toggle")).toHaveText("Play");
  expect(Number(await slider.inputValue())).toBe(start);
});

test("requires Play again after interaction pauses playback", async ({ page }) => {
  await page.clock.install();
  await page.goto("/"); await waitForReady(page);
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1_000));
  const play = page.getByTestId("play-toggle");
  await expect(play).toHaveText("Play");
  await play.click();
  await expect(play).toHaveText("Pause");
  await page.getByRole("button", { name: "Map options" }).click();
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", "interaction-paused");
  await expect(play).toHaveText("Play");
  await play.click();
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", "playing");
  await expect(play).toHaveText("Pause");
  await page.getByRole("button", { name: "Map options" }).click();
  await expect(play).toHaveText("Play");
  await page.clock.fastForward(10_000);
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", "interaction-paused");
  await expect(play).toHaveText("Play");
  await page.getByTestId("next-frame").click();
  await page.clock.fastForward(10_000);
  await expect(play).toHaveText("Play");
});

test("fades only smoke while resetting the +36h endpoint", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  const slider = page.getByRole("slider", { name: "forecast timeline" });
  const maximum = Number(await slider.getAttribute("max"));
  await slider.fill(String(maximum));
  await page.evaluate(() => {
    const explorer = document.querySelector("main.explorer");
    const sliderElement = document.querySelector<HTMLInputElement>('input[aria-label="forecast timeline"]');
    (window as typeof window & { __loopStates?: Array<{ phase: string | null; opacity: number; position: number }> }).__loopStates = [];
    if (!explorer || !sliderElement) return;
    const record = () => (window as typeof window & { __loopStates: Array<{ phase: string | null; opacity: number; position: number }> }).__loopStates.push({
      phase: explorer.getAttribute("data-playback-phase"),
      opacity: Number(explorer.getAttribute("data-smoke-opacity")),
      position: Number(sliderElement.value),
    });
    new MutationObserver(record).observe(explorer, { attributes: true, attributeFilter: ["data-playback-phase", "data-smoke-opacity"] });
    record();
  });
  await page.getByTestId("play-toggle").click();
  await expect.poll(async () => Number(await slider.inputValue()), { timeout: 12_000 }).toBeLessThan(maximum / 2);
  await expect.poll(async () => page.evaluate(() =>
    (window as typeof window & { __loopStates?: Array<{ phase: string | null }> }).__loopStates
      ?.some((state) => state.phase === "fading-in") ?? false
  ), { timeout: 12_000 }).toBe(true);
  await expect(page.getByTestId("play-toggle")).toHaveText("Pause");
  const states = await page.evaluate(() => (window as typeof window & { __loopStates?: Array<{ phase: string | null; opacity: number; position: number }> }).__loopStates ?? []);
  expect(states.some((state) => state.phase === "fading-out" && state.opacity < 1)).toBe(true);
  expect(states.some((state) => state.phase === "resetting" && state.opacity === 0)).toBe(true);
  expect(states.some((state) => state.phase === "fading-in" && state.position < maximum / 2)).toBe(true);
});

test("keeps endpoint smoke visible when reset assets fail", async ({ page }) => {
  let failReset = false;
  let failures = 0;
  const resetAssets = new Set(demoManifest.forecast.frames.slice(0, 3).flatMap(
    (frame: { textureUrl: string; sourceMaskUrl: string }) => [frame.textureUrl, frame.sourceMaskUrl],
  ));
  await page.route((url) => resetAssets.has(url.pathname), (route) => {
    if (!failReset) return route.continue();
    failures += 1;
    return route.abort("failed");
  });
  await page.goto("/"); await waitForReady(page);
  const speed = page.getByTestId("playback-speed");
  await speed.click();
  const slider = page.getByRole("slider", { name: "forecast timeline" });
  const maximum = Number(await slider.getAttribute("max"));
  await slider.fill(String(maximum - 3_600_000));
  failReset = true;
  await page.getByTestId("play-toggle").click();
  await expect.poll(() => failures, { timeout: 12_000 }).toBeGreaterThan(0);
  await expect(page.getByTestId("source-warning")).toBeVisible();
  await expect(page.locator("main.explorer")).toHaveAttribute("data-smoke-opacity", "1.000");
  expect(Number(await slider.inputValue())).toBeGreaterThan(maximum / 2);
});

test("pauses for methodology and Air Quality without restarting", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  const phase = page.locator("main.explorer");
  await page.getByTestId("play-toggle").click();
  await page.getByRole("button", { name: "Open methodology" }).click();
  await expect(phase).toHaveAttribute("data-playback-phase", "interaction-paused");
  await page.getByRole("button", { name: "Close methodology" }).click();
  await page.waitForTimeout(6_200);
  await expect(phase).toHaveAttribute("data-playback-phase", "interaction-paused");
  await page.reload(); await waitForReady(page);
  await page.getByTestId("play-toggle").click();
  await page.getByTestId("view-air").click();
  await expect(phase).toHaveAttribute("data-playback-phase", "interaction-paused");
});

test("stays paused after visibility returns until Play is pressed", async ({ page }) => {
  await page.clock.install();
  await page.goto("/"); await waitForReady(page);
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1_000));
  const phase = page.locator("main.explorer");
  const slider = page.getByRole("slider", { name: "forecast timeline" });
  await page.getByTestId("play-toggle").click();
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect(phase).toHaveAttribute("data-playback-phase", "interaction-paused");
  const hiddenPosition = await slider.inputValue();
  await page.clock.fastForward(10_000);
  expect(await slider.inputValue()).toBe(hiddenPosition);
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.clock.fastForward(10_000);
  await expect(phase).toHaveAttribute("data-playback-phase", "interaction-paused");
  await page.getByTestId("play-toggle").click();
  await expect(phase).toHaveAttribute("data-playback-phase", "playing");
});

test("keeps active playback running across an unchanged context refresh", async ({ page }) => {
  let pointerRequests = 0;
  await page.route("**/demo/context/latest.json*", (route) => {
    pointerRequests += 1;
    return route.continue();
  });
  await page.goto("/"); await waitForReady(page);
  const slider = page.getByRole("slider", { name: "forecast timeline" });
  const before = Number(await slider.inputValue());
  await page.getByTestId("play-toggle").click();
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect.poll(() => pointerRequests).toBeGreaterThan(1);
  await expect(page.locator("main.explorer")).toHaveAttribute("data-playback-phase", "playing");
  await expect.poll(async () => Number(await slider.inputValue())).toBeGreaterThan(before);
});

test("ignores an older context poll that finishes after a newer poll", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  let manifestRequests = 0;
  let releaseFirst!: () => void;
  const firstMayFinish = new Promise<void>((resolve) => { releaseFirst = resolve; });
  await page.route("**/context/manifests/*.json", async (route) => {
    const request = ++manifestRequests;
    const response = await route.fetch();
    const manifest = await response.json();
    manifest.generatedAt = request === 1 ? "2024-07-15T20:01:00Z" : "2024-07-15T20:02:00Z";
    if (request === 1) await firstMayFinish;
    await route.fulfill({ response, json: manifest });
  });

  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect.poll(() => manifestRequests).toBe(1);
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect.poll(() => manifestRequests).toBe(2);
  await expect(page.locator("main.explorer")).toHaveAttribute("data-context-generated-at", "2024-07-15T20:02:00Z");
  releaseFirst();
  await expect(page.locator("main.explorer")).toHaveAttribute("data-context-generated-at", "2024-07-15T20:02:00Z");
});

test("does not expose air observations from the previous context publication", async ({ page }) => {
  let manifestRequests = 0;
  let serveNextPublication = false;
  let nextMonitorRequests = 0;
  let releaseNextMonitors!: () => void;
  const nextMonitorsReady = new Promise<void>((resolve) => { releaseNextMonitors = resolve; });
  await page.route("**/context/manifests/*.json", async (route) => {
    const response = await route.fetch();
    const manifest = await response.json();
    manifestRequests += 1;
    if (serveNextPublication) {
      manifest.generatedAt = "2024-07-15T20:01:00Z";
      for (const monitorSet of Object.values(manifest.air.monitorSets) as Array<{ url: string }>) {
        monitorSet.url = `${monitorSet.url}?publication=next`;
      }
    }
    await route.fulfill({ response, json: manifest });
  });
  await page.route("**/*-monitors.json?publication=next", async (route) => {
    nextMonitorRequests += 1;
    await nextMonitorsReady;
    await route.continue();
  });

  await page.goto("/"); await waitForReady(page);
  await page.getByTestId("view-air").click();
  await waitForReady(page);
  serveNextPublication = true;
  const initialManifestRequests = manifestRequests;
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect.poll(() => manifestRequests).toBeGreaterThan(initialManifestRequests);
  await expect.poll(() => nextMonitorRequests).toBeGreaterThan(0);
  await expect(page.getByTestId("loading-state")).toBeVisible();

  const globe = page.getByTestId("interactive-globe");
  await globe.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("details-card")).toHaveCount(0);

  releaseNextMonitors();
  await waitForReady(page);
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("details-card")).toContainText(/AQI|AQHI/);
});

test("closes a selection surface when a new context publication arrives", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let manifestRequests = 0;
  let serveNextPublication = false;
  await page.route("**/context/manifests/*.json", async (route) => {
    const response = await route.fetch();
    const manifest = await response.json();
    manifestRequests += 1;
    if (serveNextPublication) manifest.generatedAt = "2024-07-15T20:01:00Z";
    await route.fulfill({ response, json: manifest });
  });

  await page.goto("/"); await waitForReady(page);
  await searchCity(page, "Seattle");
  await expect(page.locator("main.explorer")).toHaveClass(/context-open/);
  serveNextPublication = true;
  const initialManifestRequests = manifestRequests;
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect.poll(() => manifestRequests).toBeGreaterThan(initialManifestRequests);
  await expect(page.locator("main.explorer")).toHaveAttribute("data-context-generated-at", "2024-07-15T20:01:00Z", { timeout: 15_000 });
  await expect(page.getByTestId("location-card")).toHaveCount(0);
  await expect(page.locator("main.explorer")).not.toHaveClass(/context-open/);
  await expect(page.getByRole("region", { name: "forecast timeline and controls" })).toBeVisible();
});

test("explains forecasts, station readings, and reported wildfires and restores focus", async ({ page }) => {
  await page.goto("/"); await waitForReady(page);
  const trigger = page.getByRole("button", { name: "Open methodology" });
  await trigger.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Forecast versus observed air quality")).toBeVisible();
  await expect(dialog.getByText("How PM2.5 readings are prepared")).toBeVisible();
  await expect(dialog.getByText(/Each marker remains an individual observation/)).toBeVisible();
  await expect(dialog.getByText("Reported wildfires", { exact: true })).toBeVisible();
  await expect(dialog.getByText("Map geography", { exact: true })).toBeVisible();
  await expect(dialog.getByText("Privacy", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("link", { name: "Wikidata CC0" })).toHaveAttribute("href", "https://www.wikidata.org/wiki/Wikidata:Copyright");
  await expect(dialog.getByText(/self-hosted, includes no analytics/)).toBeVisible();
  await page.getByRole("button", { name: "Close methodology" }).click();
  await expect(trigger).toBeFocused();
});

test("restores methodology focus to the control that opened it", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/"); await waitForReady(page);
  const trigger = page.getByRole("button", { name: "More options" });
  await trigger.click();
  await page.getByRole("dialog", { name: "More" }).getByRole("button", { name: /About & sources/ }).click();
  await page.getByRole("button", { name: "Close methodology" }).click();
  await expect(trigger).toBeFocused();
});
