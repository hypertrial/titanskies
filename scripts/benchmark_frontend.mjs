#!/usr/bin/env node
import { chromium } from "@playwright/test";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";

const requireHardware = process.argv.includes("--require-hardware");
const liveData = process.argv.includes("--live-data");
const liveContextUrl = process.env.TITANSKIES_BENCHMARK_CONTEXT_URL?.trim() || "";
if (liveData && !liveContextUrl) throw new Error("--live-data requires TITANSKIES_BENCHMARK_CONTEXT_URL");
const port = Number(process.env.FRONTEND_BENCHMARK_PORT ?? 3020);
const baseURL = `http://127.0.0.1:${port}`;
const require = createRequire(import.meta.url);
const nextBin = require.resolve("next/dist/bin/next");
const server = spawn(process.execPath, [nextBin, "start", "-H", "127.0.0.1", "-p", String(port)], {
  env: {
    ...process.env,
    NEXT_PUBLIC_CONTEXT_URL: liveData ? liveContextUrl : "/demo/context/latest.json",
  },
  stdio: ["ignore", "pipe", "pipe"],
});
let serverOutput = "";
server.stdout.on("data", (chunk) => { serverOutput += chunk; });
server.stderr.on("data", (chunk) => { serverOutput += chunk; });

async function resolveLivePublication() {
  const configuredManifestUrl = process.env.TITANSKIES_BENCHMARK_MANIFEST_URL?.trim();
  let pointer;
  let pointerBase = liveContextUrl;
  if (configuredManifestUrl) {
    pointer = { manifestUrl: configuredManifestUrl };
    pointerBase = configuredManifestUrl;
  } else {
    const response = await fetch(`${liveContextUrl}${liveContextUrl.includes("?") ? "&" : "?"}t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`live context pointer returned ${response.status}`);
    pointer = await response.json();
    pointerBase = response.url;
  }
  const manifestUrl = new URL(pointer.manifestUrl, pointerBase).href;
  const manifestResponse = await fetch(manifestUrl, { cache: "no-store" });
  if (!manifestResponse.ok) throw new Error(`live manifest returned ${manifestResponse.status}`);
  const bytes = Buffer.from(await manifestResponse.arrayBuffer());
  const manifest = JSON.parse(bytes.toString("utf8"));
  if (manifest.mode !== "live" || !Number.isInteger(manifest.version) || !manifest.generatedAt) throw new Error("live benchmark requires a valid live manifest");
  const monitorSets = Object.fromEntries(Object.entries(manifest.air?.monitorSets ?? {}).map(([name, value]) => [name, Number(value?.count ?? 0)]));
  const incidentEntries = await Promise.all([
    ["wfigs", manifest.fires?.wfigsIncidentsUrl],
    ["cwfis", manifest.fires?.cwfisIncidentsUrl],
  ].map(async ([name, url]) => {
    if (!url) return [name, 0];
    const response = await fetch(new URL(url, manifestUrl), { cache: "no-store" });
    if (!response.ok) throw new Error(`${name} incident asset returned ${response.status}`);
    const payload = await response.json();
    return [name, Array.isArray(payload.incidents) ? payload.incidents.length : 0];
  }));
  const incidentSets = Object.fromEntries(incidentEntries);
  return {
    pointer: { version: manifest.version, manifestUrl, updatedAt: manifest.generatedAt },
    summary: {
      manifestUrl,
      sha256: createHash("sha256").update(bytes).digest("hex"),
      version: manifest.version,
      generatedAt: manifest.generatedAt,
      forecastFrames: Number(manifest.forecast?.frames?.length ?? 0),
      monitorSets,
      comparableMonitors: Number(monitorSets.airnow ?? 0) + Number(monitorSets.bcair ?? 0) + Number(monitorSets.sinaica ?? 0),
      incidentSets,
      incidents: Number(incidentSets.wfigs ?? 0) + Number(incidentSets.cwfis ?? 0),
    },
  };
}

async function installLivePointer(page, publication) {
  if (!publication) return;
  await page.route((url) => url.href.startsWith(liveContextUrl), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: { "Access-Control-Allow-Origin": "*", "Cache-Control": "no-store" },
      body: JSON.stringify(publication.pointer),
    });
  });
}

async function ready() {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (server.exitCode != null) break;
    try {
      if ((await fetch(baseURL)).ok) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`benchmark server did not become ready\n${serverOutput.trim()}`);
}

async function installDiagnostics(page) {
  await page.addInitScript(() => {
    globalThis.__TITANSKIES_LONG_TASKS__ = [];
    if (typeof PerformanceObserver !== "undefined" && PerformanceObserver.supportedEntryTypes.includes("longtask")) {
      new PerformanceObserver((list) => {
        globalThis.__TITANSKIES_LONG_TASKS__.push(...list.getEntries().map((entry) => ({ start: entry.startTime, duration: entry.duration })));
      }).observe({ type: "longtask", buffered: true });
    }
  });
}

async function waitForRasterDiagnostics(page, label) {
  try {
    await page.waitForFunction(() => Boolean(globalThis.__TITANSKIES_PERF__?.rasterCache), undefined, { timeout: 5_000 });
  } catch {
    throw new Error(`${label} did not expose raster diagnostics; build with NEXT_PUBLIC_PERF_DIAGNOSTICS=1`);
  }
}

async function waitForSettledPlayback(page) {
  const settled = () => {
    const explorer = document.querySelector(".explorer");
    const cache = globalThis.__TITANSKIES_PERF__?.rasterCache;
    return explorer?.getAttribute("data-playback-phase") === "playing"
      && explorer.getAttribute("data-detail-playback-mode") === "base-only"
      && Number(explorer.getAttribute("data-raster-resident-count") ?? 0) === 3
      && Number(cache?.pending ?? -1) === 0;
  };
  await page.waitForFunction(settled, undefined, { timeout: 20_000 });
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const before = await page.evaluate(() => ({
      renders: Number(globalThis.__TITANSKIES_PERF__?.explorerRenderCount ?? 0),
      pairs: Number(globalThis.__TITANSKIES_PERF__?.pairCommits ?? 0),
      publications: Number(globalThis.__TITANSKIES_PERF__?.rasterCache?.publications ?? 0),
    }));
    await page.waitForTimeout(400);
    await page.waitForFunction(settled, undefined, { timeout: 20_000 });
    const after = await page.evaluate(() => ({
      renders: Number(globalThis.__TITANSKIES_PERF__?.explorerRenderCount ?? 0),
      pairs: Number(globalThis.__TITANSKIES_PERF__?.pairCommits ?? 0),
      publications: Number(globalThis.__TITANSKIES_PERF__?.rasterCache?.publications ?? 0),
    }));
    if (JSON.stringify(after) === JSON.stringify(before)) return;
  }
  throw new Error("forecast playback did not reach a stable render-isolation baseline");
}

async function zoomToRegionalDetail(page) {
  const globe = page.getByTestId("interactive-globe");
  for (let index = 0; index < 10; index += 1) {
    await globe.press("+");
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => resolve())));
  }
  await page.waitForFunction(() => document.querySelector('[data-testid="interactive-globe"]')?.getAttribute("data-visible-detail-tiles"), undefined, { timeout: liveData ? 30_000 : 20_000 });
  return globe;
}

async function dragGlobe(page, durationMs) {
  const box = await page.getByTestId("interactive-globe").boundingBox();
  if (!box) throw new Error("interactive globe is not visible");
  const steps = 90;
  const centerX = box.x + box.width / 2;
  const centerY = box.y + box.height / 2;
  await page.mouse.move(centerX, centerY);
  await page.mouse.down();
  for (let index = 0; index < steps; index += 1) {
    const angle = index / (steps - 1) * Math.PI * 2;
    await page.mouse.move(centerX + Math.sin(angle) * box.width * 0.22, centerY + Math.sin(angle * 2) * box.height * 0.08);
    await page.waitForTimeout(durationMs / steps);
  }
  await page.mouse.up();
}

async function sample(page, label, durationMs = 2500, interaction = null) {
  if (interaction) await page.evaluate(() => {
    const performanceState = globalThis.__TITANSKIES_PERF__;
    if (performanceState?.air) performanceState.air = { ...performanceState.air, passes: 0, durationMs: 0, maxDurationMs: 0 };
    if (performanceState?.incidents) performanceState.incidents = { ...performanceState.incidents, passes: 0, durationMs: 0, maxDurationMs: 0 };
  });
  const pending = page.evaluate(async ({ duration, isolateTicks }) => {
    const intervals = [];
    const positions = [];
    const mixes = [];
    const timeline = document.querySelector('input[aria-label="forecast timeline"]');
    const explorer = document.querySelector(".explorer");
    if (isolateTicks) {
      // At quarter speed an hourly pair lasts four seconds. Start inside it,
      // so this 500ms sample measures ticks rather than boundary/buffering work.
      // Read the baseline below in this same evaluation to avoid a polling race.
      const deadline = performance.now() + 20_000;
      while (true) {
        const cache = globalThis.__TITANSKIES_PERF__?.rasterCache;
        const mix = Number(explorer?.getAttribute("data-render-mix"));
        if (mix >= 0.2 && mix <= 0.5
          && explorer?.getAttribute("data-playback-phase") === "playing"
          && explorer.getAttribute("data-detail-playback-mode") === "base-only"
          && Number(explorer.getAttribute("data-raster-resident-count")) === 3
          && Number(cache?.pending ?? -1) === 0) break;
        if (performance.now() >= deadline) throw new Error("tick isolation did not reach a settled pair interior");
        await new Promise((resolve) => requestAnimationFrame(resolve));
      }
    }
    const ui = document.querySelector('[data-testid="obs-time"]');
    const labelLayer = document.querySelector('[data-testid="map-label-layer"]');
    const beforeUiRevision = Number(ui?.getAttribute("data-ui-revision") ?? 0);
    const beforeLabelRevision = Number(labelLayer?.getAttribute("data-label-layout-revision") ?? 0);
    const before = globalThis.__TITANSKIES_PERF__ ?? {};
    const beforeCache = before.rasterCache ?? {};
    const beforeAir = before.air ?? {};
    const beforeIncidents = before.incidents ?? {};
    const startedAt = performance.now();
    let previous = startedAt;
    const end = previous + duration;
    if (timeline instanceof HTMLInputElement) positions.push(timeline.value);
    mixes.push(explorer?.getAttribute("data-render-mix") ?? "");
    await new Promise((resolve) => {
      const tick = (now) => {
        intervals.push(now - previous);
        if (timeline instanceof HTMLInputElement) positions.push(timeline.value);
        mixes.push(explorer?.getAttribute("data-render-mix") ?? "");
        previous = now;
        if (now >= end) resolve();
        else requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
    const after = globalThis.__TITANSKIES_PERF__ ?? {};
    const cache = after.rasterCache ?? {};
    const air = after.air ?? {};
    const incidents = after.incidents ?? {};
    const sorted = [...intervals].sort((a, b) => a - b);
    const percentile = (value) => sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * value))] ?? 0;
    const changed = (values) => values.filter((value, index) => index > 0 && value !== values[index - 1]).length;
    const longTasks = (globalThis.__TITANSKIES_LONG_TASKS__ ?? []).filter((entry) => entry.start >= startedAt && entry.start <= performance.now());
    return {
      durationMs: Math.round(performance.now() - startedAt),
      frames: intervals.length,
      medianFrameMs: Number(percentile(0.5).toFixed(2)),
      p95FrameMs: Number(percentile(0.95).toFixed(2)),
      framesOver50Ms: intervals.filter((value) => value > 50).length,
      framesOver50Percent: Number(((intervals.filter((value) => value > 50).length / Math.max(1, intervals.length)) * 100).toFixed(2)),
      sliderUpdates: changed(positions),
      mixUpdates: changed(mixes),
      playbackTicks: Number(after.playbackTicks ?? 0) - Number(before.playbackTicks ?? 0),
      pairCommits: Number(after.pairCommits ?? 0) - Number(before.pairCommits ?? 0),
      explorerRenders: Number(after.explorerRenderCount ?? 0) - Number(before.explorerRenderCount ?? 0),
      uiPublications: Number(ui?.getAttribute("data-ui-revision") ?? 0) - beforeUiRevision,
      labelCandidates: Number(labelLayer?.getAttribute("data-label-candidate-count") ?? 0),
      acceptedLabels: Number(labelLayer?.getAttribute("data-label-accepted-count") ?? 0),
      activeLabelDomCount: labelLayer?.querySelectorAll(".map-label").length ?? 0,
      labelLayoutRevisions: Number(labelLayer?.getAttribute("data-label-layout-revision") ?? 0) - beforeLabelRevision,
      cachePublications: Number(cache.publications ?? 0) - Number(beforeCache.publications ?? 0),
      residentFrames: Number(explorer?.getAttribute("data-resident-frame-count") ?? 0),
      residentRasters: Number(explorer?.getAttribute("data-raster-resident-count") ?? 0),
      peakFrameIndicesPerSurface: Number(explorer?.getAttribute("data-raster-peak-frame-window") ?? 0),
      temporalShaderSamples: Number(explorer?.getAttribute("data-temporal-shader-samples") ?? 0),
      detailPlaybackMode: explorer?.getAttribute("data-detail-playback-mode") ?? "",
      readyDetailTiles: Number(document.querySelector("canvas")?.getAttribute("data-ready-detail-tiles") ?? 0),
      detailMaxOpacity: Number(document.querySelector("canvas")?.getAttribute("data-detail-max-opacity") ?? 0),
      optionalDetailDecodesDuringPlayback: Number(cache.optionalDecodesDuringPlayback ?? 0) - Number(beforeCache.optionalDecodesDuringPlayback ?? 0),
      optionalDetailDisposalsDuringPlayback: Number(cache.optionalDisposalsDuringPlayback ?? 0) - Number(beforeCache.optionalDisposalsDuringPlayback ?? 0),
      canvasDpr: Number((((document.querySelector("canvas")?.width ?? 0) / (document.querySelector("canvas")?.clientWidth || 1))).toFixed(2)),
      cache,
      airMonitorCount: Number(air.monitorCount ?? 0),
      airClusterCount: Number(air.clusterCount ?? 0),
      airClusterPasses: Number(air.passes ?? 0) - Number(beforeAir.passes ?? 0),
      airClusterDurationMs: Number((Number(air.durationMs ?? 0) - Number(beforeAir.durationMs ?? 0)).toFixed(2)),
      airClusterMaxDurationMs: Number(Number(air.maxDurationMs ?? 0).toFixed(2)),
      incidentCount: Number(incidents.incidentCount ?? 0),
      incidentClusterCount: Number(incidents.clusterCount ?? 0),
      incidentClusterPasses: Number(incidents.passes ?? 0) - Number(beforeIncidents.passes ?? 0),
      incidentClusterDurationMs: Number((Number(incidents.durationMs ?? 0) - Number(beforeIncidents.durationMs ?? 0)).toFixed(2)),
      incidentClusterMaxDurationMs: Number(Number(incidents.maxDurationMs ?? 0).toFixed(2)),
      longTaskCount: longTasks.length,
      longestMainThreadTaskMs: Number(Math.max(0, ...longTasks.map((entry) => entry.duration)).toFixed(2)),
    };
  }, { duration: durationMs, isolateTicks: label === "forecast-render-isolation" });
  if (interaction) await interaction(page, durationMs);
  const result = await pending;
  return { label, ...result };
}

async function switchView(page, view) {
  await page.getByTestId(`view-${view}`).click();
  await page.getByTestId("loading-state").waitFor({ state: "hidden", timeout: 20_000 });
}

async function setReportedWildfires(page, checked) {
  const toggle = page.getByTestId("incidents-toggle");
  if (!await toggle.isVisible()) {
    const desktopTrigger = page.getByRole("button", { name: "Map options", exact: true });
    if (await desktopTrigger.isVisible()) await desktopTrigger.click();
    else {
      await page.getByRole("button", { name: "More options", exact: true }).click();
      await page.getByRole("dialog", { name: "More" }).getByRole("button", { name: /Map options/ }).click();
    }
  }
  await toggle.setChecked(checked);
  if (checked) await page.waitForFunction(() => Number(globalThis.__TITANSKIES_PERF__?.incidents?.incidentCount ?? 0) > 0, undefined, { timeout: 20_000 });
  await page.getByRole("dialog", { name: "Map options" }).getByRole("button", { name: "Close map options" }).click();
}

function assertStructuralGates(result) {
  if (result.label.startsWith("incident-drag")) {
    if (result.incidentCount < 1 || result.incidentClusterCount < 1 || result.incidentClusterCount >= result.incidentCount) throw new Error(`${result.label} did not declutter ${result.incidentCount} incidents into ${result.incidentClusterCount} visible clusters`);
    if (result.incidentClusterPasses < 1 || result.incidentClusterDurationMs <= 0) throw new Error(`${result.label} did not publish incident clustering diagnostics`);
    return;
  }
  if (result.label === "forecast-render-isolation") {
    if (result.playbackTicks < 1 || result.sliderUpdates < 1) throw new Error("render-isolation window did not animate");
    if (result.pairCommits !== 0) throw new Error("tick-isolation sample crossed a forecast pair boundary");
    // Animation ticks remain external; only raster publications may render.
    const structuralRenders = result.cachePublications + result.pairCommits * 2;
    if (result.explorerRenders > structuralRenders) throw new Error(`ordinary playback ticks rerendered SmokeExplorer ${result.explorerRenders - structuralRenders} excess times`);
    if (result.labelLayoutRevisions !== 0) throw new Error(`stationary playback triggered ${result.labelLayoutRevisions} label layout revisions`);
    return;
  }
  if (!result.label.startsWith("forecast-playing")) return;
  const memoryCap = result.label.endsWith("mobile") ? 128 * 1024 * 1024 : 256 * 1024 * 1024;
  if (result.residentFrames > 3 || result.peakFrameIndicesPerSurface > 3) throw new Error(`${result.label} exceeded the three-frame residency window`);
  if (result.canvasDpr > 1.25) throw new Error(`${result.label} canvas DPR ${result.canvasDpr} exceeded 1.25`);
  if (result.temporalShaderSamples !== 2) throw new Error(`${result.label} used ${result.temporalShaderSamples} temporal shader samples; expected 2`);
  if (result.detailPlaybackMode !== "base-only" || result.detailMaxOpacity !== 0) throw new Error(`${result.label} rendered optional regional detail during playback`);
  if (result.optionalDetailDecodesDuringPlayback !== 0) throw new Error(`${result.label} decoded ${result.optionalDetailDecodesDuringPlayback} optional regional rasters during playback`);
  if (result.playbackTicks < 1 || result.sliderUpdates < 1 || result.mixUpdates < 1) throw new Error(`${result.label} did not produce distinct animation updates`);
  if (result.explorerRenders > result.cachePublications + result.pairCommits * 4 + 4) throw new Error(`${result.label} produced ${result.explorerRenders} structural renders for ${result.cachePublications} cache publications and ${result.pairCommits} pair commits`);
  if (Number(result.cache.retainedCpuBytes ?? 0) > Number(result.cache.peakRetainedCpuBytes ?? 0)
    || Number(result.cache.estimatedGpuBytes ?? 0) > Number(result.cache.peakEstimatedGpuBytes ?? 0)) throw new Error(`${result.label} cache memory counters are inconsistent`);
  if (Number(result.cache.peakRetainedBytes ?? 0) > memoryCap) throw new Error(`${result.label} exceeded its decoded CPU plus estimated GPU memory cap`);
}

function assertHardwareGates(result) {
  const interaction = result.label.startsWith("forecast-playing") || result.label.startsWith("air-drag") || result.label.startsWith("incident-drag");
  if (!interaction) return;
  const seconds = result.durationMs / 1_000;
  const playback = result.label.startsWith("forecast-playing");
  const mobile = result.label.includes("mobile");
  const minimumRate = mobile ? 25 : 50;
  const medianBudget = mobile ? 38 : 20;
  const p95Budget = mobile ? 80 : 50;
  // Sampling can begin or end between animation frames. Allow exactly one
  // boundary tick while still requiring the sustained target update rate.
  if (playback && (result.playbackTicks + 1) / seconds < minimumRate) throw new Error(`${result.label} produced ${(result.playbackTicks / seconds).toFixed(1)} updates/sec; expected ${minimumRate}`);
  if (playback && (result.uiPublications / seconds < 3 || result.uiPublications / seconds > 5)) throw new Error(`${result.label} published forecast text ${(result.uiPublications / seconds).toFixed(1)} times/sec; expected approximately 4`);
  if (result.medianFrameMs > medianBudget) throw new Error(`${result.label} median frame ${result.medianFrameMs}ms exceeded ${medianBudget}ms`);
  if (result.p95FrameMs > p95Budget) throw new Error(`${result.label} p95 frame ${result.p95FrameMs}ms exceeded ${p95Budget}ms`);
  if (result.framesOver50Percent >= 5) throw new Error(`${result.label} had ${result.framesOver50Percent}% frames above 50ms`);
  if (result.longestMainThreadTaskMs > 50) throw new Error(`${result.label} main-thread task ${result.longestMainThreadTaskMs}ms exceeded 50ms`);
}

let browser;
try {
  const publication = liveData ? await resolveLivePublication() : null;
  await ready();
  browser = await chromium.launch({ headless: !requireHardware });
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1.5 });
  await installDiagnostics(page);
  await installLivePointer(page, publication);
  await page.goto(baseURL);
  await page.getByTestId("source-badge").waitFor();
  await waitForRasterDiagnostics(page, "forecast page");
  await page.waitForFunction(() => document.querySelectorAll(".map-label").length > 0);
  await page.getByTestId("play-toggle").waitFor({ state: "visible" });
  await page.waitForFunction(() => Number(document.querySelector(".explorer")?.getAttribute("data-raster-resident-count")) >= 2);
  const renderer = await page.evaluate(() => {
    const gl = document.createElement("canvas").getContext("webgl2");
    if (!gl) return "unavailable";
    const debug = gl.getExtension("WEBGL_debug_renderer_info");
    return debug ? String(gl.getParameter(debug.UNMASKED_RENDERER_WEBGL)) : String(gl.getParameter(gl.RENDERER));
  });
  const softwareRenderer = /SwiftShader|llvmpipe|Microsoft Basic Render/i.test(renderer);
  if (requireHardware && softwareRenderer) throw new Error(`hardware benchmark rejected software renderer: ${renderer}`);

  const results = [];
  if (liveData) {
    await setReportedWildfires(page, true);
    for (let index = 1; index <= 3; index += 1) results.push(await sample(page, `incident-drag-desktop-${index}`, 2500, dragGlobe));
    await page.reload();
    await page.getByTestId("source-badge").waitFor();
    await waitForRasterDiagnostics(page, "reloaded forecast page");
    await page.getByTestId("play-toggle").waitFor({ state: "visible" });
    await page.waitForFunction(() => Number(document.querySelector(".explorer")?.getAttribute("data-raster-resident-count")) >= 2);
  }
  results.push(await sample(page, "forecast-paused"));
  await zoomToRegionalDetail(page);
  await page.waitForFunction(() => Number(document.querySelector("canvas")?.getAttribute("data-ready-detail-tiles") ?? 0) > 0
    && Number(document.querySelector("canvas")?.getAttribute("data-detail-max-opacity") ?? 0) === 1, undefined, { timeout: 20_000 });
  await page.getByTestId("play-toggle").click();
  await page.waitForFunction(() => document.querySelector(".explorer")?.getAttribute("data-playback-phase") === "playing");
  const speed = page.getByTestId("playback-speed");
  await speed.click();
  await speed.click();
  await waitForSettledPlayback(page);
  results.push(await sample(page, "forecast-render-isolation", 500));
  await speed.click();
  await page.waitForTimeout(150);
  results.push(await sample(page, "forecast-playing-desktop"));
  await speed.click();
  await page.waitForTimeout(150);
  results.push(await sample(page, "forecast-playing-3x-desktop"));
  await switchView(page, "air");
  results.push(await sample(page, "air-quality"));
  if (liveData) {
    for (let index = 1; index <= 3; index += 1) results.push(await sample(page, `air-drag-desktop-${index}`, 2500, dragGlobe));
  }

  const mobilePage = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  await installDiagnostics(mobilePage);
  await installLivePointer(mobilePage, publication);
  await mobilePage.goto(baseURL);
  await mobilePage.getByTestId("source-badge").waitFor();
  await mobilePage.getByTestId("play-toggle").waitFor({ state: "visible" });
  await mobilePage.waitForFunction(() => Number(document.querySelector(".explorer")?.getAttribute("data-raster-resident-count")) >= 2);
  if (liveData) {
    await setReportedWildfires(mobilePage, true);
    for (let index = 1; index <= 3; index += 1) results.push(await sample(mobilePage, `incident-drag-mobile-${index}`, 2500, dragGlobe));
    await mobilePage.reload();
    await mobilePage.getByTestId("source-badge").waitFor();
    await mobilePage.getByTestId("play-toggle").waitFor({ state: "visible" });
    await mobilePage.waitForFunction(() => Number(document.querySelector(".explorer")?.getAttribute("data-raster-resident-count")) >= 2);
  }
  await zoomToRegionalDetail(mobilePage);
  await mobilePage.waitForFunction(() => Number(document.querySelector("canvas")?.getAttribute("data-ready-detail-tiles") ?? 0) > 0
    && Number(document.querySelector("canvas")?.getAttribute("data-detail-max-opacity") ?? 0) === 1, undefined, { timeout: 20_000 });
  await mobilePage.getByTestId("play-toggle").click();
  results.push(await sample(mobilePage, "forecast-playing-mobile"));
  if (liveData) {
    await switchView(mobilePage, "air");
    for (let index = 1; index <= 3; index += 1) results.push(await sample(mobilePage, `air-drag-mobile-${index}`, 2500, dragGlobe));
  }
  await mobilePage.close();

  const fallbackPage = await browser.newPage({ viewport: { width: 768, height: 640 } });
  await installDiagnostics(fallbackPage);
  await installLivePointer(fallbackPage, publication);
  await fallbackPage.goto(`${baseURL}/?rasterDecoder=fallback`);
  await fallbackPage.getByTestId("play-toggle").waitFor({ state: "visible" });
  await waitForRasterDiagnostics(fallbackPage, "fallback decoder page");
  try {
    await fallbackPage.waitForFunction(() => Number(globalThis.__TITANSKIES_PERF__?.rasterCache?.fallbackDecodes ?? 0) >= 2
      && Number(globalThis.__TITANSKIES_PERF__?.rasterCache?.pending ?? -1) === 0, undefined, { timeout: 30_000 });
  } catch {
    const diagnostics = await fallbackPage.evaluate(() => globalThis.__TITANSKIES_PERF__?.rasterCache ?? null);
    throw new Error(`fallback decoder did not settle: ${JSON.stringify(diagnostics)}`);
  }
  const fallback = await fallbackPage.evaluate(() => globalThis.__TITANSKIES_PERF__?.rasterCache ?? {});
  await fallbackPage.close();

  const worker = results[0].cache;
  if (Number(worker.workerDecodes ?? 0) < 2 || Number(worker.fallbackDecodes ?? 0) !== 0) throw new Error(`current Chromium did not use the raster worker: ${JSON.stringify(worker)}`);
  if (Number(fallback.fallbackDecodes ?? 0) < 2 || Number(fallback.workerDecodes ?? 0) !== 0) throw new Error(`forced raster fallback did not decode through the fallback path: ${JSON.stringify(fallback)}`);
  console.log(JSON.stringify({ viewport: "1280x720@1.5", renderer, rendererClass: softwareRenderer ? "software" : "hardware", requireHardware, liveData, publication: publication?.summary ?? null, results, forcedFallback: fallback }, null, 2));
  results.forEach(assertStructuralGates);
  if (requireHardware) results.forEach(assertHardwareGates);
} finally {
  await browser?.close();
  server.kill("SIGTERM");
}
