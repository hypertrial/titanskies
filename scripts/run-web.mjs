#!/usr/bin/env node
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";

const host = (process.env.TITANSKIES_BIND_ADDR || "127.0.0.1").trim();
const rawPort = process.env.TITANSKIES_PORT || "8080";
const port = Number(rawPort);
if (!/^(?:127\.0\.0\.1|0\.0\.0\.0|::1)$/.test(host)) {
  console.error("TITANSKIES_BIND_ADDR must be 127.0.0.1, ::1, or 0.0.0.0");
  process.exit(2);
}
if (!Number.isInteger(port) || port < 1 || port > 65535) {
  console.error("TITANSKIES_PORT must be an integer from 1 to 65535");
  process.exit(2);
}

const standalone = ".next/standalone/server.js";
const command = existsSync(standalone) ? process.execPath : "node_modules/.bin/next";
const args = existsSync(standalone) ? [standalone] : ["start", "--hostname", host, "--port", String(port)];
const child = spawn(command, args, {
  stdio: "inherit",
  env: { ...process.env, HOSTNAME: host, PORT: String(port) },
});
child.on("error", (error) => { console.error(error); process.exit(1); });
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => child.kill(signal));
child.on("exit", (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
