# TitanSkies product contract

TitanSkies helps a person explore where smoke models forecast near-surface wildfire PM2.5, compare official point observations, and inspect agency-reported incidents across North America. It does not infer plume origin, issue alerts, or provide medical advice.

The canonical public data contract is v8. A publication contains 37 consecutive absolute valid-time hours; integrated display rasters and 4×4 detail tiles; ECCC FireWork and NOAA HRRR metadata; AirNow, B.C. ENV, INECC/SINAICA, and ECCC AQHI monitor sets; WFIGS and CWFIS incidents/perimeters; source timestamps/states; and content-addressed assets. Forecasts, observations, incidents, AQI, and AQHI remain semantically distinct.

Publication is transactional: source work is isolated, healthy results survive peer failures, the previous compatible source may be retained, assets and manifest are immutable, and only the pointer changes atomically after validation. A failed attempt never replaces the last valid pointer. Missing is never zero.

The application has two read-only deployment processes sharing local volumes: a web server and a periodic ingest worker. It binds to localhost by default, contains no analytics, serves no remote fonts, has no HTTP ingest endpoint, and exposes redacted health suitable for a future Omarchy badge. On Apple Silicon macOS, those same two processes run from a per-user `TitanSkies.app` with bundled Node and Python; closing the window does not stop the services.


Acceptance for v0.1.0 requires all eight current providers represented, operation without an AirNow key, no retired TEMPO/HMS/FIRMS activation, no proprietary runtime or live-data redistribution, reproducible checks, hardened container/native installation, and accessible presentation of the public-data warning and source attribution.
