import { afterEach, expect, it } from "vitest";
import { createHash } from "node:crypto";
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { GET, HEAD } from "./[...path]/route";

const previousRoot = process.env.TITANSKIES_DATA_DIR;

afterEach(() => {
  if (previousRoot === undefined) delete process.env.TITANSKIES_DATA_DIR;
  else process.env.TITANSKIES_DATA_DIR = previousRoot;
});

it("streams hashed assets with immutable caching and supports HEAD", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "titanskies-route-"));
  process.env.TITANSKIES_DATA_DIR = directory;
  const png = Buffer.from("89504e470d0a1a0a", "hex");
  const digest = createHash("sha256").update(png).digest("hex").slice(0, 20);
  await mkdir(path.join(directory, `context/assets/${digest}`), { recursive: true });
  await writeFile(path.join(directory, `context/assets/${digest}/smoke.png`), png);
  const context = { params: Promise.resolve({ path: ["context", "assets", digest, "smoke.png"] }) };

  const get = await GET(new Request("http://localhost/data/test"), context);
  expect(get.status).toBe(200);
  expect(get.headers.get("cache-control")).toContain("immutable");
  expect(get.headers.get("content-type")).toBe("image/png");
  expect(new Uint8Array(await get.arrayBuffer())).toEqual(new Uint8Array(png));

  const head = await HEAD(new Request("http://localhost/data/test"), context);
  expect(head.status).toBe(200);
  expect(head.headers.get("content-length")).toBe("8");
  expect(await head.text()).toBe("");
});

it("returns a no-store 404 for invalid paths", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "titanskies-route-"));
  process.env.TITANSKIES_DATA_DIR = directory;
  const response = await GET(new Request("http://localhost/data/test"), {
    params: Promise.resolve({ path: ["..", "secret.json"] }),
  });
  expect(response.status).toBe(404);
  expect(response.headers.get("cache-control")).toBe("no-store");
});
