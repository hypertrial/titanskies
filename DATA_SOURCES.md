# Data sources and terms

TitanSkies is MIT-licensed software, but it does not own or relicense upstream data. A self-hosted ingest downloads current public government data for local use. The repository contains synthetic demo fixtures only; it does not redistribute fetched live datasets.

> TitanSkies uses preliminary observations, model forecasts, and agency-reported incidents that may be delayed, incomplete, inaccurate, retained from an earlier update, or unavailable. It does not identify smoke origin and is not an emergency alert or medical service. Check source timestamps, official alerts, and local health guidance before acting.

The authoritative machine-readable inventory is [`shared/data-sources.json`](shared/data-sources.json). It records owners, runtime host allowlists, attribution, terms, credentials, transformations, freshness limits, and review dates. `python scripts/check_public_release.py` checks that all eight v8 sources are registered and that runtime data URLs use only those declared government hosts.

| Source | Product role | Terms and attribution | Credentials |
| --- | --- | --- | --- |
| ECCC FireWork | Base smoke forecast | Open Government Licence – Canada; attribute ECCC | None |
| NOAA HRRR-Smoke | U.S. smoke forecast enhancement | U.S. public-domain NOAA data; no endorsement implied | None |
| AirNow | Provider-reported PM2.5 AQI | AirNow Data Exchange Guidelines; attribute EPA/reporting agencies and treat readings as preliminary | Optional `AIRNOW_API_KEY` |
| B.C. ENV | Hourly PM2.5 observations | Open Government Licence – British Columbia | None |
| INECC/SINAICA | Hourly PM2.5 observations | Public Government of Mexico service; provider terms apply | None |
| ECCC AQHI | Canadian AQHI observations | Open Government Licence – Canada; attribute ECCC | None |
| WFIGS | U.S. reported wildfire incidents | U.S. government public data; item-specific provider terms apply | None |
| CWFIS | Canadian reported wildfire incidents | Open Government Licence – Canada; attribute NRCan/CWFIS | None |

Forecasts are model guidance, not observations. WFIGS and CWFIS incidents do not prove plume origin. Station readings apply only at monitor locations. AirNow AQI remains provider-reported. TitanSkies calculates U.S. EPA NowCast AQI for B.C. ENV and SINAICA PM2.5 concentrations. AQHI remains a separate Canadian scale and is never converted to AQI.

Operators are responsible for reviewing provider terms for their jurisdiction and intended use. The registry's `lastReviewed` field makes that review auditable; update it whenever endpoints or terms change.
