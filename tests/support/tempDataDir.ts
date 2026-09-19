import { afterEach } from "vitest";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

export function installTempDataDir(): (prefix?: string) => Promise<string> {
  const previous = process.env.TITANSKIES_DATA_DIR;
  afterEach(() => {
    if (previous === undefined) delete process.env.TITANSKIES_DATA_DIR;
    else process.env.TITANSKIES_DATA_DIR = previous;
  });
  return async (prefix = "titanskies-data-") => {
    const directory = await mkdtemp(path.join(tmpdir(), prefix));
    process.env.TITANSKIES_DATA_DIR = directory;
    return directory;
  };
}
