#!/usr/bin/env node
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { devConfiguration } from "./dev_mode.mjs";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const refreshOnce = process.argv.includes("--refresh-once");

function pythonBin() {
  for (const relative of [".venv/bin/python", ".venv311/bin/python", ".venv/Scripts/python.exe"]) {
    const candidate = path.join(repoRoot, relative);
    if (existsSync(candidate)) return candidate;
  }
  return "python3";
}

function runPythonWatch(args, env = process.env) {
  return spawn(pythonBin(), ["scripts/watch_context.py", ...args], {
    cwd: repoRoot,
    stdio: "inherit",
    env,
  });
}

if (refreshOnce) {
  const child = runPythonWatch(["--once"]);
  child.on("error", (error) => {
    console.error(error);
    process.exit(1);
  });
  child.on("exit", (code, signal) => {
    process.exit(code ?? (signal ? 1 : 0));
  });
} else {
  const nextBin = path.join(repoRoot, "node_modules", ".bin", "next");
  const config = devConfiguration(process.argv.slice(2));
  const dataEnv = config.env;
  const next = spawn(nextBin, ["dev"], {
    cwd: repoRoot,
    stdio: "inherit",
    env: dataEnv,
  });
  const watch = config.watch ? runPythonWatch([], dataEnv) : null;
  next.on("error", (error) => {
    console.error(error);
    process.exit(1);
  });
  watch?.on("error", (error) => {
    console.error(error);
  });
  let shuttingDown = false;
  function shutdown(code = 0) {
    if (shuttingDown) return;
    shuttingDown = true;
    if (watch && !watch.killed) watch.kill("SIGTERM");
    if (!next.killed) next.kill("SIGTERM");
    process.exit(code);
  }
  process.on("SIGINT", () => shutdown(0));
  process.on("SIGTERM", () => shutdown(0));
  next.on("exit", (code) => shutdown(code ?? 0));
  watch?.on("exit", (code) => {
    if (!shuttingDown && code) {
      console.error(`context watch exited ${code}; Next continues`);
    }
  });
}
