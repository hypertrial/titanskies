from __future__ import annotations

from typing import Any

from ingest.http import fetch, session_get

CONTEXT_RESPONSE_MAX_BYTES = 16 * 1024 * 1024
FIREWORK_HOSTS = frozenset({"geo.weather.gc.ca"})
CONTEXT_DATA_HOSTS = frozenset({
    "geo.weather.gc.ca",
    "services3.arcgis.com",
    "geoserver.cwfif.nrcan.gc.ca",
    "cwfis.cfs.nrcan.gc.ca",
})
FIREWORK_COVERAGE_MAX_BYTES = 24 * 1024 * 1024

def _get(url: str, *, params: dict[str, Any] | None = None, timeout: int = 60) -> Any:
    return session_get(
        url,
        hosts=CONTEXT_DATA_HOSTS,
        params=params,
        timeout=timeout,
        max_bytes=CONTEXT_RESPONSE_MAX_BYTES,
    )


def _firework_bytes(url: str, *, timeout: int = 90) -> bytes:
    return fetch(url, hosts=FIREWORK_HOSTS, timeout=timeout, max_bytes=FIREWORK_COVERAGE_MAX_BYTES)
