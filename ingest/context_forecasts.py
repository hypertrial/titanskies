from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from ingest.auth import redact
from ingest.config import Settings
from ingest.context_contracts import (
    DETAIL_GRID,
    DETAIL_RASTER_PROCESSING_VERSION,
    coverage_mask_version_for_version,
    detail_grid_for_version,
    detail_raster_processing_version_for_version,
    detail_tile_count_for_version,
    iso_utc,
    palette_version_for_version,
    uses_native_forecast_fields,
)
from ingest.context_publish import legend_url, publish_integrated_frames
from ingest.context_sources import fallback_source, source_state
from ingest.context_types import (
    ForecastProviderName,
    ProviderStageFailure,
    ProviderStageOutcome,
    ProviderStageSuccess,
)
from ingest.forecast_composer import (
    BEST_POLICY_VERSION,
    COASTAL_BUFFER_KM,
    COVERAGE_MASK_VERSION,
    COVERAGE_POLICY,
    DETAIL_COVERAGE_MASK_VERSION,
    FEATHER_DISTANCE_KM,
    PUBLIC_OUTLOOK_HORIZON_HOURS,
    SOURCE_MASK_VERSION,
    canonical_hours,
    compose_v8_frame,
    v8_hrrr_target_coordinates,
)
from ingest.forecast_native_cache import NativeFieldCache
from ingest.forecast_palette import PALETTE_VERSION, V8_PALETTE_VERSION, legend_png
from ingest.forecast_publication import (
    best_run_metadata,
    firework_run_metadata,
    publish_or_reuse_v8_frame,
    reusable_v8_best,
)
from ingest.forecast_publication import (
    hrrr_run_metadata as published_hrrr_run_metadata,
)
from ingest.forecast_raster import PROCESSING_VERSION, HrrrGrid
from ingest.http import HttpFetchError
from ingest.http_pool import map_bounded, map_isolated, map_isolated_batches
from ingest.local_store import FrameStore
from ingest.perf import (
    acquisition_reserve,
    current_budget,
    current_metrics,
    timed_provider_phase,
)
from ingest.sources.firework import (
    FIREWORK_NATIVE_PROCESSING_VERSION,
    FIREWORK_PROCESSING_VERSION,
    discover_firework,
    fetch_firework_native_hour,
    firework_native_times,
)
from ingest.sources.hrrr import (
    HRRR_NATIVE_PROCESSING_VERSION,
    discover_hrrr,
    fetch_hrrr_native_hour,
    planned_hours,
)

LOGGER = logging.getLogger("titanskies.context")


def _forecast_frame_runs(run: dict[str, Any] | None) -> tuple[tuple[str, object | None], ...]:
    return tuple(
        (str(frame["validTime"]), frame.get("modelRun"))
        for frame in (run or {}).get("frames", [])
        if isinstance(frame, dict) and frame.get("validTime")
    )


def _forecast_valid_times(run: dict[str, Any] | None) -> tuple[str, ...]:
    return tuple(valid for valid, _model_run in _forecast_frame_runs(run))


def complete_integrated(
    run: dict[str, Any] | None,
    *,
    require_versions: bool = False,
    require_detail: bool = False,
    horizon_hours: int | None = None,
    contract_version: int = 7,
) -> bool:
    if not run or not run.get("frames"):
        return False
    expected_horizon = horizon_hours if horizon_hours is not None else PUBLIC_OUTLOOK_HORIZON_HOURS
    if run.get("horizonHours") != expected_horizon:
        return False
    if run.get("selectionPolicy") != BEST_POLICY_VERSION or run.get("incompleteNumeric"):
        return False
    expected_mask = SOURCE_MASK_VERSION
    if (
        run.get("featherDistanceKm") != FEATHER_DISTANCE_KM
        or run.get("paletteVersion") != palette_version_for_version(contract_version)
        or run.get("sourceMaskVersion") != expected_mask
        or run.get("coveragePolicy") != COVERAGE_POLICY
        or run.get("coastalBufferKm") != COASTAL_BUFFER_KM
        or run.get("coverageMaskVersion")
        != (coverage_mask_version_for_version(contract_version) if require_detail else COVERAGE_MASK_VERSION)
    ):
        return False
    if require_detail:
        if run.get("detailGrid") != detail_grid_for_version(contract_version) or run.get(
            "rasterProcessingVersion"
        ) != detail_raster_processing_version_for_version(contract_version):
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
        return ("firework" not in sources or firework_version == expected_firework) and (
            "hrrr" not in sources or hrrr_version == expected_hrrr
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


def _empty_integrated(*, detail: bool = False, horizon_hours: int | None = None) -> dict[str, Any]:
    horizon = horizon_hours if horizon_hours is not None else PUBLIC_OUTLOOK_HORIZON_HOURS
    return {
        "modelId": "best",
        "variable": "best_available_smoke",
        "ingestMethod": "compose",
        "horizonHours": horizon,
        "selectionPolicy": BEST_POLICY_VERSION,
        "featherDistanceKm": FEATHER_DISTANCE_KM,
        "paletteVersion": PALETTE_VERSION,
        "sourceMaskVersion": SOURCE_MASK_VERSION,
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
    detail: bool = False,
    horizon_hours: int | None = None,
    contract_version: int = 8,
) -> dict[str, Any]:
    if (
        previous_best is not None
        and complete_integrated(
            previous_best,
            require_versions=True,
            require_detail=detail,
            horizon_hours=horizon_hours,
            contract_version=contract_version,
        )
        and _integrated_still_valid(previous_best, now)
    ):
        retained = dict(previous_best)
        retained["integratedStatus"] = "retained"
        return retained
    return _empty_integrated(detail=detail, horizon_hours=horizon_hours)


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
        raise ValueError(f"{provider} staged cycle is incomplete for the public horizon (missing={len(missing)}, invalid={len(invalid)})")


def attach_v8_forecasts(
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
    hrrr_by_valid = {iso_utc(cycle.run + timedelta(hours=hour)): iso_utc(cycle.run) for cycle, hour in hrrr_jobs}
    if settings.hrrr_smoke_enabled and "hrrr" not in provider_errors and any(valid not in hrrr_by_valid for valid in required):
        provider_errors["hrrr"] = ValueError("HRRR native cycle is incomplete for v8")

    checkpoint_lock = threading.Lock()
    checkpoint_counts = {"firework": 0, "hrrr": 0}
    previous_best = ((previous or {}).get("forecasts") or {}).get("best")
    prior_frames = {frame.get("validTime"): frame for frame in (previous_best or {}).get("frames", [])}
    planned_hrrr = settings.hrrr_smoke_enabled and "hrrr" not in provider_errors
    planned_firework = "firework" not in provider_errors
    reusable_staging = reusable_v8_best(previous_best, planned_hrrr, planned_firework)

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
            model_id="firework",
            model_run=firework_run,
            valid_time=valid,
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
            field,
            model_id="firework",
            model_run=firework_run,
            valid_time=valid,
            processing_version=FIREWORK_NATIVE_PROCESSING_VERSION,
        )
        checkpoint("firework", settings.firework_concurrency)

    def stage_hrrr(valid: str) -> None:
        model_run = hrrr_by_valid[valid]
        cached = native_cache.get(
            model_id="hrrr",
            model_run=model_run,
            valid_time=valid,
            processing_version=HRRR_NATIVE_PROCESSING_VERSION,
            remember=needs_staged_bytes(valid),
        )
        if cached is not None:
            return
        if budget and budget.remaining() <= 120:
            raise RuntimeError("v8 acquisition deadline reached")
        field = fetch_hrrr_native_hour(settings, model_run, valid)
        native_cache.put(
            field,
            model_id="hrrr",
            model_run=model_run,
            valid_time=valid,
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
            stop_after=lambda batch: any(isinstance(result, HttpFetchError) and result.provider_outage for result in batch),
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
            provider_errors[outcome.provider] = RuntimeError(f"{outcome.provider} native staging failed: {outcome.error}")
    for provider, error in provider_errors.items():
        LOGGER.warning("%s native forecast failed: %s", provider, redact(str(error)))

    firework_available = firework_run is not None and "firework" not in provider_errors
    hrrr_available = standard is not None and "hrrr" not in provider_errors
    prior_forecasts = (previous or {}).get("forecasts") or {} if (previous or {}).get("version") == 8 else {}

    if firework_available and firework_run is not None:
        manifest["sources"]["firework"] = source_state("firework", now, datetime.fromisoformat(firework_run.replace("Z", "+00:00")))
    else:
        firework_error = provider_errors.get("firework") or RuntimeError("FireWork is unavailable")
        fallback_source(manifest, previous, "firework", "firework", now, firework_error)
        manifest.pop("firework", None)
    if hrrr_available and standard is not None:
        manifest["sources"]["hrrr"] = source_state("hrrr", now, standard.run)
    elif settings.hrrr_smoke_enabled:
        hrrr_error = provider_errors.get("hrrr") or RuntimeError("HRRR is unavailable")
        fallback_source(manifest, previous, "hrrr", "hrrr", now, hrrr_error)
        manifest.pop("hrrr", None)
    else:
        manifest["sources"]["hrrr"] = source_state("hrrr", now, None, status="unavailable", error="HRRR_SMOKE_ENABLED is off")

    firework_public = (
        firework_run_metadata(
            firework_run,
            [{"validTime": valid, "modelRun": firework_run} for valid in firework_native],
            ingest_method="geomet-wcs-native",
        )
        if firework_available
        else prior_forecasts.get("firework") or {"frames": []}
    )
    if hrrr_available and standard is not None:
        hrrr_public = {
            **published_hrrr_run_metadata(standard, extended),
            "frames": [
                {
                    "validTime": iso_utc(cycle.run + timedelta(hours=hour)),
                    "modelRun": iso_utc(cycle.run),
                    "processingVersion": HRRR_NATIVE_PROCESSING_VERSION,
                }
                for cycle, hour in hrrr_jobs
            ],
        }
    else:
        hrrr_public = prior_forecasts.get("hrrr") or {"frames": []}

    def publish_retained() -> bool:
        retained = _retain_complete_integrated(previous_best, now, detail=True, horizon_hours=36, contract_version=8)
        if retained.get("integratedStatus") != "retained":
            return False
        manifest["forecasts"] = {"firework": firework_public, "hrrr": hrrr_public, "best": retained}
        manifest["forecast"] = retained
        return True

    if jobs:
        native_cache.checkpoint()
    if not firework_available and not hrrr_available:
        errors = "; ".join(f"{name}: {error}" for name, error in provider_errors.items())
        if publish_retained():
            return
        raise RuntimeError(errors or "no native forecast provider is available")

    reuse_ok = reusable_v8_best(previous_best, hrrr_available, firework_available)
    hrrr_target_xy = None
    hrrr_target_grid = None

    def contributors_for(valid: str) -> list[dict[str, Any]]:
        contributors = []
        hrrr_run = hrrr_by_valid.get(valid) if hrrr_available else None
        if hrrr_run:
            contributors.append({"source": "hrrr", "modelRun": hrrr_run})
        if firework_available and firework_run is not None:
            contributors.append({"source": "firework", "modelRun": firework_run})
        return contributors

    def load_fields(valid: str) -> tuple[Any, Any, str | None]:
        hrrr_run = hrrr_by_valid.get(valid) if hrrr_available else None
        fw_field = (
            native_cache.get(
                model_id="firework",
                model_run=firework_run,
                valid_time=valid,
                processing_version=FIREWORK_NATIVE_PROCESSING_VERSION,
            )
            if firework_available and firework_run is not None
            else None
        )
        hr_field = (
            native_cache.get(
                model_id="hrrr",
                model_run=hrrr_run,
                valid_time=valid,
                processing_version=HRRR_NATIVE_PROCESSING_VERSION,
            )
            if hrrr_run
            else None
        )
        if (firework_available and fw_field is None) or (hrrr_run and hr_field is None):
            raise ValueError("v8 native field disappeared before composition")
        return fw_field, hr_field, hrrr_run

    def bind_hrrr_target(hr_field: Any) -> None:
        nonlocal hrrr_target_xy, hrrr_target_grid
        if hr_field is None:
            return
        if not isinstance(hr_field.grid, HrrrGrid):
            raise ValueError("v8 HRRR projection is invalid")
        if hrrr_target_grid is None:
            hrrr_target_grid = hr_field.grid
            hrrr_target_xy = v8_hrrr_target_coordinates(hrrr_target_grid)
        elif hr_field.grid != hrrr_target_grid:
            raise ValueError("v8 HRRR grid changed within the stitched outlook")

    def compose_pair(valid: str) -> tuple[bytes, bytes, list[dict[str, Any]]]:
        fw_field, hr_field, _hrrr_run = load_fields(valid)
        bind_hrrr_target(hr_field)
        texture, mask, tiles = compose_v8_frame(fw_field, hr_field, hrrr_target_xy=hrrr_target_xy)
        metrics = current_metrics()
        if metrics:
            metrics.increment("detail_frames_composed")
        return texture, mask, tiles

    def compose_unpublished(valid: str) -> dict[str, Any]:
        if budget and budget.remaining() <= 0:
            metrics = current_metrics()
            if metrics:
                metrics.increment("budget_gate_failures")
            raise RuntimeError("v8 composition deadline reached")
        contributors = contributors_for(valid)
        prior = prior_frames.get(valid) if reuse_ok else None
        if prior and prior.get("contributors") == contributors and len(prior.get("detailTiles") or []) == 16:
            return prior
        fw_field, hr_field, hrrr_run = load_fields(valid)
        bind_hrrr_target(hr_field)
        texture, mask, tiles = compose_v8_frame(fw_field, hr_field, hrrr_target_xy=hrrr_target_xy)
        metrics = current_metrics()
        if metrics:
            metrics.increment("detail_frames_composed")
        del fw_field, hr_field
        return {
            "validTime": valid,
            "modelRun": hrrr_run or firework_run,
            "texturePng": texture,
            "maskPng": mask,
            "detailTilePngs": tiles,
            "contributors": contributors,
        }

    try:
        if settings.compose_concurrency > 1:
            if hrrr_available:
                for valid in required:
                    _fw_field, hr_field, _hrrr_run = load_fields(valid)
                    if hr_field is not None:
                        bind_hrrr_target(hr_field)
                        break
            unpublished = map_bounded(required, compose_unpublished, workers=settings.compose_concurrency)
            frames = publish_integrated_frames(store, unpublished)
            frames.sort(key=lambda frame: str(frame.get("validTime") or ""))
        else:
            frames = []
            for valid in required:
                if budget and budget.remaining() <= 0:
                    metrics = current_metrics()
                    if metrics:
                        metrics.increment("budget_gate_failures")
                    raise RuntimeError("v8 composition deadline reached")
                hrrr_run = hrrr_by_valid.get(valid) if hrrr_available else None

                def compose(current: str = valid) -> tuple[bytes, bytes, list[dict[str, Any]]]:
                    return compose_pair(current)

                frames.append(
                    publish_or_reuse_v8_frame(
                        store,
                        valid=valid,
                        model_run=hrrr_run or firework_run,
                        contributors=contributors_for(valid),
                        prior=prior_frames.get(valid) if reuse_ok else None,
                        compose=compose,
                    )
                )
    except ValueError as exc:
        if str(exc) == "v8 native field disappeared before composition" and publish_retained():
            return
        raise

    best_model_run = iso_utc(standard.run) if hrrr_available and standard is not None else firework_run
    if best_model_run is None:
        raise RuntimeError("no native forecast model run is available")
    best = best_run_metadata(
        model_run=best_model_run,
        frames=frames,
        firework_processing_version=FIREWORK_NATIVE_PROCESSING_VERSION if firework_available else None,
        hrrr_processing_version=HRRR_NATIVE_PROCESSING_VERSION if hrrr_available else None,
        legend_url=legend_url(store, previous_best, legend_png(palette_version=V8_PALETTE_VERSION)),
    )
    manifest["forecasts"] = {"firework": firework_public, "hrrr": hrrr_public, "best": best}
    manifest["forecast"] = best
