import { rm } from "node:fs/promises";
import { resolve } from "node:path";

export default async function globalTeardown() {
  const relative = process.env.TITANSKIES_E2E_DIST_DIR;
  if (relative !== ".next-e2e") return;
  const target = resolve(process.cwd(), relative);
  const root = resolve(process.cwd()) + "/";
  if (!target.startsWith(root)) return;
  if (process.env.CI) await rm(target, { recursive: true, force: true });
}
