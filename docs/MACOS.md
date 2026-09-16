# TitanSkies on macOS

TitanSkies ships an Apple Silicon application that keeps Node, Python, Next.js, and ingest inside `TitanSkies.app`. Linux systemd and Docker paths are unchanged.

## Requirements

- macOS 13 or later on Apple Silicon
- No host Node, Python, uv, Docker, or administrator password
- Per-user install at `~/Applications/TitanSkies.app`

## Production trust

The public channel is Developer ID signed, uses Hardened Runtime, and is notarized and stapled by Apple. Gatekeeper must identify the publisher without an unidentified-developer bypass. Ad-hoc signed builds remain available only for local development and show an explicit unsigned warning.

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

Updates are user-initiated. **Check for update** downloads the signed `macos-arm64` manifest and verifies Ed25519, architecture, minimum OS, channel, URLs, digest, and version. The installed version reports **TitanSkies is up to date** rather than an error. **Install verified update** streams the DMG to disk, hashes it incrementally, mounts it read-only, and validates the app's Developer ID, Team ID, Hardened Runtime, nested signatures, and Gatekeeper acceptance before launching its updater. The updater unregisters the production agents, parks the current app in the rollback slot, moves the staged app into `~/Applications`, re-registers the agents, verifies the exact launchd listener identity, and relaunches the new GUI. If startup fails, it restores and validates the previous app, re-registers its services, relaunches it, and reports the rollback on next launch. Finder drag-replacement cannot promise automatic rollback.

## Uninstall

Uninstall unregisters LaunchAgents, confirms they are inactive, and moves the app to Trash. Default uninstall keeps configuration, publications, and cache. **Uninstall and delete data** removes only TitanSkies-owned default folders and never follows symlinks or custom paths.

## Background services

Production uses `SMAppService` agents embedded in the signed application. If macOS requires approval, TitanSkies opens Login Items settings and shows an explicit approval-required state; it does not continue into an opaque launch failure. Local unsigned builds use classic per-user LaunchAgents. Child-process output from both channels is appended to the private TitanSkies web and ingest logs.

## Building on Darwin arm64

```sh
./scripts/build-macos-runtime
./scripts/build-macos-app
./scripts/build-macos-dmg
./scripts/verify-macos
```

These commands make an unsigned local artifact by default. A production build requires an explicit channel and positive build number, plus a full Xcode selected with `xcode-select`. See [the production release runbook](MACOS_RELEASE.md); do not create a production DMG before the app has been signed, notarized, and stapled.

Pinned Node 22 and CPython 3.12 URLs and SHA-256 values live in [`packaging/macos/runtime-lock.json`](../packaging/macos/runtime-lock.json). Bundled Node and Python live at `Contents/Resources/Host` so codesign does not treat the CPython stdlib as a nested bundle. The Swift package is [`macos/TitanSkies`](../macos/TitanSkies). Open `macos/TitanSkies/Package.swift` in Xcode for GUI work.
