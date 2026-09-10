import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createHash } from "node:crypto";
import { cp, mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { GET } from "./route";

const previousRoot = process.env.TITANSKIES_DATA_DIR;
const previousWatchSeconds = process.env.CONTEXT_WATCH_SECONDS;

async function demoPublication(): Promise<string> {
  const directory = await mkdtemp(path.join(tmpdir(), "titanskies-health-"));
  const pointer = JSON.parse(await readFile(path.join(process.cwd(), "public/demo/context/latest.json"), "utf8"));
  await mkdir(path.join(directory, "context/manifests"), { recursive: true });
  await cp(path.join(process.cwd(), "public/demo", pointer.manifestPath), path.join(directory, pointer.manifestPath));
  await cp(path.join(process.cwd(), "public/demo/context/latest.json"), path.join(directory, "context/latest.json"));
  await cp(path.join(process.cwd(), "public/demo/context/status.json"), path.join(directory, "context/status.json"));
  process.env.TITANSKIES_DATA_DIR = directory;
  return directory;
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  if (previousRoot === undefined) delete process.env.TITANSKIES_DATA_DIR;
  else process.env.TITANSKIES_DATA_DIR = previousRoot;
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
});

it("reports initializing with 503 before the first publication", async () => {
  process.env.TITANSKIES_DATA_DIR = await mkdtemp(path.join(tmpdir(), "titanskies-empty-"));
  const response = await GET(new Request("http://localhost/api/context-health"));
  const body = await response.json();
  expect(response.status).toBe(503);
  expect(body).toMatchObject({ schemaVersion: 1, ok: false, status: "unhealthy" });
  expect(body.issues).toContain("initializing");
});

it("returns all source states, coverage, and monitoring query validation without private errors", async () => {
  const directory = await demoPublication();
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
});
