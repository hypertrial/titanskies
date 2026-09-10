# Operations

## Health and logs

```sh
curl -fsS http://127.0.0.1:8080/api/healthz
curl -sS http://127.0.0.1:8080/api/context-health?expectedVersion=8
docker compose logs web ingest
```

For a native install, use `systemctl --user status titanskies-web titanskies-ingest.timer` and `journalctl --user -u titanskies-ingest`.

HTTP 503 is expected during first-start initialization. `degraded` with HTTP 200 means a valid publication remains usable but one or more providers failed, became stale, or were retained. AirNow `unavailable` is expected when `AIRNOW_API_KEY` is empty. The health heartbeat expires after twice `CONTEXT_WATCH_SECONDS`, with a 30-minute minimum, so every supported watcher interval has time to complete its next scheduled refresh.

## Backup and recovery

Back up `TITANSKIES_DATA_DIR` (or `$XDG_STATE_HOME/titanskies`) while ingest is stopped. The cache directory can be discarded. Restore into the same path, verify ownership, start web, and run one ingest. The atomic pointer and retained previous manifest prevent a half-published state.

## Updates and rollback

Container updates are explicit: verify the signed GHCR manifest, change `TITANSKIES_VERSION` to the desired `vX.Y.Z` tag, run `docker compose pull`, then `docker compose up -d`. Keep the old image until `/api/healthz` succeeds and `/api/context-health` reports a valid publication.

Native updates use `scripts/update-user`, which verifies the release archive checksum and keyless signature before invoking the versioned installer. A failed activation, immediate ingest, or web self-check restores the prior `current` symlink. A failed first installation disables the new services and timer. Older releases remain under `$XDG_DATA_HOME/titanskies/releases` for manual rollback.

Native `TITANSKIES_DATA_DIR` and `TITANSKIES_CACHE_DIR` values must be non-root absolute paths outside the versioned release directory. The installer resolves symlinks before rendering the systemd filesystem boundaries; uninstall refuses a symlinked installation directory. Purge removes the default XDG publication/cache directories, while custom paths are preserved for explicit manual removal.

Uninstall confirms that the web and ingest services and ingest timer are inactive before removing application files. If no systemd user manager is available, uninstall continues with a prominent warning because shutdown cannot be confirmed; check for any surviving TitanSkies process before assuming a LAN-bound instance has stopped. A detected active unit aborts uninstall and preserves its files.

## Troubleshooting

- `initializing`: run a single ingest and inspect its logs. Demo mode can confirm the application without network access.
- stale forecast: verify outbound HTTPS/DNS access to the registered hosts and available cache/state disk.
- one failed provider: leave the publication intact; source isolation is expected. Check the provider's official status and retry later.
- permission errors: web needs read-only access to data; ingest needs write access to data and cache.
- low space: preserve the data directory, clear only cache, then rerun ingest. Recommended persistent capacity is 1 GiB minimum.

Never log or publish `AIRNOW_API_KEY`. Do not manually edit a manifest or pointer; regenerate through ingest.
