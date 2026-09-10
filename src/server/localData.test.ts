import { afterEach, describe, expect, it } from "vitest";
import { createHash } from "node:crypto";
import { mkdtemp, mkdir, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

import { readLocalDataFile } from "./localData";

const previousRoot = process.env.TITANSKIES_DATA_DIR;

async function root(): Promise<string> {
  const directory = await mkdtemp(path.join(tmpdir(), "titanskies-data-"));
  process.env.TITANSKIES_DATA_DIR = directory;
  return directory;
}

afterEach(() => {
  if (previousRoot === undefined) delete process.env.TITANSKIES_DATA_DIR;
  else process.env.TITANSKIES_DATA_DIR = previousRoot;
});

describe("local publication reader", () => {
  it("serves only allowlisted mutable and hashed publication paths", async () => {
    const directory = await root();
    const png = Buffer.from("89504e470d0a1a0a", "hex");
    const digest = createHash("sha256").update(png).digest("hex").slice(0, 20);
    await mkdir(path.join(directory, `context/assets/${digest}`), { recursive: true });
    await writeFile(path.join(directory, "context/latest.json"), "{}", "utf8");
    await writeFile(path.join(directory, `context/assets/${digest}/smoke.png`), png);

    await expect(readLocalDataFile(["context", "latest.json"])).resolves.toMatchObject({
      contentType: "application/json",
      immutable: false,
    });
    await expect(readLocalDataFile(["context", "assets", digest, "smoke.png"])).resolves.toMatchObject({
      contentType: "image/png",
      immutable: true,
    });
    await expect(readLocalDataFile(["context", "assets", "not-hashed", "smoke.png"])).resolves.toBeNull();
    await expect(readLocalDataFile(["context", "latest.xml"])).resolves.toBeNull();
  });

  it("rejects traversal, symlinks, directories, and oversized files", async () => {
    const directory = await root();
    await mkdir(path.join(directory, "context/manifests"), { recursive: true });
    const outside = path.join(await mkdtemp(path.join(tmpdir(), "titanskies-outside-")), "secret.json");
    await writeFile(outside, "{}", "utf8");
    await symlink(outside, path.join(directory, "context/manifests/aaaaaaaaaaaaaaaaaaaa.json"));
    await writeFile(path.join(directory, "context/manifests/bbbbbbbbbbbbbbbbbbbb.json"), Buffer.alloc(2 * 1024 * 1024 + 1));

    await expect(readLocalDataFile(["..", "secret.json"])).resolves.toBeNull();
    await expect(readLocalDataFile(["context", "manifests", "aaaaaaaaaaaaaaaaaaaa.json"])).resolves.toBeNull();
    await expect(readLocalDataFile(["context", "manifests", "bbbbbbbbbbbbbbbbbbbb.json"])).resolves.toBeNull();
    await expect(readLocalDataFile(["context", "manifests"])).resolves.toBeNull();
  });

  it("rejects invalid JSON, invalid PNG signatures, and mismatched content hashes", async () => {
    const directory = await root();
    await mkdir(path.join(directory, "context/assets/aaaaaaaaaaaaaaaaaaaa"), { recursive: true });
    await mkdir(path.join(directory, "context/assets/bbbbbbbbbbbbbbbbbbbb"), { recursive: true });
    await writeFile(path.join(directory, "context/status.json"), "not json", "utf8");
    await writeFile(path.join(directory, "context/assets/aaaaaaaaaaaaaaaaaaaa/smoke.png"), Buffer.from("not png"));
    await writeFile(path.join(directory, "context/assets/bbbbbbbbbbbbbbbbbbbb/data.json"), "{}", "utf8");

    await expect(readLocalDataFile(["context", "status.json"])).resolves.toBeNull();
    await expect(readLocalDataFile(["context", "assets", "aaaaaaaaaaaaaaaaaaaa", "smoke.png"])).resolves.toBeNull();
    await expect(readLocalDataFile(["context", "assets", "bbbbbbbbbbbbbbbbbbbb", "data.json"])).resolves.toBeNull();
  });
});
