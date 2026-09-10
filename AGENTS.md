# TitanSkies engineering guardrails

- Preserve the v8 publication contract and atomic publish sequence: immutable assets, immutable manifest, then atomic `context/latest.json` replacement.
- Decode HRRR `MASSDEN` from kg/m³ to µg/m³. Accept ecCodes `shortName=UNKNOWN` only for GRIB identity `(discipline=0, parameterCategory=20, parameterNumber=0, level=8, typeOfLevel=heightAboveGround)`.
- Stitch forecast cycles by absolute valid time, not lead index. Treat all-black FireWork rasters as invalid. Keep the 200 km HRRR edge feather and land/coastal mask.
- Missing values are unavailable, never zero. A provider failure must not erase healthy providers; retain only validated last-good source assets and the last valid publication.
- Keep the application read-only over HTTP. Validate all served paths against traversal, symlink escape, size, and MIME allowlists.
- Never commit secrets or fetched live datasets. Register and document every runtime data host and its provider terms.
- Run `./scripts/verify-fast` for focused changes, `./scripts/verify` before merging, and `npm run verify:release` before a public release.
