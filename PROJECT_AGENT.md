# TitanSkies project notes

TitanSkies is a self-hosted North American wildfire-smoke forecast, air-quality
observation, and reported-wildfire explorer. This checkout is the product
application. Pad work for it shares `titanskies-engineering` with
`titanskies-vercel`. Every Work item in that workspace MUST start with
`Repository: titanskies`.

Use the workspace from `.pad.toml`. Follow `AGENTS.md` and the local
`pad-engineering` skill. Keep ticket bodies, exports, credentials, and local Pad
state out of this public repository.

## Invariants

- Preserve the v8 publication contract and atomic publish sequence: immutable
  assets, immutable manifest, then atomic `context/latest.json` replacement.
- Decode HRRR `MASSDEN` from kg/m³ to µg/m³. Accept ecCodes `shortName=UNKNOWN`
  only for GRIB identity `(discipline=0, parameterCategory=20, parameterNumber=0,
  level=8, typeOfLevel=heightAboveGround)`.
- Stitch forecast cycles by absolute valid time, not lead index. Treat all-black
  FireWork rasters as invalid. Keep the 200 km HRRR edge feather and land/coastal
  mask.
- Missing values are unavailable, never zero. A provider failure must not erase
  healthy providers; retain only validated last-good source assets and the last
  valid publication.
- Keep local deployments read-only over HTTP. The hosted `/api/context`
  endpoint is Vercel-Cron-only and must fail closed without its bearer secret.
  Validate all served paths against traversal, symlink escape, size, and MIME
  allowlists.
- Never commit secrets or fetched live datasets. Register and document every
  runtime data host and its provider terms.

## Verification

Wrappers (do not rewrite without a dedicated ticket):

- Fast: `npm test`, `npm run lint`, `npm run typecheck`
- Completion: also `npm run build`

Additional completion evidence named by native CI, recorded on the ticket when
run: `npm run check:public`, `uv run mypy`, `uv run pytest`, Playwright e2e,
docker image build, and compose smoke. `npm run verify:release` remains the
public-release gate. `check:public` allows committed `.pad.toml` (workspace slug
only), the canonical `.pad/universal.lock.json`, and the audited Vercel adapter;
it rejects credentials, local deployment state, and all other `.pad/` metadata.
Release verification also covers the multi-architecture container, keyless
release policy, and Docker Compose smoke.

## Repository owner authority

The repository owner, GitHub user `mattfaltyn`, may merge without another
person's approval and may bypass branch protections, required reviews, or
required checks when they explicitly request that action. Treat the owner's
direct merge instruction as authorization to use the narrowest necessary
bypass; do not wait for a separate reviewer. This authority does not extend to
other users. Force-pushing or rewriting a published release tag still requires
an explicit instruction naming that destructive action.
