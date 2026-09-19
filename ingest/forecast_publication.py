from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ingest.context_contracts import (
    V8_DETAIL_RASTER_PROCESSING_VERSION,
    coverage_mask_version_for_version,
    detail_grid_for_version,
)
from ingest.context_publish import publish_integrated_frame
from ingest.forecast_composer import (
    BEST_POLICY_VERSION,
    COASTAL_BUFFER_KM,
    COVERAGE_POLICY,
    FEATHER_DISTANCE_KM,
    SOURCE_MASK_VERSION,
)
from ingest.forecast_palette import V8_PALETTE_VERSION
from ingest.sources.firework import FIREWORK_NATIVE_PROCESSING_VERSION
from ingest.sources.hrrr import HRRR_NATIVE_PROCESSING_VERSION, HrrrCycle
from ingest.sources.hrrr import hrrr_run_metadata as provider_hrrr_run_metadata
from ingest.store import FrameStore


def firework_run_metadata(
    model_run: str | None,
    frames: list[dict[str, Any]],
    *,
    ingest_method: str,
) -> dict[str, Any]:
    return {
        "modelId": "firework",
        "variable": "firework_wildfire_pm25",
        "modelRun": model_run,
        "units": "µg/m³ PM2.5 from wildfire smoke",
        "nativeResolutionKm": 10,
        "ingestMethod": ingest_method,
        "processingVersion": FIREWORK_NATIVE_PROCESSING_VERSION,
        "horizonHours": 72,
        "frames": frames,
    }


def hrrr_run_metadata(standard: HrrrCycle, extended: HrrrCycle | None) -> dict[str, Any]:
    metadata = provider_hrrr_run_metadata(standard, extended)
    metadata["processingVersion"] = HRRR_NATIVE_PROCESSING_VERSION
    return metadata


def best_run_metadata(
    *,
    model_run: str | None,
    frames: list[dict[str, Any]],
    firework_processing_version: str | None,
    hrrr_processing_version: str | None,
    legend_url: str,
    units: str = "µg/m³",
    incomplete_numeric: bool = False,
) -> dict[str, Any]:
    return {
        "modelId": "best",
        "variable": "best_available_smoke",
        "units": units,
        "nativeResolutionKm": None,
        "ingestMethod": "compose",
        "horizonHours": 36,
        "selectionPolicy": BEST_POLICY_VERSION,
        "featherDistanceKm": FEATHER_DISTANCE_KM,
        "sourceMaskVersion": SOURCE_MASK_VERSION,
        "coveragePolicy": COVERAGE_POLICY,
        "coastalBufferKm": COASTAL_BUFFER_KM,
        "coverageMaskVersion": coverage_mask_version_for_version(8),
        "fireworkProcessingVersion": firework_processing_version,
        "hrrrProcessingVersion": hrrr_processing_version,
        "incompleteNumeric": incomplete_numeric,
        "integratedStatus": "complete",
        "paletteVersion": V8_PALETTE_VERSION,
        "detailGrid": detail_grid_for_version(8),
        "rasterProcessingVersion": V8_DETAIL_RASTER_PROCESSING_VERSION,
        "modelRun": model_run,
        "legendUrl": legend_url,
        "frames": frames,
    }


def reusable_v8_best(
    previous_best: dict[str, Any] | None,
    hrrr_available: bool,
    firework_available: bool = True,
) -> bool:
    return bool(
        previous_best
        and previous_best.get("detailGrid") == detail_grid_for_version(8)
        and previous_best.get("rasterProcessingVersion") == V8_DETAIL_RASTER_PROCESSING_VERSION
        and previous_best.get("paletteVersion") == V8_PALETTE_VERSION
        and previous_best.get("coverageMaskVersion") == coverage_mask_version_for_version(8)
        and previous_best.get("fireworkProcessingVersion") == (FIREWORK_NATIVE_PROCESSING_VERSION if firework_available else None)
        and previous_best.get("hrrrProcessingVersion") == (HRRR_NATIVE_PROCESSING_VERSION if hrrr_available else None)
        and previous_best.get("selectionPolicy") == BEST_POLICY_VERSION
        and previous_best.get("horizonHours") == 36
        and previous_best.get("featherDistanceKm") == FEATHER_DISTANCE_KM
        and previous_best.get("sourceMaskVersion") == SOURCE_MASK_VERSION
        and previous_best.get("coveragePolicy") == COVERAGE_POLICY
        and previous_best.get("coastalBufferKm") == COASTAL_BUFFER_KM
        and not previous_best.get("incompleteNumeric")
    )


def publish_or_reuse_v8_frame(
    store: FrameStore,
    *,
    valid: str,
    model_run: str | None,
    contributors: list[dict[str, Any]],
    prior: dict[str, Any] | None,
    compose: Callable[[], tuple[bytes, bytes, list[dict[str, Any]]]],
) -> dict[str, Any]:
    if prior and prior.get("contributors") == contributors and len(prior.get("detailTiles") or []) == 16:
        return publish_integrated_frame(store, prior)
    texture, mask, tiles = compose()
    return publish_integrated_frame(
        store,
        {
            "validTime": valid,
            "modelRun": model_run,
            "texturePng": texture,
            "maskPng": mask,
            "detailTilePngs": tiles,
            "contributors": contributors,
        },
    )
