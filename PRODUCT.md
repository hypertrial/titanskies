# TitanSkies product contract

TitanSkies helps a person explore where smoke models forecast near-surface wildfire PM2.5, compare official point observations, and inspect agency-reported incidents across North America. It does not infer plume origin, issue alerts, or provide medical advice.

The canonical public data contract is v8. A publication contains 37 consecutive absolute valid-time hours; integrated display rasters and 4×4 detail tiles; ECCC FireWork and NOAA HRRR metadata; AirNow, B.C. ENV, INECC/SINAICA, and ECCC AQHI monitor sets; WFIGS and CWFIS incidents/perimeters; source timestamps/states; and content-addressed assets. Forecasts, observations, incidents, AQI, and AQHI remain semantically distinct.

Publication is transactional: source work is isolated, healthy results survive peer failures, the previous compatible source may be retained, assets and manifest are immutable, and only the pointer changes atomically after validation. A failed attempt never replaces the last valid pointer. Missing is never zero.

The canonical application has two deployment adapters. Docker Compose runs the web server and periodic ingest worker with local persistent volumes on supported 64-bit hosts. Vercel serves the public site and stores publications in Vercel Blob; its only write endpoint is the bearer-authenticated Vercel Cron function. Local deployments bind to localhost by default. Both modes contain no analytics, serve no remote fonts, and expose redacted health.


Acceptance for v0.1.0 requires all eight current providers represented, operation without an AirNow key, no retired TEMPO/HMS/FIRMS activation, no live-data redistribution, reproducible checks, a hardened signed two-architecture container, an isolated demo-only Vercel preview, and accessible presentation of the public-data warning and source attribution.
