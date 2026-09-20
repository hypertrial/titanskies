import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createHash } from "node:crypto";
import { cp, mkdir, readFile, unlink, writeFile } from "node:fs/promises";
import path from "node:path";

import { installTempDataDir } from "../../../tests/support/tempDataDir";
import { GET } from "./route";

const createDataDir = installTempDataDir();
const previousWatchSeconds = process.env.CONTEXT_WATCH_SECONDS;

async function demoPublication(): Promise<string> {
  const directory = await createDataDir("titanskies-health-");
  const pointer = JSON.parse(await readFile(path.join(process.cwd(), "public/demo/context/latest.json"), "utf8"));
  await mkdir(path.join(directory, "context/manifests"), { recursive: true });
  await cp(path.join(process.cwd(), "public/demo/context/assets"), path.join(directory, "context/assets"), { recursive: true });
  const sourceManifest = await readFile(path.join(process.cwd(), "public/demo", pointer.manifestPath), "utf8");
  const encodedManifest = sourceManifest.replaceAll("/demo/context/assets/", "/data/context/assets/");
  const manifestHash = createHash("sha256").update(encodedManifest).digest("hex").slice(0, 20);
  pointer.manifestPath = `context/manifests/${manifestHash}.json`;
  pointer.manifestUrl = `/data/${pointer.manifestPath}`;
  await writeFile(path.join(directory, pointer.manifestPath), encodedManifest);
  await writeFile(path.join(directory, "context/latest.json"), JSON.stringify(pointer));
  await cp(path.join(process.cwd(), "public/demo/context/status.json"), path.join(directory, "context/status.json"));
  return directory;
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  if (previousWatchSeconds === undefined) delete process.env.CONTEXT_WATCH_SECONDS;
  else process.env.CONTEXT_WATCH_SECONDS = previousWatchSeconds;
});

it("uses the configured watcher interval when deciding publication expiry", async () => {
  const directory = await demoPublication();
  process.env.CONTEXT_WATCH_SECONDS = "3600";
  const statusPath = path.join(directory, "context/status.json");
  const status = JSON.parse(await readFile(statusPath, "utf8"));
  status.lastAttemptAt = "2024-07-15T19:25:00Z";
  status.lastCompleteForecastAt = "2024-07-15T19:25:00Z";
  await writeFile(statusPath, JSON.stringify(status));
  vi.setSystemTime(new Date("2024-07-15T20:10:00Z"));

  const response = await GET(new Request("http://localhost/api/context-health"));
  const body = await response.json();
  expect(response.status).toBe(200);
  expect(body.issues).not.toContain("stale-heartbeat");
  expect(body.issues).not.toContain("stale-forecast");
}, 30_000);

it("reports initializing with 503 before the first publication", async () => {
  await createDataDir("titanskies-empty-");
  const response = await GET(new Request("http://localhost/api/context-health"));
  const body = await response.json();
  expect(response.status).toBe(503);
  expect(body).toMatchObject({ schemaVersion: 1, ok: false, status: "unhealthy" });
  expect(body.issues).toContain("initializing");
});

it("rejects a pointer whose URL disagrees with its local manifest path", async () => {
  const directory = await demoPublication();
  const pointerPath = path.join(directory, "context/latest.json");
  const pointer = JSON.parse(await readFile(pointerPath, "utf8"));
  pointer.manifestUrl = "https://example.invalid/wrong.json";
  await writeFile(pointerPath, JSON.stringify(pointer));
  const response = await GET(new Request("http://localhost/api/context-health"));
  expect(response.status).toBe(503);
  expect((await response.json()).issues).toContain("invalid-pointer");
}, 30_000);

it("reports a publication unhealthy when the lowercase AQHI asset is missing", async () => {
  const directory = await demoPublication();
  const pointer = JSON.parse(await readFile(path.join(directory, "context/latest.json"), "utf8"));
  const manifest = JSON.parse(await readFile(path.join(directory, pointer.manifestPath), "utf8"));
  const assetPath = manifest.air.monitorSets.aqhi.url.replace("/data/", "");
  await unlink(path.join(directory, assetPath));
  const response = await GET(new Request("http://localhost/api/context-health"));
  expect(response.status).toBe(503);
  expect((await response.json()).issues).toContain("invalid-assets");
}, 30_000);

it("returns all source states, coverage, and monitoring query validation without private errors", async () => {
  const directory = await demoPublication();
  const initialPointer = JSON.parse(await readFile(path.join(directory, "context/latest.json"), "utf8"));
  const initialManifestPath = path.join(directory, initialPointer.manifestPath);
  const initialManifest = JSON.parse(await readFile(initialManifestPath, "utf8"));
  initialManifest.sources.hms = { ...initialManifest.sources.airnow };
  initialManifest.sources.firms = { ...initialManifest.sources.wfigs };
  const deployed = JSON.stringify(initialManifest);
  const deployedHash = createHash("sha256").update(deployed).digest("hex").slice(0, 20);
  initialPointer.manifestPath = `context/manifests/${deployedHash}.json`;
  initialPointer.manifestUrl = `/data/${initialPointer.manifestPath}`;
  await writeFile(path.join(directory, initialPointer.manifestPath), deployed);
  await writeFile(path.join(directory, "context/latest.json"), JSON.stringify(initialPointer));
  vi.setSystemTime(new Date("2024-07-15T20:10:00Z"));
  const response = await GET(new Request("http://localhost/api/context-health?expectedVersion=8&expectedSource=airnow&expectedSource=hrrr"));
  const body = await response.json();
  expect(response.status).toBe(200);
  expect(body).toMatchObject({ schemaVersion: 1, ok: true, status: "healthy", contextVersion: 8, forecastFrameCount: 37 });
  expect(body.remainingCoverageHours).toBeGreaterThan(35);
  expect(Object.keys(body.sources)).toHaveLength(8);
  expect(JSON.stringify(body)).not.toContain("error");

  const manifestPath = JSON.parse(await readFile(path.join(directory, "context/latest.json"), "utf8")).manifestPath;
  const manifestFile = path.join(directory, manifestPath);
  const manifest = JSON.parse(await readFile(manifestFile, "utf8"));
  manifest.sources.airnow.status = "unavailable";
  manifest.sources.airnow.error = "AIRNOW_API_KEY=should-never-leak";
  const encoded = JSON.stringify(manifest);
  const digest = createHash("sha256").update(encoded).digest("hex").slice(0, 20);
  const changedPath = `context/manifests/${digest}.json`;
  await writeFile(path.join(directory, changedPath), encoded);
  const pointerFile = path.join(directory, "context/latest.json");
  const pointer = JSON.parse(await readFile(pointerFile, "utf8"));
  pointer.manifestPath = changedPath;
  pointer.manifestUrl = `/data/${changedPath}`;
  await writeFile(pointerFile, JSON.stringify(pointer));
  const degraded = await GET(new Request("http://localhost/api/context-health?expectedSource=airnow"));
  const degradedBody = await degraded.json();
  expect(degraded.status).toBe(200);
  expect(degradedBody.status).toBe("degraded");
  expect(degradedBody.issues).toContain("source-airnow-unavailable");
  expect(JSON.stringify(degradedBody)).not.toContain("should-never-leak");

  const invalid = await GET(new Request("http://localhost/api/context-health?expectedSource=unknown"));
  expect(invalid.status).toBe(503);
  expect((await invalid.json()).issues).toContain("invalid-expected-source");
}, 30_000);
