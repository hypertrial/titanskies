# Operations

## Health and logs

```sh
curl -fsS http://127.0.0.1:8080/api/healthz
curl -sS http://127.0.0.1:8080/api/context-health?expectedVersion=8
docker compose logs web ingest
```

HTTP 503 is expected during first-start initialization. `degraded` with HTTP 200 means a valid publication remains usable but one or more providers failed, became stale, or were retained. AirNow `unavailable` is expected when `AIRNOW_API_KEY` is empty. The health heartbeat expires after twice `CONTEXT_WATCH_SECONDS`, with a 30-minute minimum. `COMPOSE_CONCURRENCY` (1-4, default 1) parallelizes v8 frame composition; raise it only after `scripts/benchmark_context.py` shows a wall-time win under the 768 MiB RSS cap.

## Backup and recovery

Stop the Compose project before copying the `data` volume. The cache volume is disposable. Restore the publication volume, start the project, and run one ingest. The atomic pointer and retained previous manifest prevent a half-published state.

## Updates and rollback

`latest` advances only after a verified release. To update on that channel:

```sh
docker compose pull
docker compose up -d
```

For reproducible deployments, set `TITANSKIES_VERSION=vX.Y.Z`. For exact digest pinning, replace the image reference with `ghcr.io/hypertrial/titanskies@sha256:...` in an operator Compose override file.

Before an update, record the running digest. Keep the old image until `/api/healthz` succeeds and `/api/context-health` reports a valid v8 publication. Roll back by restoring the prior immutable tag or digest and running `docker compose up -d` again. Publication and cache volumes remain independent of the image.

Vercel rollback promotes the recorded prior deployment or reverts the private wrapper repository's public-core submodule pin. Blob publications are immutable and must not be deleted or rewritten during rollback.

## Troubleshooting

- `initializing`: run a single ingest and inspect `docker compose logs ingest`. Demo mode can confirm the application without network access.
- stale forecast: verify outbound HTTPS/DNS access to registered hosts and available volume space.
- one failed provider: leave the publication intact; source isolation is expected. Check the provider's official status and retry later.
- permission errors: web needs read-only access to the publication volume; ingest needs write access to publication and cache volumes.
- low space: preserve publication data, clear only cache, then rerun ingest. Recommended persistent capacity is 1 GiB minimum.

Never log or publish credentials. Do not manually edit a manifest or pointer; regenerate through ingest.
