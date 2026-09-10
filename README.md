# TitanSkies

TitanSkies is a self-hosted North American wildfire-smoke forecast, air-quality observation, and reported-wildfire explorer. The application is MIT-licensed, has no analytics, and keeps its publications and caches on your machine.

> TitanSkies uses preliminary observations, model forecasts, and agency-reported incidents that may be delayed, incomplete, inaccurate, retained from an earlier update, or unavailable. It does not identify smoke origin and is not an emergency alert or medical service. Check source timestamps, official alerts, and local health guidance before acting.

The product includes the current 37-hour ECCC FireWork/NOAA HRRR-Smoke outlook, AirNow, B.C. ENV, INECC/SINAICA, ECCC AQHI, WFIGS, CWFIS, search, Worst 5, responsive and accessible interaction, installable PWA support, source freshness, last-good fallback, and deterministic offline demo mode.

## Data and licensing

The software, dependencies, and bundled assets use open licenses. Live data is fetched from public government services under each provider's own terms; it is not relicensed under MIT or committed to this repository. AirNow requires attribution and preliminary-data treatment. See [`DATA_SOURCES.md`](DATA_SOURCES.md), [`shared/data-sources.json`](shared/data-sources.json), [`shared/bundled-assets.json`](shared/bundled-assets.json), and [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

Every enabled source is attempted on every ingest. `AIRNOW_API_KEY` is optional: without it, AirNow reports `unavailable` while the other seven sources and application continue.

## Quick start with Docker Compose

Requirements: Docker Engine with Compose v2, at least 1 GiB RAM, and at least 1 GiB persistent free space. Source builds need extra temporary space.

```sh
cp .env.example .env
docker compose up -d
```

Open <http://127.0.0.1:8080>. The `ingest` service publishes immediately and every 15 minutes. The `web` service mounts the publication volume read-only. Use the signed release image as declared in `compose.yaml`, or build the same image locally with `docker compose build --pull`.

For a network-free demonstration, set `CONTEXT_SOURCE=demo`. Demo files are synthetic and contain no fetched provider records.

## Native user installation (Omarchy/Linux)

Requirements: Node.js 22, Python 3.11+, `uv`, and a working systemd user manager. From a verified release checkout:

```sh
./scripts/install-user
```

The installer uses no sudo. It creates:

- `$XDG_CONFIG_HOME/titanskies/env` with mode `0600`;
- `$XDG_DATA_HOME/titanskies/releases/<version>` and atomic `current` symlink;
- `$XDG_STATE_HOME/titanskies` and `$XDG_CACHE_HOME/titanskies`;
- a systemd user web service and persistent 15-minute ingest timer;
- a desktop entry opening <http://127.0.0.1:8080>.

It runs one immediate ingest and rolls back the `current` symlink when the web self-check fails. Missing-tool errors include Omarchy guidance. For signed manual updates, download the release archive, `SHA256SUMS`, signature, and certificate, then run:

```sh
./scripts/update-user titanskies-VERSION.tar.gz SHA256SUMS SHA256SUMS.sig SHA256SUMS.pem
```

Normal uninstall preserves configuration, publications, and cache:

```sh
./scripts/uninstall-user
```

Use `./scripts/uninstall-user --purge` only when you also want those files removed.

## Source development

```sh
npm ci
uv sync --all-extras
npm run generate:demo
npm run dev:demo
```

Useful modes:

- `npm run dev` serves an existing local publication without starting ingest.
- `npm run dev:local-ingest` starts the live watcher and web app together.
- `npm run ingest:once` performs one live refresh.
- `npm run dev:demo` uses bundled synthetic fixtures and makes no provider requests.

Run `./scripts/verify-fast` during development, `./scripts/verify` before merging, and `npm run verify:release` before a release. Scientific and publication invariants are summarized in [`AGENTS.md`](AGENTS.md).

## Read-only HTTP interfaces

- `GET /api/context-data` returns the current v8 pointer.
- `GET` or `HEAD /data/context/...` streams only allowlisted JSON/PNG files beneath `TITANSKIES_DATA_DIR`. Hashed assets are immutable; traversal, symlinks, oversized files, unsupported MIME types, and directory listing fail closed.
- `GET /api/context-health` returns health schema v1, publication timestamps and state, 37-hour coverage, remaining hours, all eight source states, and redacted issue codes. Healthy/degraded is HTTP 200; initializing, missing, invalid, or expired is HTTP 503. Monitoring may repeat `expectedSource` and provide `expectedVersion=8`.
- `GET /api/healthz` is a process liveness probe.

There is no HTTP ingest or administration endpoint.

## Configuration

See [`.env.example`](.env.example). The stable public settings are:

| Variable | Default | Purpose |
| --- | --- | --- |
| `TITANSKIES_DATA_DIR` | `.local/data` | Immutable manifests/assets and atomic pointer |
| `TITANSKIES_CACHE_DIR` | `.local/cache` | Reusable numeric/model downloads |
| `TITANSKIES_BIND_ADDR` | `127.0.0.1` | Web listener |
| `TITANSKIES_PORT` | `8080` | Web port |
| `CONTEXT_WATCH_SECONDS` | `900` | Watch interval, clamped to 60–3600 seconds |
| `CONTEXT_SOURCE` | `live` | `live` or explicit network-free `demo` |
| `FRAME_RETENTION_HOURS` | `48` | Local retention window |
| `AIRNOW_API_KEY` | empty | Optional AirNow API key |

### LAN exposure

Localhost is the secure default. To opt in, set `TITANSKIES_BIND_ADDR=0.0.0.0`, restrict access with the host firewall, and put TitanSkies behind an operator-managed TLS reverse proxy. Do not expose it directly to the public internet.

## Operations

Back up the state directory to preserve the current and previous publication. Cache is disposable. If health remains `initializing`, inspect the ingest service; if one source fails, the source state and redacted issue code identify it while healthy data remains available. Never replace a missing value with zero.

See [`docs/OPERATIONS.md`](docs/OPERATIONS.md) for service commands, recovery, updates, and troubleshooting; [`SECURITY.md`](SECURITY.md) for reporting; and [`CONTRIBUTING.md`](CONTRIBUTING.md) for contribution rules.

## Omarchy plugin

TitanSkies is being prepared as the application behind a separate `titanskies-omarchy` launcher/health-badge repository. The future plugin will poll `http://127.0.0.1:8080/api/context-health`; it will not install or manage TitanSkies. Omarchy shell plugins are root-manifest QML repositories and intentionally do not run install hooks or sudo commands. See the [official Omarchy plugin manual](https://omarchy.org/manual/shell-plugins/).

## License

Code and project-authored synthetic/static assets are MIT licensed. Upstream data and third-party assets retain their own terms. See [`LICENSE`](LICENSE) and [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
