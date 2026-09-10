from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable

import numpy as np

from ingest.auth import redact
from ingest.local_store import FrameStore
from ingest.config import Settings
from ingest.forecast_field_cache import FieldCache
from ingest.forecast_native_cache import NativeFieldCache
from ingest.forecast_composer import (
    BEST_POLICY_VERSION,
    COASTAL_BUFFER_KM,
    COVERAGE_MASK_VERSION,
    DETAIL_COVERAGE_MASK_VERSION,
    COVERAGE_POLICY,
    FEATHER_DISTANCE_KM,
    LEGACY_OUTLOOK_HORIZON_HOURS,
    LEGACY_SOURCE_MASK_VERSION,
    SOURCE_MASK_VERSION,
    PUBLIC_OUTLOOK_HORIZON_HOURS,
    canonical_hours,
    compose_best_run,
    compose_v8_frame,
    hours_needing_png,
    outlook_horizon_for_version,
    v8_hrrr_target_coordinates,
)
from ingest.context_contracts import (
    DETAIL_GRID,
    DETAIL_RASTER_PROCESSING_VERSION,
    coverage_mask_version_for_version,
    detail_grid_for_version,
    detail_raster_processing_version_for_version,
    detail_tile_count_for_version,
    context_capabilities,
    iso_utc,
    palette_version_for_version,
    published_context_version,
    uses_native_forecast_fields,
    V8_DETAIL_RASTER_PROCESSING_VERSION,
)
from ingest.context_types import (
    ForecastProviderName,
    ProviderStageFailure,
    ProviderStageOutcome,
    ProviderStageSuccess,
    SourceFailure,
    SourceOutcome,
    SourceSuccess,
)
from ingest.forecast_palette import PALETTE_UNITS, PALETTE_VERSION, V8_PALETTE_VERSION, encode_official_concentration, legend_png
from ingest.forecast_raster import BASE_RASTER_GRID, DETAIL_RASTER_GRID, HrrrGrid, PROCESSING_VERSION, RasterGrid
from ingest.http import HttpFetchError
from ingest.http_pool import map_bounded, map_isolated, map_isolated_batches
from ingest.perf import (
    COMPOSE_MIN_SECONDS,
    FORECAST_WAVE_MIN_SECONDS,
    acquisition_reserve,
    current_budget,
    current_metrics,
    timed_phase,
    timed_provider_phase,
    timed_source,
)
from ingest.sources.firework import (
    FIREWORK_NATIVE_PROCESSING_VERSION,
    FIREWORK_PROCESSING_VERSION,
    discover_firework,
    fetch_firework,
    fetch_firework_hour,
    fetch_firework_native_hour,
    firework_acquisition_times,
    firework_native_times,
)
from ingest.sources.hrrr import (
    HRRR_NATIVE_PROCESSING_VERSION,
    discover_hrrr,
    fetch_hrrr,
    fetch_hrrr_hour,
    fetch_hrrr_native_hour,
    hrrr_run_metadata,
    planned_hours,
)
from ingest.context_publish import (
    _legend_url,
    _load_asset_bytes,
    _publish_integrated_frame,
    _publish_integrated_frames,
    _publish_source_frames,
    _put_asset,
)
from ingest.context_sources import _fallback_source, _run_source, _state

LOGGER = logging.getLogger("titanskies.context")


def _firework_run(model_run: str, frames: list[dict[str, Any]], legend_url: str | None = None) -> dict[str, Any]:
    payload = {
        "modelId": "firework",
        "variable": "firework_wildfire_pm25",
        "modelRun": model_run,
        "units": "µg/m³ PM2.5 from wildfire smoke",
        "nativeResolutionKm": 10,
        "ingestMethod": "geomet-wcs",
        "processingVersion": FIREWORK_PROCESSING_VERSION,
        "horizonHours": 72,
        "frames": frames,
    }
    if legend_url:
        payload["legendUrl"] = legend_url
    return payload

def _public_forecast_run(run: dict[str, Any] | None, *, metadata_only: bool = False) -> dict[str, Any]:
    if not run:
        return {"frames": []}
    published = dict(run)
    frames = []
    private = {"png", "texturePng", "maskPng", "detailTilePngs", "values", "valid", "numericUrl", "fieldUrl"}
    for frame in run.get("frames", []):
        item = {key: value for key, value in frame.items() if key not in private}
        if metadata_only:
            item.pop("textureUrl", None)
            item.pop("sourceMaskUrl", None)
        frames.append(item)
    published["frames"] = frames
    published.pop("legendPng", None)
    if metadata_only:
        published.pop("legendUrl", None)
    return published


def _hydrate_frames(store: FrameStore, run: dict[str, Any] | None, *, times: set[str] | None = None) -> dict[str, Any] | None:
    if not run:
        return None
    frames = []
    pending: list[tuple[int, str]] = []
    for index, frame in enumerate(run.get("frames", [])):
        item = dict(frame)
        valid = str(item.get("validTime") or "")
        if times is not None and valid not in times:
            frames.append(item)
            continue
        if "png" not in item and item.get("textureUrl"):
            pending.append((index, item["textureUrl"]))
        frames.append(item)
    if pending:
        loaded = map_bounded(pending, lambda item: (item[0], _load_asset_bytes(store, item[1])), workers=min(6, len(pending)))
        for index, png in loaded:
            if png:
                frames[index]["png"] = png
    return {**run, "frames": frames}


def _forecast_frame_runs(run: dict[str, Any] | None) -> tuple[tuple[str, object | None], ...]:
    return tuple(
        (str(frame["validTime"]), frame.get("modelRun"))
        for frame in (run or {}).get("frames", [])
        if isinstance(frame, dict) and frame.get("validTime")
    )


def _forecast_valid_times(run: dict[str, Any] | None) -> tuple[str, ...]:
    return tuple(valid for valid, _model_run in _forecast_frame_runs(run))


def same_forecast_inputs(
    previous: dict[str, Any] | None,
    firework: dict[str, Any] | None,
    hrrr: dict[str, Any] | None,
    required_best_times: tuple[str, ...] | None = None,
) -> bool:
    previous_best = ((previous or {}).get("forecasts") or {}).get("best")
    if not previous_best or not previous_best.get("frames"):
        return False
    prior_forecasts = (previous or {}).get("forecasts") or {}
    prior_firework = prior_forecasts.get("firework") or {}
    prior_hrrr = prior_forecasts.get("hrrr") or {}
    prior_horizon = previous_best.get("horizonHours")
    return (
        _complete_integrated(
            previous_best,
            require_detail=previous_best.get("detailGrid") is not None,
            horizon_hours=prior_horizon if isinstance(prior_horizon, int) else None,
        )
        and (required_best_times is None or _forecast_valid_times(previous_best) == required_best_times)
        and (firework or {}).get("modelRun") == prior_firework.get("modelRun")
        and (hrrr or {}).get("modelRun") == prior_hrrr.get("modelRun")
        and (hrrr or {}).get("extendedModelRun") == prior_hrrr.get("extendedModelRun")
        and (firework or {}).get("processingVersion") == prior_firework.get("processingVersion")
        and (hrrr or {}).get("processingVersion") == prior_hrrr.get("processingVersion")
        and _forecast_frame_runs(firework) == _forecast_frame_runs(prior_firework)
        and _forecast_frame_runs(hrrr) == _forecast_frame_runs(prior_hrrr)
    )


def _complete_integrated(
    run: dict[str, Any] | None,
    *,
    require_versions: bool = False,
    legacy_mask: bool = False,
    require_detail: bool = False,
    horizon_hours: int | None = None,
    contract_version: int = 7,
) -> bool:
    if not run or not run.get("frames"):
        return False
    expected_horizon = horizon_hours if horizon_hours is not None else LEGACY_OUTLOOK_HORIZON_HOURS if legacy_mask else PUBLIC_OUTLOOK_HORIZON_HOURS
    if run.get("horizonHours") != expected_horizon:
        return False
    if run.get("selectionPolicy") != BEST_POLICY_VERSION or run.get("incompleteNumeric"):
        return False
    expected_mask = LEGACY_SOURCE_MASK_VERSION if legacy_mask else SOURCE_MASK_VERSION
    if (
        run.get("featherDistanceKm") != FEATHER_DISTANCE_KM
        or run.get("paletteVersion") != palette_version_for_version(contract_version)
        or run.get("sourceMaskVersion") != expected_mask
        or run.get("coveragePolicy") != COVERAGE_POLICY
        or run.get("coastalBufferKm") != COASTAL_BUFFER_KM
        or run.get("coverageMaskVersion") != (coverage_mask_version_for_version(contract_version) if require_detail else COVERAGE_MASK_VERSION)
    ):
        return False
    if require_detail:
        if run.get("detailGrid") != detail_grid_for_version(contract_version) or run.get("rasterProcessingVersion") != detail_raster_processing_version_for_version(contract_version):
            return False
        if any(len(frame.get("detailTiles") or []) != detail_tile_count_for_version(contract_version) for frame in run.get("frames", [])):
            return False
    elif run.get("detailGrid") is not None or run.get("rasterProcessingVersion") is not None:
        return False
    if run.get("integratedStatus") == "unavailable":
        return False
    firework_version = run.get("fireworkProcessingVersion")
    hrrr_version = run.get("hrrrProcessingVersion")
    native_forecast = uses_native_forecast_fields(contract_version)
    expected_firework = FIREWORK_NATIVE_PROCESSING_VERSION if native_forecast else FIREWORK_PROCESSING_VERSION
    expected_hrrr = HRRR_NATIVE_PROCESSING_VERSION if native_forecast else PROCESSING_VERSION
    if require_versions:
        if firework_version is not None and firework_version != expected_firework:
            return False
        if hrrr_version is not None and hrrr_version != expected_hrrr:
            return False
        sources = {
            contributor.get("source")
            for frame in run.get("frames", [])
            for contributor in frame.get("contributors", [])
            if isinstance(contributor, dict)
        }
        if not sources:
            return firework_version == expected_firework and hrrr_version == expected_hrrr
        return (
            ("firework" not in sources or firework_version == expected_firework)
            and ("hrrr" not in sources or hrrr_version == expected_hrrr)
        )
    if firework_version is not None and firework_version != expected_firework:
        return False
    if hrrr_version is not None and hrrr_version != expected_hrrr:
        return False
    return True


def _integrated_still_valid(run: dict[str, Any] | None, now: datetime) -> bool:
    times = _forecast_valid_times(run)
    if not times:
        return False
    last = datetime.fromisoformat(times[-1].replace("Z", "+00:00"))
    return last >= now


def _empty_integrated(*, legacy_mask: bool = False, detail: bool = False, horizon_hours: int | None = None) -> dict[str, Any]:
    horizon = horizon_hours if horizon_hours is not None else LEGACY_OUTLOOK_HORIZON_HOURS if legacy_mask else PUBLIC_OUTLOOK_HORIZON_HOURS
    return {
        "modelId": "best",
        "variable": "best_available_smoke",
        "ingestMethod": "compose",
        "horizonHours": horizon,
        "selectionPolicy": BEST_POLICY_VERSION,
        "featherDistanceKm": FEATHER_DISTANCE_KM,
        "paletteVersion": PALETTE_VERSION,
        "sourceMaskVersion": LEGACY_SOURCE_MASK_VERSION if legacy_mask else SOURCE_MASK_VERSION,
        "coveragePolicy": COVERAGE_POLICY,
        "coastalBufferKm": COASTAL_BUFFER_KM,
        "coverageMaskVersion": DETAIL_COVERAGE_MASK_VERSION if detail else COVERAGE_MASK_VERSION,
        **({"detailGrid": DETAIL_GRID, "rasterProcessingVersion": DETAIL_RASTER_PROCESSING_VERSION} if detail else {}),
        "incompleteNumeric": False,
        "integratedStatus": "unavailable",
        "frames": [],
    }


def _retain_complete_integrated(
    previous_best: dict[str, Any] | None,
    now: datetime,
    *,
    legacy_mask: bool = False,
    detail: bool = False,
    horizon_hours: int | None = None,
) -> dict[str, Any]:
    if (
        previous_best is not None
        and _complete_integrated(previous_best, require_versions=True, legacy_mask=legacy_mask, require_detail=detail, horizon_hours=horizon_hours)
        and _integrated_still_valid(previous_best, now)
    ):
        retained = dict(previous_best)
        retained["integratedStatus"] = "retained"
        return retained
    return _empty_integrated(legacy_mask=legacy_mask, detail=detail, horizon_hours=horizon_hours)
def _reusable_v8_best(
    previous_best: dict[str, Any] | None,
    hrrr_available: bool,
    firework_available: bool = True,
) -> bool:
    """Return whether a prior v8 run has identical output semantics."""
    return bool(
        previous_best
        and previous_best.get("detailGrid") == detail_grid_for_version(8)
        and previous_best.get("rasterProcessingVersion") == V8_DETAIL_RASTER_PROCESSING_VERSION
        and previous_best.get("paletteVersion") == V8_PALETTE_VERSION
        and previous_best.get("coverageMaskVersion") == coverage_mask_version_for_version(8)
        and previous_best.get("fireworkProcessingVersion") == (
            FIREWORK_NATIVE_PROCESSING_VERSION if firework_available else None
        )
        and previous_best.get("hrrrProcessingVersion") == (
            HRRR_NATIVE_PROCESSING_VERSION if hrrr_available else None
        )
        and previous_best.get("selectionPolicy") == BEST_POLICY_VERSION
        and previous_best.get("horizonHours") == 36
        and previous_best.get("featherDistanceKm") == FEATHER_DISTANCE_KM
        and previous_best.get("sourceMaskVersion") == SOURCE_MASK_VERSION
        and previous_best.get("coveragePolicy") == COVERAGE_POLICY
        and previous_best.get("coastalBufferKm") == COASTAL_BUFFER_KM
        and not previous_best.get("incompleteNumeric")
    )


def _validate_staged_provider(
    provider: str,
    frames: list[dict[str, Any]],
    required_times: set[str],
) -> None:
    by_time = {str(frame.get("validTime") or ""): frame for frame in frames}
    missing = sorted(required_times - by_time.keys())
    invalid = []
    for valid_time in sorted(required_times & by_time.keys()):
        frame = by_time[valid_time]
        values = frame.get("values")
        valid = frame.get("valid")
        if (
            not isinstance(values, np.ndarray)
            or not isinstance(valid, np.ndarray)
            or values.shape != valid.shape
            or not np.any(valid & np.isfinite(values))
        ):
            invalid.append(valid_time)
    if missing or invalid:
        raise ValueError(
            f"{provider} staged cycle is incomplete for the public horizon "
            f"(missing={len(missing)}, invalid={len(invalid)})"
        )


def _attach_v8_forecasts(
    manifest: dict[str, Any],
    store: FrameStore,
    previous: dict[str, Any] | None,
    now: datetime,
    settings: Settings,
    native_cache: NativeFieldCache,
) -> None:
    hours = canonical_hours(now, 36)
    required = [iso_utc(hour) for hour in hours]
    budget = current_budget()
    provider_errors: dict[ForecastProviderName, Exception] = {}

    firework_run: str | None = None
    firework_native: list[str] = []
    try:
        with timed_provider_phase("firework", "discovery"):
            firework_run, firework_times = discover_firework(settings)
        firework_native = firework_native_times(firework_times)
        if any(valid not in firework_native for valid in required):
            raise ValueError("FireWork native cycle is incomplete for v8")
    except Exception as exc:
        provider_errors["firework"] = exc

    standard = extended = None
    hrrr_jobs = []
    if settings.hrrr_smoke_enabled:
        try:
            with timed_provider_phase("hrrr", "discovery"):
                standard, extended = discover_hrrr(settings, now)
            hrrr_jobs = planned_hours(standard, extended)
        except Exception as exc:
            provider_errors["hrrr"] = exc
    hrrr_by_valid = {
        iso_utc(cycle.run + timedelta(hours=hour)): iso_utc(cycle.run)
        for cycle, hour in hrrr_jobs
    }
    if (
        settings.hrrr_smoke_enabled
        and "hrrr" not in provider_errors
        and any(valid not in hrrr_by_valid for valid in required)
    ):
        provider_errors["hrrr"] = ValueError("HRRR native cycle is incomplete for v8")

    checkpoint_lock = threading.Lock()
    checkpoint_counts = {"firework": 0, "hrrr": 0}
    previous_best = ((previous or {}).get("forecasts") or {}).get("best")
    prior_frames = {frame.get("validTime"): frame for frame in (previous_best or {}).get("frames", [])}
    planned_hrrr = settings.hrrr_smoke_enabled and "hrrr" not in provider_errors
    planned_firework = "firework" not in provider_errors
    reusable_staging = _reusable_v8_best(previous_best, planned_hrrr, planned_firework)

    def needs_staged_bytes(valid: str) -> bool:
        # This predicts disk reuse only; actual reuse is rechecked after staging.
        # An unexpected provider failure can always fall back to remote reads.
        prior = prior_frames.get(valid) if reusable_staging else None
        contributors = []
        if planned_hrrr:
            contributors.append({"source": "hrrr", "modelRun": hrrr_by_valid.get(valid)})
        if planned_firework:
            contributors.append({"source": "firework", "modelRun": firework_run})
        return not (prior and prior.get("contributors") == contributors and len(prior.get("detailTiles") or []) == 16)

    def checkpoint(provider: str, every: int) -> None:
        with checkpoint_lock:
            checkpoint_counts[provider] += 1
            due = checkpoint_counts[provider] % every == 0
        if due:
            native_cache.checkpoint()

    def stage_firework(valid: str) -> None:
        if firework_run is None:
            raise RuntimeError("FireWork discovery is unavailable")
        cached = native_cache.get(
            model_id="firework", model_run=firework_run, valid_time=valid,
            processing_version=FIREWORK_NATIVE_PROCESSING_VERSION,
            remember=needs_staged_bytes(valid),
        )
        if cached is None:
            if budget and budget.remaining() <= 120:
                raise RuntimeError("v8 acquisition deadline reached")
            field = fetch_firework_native_hour(settings, firework_run, valid)
        else:
            field = cached
        if not np.any(field.valid & np.isfinite(field.values) & (field.values >= 0)):
            raise ValueError("FireWork native coverage has no valid concentrations")
        if cached is not None:
            return
        native_cache.put(
            field, model_id="firework", model_run=firework_run, valid_time=valid,
            processing_version=FIREWORK_NATIVE_PROCESSING_VERSION,
        )
        checkpoint("firework", settings.firework_concurrency)

    def stage_hrrr(valid: str) -> None:
        model_run = hrrr_by_valid[valid]
        cached = native_cache.get(
            model_id="hrrr", model_run=model_run, valid_time=valid,
            processing_version=HRRR_NATIVE_PROCESSING_VERSION,
            remember=needs_staged_bytes(valid),
        )
        if cached is not None:
            return
        if budget and budget.remaining() <= 120:
            raise RuntimeError("v8 acquisition deadline reached")
        field = fetch_hrrr_native_hour(settings, model_run, valid)
        native_cache.put(
            field, model_id="hrrr", model_run=model_run, valid_time=valid,
            processing_version=HRRR_NATIVE_PROCESSING_VERSION,
        )
        checkpoint("hrrr", settings.hrrr_concurrency)

    jobs: list[tuple[ForecastProviderName, Callable[[str], None], list[str], int]] = []
    if "firework" not in provider_errors:
        jobs.append(("firework", stage_firework, required, settings.firework_concurrency))
    if settings.hrrr_smoke_enabled and "hrrr" not in provider_errors:
        jobs.append(("hrrr", stage_hrrr, required, settings.hrrr_concurrency))

    def stage_provider(
        item: tuple[ForecastProviderName, Callable[[str], None], list[str], int],
    ) -> ProviderStageOutcome:
        name, loader, times, workers = item
        scheduled = 0

        def before_batch() -> RuntimeError | None:
            nonlocal scheduled
            if budget and budget.remaining() <= 120:
                return RuntimeError("v8 acquisition deadline reached")
            scheduled += min(workers, len(times) - scheduled)
            return None

        results = map_isolated_batches(
            times,
            loader,
            workers=workers,
            stop_after=lambda batch: any(
                isinstance(result, HttpFetchError) and result.provider_outage for result in batch
            ),
            before_batch=before_batch,
        )
        metrics = current_metrics()
        if metrics and scheduled < len(times):
            metrics.increment("numeric_frames_skipped", len(times) - scheduled)
            metrics.add_provider_count(name, "skipped", len(times) - scheduled)
        error = next((result for result in results if isinstance(result, BaseException)), None)
        return ProviderStageFailure(name, error) if error else ProviderStageSuccess(name)

    with acquisition_reserve(120):
        staged_results = map_isolated(jobs, stage_provider, workers=len(jobs))
    outer_error = next((result for result in staged_results if isinstance(result, BaseException)), None)
    if outer_error:
        raise RuntimeError(f"native staging failed: {outer_error}")
    for outcome in staged_results:
        if isinstance(outcome, ProviderStageFailure):
            provider_errors[outcome.provider] = RuntimeError(
                f"{outcome.provider} native staging failed: {outcome.error}"
            )
    for provider, error in provider_errors.items():
        LOGGER.warning("%s native forecast failed: %s", provider, redact(str(error)))

    firework_available = firework_run is not None and "firework" not in provider_errors
    hrrr_available = standard is not None and "hrrr" not in provider_errors
    prior_forecasts = (
        (previous or {}).get("forecasts") or {}
        if (previous or {}).get("version") == 8
        else {}
    )

    if firework_available and firework_run is not None:
        manifest["sources"]["firework"] = _state(
            "firework", now, datetime.fromisoformat(firework_run.replace("Z", "+00:00"))
        )
    else:
        firework_error = provider_errors.get("firework") or RuntimeError("FireWork is unavailable")
        _fallback_source(manifest, previous, "firework", "firework", now, firework_error)
        manifest.pop("firework", None)
    if hrrr_available and standard is not None:
        manifest["sources"]["hrrr"] = _state("hrrr", now, standard.run)
    elif settings.hrrr_smoke_enabled:
        hrrr_error = provider_errors.get("hrrr") or RuntimeError("HRRR is unavailable")
        _fallback_source(manifest, previous, "hrrr", "hrrr", now, hrrr_error)
        manifest.pop("hrrr", None)
    else:
        manifest["sources"]["hrrr"] = _state(
            "hrrr", now, None, status="unavailable", error="HRRR_SMOKE_ENABLED is off"
        )

    firework_public = {
        "modelId": "firework",
        "variable": "firework_wildfire_pm25",
        "modelRun": firework_run,
        "units": "µg/m³ PM2.5 from wildfire smoke",
        "nativeResolutionKm": 10,
        "ingestMethod": "geomet-wcs-native",
        "processingVersion": FIREWORK_NATIVE_PROCESSING_VERSION,
        "horizonHours": 72,
        "frames": [{"validTime": valid, "modelRun": firework_run} for valid in firework_native],
    } if firework_available else prior_forecasts.get("firework") or {"frames": []}
    if hrrr_available and standard is not None:
        hrrr_public = {
            **hrrr_run_metadata(standard, extended),
            "processingVersion": HRRR_NATIVE_PROCESSING_VERSION,
            "frames": [
                {"validTime": iso_utc(cycle.run + timedelta(hours=hour)), "modelRun": iso_utc(cycle.run), "processingVersion": HRRR_NATIVE_PROCESSING_VERSION}
                for cycle, hour in hrrr_jobs
            ],
        }
    else:
        hrrr_public = prior_forecasts.get("hrrr") or {"frames": []}

    if jobs:
        native_cache.checkpoint()
    if not firework_available and not hrrr_available:
        errors = "; ".join(f"{name}: {error}" for name, error in provider_errors.items())
        raise RuntimeError(errors or "no native forecast provider is available")

    v8_grid = detail_grid_for_version(8)
    reuse_ok = _reusable_v8_best(previous_best, hrrr_available, firework_available)
    hrrr_target_xy = None
    hrrr_target_grid = None
    frames = []
    for valid in required:
        if budget and budget.remaining() <= 0:
            metrics = current_metrics()
            if metrics:
                metrics.increment("budget_gate_failures")
            raise RuntimeError("v8 composition deadline reached")
        contributors = []
        hrrr_run = hrrr_by_valid.get(valid) if hrrr_available else None
        if hrrr_run:
            contributors.append({"source": "hrrr", "modelRun": hrrr_run})
        if firework_available and firework_run is not None:
            contributors.append({"source": "firework", "modelRun": firework_run})
        prior = prior_frames.get(valid) if reuse_ok else None
        if prior and prior.get("contributors") == contributors and len(prior.get("detailTiles") or []) == 16:
            frames.append(_publish_integrated_frame(store, prior))
            continue
        fw_field = native_cache.get(
            model_id="firework", model_run=firework_run, valid_time=valid,
            processing_version=FIREWORK_NATIVE_PROCESSING_VERSION,
        ) if firework_available and firework_run is not None else None
        hr_field = native_cache.get(
            model_id="hrrr", model_run=hrrr_run, valid_time=valid,
            processing_version=HRRR_NATIVE_PROCESSING_VERSION,
        ) if hrrr_run else None
        if (firework_available and fw_field is None) or (hrrr_run and hr_field is None):
            raise ValueError("v8 native field disappeared before composition")
        if hr_field is not None:
            if not isinstance(hr_field.grid, HrrrGrid):
                raise ValueError("v8 HRRR projection is invalid")
            if hrrr_target_grid is None:
                hrrr_target_grid = hr_field.grid
                hrrr_target_xy = v8_hrrr_target_coordinates(hrrr_target_grid)
            elif hr_field.grid != hrrr_target_grid:
                raise ValueError("v8 HRRR grid changed within the stitched outlook")
        texture, mask, tiles = compose_v8_frame(fw_field, hr_field, hrrr_target_xy=hrrr_target_xy)
        frames.append(_publish_integrated_frame(store, {
            "validTime": valid,
            "modelRun": hrrr_run or firework_run,
            "texturePng": texture,
            "maskPng": mask,
            "detailTilePngs": tiles,
            "contributors": contributors,
        }))
        metrics = current_metrics()
        if metrics:
            metrics.increment("detail_frames_composed")
        del fw_field, hr_field, texture, mask, tiles

    best_model_run = iso_utc(standard.run) if hrrr_available and standard is not None else firework_run
    if best_model_run is None:
        raise RuntimeError("no native forecast model run is available")
    best = {
        "modelId": "best",
        "variable": "best_available_smoke",
        "units": "µg/m³",
        "nativeResolutionKm": None,
        "ingestMethod": "compose",
        "horizonHours": 36,
        "selectionPolicy": BEST_POLICY_VERSION,
        "featherDistanceKm": FEATHER_DISTANCE_KM,
        "sourceMaskVersion": SOURCE_MASK_VERSION,
        "coveragePolicy": COVERAGE_POLICY,
        "coastalBufferKm": COASTAL_BUFFER_KM,
        "coverageMaskVersion": coverage_mask_version_for_version(8),
        "fireworkProcessingVersion": FIREWORK_NATIVE_PROCESSING_VERSION if firework_available else None,
        "hrrrProcessingVersion": HRRR_NATIVE_PROCESSING_VERSION if hrrr_available else None,
        "paletteVersion": V8_PALETTE_VERSION,
        "detailGrid": v8_grid,
        "rasterProcessingVersion": V8_DETAIL_RASTER_PROCESSING_VERSION,
        "incompleteNumeric": False,
        "integratedStatus": "complete",
        "modelRun": best_model_run,
        "legendUrl": _legend_url(store, previous_best, legend_png(palette_version=V8_PALETTE_VERSION)),
        "frames": frames,
    }
    manifest["forecasts"] = {"firework": firework_public, "hrrr": hrrr_public, "best": best}
    manifest["forecast"] = best


def _fill_missing_values(
    settings: Settings,
    run: dict[str, Any] | None,
    source: str,
    times: set[str],
    field_cache: FieldCache | None = None,
    *,
    encode_png: bool = False,
    allow_network: bool = True,
    raster_grid: RasterGrid = BASE_RASTER_GRID,
) -> dict[str, Any] | None:
    if not run:
        return None
    frames = [dict(frame) for frame in run.get("frames", [])]
    pending: list[tuple[int, dict[str, Any]]] = []
    processing_version = FIREWORK_PROCESSING_VERSION if source == "firework" else PROCESSING_VERSION
    for index, item in enumerate(frames):
        valid = str(item.get("validTime") or "")
        if valid not in times or item.get("values") is not None:
            continue
        model_run = str(item.get("modelRun") or run.get("modelRun") or "")
        cached = field_cache.get(
            model_id=source,
            model_run=model_run,
            valid_time=valid,
            processing_version=processing_version,
            grid_version=raster_grid.version,
        ) if field_cache else None
        if cached is not None:
            item["values"], item["valid"] = cached
            continue
        pending.append((index, item))
    if not allow_network:
        return {**run, "frames": frames}

    def _refetch(item: tuple[int, dict[str, Any]]) -> tuple[int, dict[str, Any]]:
        index, frame = item
        model_run = str(frame.get("modelRun") or run.get("modelRun") or "")
        valid = str(frame.get("validTime") or "")
        filled = (
            fetch_firework_hour(settings, model_run, valid, encode_png=encode_png, raster_grid=raster_grid)
            if source == "firework"
            else fetch_hrrr_hour(settings, model_run, valid, encode_png=encode_png, raster_grid=raster_grid)
        )
        if field_cache and filled.get("values") is not None:
            field_cache.put(
                filled["values"],
                filled["valid"],
                model_id=source,
                model_run=model_run,
                valid_time=valid,
                processing_version=processing_version,
                grid_version=raster_grid.version,
            )
        return index, {key: filled[key] for key in ("values", "valid", "png") if key in filled}

    workers = settings.firework_concurrency if source == "firework" else settings.hrrr_concurrency
    host = "geo.weather.gc.ca" if source == "firework" else "nomads.ncep.noaa.gov"
    for result in map_isolated_batches(
        pending,
        _refetch,
        workers=max(1, workers),
        stop_after=lambda batch: any(
            isinstance(item, HttpFetchError) and item.provider_outage for item in batch
        ),
        before_batch=lambda: (
            HttpFetchError("ingest acquisition deadline reached", host=host, provider_outage=True)
            if current_budget() and current_budget().acquisition_remaining() <= 0
            else None
        ),
    ):
        if isinstance(result, BaseException):
            LOGGER.warning("%s numeric refetch failed: %s", source, redact(str(result)))
            continue
        index, updates = result
        frames[index].update(updates)
    return {**run, "frames": frames}

def _attach_forecasts(
    manifest: dict[str, Any],
    store: FrameStore,
    previous: dict[str, Any] | None,
    now: datetime,
    settings: Settings,
    firework: dict[str, Any] | None,
    hrrr: dict[str, Any] | None,
    field_cache: FieldCache | None = None,
    encode_png: bool = False,
    network_sources: dict[str, bool] | None = None,
) -> None:
    contract_version = published_context_version(settings)
    capabilities = context_capabilities(contract_version)
    raster_grid = DETAIL_RASTER_GRID if capabilities["detailTilesRequired"] else BASE_RASTER_GRID
    detail = bool(capabilities["detailTilesRequired"])
    metadata_only = not bool(capabilities["sourceTexturesPublic"])
    legacy_mask = not bool(capabilities["outlookMetadataRequired"])
    horizon_hours = outlook_horizon_for_version(contract_version)
    network_sources = network_sources or {"firework": True, "hrrr": True}
    if firework:
        manifest["forecasts"]["firework"] = _public_forecast_run(firework, metadata_only=metadata_only)
    else:
        manifest["forecasts"]["firework"] = {"frames": []}
    if not settings.hrrr_smoke_enabled:
        manifest["forecasts"]["hrrr"] = {"frames": []}
        hrrr = None
    elif hrrr:
        manifest["forecasts"]["hrrr"] = _public_forecast_run(hrrr, metadata_only=metadata_only)
    else:
        manifest["forecasts"]["hrrr"] = {"frames": []}
    previous_best = ((previous or {}).get("forecasts") or {}).get("best")
    if not settings.hrrr_smoke_enabled and previous_best and (
        previous_best.get("hrrrProcessingVersion")
        or any(
            contributor.get("source") == "hrrr"
            for frame in previous_best.get("frames", [])
            for contributor in frame.get("contributors", [])
            if isinstance(contributor, dict)
        )
    ):
        previous_best = None
    expected_times = [iso_utc(hour) for hour in canonical_hours(now, horizon_hours)]
    try:
        if (
            previous_best is not None
            and not legacy_mask
            and bool((previous_best or {}).get("detailGrid")) == detail
            and same_forecast_inputs(
                previous,
                firework,
                hrrr,
                tuple(iso_utc(hour) for hour in canonical_hours(now, horizon_hours)),
            )
        ):
            best = {**previous_best, "integratedStatus": previous_best.get("integratedStatus") or "complete"}
            metrics = current_metrics()
            if metrics:
                metrics.set_forecast_window(expected_times, [str(frame.get("validTime")) for frame in best.get("frames", [])])
        else:
            budget = current_budget()
            if budget and not budget.allow(COMPOSE_MIN_SECONDS, reason="best"):
                best = _retain_complete_integrated(previous_best, now, legacy_mask=legacy_mask, detail=detail, horizon_hours=horizon_hours)
            else:
                needed = hours_needing_png(
                    firework,
                    hrrr,
                    previous_best,
                    now,
                    legacy_mask=legacy_mask,
                    horizon_hours=horizon_hours,
                    raster_grid=raster_grid,
                )
                if encode_png:
                    firework = _hydrate_frames(store, firework, times=needed)
                    hrrr = _hydrate_frames(store, hrrr, times=needed)
                refill_jobs = [
                    ("firework", firework, network_sources.get("firework", False)),
                    ("hrrr", hrrr, network_sources.get("hrrr", False)),
                ]

                def refill(item: tuple[str, dict[str, Any] | None, bool]) -> tuple[str, dict[str, Any] | None]:
                    source, run, allow_network = item
                    try:
                        filled = _fill_missing_values(
                            settings, run, source, needed, field_cache,
                            encode_png=encode_png, allow_network=allow_network, raster_grid=raster_grid,
                        )
                    except Exception as exc:
                        LOGGER.warning("%s numeric refill failed: %s", source, redact(str(exc)))
                        filled = run
                    return source, filled

                refilled = dict(map_isolated(refill_jobs, refill, workers=2))
                firework = refilled.get("firework")
                hrrr = refilled.get("hrrr")
                with timed_phase("composition"):
                    composed = compose_best_run(
                        firework,
                        hrrr,
                        now,
                        previous_best=previous_best,
                        legacy_mask=legacy_mask,
                        horizon_hours=horizon_hours,
                        raster_grid=raster_grid,
                    )
                metrics = current_metrics()
                if metrics:
                    metrics.set_forecast_window(expected_times, [str(frame.get("validTime")) for frame in composed.get("frames", [])])
                if composed.get("incompleteNumeric") or len(composed.get("frames", [])) != horizon_hours + 1:
                    best = _retain_complete_integrated(previous_best, now, legacy_mask=legacy_mask, detail=detail, horizon_hours=horizon_hours)
                else:
                    frames = _publish_integrated_frames(store, composed["frames"])
                    best = {
                        "modelId": "best",
                        "variable": "best_available_smoke",
                        "units": composed["units"],
                        "nativeResolutionKm": None,
                        "ingestMethod": "compose",
                        "horizonHours": horizon_hours,
                        "selectionPolicy": composed.get("selectionPolicy") or BEST_POLICY_VERSION,
                        "featherDistanceKm": composed["featherDistanceKm"],
                        "sourceMaskVersion": composed["sourceMaskVersion"],
                        "coveragePolicy": composed.get("coveragePolicy") or COVERAGE_POLICY,
                        "coastalBufferKm": composed.get("coastalBufferKm", COASTAL_BUFFER_KM),
                        "coverageMaskVersion": composed.get("coverageMaskVersion") or COVERAGE_MASK_VERSION,
                        "fireworkProcessingVersion": composed.get("fireworkProcessingVersion"),
                        "hrrrProcessingVersion": composed.get("hrrrProcessingVersion"),
                        "paletteVersion": PALETTE_VERSION,
                        **({
                            "detailGrid": composed["detailGrid"],
                            "rasterProcessingVersion": composed["rasterProcessingVersion"],
                        } if detail else {}),
                        "incompleteNumeric": False,
                        "integratedStatus": "complete",
                        "modelRun": composed["modelRun"],
                        "legendUrl": _legend_url(store, previous_best, composed["legendPng"]),
                        "frames": frames,
                    }
        published = _public_forecast_run(best)
        manifest["forecasts"]["best"] = published
        manifest["forecast"] = published if published.get("frames") else _public_forecast_run(
            _empty_integrated(legacy_mask=legacy_mask, detail=detail, horizon_hours=horizon_hours)
        )
    except Exception as exc:
        metrics = current_metrics()
        if detail and metrics:
            metrics.increment("detail_failures")
        LOGGER.warning("best available compose failed: %s", redact(str(exc)))
        retained = _public_forecast_run(
            _retain_complete_integrated(previous_best, now, legacy_mask=legacy_mask, detail=detail, horizon_hours=horizon_hours)
        )
        manifest["forecasts"]["best"] = retained
        manifest["forecast"] = retained
    manifest["forecast"] = _public_forecast_run(
        manifest.get("forecast") or _empty_integrated(legacy_mask=legacy_mask, detail=detail, horizon_hours=horizon_hours)
    )
    manifest["forecasts"]["firework"] = _public_forecast_run(
        firework or manifest["forecasts"].get("firework") or {"frames": []},
        metadata_only=metadata_only,
    )
    manifest["forecasts"]["hrrr"] = _public_forecast_run(
        hrrr or manifest["forecasts"].get("hrrr") or {"frames": []},
        metadata_only=metadata_only,
    )
    manifest["forecasts"].setdefault(
        "best", _public_forecast_run(_empty_integrated(legacy_mask=legacy_mask, detail=detail, horizon_hours=horizon_hours))
    )


def attach_legacy_forecasts(
    manifest: dict[str, Any],
    store: FrameStore,
    previous: dict[str, Any] | None,
    now: datetime,
    settings: Settings,
    field_cache: FieldCache,
    contract_version: int,
    raster_grid: RasterGrid,
    encode_png: bool,
    required_times: set[str],
) -> None:
    capabilities = context_capabilities(contract_version)

    def _cache_get(model_id: str, model_run: str, valid_time: str, processing_version: str) -> tuple[np.ndarray, np.ndarray] | None:
        return field_cache.get(
            model_id=model_id,
            model_run=model_run,
            valid_time=valid_time,
            processing_version=processing_version,
            grid_version=raster_grid.version,
        )

    checkpoint_counts = {"firework": 0, "hrrr": 0}
    checkpoint_lock = threading.Lock()

    def _cache_put(frame: dict[str, Any], model_id: str, processing_version: str, checkpoint_every: int | None = None) -> None:
        values = frame.get("values")
        valid = frame.get("valid")
        if values is None or valid is None:
            return
        field_cache.put(
            values,
            valid,
            model_id=model_id,
            model_run=str(frame.get("modelRun") or ""),
            valid_time=str(frame.get("validTime") or ""),
            processing_version=processing_version,
            grid_version=raster_grid.version,
        )
        if checkpoint_every:
            with checkpoint_lock:
                checkpoint_counts[model_id] += 1
                checkpoint = checkpoint_counts[model_id] % checkpoint_every == 0
            if checkpoint:
                field_cache.checkpoint()

    def build_firework() -> tuple[dict[str, Any], datetime]:
        with timed_provider_phase("firework", "discovery"):
            discovery = discover_firework(settings)
        model_run, valid_times = discovery
        native_times = firework_native_times(valid_times)
        acquisition_times = firework_acquisition_times(native_times, now, encode_png=encode_png)
        numeric_times = set(acquisition_times) if encode_png else set(acquisition_times) & required_times
        reuse: dict[str, dict[str, Any]] = {}
        with timed_provider_phase("firework", "cache"):
            for valid in native_times:
                if valid not in numeric_times:
                    continue
                cached = _cache_get("firework", model_run, valid, FIREWORK_PROCESSING_VERSION)
                if cached is None:
                    continue
                values, valid_mask = cached
                item = {"validTime": valid, "modelRun": model_run, "values": values, "valid": valid_mask}
                if encode_png:
                    item["png"] = encode_official_concentration(values, valid_mask)
                reuse[valid] = item
        metrics = current_metrics()
        if metrics:
            metrics.increment("numeric_frames_planned", len(numeric_times))
            metrics.increment("numeric_frames_skipped", len(native_times) - len(numeric_times))
            metrics.add_provider_count("firework", "planned", len(numeric_times))
            metrics.add_provider_count("firework", "skipped", len(native_times) - len(numeric_times))
        downloaded = 0
        downloaded_lock = threading.Lock()

        def cache_frame(frame: dict[str, Any]) -> None:
            nonlocal downloaded
            _cache_put(frame, "firework", FIREWORK_PROCESSING_VERSION, settings.firework_concurrency)
            with downloaded_lock:
                downloaded += 1

        with timed_provider_phase("firework", "queue"):
            model_run, frames, legend = fetch_firework(
                settings,
                now,
                (model_run, native_times),
                reuse=reuse,
                required_times=numeric_times,
                on_frame=cache_frame,
                encode_png=encode_png,
                raster_grid=raster_grid,
            )
        if downloaded:
            field_cache.checkpoint()
        if metrics:
            metrics.increment("forecast_downloaded", downloaded)
            metrics.increment("forecast_reused", len(reuse))
            if raster_grid == DETAIL_RASTER_GRID:
                metrics.increment("detail_fetches", downloaded)
                metrics.increment("detail_cache_hits", len(reuse))
            metrics.add_provider_count("firework", "downloaded", downloaded)
            metrics.add_provider_count("firework", "reused", len(reuse))
            if not encode_png:
                metrics.increment("source_pngs_skipped", len(numeric_times))
        if capabilities["canonicalHourlyOutlook"]:
            _validate_staged_provider("FireWork", frames, required_times)
        last_valid = datetime.fromisoformat(frames[-1]["validTime"].replace("Z", "+00:00"))
        if last_valid < now:
            raise PermissionError("FireWork forecast has expired")
        published = _publish_source_frames(store, frames, "firework.png", encode_png)
        legend_url = _put_asset(store, "firework-legend.png", legend, "image/png") if encode_png else None
        return _firework_run(model_run, published, legend_url), datetime.fromisoformat(model_run.replace("Z", "+00:00"))

    def build_hrrr() -> tuple[dict[str, Any], datetime]:
        if not settings.hrrr_smoke_enabled:
            raise PermissionError("HRRR_SMOKE_ENABLED is off")
        from ingest.sources.hrrr import planned_hours
        with timed_provider_phase("hrrr", "discovery"):
            discovery = discover_hrrr(settings, now)
        standard, extended = discovery
        jobs = planned_hours(standard, extended)
        numeric_jobs = jobs if encode_png else [
            item for item in jobs if iso_utc(item[0].run + timedelta(hours=item[1])) in required_times
        ]
        reuse: dict[tuple[str, int], dict[str, Any]] = {}
        with timed_provider_phase("hrrr", "cache"):
            for cycle, hour in numeric_jobs:
                model_run = iso_utc(cycle.run)
                valid = iso_utc(cycle.run + timedelta(hours=hour))
                cached = _cache_get("hrrr", model_run, valid, PROCESSING_VERSION)
                if cached is None:
                    continue
                values, valid_mask = cached
                item = {"validTime": valid, "modelRun": model_run, "values": values, "valid": valid_mask, "processingVersion": PROCESSING_VERSION}
                if encode_png:
                    item["png"] = encode_official_concentration(values, valid_mask)
                reuse[(model_run, hour)] = item
        metrics = current_metrics()
        if metrics:
            metrics.increment("numeric_frames_planned", len(numeric_jobs))
            metrics.increment("numeric_frames_skipped", len(jobs) - len(numeric_jobs))
            metrics.add_provider_count("hrrr", "planned", len(numeric_jobs))
            metrics.add_provider_count("hrrr", "skipped", len(jobs) - len(numeric_jobs))

        def cache_frame(frame: dict[str, Any]) -> None:
            _cache_put(frame, "hrrr", PROCESSING_VERSION, settings.hrrr_concurrency)

        with timed_provider_phase("hrrr", "queue"):
            frames, legend, stats = fetch_hrrr(
                settings,
                now,
                discovery,
                reuse,
                required_times={iso_utc(cycle.run + timedelta(hours=hour)) for cycle, hour in numeric_jobs},
                on_frame=cache_frame,
                encode_png=encode_png,
                raster_grid=raster_grid,
            )
        if int(stats.get("downloaded") or 0):
            field_cache.checkpoint()
        metrics = current_metrics()
        if metrics:
            metrics.increment("forecast_downloaded", int(stats.get("downloaded") or 0))
            metrics.increment("forecast_reused", int(stats.get("reused") or 0))
            if raster_grid == DETAIL_RASTER_GRID:
                metrics.increment("detail_fetches", int(stats.get("downloaded") or 0))
                metrics.increment("detail_cache_hits", int(stats.get("reused") or 0))
            metrics.add_provider_count("hrrr", "downloaded", int(stats.get("downloaded") or 0))
            metrics.add_provider_count("hrrr", "reused", int(stats.get("reused") or 0))
            if not encode_png:
                metrics.increment("source_pngs_skipped", len(numeric_jobs))
        if capabilities["canonicalHourlyOutlook"]:
            _validate_staged_provider("HRRR", frames, required_times)
        published = _publish_source_frames(store, frames, "hrrr.png", encode_png)
        meta = hrrr_run_metadata(standard, extended)
        return {
            **meta,
            "units": PALETTE_UNITS + " smoke concentration at 8 m AGL",
            "legendUrl": _put_asset(store, "hrrr-legend.png", legend, "image/png") if encode_png else None,
            "frames": published,
        }, standard.run

    if contract_version == 1:
        with timed_source("firework"):
            _run_source(manifest, previous, "firework", "forecast", now, build_firework)
        manifest.pop("forecasts", None)
        manifest["version"] = 1
        return

    budget = current_budget()
    if budget and not budget.allow(FORECAST_WAVE_MIN_SECONDS + COMPOSE_MIN_SECONDS, reason="forecast-wave"):
        _fallback_source(manifest, previous, "firework", "forecast", now, RuntimeError("ingest deadline"))
        if settings.hrrr_smoke_enabled:
            _fallback_source(manifest, previous, "hrrr", "hrrr", now, RuntimeError("ingest deadline"))
        else:
            manifest["sources"]["hrrr"] = _state("hrrr", now, None, status="unavailable", error="HRRR_SMOKE_ENABLED is off")
        firework = manifest.get("forecast")
        hrrr = manifest.pop("hrrr", ((previous or {}).get("forecasts") or {}).get("hrrr"))
        _attach_forecasts(
            manifest, store, previous, now, settings, firework, hrrr, field_cache, encode_png,
            network_sources={"firework": False, "hrrr": False},
        )
        return

    jobs: list[tuple[ForecastProviderName, Callable[[], tuple[dict[str, Any], datetime]]]] = [
        ("firework", build_firework),
    ]
    if settings.hrrr_smoke_enabled:
        jobs.append(("hrrr", build_hrrr))
    else:
        manifest["sources"]["hrrr"] = _state("hrrr", now, None, status="unavailable", error="HRRR_SMOKE_ENABLED is off")

    wave_started = time.perf_counter()

    def _forecast_job(
        item: tuple[ForecastProviderName, Callable[[], tuple[dict[str, Any], datetime]]],
    ) -> tuple[ForecastProviderName, SourceOutcome]:
        name, builder = item
        started = time.perf_counter() - wave_started
        with timed_source(name):
            try:
                payload, observed = builder()
                result: SourceOutcome = SourceSuccess(payload, observed)
            except Exception as exc:
                result = SourceFailure(exc)
        metrics = current_metrics()
        if metrics:
            metrics.add_phase(f"{name}StartOffset", started)
            metrics.add_phase(f"{name}FinishOffset", time.perf_counter() - wave_started)
        return name, result

    raw_forecast_results = map_isolated(jobs, _forecast_job, workers=min(2, len(jobs)))
    outer_error = next((result for result in raw_forecast_results if isinstance(result, BaseException)), None)
    if outer_error:
        raise RuntimeError(f"forecast worker failed: {outer_error}")
    forecast_results: dict[ForecastProviderName, SourceOutcome] = dict(
        result for result in raw_forecast_results if isinstance(result, tuple)
    )
    metrics = current_metrics()
    if metrics:
        metrics.add_phase("forecastWave", time.perf_counter() - wave_started)

    firework_result = forecast_results["firework"]
    firework_network_ok = isinstance(firework_result, SourceSuccess)
    if isinstance(firework_result, SourceFailure):
        error = firework_result.error
        LOGGER.warning("firework context source failed: %s", redact(str(error)))
        if isinstance(error, HttpFetchError) and error.provider_outage and metrics:
            metrics.increment("provider_circuit_opens")
        _fallback_source(manifest, previous, "firework", "forecast", now, error)
    else:
        manifest["forecast"] = firework_result.payload
        manifest["sources"]["firework"] = _state("firework", now, firework_result.observed_at)

    hrrr = None
    hrrr_network_ok = False
    if settings.hrrr_smoke_enabled:
        hrrr_result = forecast_results["hrrr"]
        hrrr_network_ok = isinstance(hrrr_result, SourceSuccess)
        if isinstance(hrrr_result, SourceFailure):
            error = hrrr_result.error
            LOGGER.warning("hrrr context source failed: %s", redact(str(error)))
            if isinstance(error, HttpFetchError) and error.provider_outage and metrics:
                metrics.increment("provider_circuit_opens")
            _fallback_source(manifest, previous, "hrrr", "hrrr", now, error)
            hrrr = manifest.pop("hrrr", None)
        else:
            hrrr = hrrr_result.payload
            manifest["sources"]["hrrr"] = _state("hrrr", now, hrrr_result.observed_at)
    _attach_forecasts(
        manifest, store, previous, now, settings, manifest.get("forecast"), hrrr, field_cache, encode_png,
        network_sources={"firework": firework_network_ok, "hrrr": hrrr_network_ok},
    )
    return
