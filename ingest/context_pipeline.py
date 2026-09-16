from __future__ import annotations

import hashlib
import logging
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import copy_context
from datetime import datetime, timedelta, timezone
from typing import Any

from ingest.auth import redact
from ingest.local_store import FrameStore, open_cache_store, open_store
from ingest.config import Settings
from ingest.context_contracts import (
    CONTEXT_BOUNDS,
    display_bounds_for_version,
    iso_utc,
    monitor_regions_for_version,
    published_context_version,
    validate_context_manifest,
)
from ingest.forecast_composer import outlook_horizon_for_version
from ingest.forecast_native_cache import NATIVE_SPOOL_MAX_BYTES, NativeFieldCache
from ingest.http import configure_http_limit
from ingest.perf import (
    ingest_run,
    timed_phase,
)
from ingest.context_demo import _demo_manifest
from ingest.context_gc import reconcile_orphans
from ingest.context_forecasts import _attach_v8_forecasts, _complete_integrated
from ingest.context_publish import (
    CONTEXT_LATEST_PATH,
    CONTEXT_LOCK_PATH,
    CONTEXT_STATUS_PATH,
    _cleanup_context_assets,
    _json_bytes,
    load_json,
    _previous_context,
    _seed_asset_memo,
    context_status_from_manifest,
    context_status_payload,
    context_status_metrics,
    write_context_status,
)
from ingest.context_sources import (
    apply_light_sources,
    gather_light_sources,
    _state,
)

LOGGER = logging.getLogger("titanskies.context")


def _live_manifest(
    settings: Settings,
    store: FrameStore,
    now: datetime,
    previous: dict[str, Any] | None,
    native_cache: NativeFieldCache | None = None,
) -> dict[str, Any]:
    contract_version = published_context_version(settings)
    published_display_bounds = display_bounds_for_version(contract_version)
    published_monitor_regions = monitor_regions_for_version(contract_version)
    native_cache = native_cache or NativeFieldCache(open_cache_store(settings))
    manifest: dict[str, Any] = {
        "version": contract_version,
        "mode": "live",
        "generatedAt": iso_utc(now),
        "bounds": dict(CONTEXT_BOUNDS),
        "displayBounds": published_display_bounds,
        "monitorRegions": list(published_monitor_regions),
        "sources": {},
        "air": {},
        "fires": {},
        "forecast": {"frames": []},
        "forecasts": {},
    }

    forecast_error: Exception | None = None
    forecast_executor: ThreadPoolExecutor | None = None
    forecast_future: Future[None] | None = None
    forecast_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="v8-forecast")
    forecast_context = copy_context()

    def build_v8_forecast() -> None:
        with timed_phase("forecastWave"):
            _attach_v8_forecasts(manifest, store, previous, now, settings, native_cache)

    forecast_future = forecast_executor.submit(forecast_context.run, build_v8_forecast)
    try:
        gathered = gather_light_sources(manifest, settings, store, now, previous, contract_version)
        if forecast_future is not None:
            try:
                forecast_future.result()
            except Exception as exc:
                forecast_error = exc
    finally:
        if forecast_executor is not None:
            forecast_executor.shutdown(wait=True)

    if forecast_error is not None:
        forecast_exc = forecast_error
        LOGGER.warning("v8 native forecast failed: %s", redact(str(forecast_exc)))
        safe_error = redact(str(forecast_exc))
        manifest["sources"].setdefault(
            "firework", _state("firework", now, None, status="error", error=safe_error)
        )
        manifest["sources"].setdefault("hrrr", _state(
            "hrrr", now, None,
            status="error" if settings.hrrr_smoke_enabled else "unavailable",
            error=safe_error if settings.hrrr_smoke_enabled else "HRRR_SMOKE_ENABLED is off",
        ))
        prior_forecasts = ((previous or {}).get("forecasts") or {}) if (previous or {}).get("version") == 8 else {}
        prior_best = prior_forecasts.get("best") or {}
        prior_times = [str(frame.get("validTime") or "") for frame in prior_best.get("frames", [])]
        still_valid = bool(prior_times) and datetime.fromisoformat(prior_times[-1].replace("Z", "+00:00")) >= now
        if still_valid and len(prior_best.get("frames", [])) == 37:
            retained = {**prior_best, "integratedStatus": "retained"}
            manifest["forecasts"] = {
                "firework": prior_forecasts.get("firework") or {"frames": []},
                "hrrr": prior_forecasts.get("hrrr") or {"frames": []},
                "best": retained,
            }
            manifest["forecast"] = retained
        else:
            manifest["forecasts"] = {"firework": {"frames": []}, "hrrr": {"frames": []}, "best": {"frames": []}}
            manifest["forecast"] = {"frames": []}

    apply_light_sources(manifest, gathered, settings, now, previous, contract_version)
    manifest["displayBounds"] = published_display_bounds
    manifest["monitorRegions"] = list(published_monitor_regions)

    return manifest


def run_context_ingest(settings: Settings | None = None, now: datetime | None = None) -> dict[str, Any]:
    settings = settings or Settings.from_env()
    now = now or datetime.now(timezone.utc)
    contract_version = published_context_version(settings)
    configure_http_limit(settings.http_concurrency)
    with ingest_run(budget_seconds=settings.ingest_budget_seconds) as (metrics, _budget):
        store = open_store(settings)
        lease = store.acquire_lease(CONTEXT_LOCK_PATH, uuid.uuid4().hex, now, now + timedelta(seconds=settings.lock_seconds))
        if lease is None:
            return {"ok": True, "skipped": True, "reason": "context ingest already running", **metrics.snapshot()}
        native_cache = None
        try:
            previous, previous_manifest_path = _previous_context(store)
            previous_status = load_json(store, CONTEXT_STATUS_PATH)
            if not isinstance(previous_status, dict) and previous:
                previous_status = context_status_from_manifest(previous)
            live = settings.context_source == "live"
            reusable_previous = previous if previous and previous.get("mode") == settings.context_source else None
            if reusable_previous is not None and not _seed_asset_memo(store, reusable_previous):
                reusable_previous = None
            cache_store = open_cache_store(settings)
            native_cache = NativeFieldCache(
                cache_store,
                spool_max_bytes=NATIVE_SPOOL_MAX_BYTES if live else 0,
            )
            active_forecast_cache = native_cache if live else None
            with timed_phase("build"):
                manifest = (
                    _live_manifest(settings, store, now, reusable_previous, native_cache)
                    if live
                    else _demo_manifest(store, now, settings, previous=reusable_previous)
                )
            if not _complete_integrated(
                manifest.get("forecast"),
                require_versions=True,
                require_detail=True,
                horizon_hours=outlook_horizon_for_version(contract_version),
                contract_version=contract_version,
            ):
                raise RuntimeError("no compatible complete smoke outlook is available")
            with timed_phase("validation"):
                validate_context_manifest(manifest)
                encoded = _json_bytes(manifest)
            digest = hashlib.sha256(encoded).hexdigest()[:20]
            manifest_path = f"context/manifests/{digest}.json"
            forecast_cache_to_finalize = (
                active_forecast_cache
                if manifest["forecast"].get("integratedStatus") != "retained"
                else None
            )
            with timed_phase("publish"):
                manifest_url = store.put_bytes(manifest_path, encoded, "application/json", cache_seconds=60 * 60 * 24 * 30, overwrite=False)
                if forecast_cache_to_finalize:
                    forecast_cache_to_finalize.publish_index()
            with timed_phase("cleanup"):
                if forecast_cache_to_finalize:
                    forecast_cache_to_finalize.gc()
            pointer = {"version": manifest["version"], "manifestUrl": manifest_url, "manifestPath": manifest_path, "updatedAt": iso_utc(now)}
            with timed_phase("finalPublication"):
                store.put_json(CONTEXT_LATEST_PATH, pointer, cache_seconds=60, overwrite=True)
            if not live:
                try:
                    with timed_phase("publicCleanup"):
                        _cleanup_context_assets(store, manifest, manifest_path, previous, previous_manifest_path)
                except Exception:
                    LOGGER.warning("public cleanup failed; publication preserved", exc_info=True)
            if live and settings.context_orphan_gc_enabled:
                with timed_phase("orphanReconciliation"):
                    reconcile_orphans(
                        store,
                        lease,
                        pointer,
                        manifest,
                        previous,
                        previous_manifest_path,
                        retention_hours=settings.retention_hours,
                    )
            metrics.publication_status = "retained" if manifest["forecast"].get("integratedStatus") == "retained" else "fresh"
            forecasts = manifest.get("forecasts") or {}
            result = {
                "ok": True,
                "mode": manifest["mode"],
                "manifestUrl": manifest_url,
                "sources": {name: state["status"] for name, state in manifest["sources"].items()},
                "forecastFrameCount": len(manifest["forecast"].get("frames", [])),
                "forecastModels": {name: len((run or {}).get("frames", [])) for name, run in forecasts.items()},
                **metrics.snapshot(),
            }
            write_context_status(
                store,
                context_status_payload(
                    previous_status,
                    now,
                    metrics.publication_status,
                    manifest=manifest,
                    metrics=context_status_metrics(manifest, metrics.snapshot()),
                ),
            )
            metrics.log_summary("context", {"ok": True, "mode": manifest["mode"]})
            return result
        except Exception as exc:
            LOGGER.error("context ingest failed: %s", redact(str(exc)))
            status = locals().get("previous_status")
            write_context_status(
                store,
                context_status_payload(status if isinstance(status, dict) else None, now, "failed", metrics=metrics.snapshot(), error=exc),
            )
            metrics.log_summary("context", {"ok": False})
            return {"ok": False, "error": redact(str(exc)), **metrics.snapshot()}
        finally:
            if native_cache is not None:
                native_cache.close()
            try:
                store.release_lease(lease)
            except Exception:
                LOGGER.warning("failed to release context ingest lock")
