# TitanSkies on macOS

TitanSkies ships an Apple Silicon application that keeps Node, Python, Next.js, and ingest inside `TitanSkies.app`. Linux systemd and Docker paths are unchanged.

## Requirements

- macOS 13 or later on Apple Silicon
- No host Node, Python, uv, Docker, or administrator password
- Per-user install at `~/Applications/TitanSkies.app`

## Unsigned beta

The first channel is an ad-hoc signed DMG. Gatekeeper will warn that the developer is unidentified. That is expected. Verify the published SHA-256 before opening. Do not treat this channel as Developer ID or notarized trust.

## Install

1. Open the DMG and launch TitanSkies.
2. Choose **Install for Me**. The app copies itself to `~/Applications/TitanSkies.app` and relaunches. LaunchAgents are never registered from `/Volumes` or App Translocation.
3. The web service binds `127.0.0.1:8080`. Ingest runs immediately, then at minutes 0/15/30/45.

Closing the window leaves both jobs running. Use Settings for Start, Stop, Restart, Repair, Logs, Update, and Uninstall.

## Settings and data

Configuration is `~/Library/Application Support/TitanSkies/config/env` mode `0600`. Native launchers parse `KEY=value` and never source the file through a shell. They start Node and Python with `PATH=/usr/bin:/bin` and do not inherit the host `PATH`, `PYTHONPATH`, or `NODE_OPTIONS`. `AIRNOW_API_KEY` is passed only to ingest.

| Path | Mode | Contents |
| --- | --- | --- |
| `~/Library/Application Support/TitanSkies/config/env` | `0600` | Settings |
| `~/Library/Application Support/TitanSkies/data` | `0700` | Publications |
| `~/Library/Caches/TitanSkies` | `0700` | Reusable cache |
| `~/Library/Logs/TitanSkies` | `0700` | Web and ingest logs |
| `~/Library/Application Support/TitanSkies/rollback` | `0700` | Previous sealed `.app` (never executed in place) |

## Updates

Updates are user-initiated. **Check for update** downloads the signed `macos-arm64` manifest and verifies Ed25519, architecture, channel, and version. **Install verified update** then downloads the DMG, checks its SHA-256, mounts it read-only, stages the new `.app`, and launches the in-bundle updater. The updater boots out both jobs, parks the current app in the rollback slot, moves the staged app into `~/Applications`, and rolls back on `/api/healthz` failure. Finder drag-replacement cannot promise automatic rollback.

## Uninstall

Uninstall unregisters LaunchAgents, confirms they are inactive, and moves the app to Trash. Default uninstall keeps configuration, publications, and cache. **Uninstall and delete data** removes only TitanSkies-owned default folders and never follows symlinks or custom paths.

## Production signing

Developer ID, Hardened Runtime, notarization, and stapling are a later gate. `scripts/sign-macos-release` fails closed unless `MACOS_DEVELOPER_ID_IDENTITY` and `MACOS_NOTARY_PROFILE` are set. Unsigned beta uses classic LaunchAgents and does not auto-register production login items. Production sets `runtime-lock.json` `channel` to `production`, migrates away from beta labels, and registers `SMAppService` agents.

## Building on Darwin arm64

```sh
./scripts/build-macos-runtime
./scripts/build-macos-app
./scripts/build-macos-dmg
./scripts/verify-macos
```

Pinned Node 22 and CPython 3.12 URLs and SHA-256 values live in [`packaging/macos/runtime-lock.json`](../packaging/macos/runtime-lock.json). Bundled Node and Python live at `Contents/Resources/Host` so codesign does not treat the CPython stdlib as a nested bundle. The Swift package is [`macos/TitanSkies`](../macos/TitanSkies). Open `macos/TitanSkies/Package.swift` in Xcode for GUI work.
