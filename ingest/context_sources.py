from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable

from ingest.auth import redact
from ingest.local_store import FrameStore
from ingest.config import Settings
from ingest.context_contracts import STALE_AFTER_HOURS, iso_utc
from ingest.context_publish import _put_asset, _put_json_asset, _put_monitor_asset
from ingest.context_types import SourceFailure, SourceOutcome, SourceSuccess
from ingest.http_pool import map_isolated
from ingest.perf import timed_source
from ingest.sources.airnow import AIRNOW_URL
from ingest.sources.airnow import fetch_airnow
from ingest.sources.aqhi import AQHI_COUNT_VERSION, AQHI_URL, fetch_aqhi
from ingest.sources.bc_air import BC_AIR_URL, fetch_bc_air
from ingest.sources.context_raster import rasterize_perimeters
from ingest.sources.firework import FIREWORK_URL
from ingest.sources.wildfires import (
    CWFIS_URL,
    WFIGS_URL,
    fetch_cwfis,
    fetch_cwfis_perimeter_texture,
    fetch_wfigs_parts,
)
from ingest.sources.hrrr import HRRR_URL
from ingest.sources.sinaica import SINAICA_COUNT_VERSION, SINAICA_URL, fetch_sinaica

LOGGER = logging.getLogger("titanskies.context")
PROVENANCE = {
    "airnow": AIRNOW_URL,
    "bcair": BC_AIR_URL,
    "sinaica": SINAICA_URL,
    "aqhi": AQHI_URL,
    "wfigs": WFIGS_URL,
    "cwfis": CWFIS_URL,
    "firework": FIREWORK_URL,
    "hrrr": HRRR_URL,
}


def _state(name: str, now: datetime, observed_at: datetime | None, *, status: str = "ok", error: str | None = None) -> dict[str, Any]:
    if observed_at and status == "ok" and now - observed_at > timedelta(hours=STALE_AFTER_HOURS[name]):
        status = "stale"
    return {
        "status": status,
        "checkedAt": iso_utc(now),
        "observedAt": iso_utc(observed_at) if observed_at else None,
        "provenance": PROVENANCE[name],
        "error": redact(error) if error else None,
    }


def _fallback_source(
    manifest: dict[str, Any],
    previous: dict[str, Any] | None,
    source: str,
    section: str,
    now: datetime,
    error: Exception,
) -> None:
    prior_state = (previous or {}).get("sources", {}).get(source)
    prior_section = (previous or {}).get(section)
    if source in {"firework", "hrrr"}:
        prior_section = ((previous or {}).get("forecasts") or {}).get(source) or prior_section
    missing_key = isinstance(error, PermissionError)
    if source in {"firework", "hrrr"} and prior_section:
        frames = prior_section.get("frames", [])
        try:
            last_valid = datetime.fromisoformat(frames[-1]["validTime"].replace("Z", "+00:00"))
        except (IndexError, KeyError, TypeError, ValueError):
            last_valid = None
        if last_valid is None or last_valid < now:
            observed_raw = prior_state.get("observedAt") if prior_state else None
            observed = datetime.fromisoformat(observed_raw.replace("Z", "+00:00")) if observed_raw else None
            manifest["sources"][source] = _state(source, now, observed, status="unavailable", error=str(error))
            return
    if prior_state and prior_section:
        observed_raw = prior_state.get("observedAt")
        observed = datetime.fromisoformat(observed_raw.replace("Z", "+00:00")) if observed_raw else None
        age_stale = observed is None or now - observed > timedelta(hours=STALE_AFTER_HOURS[source])
        manifest["sources"][source] = _state(source, now, observed, status="stale" if age_stale else "error", error=str(error))
        manifest[section] = prior_section
    else:
        manifest["sources"][source] = _state(source, now, None, status="unavailable" if missing_key else "error", error=str(error))


def _run_source(
    manifest: dict[str, Any],
    previous: dict[str, Any] | None,
    source: str,
    section: str,
    now: datetime,
    build: Callable[[], tuple[dict[str, Any], datetime]],
) -> None:
    try:
        payload, observed = build()
        manifest[section] = payload
        manifest["sources"][source] = _state(source, now, observed)
    except Exception as exc:  # Source isolation is the product contract.
        LOGGER.warning("%s context source failed: %s", source, redact(str(exc)))
        _fallback_source(manifest, previous, source, section, now, exc)


def _latest_incident_time(incidents: list[dict[str, Any]], fallback: datetime) -> datetime:
    values = []
    latest_plausible = fallback + timedelta(minutes=5)
    for item in incidents:
        try:
            value = datetime.fromisoformat(item["updatedAt"].replace("Z", "+00:00"))
            if value.year > 2000 and value <= latest_plausible:
                values.append(value)
        except (KeyError, ValueError):
            continue
    return max(values, default=fallback)


def _prior_monitor_count(
    previous: dict[str, Any] | None,
    source: str,
    count_version: str | None = None,
) -> int | None:
    item = ((previous or {}).get("air", {}).get("monitorSets") or {}).get(source)
    if (
        isinstance(item, dict)
        and (count_version is None or item.get("countVersion") == count_version)
        and isinstance(item.get("count"), int)
    ):
        return item["count"]
    return None


def gather_light_sources(
    manifest: dict[str, Any],
    settings: Settings,
    store: FrameStore,
    now: datetime,
    previous: dict[str, Any] | None,
    contract_version: int,
) -> dict[str, SourceOutcome]:
    """Acquire independent non-forecast sources without mutating their sections."""

    def build_airnow() -> SourceSuccess:
        monitors = fetch_airnow(settings, now, _prior_monitor_count(previous, "airnow"))
        observed = max(datetime.fromisoformat(item["observedAt"].replace("Z", "+00:00")) for item in monitors)
        url = _put_monitor_asset(store, "airnow-monitors.json", monitors)
        return SourceSuccess({
            "observedAt": iso_utc(observed),
            "monitorsUrl": url,
            "monitorSet": {"url": url, "observedAt": iso_utc(observed), "count": len(monitors)},
        }, observed)

    def build_bcair() -> SourceSuccess:
        monitors = fetch_bc_air(settings, now, _prior_monitor_count(previous, "bcair"))
        observed = max(datetime.fromisoformat(item["observedAt"].replace("Z", "+00:00")) for item in monitors)
        url = _put_monitor_asset(store, "bc-monitors.json", monitors)
        return SourceSuccess({
            "bcMonitorsUrl": url,
            "monitorSet": {"url": url, "observedAt": iso_utc(observed), "count": len(monitors)},
        }, observed)

    def build_sinaica() -> SourceSuccess:
        monitors = fetch_sinaica(
            settings,
            now,
            _prior_monitor_count(previous, "sinaica", SINAICA_COUNT_VERSION),
            store=store,
        )
        observed = max(datetime.fromisoformat(item["observedAt"].replace("Z", "+00:00")) for item in monitors)
        url = _put_monitor_asset(store, "sinaica-monitors.json", monitors)
        return SourceSuccess({
            "sinaicaMonitorsUrl": url,
            "monitorSet": {
                "url": url,
                "observedAt": iso_utc(observed),
                "count": len(monitors),
                "countVersion": SINAICA_COUNT_VERSION,
            },
        }, observed)

    def build_aqhi() -> SourceSuccess:
        monitors = fetch_aqhi(now, _prior_monitor_count(previous, "aqhi", AQHI_COUNT_VERSION))
        observed = max(datetime.fromisoformat(item["observedAt"].replace("Z", "+00:00")) for item in monitors)
        url = _put_monitor_asset(store, "aqhi-monitors.json", monitors)
        return SourceSuccess({
            "monitorSet": {
                "url": url,
                "observedAt": iso_utc(observed),
                "count": len(monitors),
                "countVersion": AQHI_COUNT_VERSION,
            },
        }, observed)

    prior_fires = (previous or {}).get("fires", {})

    def build_wfigs() -> SourceSuccess:
        incidents, rings, perimeter_error = fetch_wfigs_parts(settings)
        observed = _latest_incident_time(incidents, now)
        payload = {"wfigsIncidentsUrl": _put_json_asset(store, "wfigs-incidents.json", {"incidents": incidents})}
        if rings is not None:
            payload["wfigsPerimeterTextureUrl"] = _put_asset(
                store, "wfigs-perimeters.png", rasterize_perimeters(rings), "image/png"
            )
            perimeter_observed = now
        elif prior_fires.get("wfigsPerimeterTextureUrl"):
            payload["wfigsPerimeterTextureUrl"] = prior_fires["wfigsPerimeterTextureUrl"]
            raw = ((previous or {}).get("sources", {}).get("wfigs") or {}).get("perimeterObservedAt") \
                or ((previous or {}).get("sources", {}).get("wfigs") or {}).get("observedAt")
            perimeter_observed = datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else observed
        else:
            perimeter_observed = None
        return SourceSuccess(payload, observed, perimeter_error, perimeter_observed)

    def build_cwfis() -> SourceSuccess:
        incidents, _, provider_observed = fetch_cwfis(settings, now)
        observed = provider_observed or _latest_incident_time(incidents, now)
        payload = {"cwfisIncidentsUrl": _put_json_asset(store, "cwfis-incidents.json", {"incidents": incidents})}
        perimeter_error = None
        try:
            payload["cwfisPerimeterTextureUrl"] = _put_asset(
                store, "cwfis-perimeters.png", fetch_cwfis_perimeter_texture(settings), "image/png"
            )
            perimeter_observed = now
        except Exception as exc:
            perimeter_error = str(exc)
            if prior_fires.get("cwfisPerimeterTextureUrl"):
                payload["cwfisPerimeterTextureUrl"] = prior_fires["cwfisPerimeterTextureUrl"]
                raw = ((previous or {}).get("sources", {}).get("cwfis") or {}).get("perimeterObservedAt") \
                    or ((previous or {}).get("sources", {}).get("cwfis") or {}).get("observedAt")
                perimeter_observed = datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else observed
            else:
                perimeter_observed = None
        return SourceSuccess(payload, observed, perimeter_error, perimeter_observed)

    def invoke(item: tuple[str, Callable[[], SourceSuccess]]) -> tuple[str, SourceOutcome]:
        name, builder = item
        with timed_source(name):
            try:
                return name, builder()
            except Exception as exc:
                return name, SourceFailure(exc)

    jobs: list[tuple[str, Callable[[], SourceSuccess]]] = [("airnow", build_airnow), ("bcair", build_bcair)]
    if settings.sinaica_enabled:
        jobs.append(("sinaica", build_sinaica))
    else:
        manifest["sources"]["sinaica"] = _state(
            "sinaica", now, None, status="unavailable", error="SINAICA_ENABLED is off"
        )
    jobs.extend((("wfigs", build_wfigs), ("cwfis", build_cwfis)))
    jobs.append(("aqhi", build_aqhi))
    gathered = map_isolated(jobs, invoke, workers=settings.context_source_concurrency)
    return dict(result for result in gathered if isinstance(result, tuple))


def apply_light_sources(
    manifest: dict[str, Any],
    gathered: dict[str, SourceOutcome],
    settings: Settings,
    now: datetime,
    previous: dict[str, Any] | None,
    contract_version: int,
) -> None:
    """Apply collected source outcomes with per-source last-good fallback."""
    prior_air = dict((previous or {}).get("air", {}))
    air: dict[str, Any] = {}
    monitor_sets: dict[str, Any] = {}
    air_sources = ("airnow", "bcair") + (("sinaica",) if settings.sinaica_enabled else ()) + ("aqhi",)
    for source in air_sources:
        result = gathered[source]
        if isinstance(result, SourceSuccess):
            payload = dict(result.payload)
            monitor_sets[source] = payload.pop("monitorSet")
            air.update(payload)
            manifest["sources"][source] = _state(source, now, result.observed_at)
            continue
        exc = result.error
        LOGGER.warning("%s context source failed: %s", source, redact(str(exc)))
        prior_state = (previous or {}).get("sources", {}).get(source)
        keys = {
            "airnow": ("monitorsUrl", "observedAt"),
            "bcair": ("bcMonitorsUrl",),
            "sinaica": ("sinaicaMonitorsUrl",),
            "aqhi": (),
        }[source]
        restored = {key: prior_air[key] for key in keys if key in prior_air}
        prior_set = (prior_air.get("monitorSets") or {}).get(source)
        if prior_set:
            monitor_sets[source] = prior_set
            if keys:
                restored.setdefault("monitorsUrl" if source == "airnow" else keys[0], prior_set.get("url"))
        air.update(restored)
        missing_key = isinstance(exc, PermissionError)
        if prior_state and (restored or prior_set):
            observed_raw = prior_state.get("observedAt")
            observed = datetime.fromisoformat(observed_raw.replace("Z", "+00:00")) if observed_raw else None
            is_stale = observed is None or now - observed > timedelta(hours=STALE_AFTER_HOURS[source])
            manifest["sources"][source] = _state(
                source, now, observed, status="stale" if is_stale else "error", error=str(exc)
            )
        else:
            manifest["sources"][source] = _state(
                source, now, None, status="unavailable" if missing_key else "error", error=str(exc)
            )
    if monitor_sets:
        air["monitorSets"] = monitor_sets
    manifest["air"] = air

    prior_fires = (previous or {}).get("fires", {})
    fires: dict[str, Any] = {}
    for source in ("wfigs", "cwfis"):
        result = gathered[source]
        if isinstance(result, SourceSuccess):
            fires.update(result.payload)
            partial_stale = now - result.observed_at > timedelta(hours=STALE_AFTER_HOURS[source]) or (
                result.perimeter_observed_at is not None
                and now - result.perimeter_observed_at > timedelta(hours=STALE_AFTER_HOURS[source])
            )
            state = _state(
                source,
                now,
                result.observed_at,
                status="stale" if result.partial_error and partial_stale else "error" if result.partial_error else "ok",
                error=result.partial_error,
            )
            if result.perimeter_observed_at is not None:
                state["perimeterObservedAt"] = iso_utc(result.perimeter_observed_at)
            manifest["sources"][source] = state
            continue
        exc = result.error
        LOGGER.warning("%s context source failed: %s", source, redact(str(exc)))
        fires.update({key: value for key, value in prior_fires.items() if key.lower().startswith(source)})
        prior_state = (previous or {}).get("sources", {}).get(source)
        if prior_state and any(key.lower().startswith(source) for key in prior_fires):
            observed_raw = prior_state.get("observedAt")
            observed = datetime.fromisoformat(observed_raw.replace("Z", "+00:00")) if observed_raw else None
            perimeter_raw = prior_state.get("perimeterObservedAt")
            perimeter_observed = datetime.fromisoformat(perimeter_raw.replace("Z", "+00:00")) if perimeter_raw else None
            oldest_observed = min(value for value in (observed, perimeter_observed) if value is not None) if observed or perimeter_observed else None
            is_stale = oldest_observed is None or now - oldest_observed > timedelta(hours=STALE_AFTER_HOURS[source])
            state = _state(source, now, observed, status="stale" if is_stale else "error", error=str(exc))
            if perimeter_raw:
                state["perimeterObservedAt"] = perimeter_raw
            manifest["sources"][source] = state
        else:
            manifest["sources"][source] = _state(source, now, None, status="error", error=str(exc))
    manifest["fires"] = fires
