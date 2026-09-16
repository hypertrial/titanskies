# Vercel deployment

TitanSkies production runs on Vercel Pro with Fluid Compute. The private deployment repository contains only deployment metadata and a public HTTPS submodule named `titanskies`, pinned to an exact reviewed commit from this repository. Configure the Vercel project's Root Directory as `titanskies`; promote production only through a reviewed submodule-pin change.

## Environment separation

Production requires these server-side variables:

- `STORAGE_BACKEND=blob`
- `CONTEXT_SOURCE=live`
- `BLOB_READ_WRITE_TOKEN`
- `BLOB_STORE_ID`
- `PUBLIC_BLOB_BASE_URL`
- `CRON_SECRET`
- optional provider credentials such as `AIRNOW_API_KEY`

`PUBLIC_BLOB_BASE_URL` must be the HTTPS origin of the selected Vercel Blob store. Blob mode refuses to start when any required storage setting is missing or malformed. `BLOB_READ_WRITE_TOKEN`, `CRON_SECRET`, and provider credentials must never use a `NEXT_PUBLIC_` name.

Preview deployments set `STORAGE_BACKEND=local`, `CONTEXT_SOURCE=demo`, and `NEXT_PUBLIC_CONTEXT_URL=/demo/context/latest.json`. Do not assign Blob credentials, provider keys, or `CRON_SECRET` to Preview. Without a valid Cron secret, `/api/context` fails closed with HTTP 401; preview rendering reads only bundled synthetic data.

## Cutover and rollback

Before promoting a new pin, record the current production deployment ID, `/api/context-health` response, `context/latest.json` pointer, domain, Blob store ID, and environment-variable names without recording their values. Deploy and inspect a demo-only preview, then promote the reviewed pin.

After promotion, verify that the previous pointer remains readable, the next Cron run publishes successfully, `/api/context-health?expectedVersion=8` is healthy or intentionally degraded, and `www.titanskies.com` serves the v8 contract.

Rollback by promoting the recorded deployment or reverting the private repository's submodule pin. Never delete or rewrite Blob publications during rollback.
