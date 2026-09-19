from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest

from ingest.config import Settings
from ingest.context_contracts import (
    V8_DETAIL_RASTER_PROCESSING_VERSION,
    coverage_mask_version_for_version,
    detail_grid_for_version,
    iso_utc,
)
from ingest.context_forecasts import attach_v8_forecasts
from ingest.forecast_composer import (
    BEST_POLICY_VERSION,
    COASTAL_BUFFER_KM,
    COVERAGE_POLICY,
    FEATHER_DISTANCE_KM,
    SOURCE_MASK_VERSION,
    canonical_hours,
)
from ingest.forecast_native_cache import NativeFieldCache
from ingest.forecast_palette import V8_PALETTE_VERSION
from ingest.local_store import LocalFrameStore
from ingest.sources.firework import FIREWORK_NATIVE_PROCESSING_VERSION

NOW = datetime(2026, 9, 10, 10, tzinfo=UTC)


def _complete_best(now: datetime) -> dict:
    tiles = [
        {"column": column, "row": row, "textureUrl": f"t-{column}-{row}", "sourceMaskUrl": f"m-{column}-{row}"}
        for row in range(4)
        for column in range(4)
    ]
    model_run = iso_utc(now.replace(minute=0, second=0, microsecond=0))
    frames = [
        {
            "validTime": iso_utc(hour),
            "modelRun": model_run,
            "textureUrl": "tex",
            "sourceMaskUrl": "mask",
            "contributors": [{"source": "firework", "modelRun": model_run}],
            "detailTiles": tiles,
        }
        for hour in canonical_hours(now, 36)
    ]
    return {
        "modelId": "best",
        "variable": "best_available_smoke",
        "horizonHours": 36,
        "selectionPolicy": BEST_POLICY_VERSION,
        "featherDistanceKm": FEATHER_DISTANCE_KM,
        "paletteVersion": V8_PALETTE_VERSION,
        "sourceMaskVersion": SOURCE_MASK_VERSION,
        "coveragePolicy": COVERAGE_POLICY,
        "coastalBufferKm": COASTAL_BUFFER_KM,
        "coverageMaskVersion": coverage_mask_version_for_version(8),
        "detailGrid": detail_grid_for_version(8),
        "rasterProcessingVersion": V8_DETAIL_RASTER_PROCESSING_VERSION,
        "fireworkProcessingVersion": FIREWORK_NATIVE_PROCESSING_VERSION,
        "hrrrProcessingVersion": None,
        "incompleteNumeric": False,
        "integratedStatus": "complete",
        "frames": frames,
    }


def _attach(store: Any, now: Any, previous: Any) -> Any:
    settings = Settings(
        local_frame_dir=store.root,
        local_cache_dir=store.root / "cache",
        hrrr_smoke_enabled=False,
    )
    manifest: dict[str, Any] = {"sources": {}, "forecasts": {}, "forecast": {}}
    run = iso_utc(now.replace(minute=0, second=0, microsecond=0))
    hours = [iso_utc(hour) for hour in canonical_hours(now, 72)]
    with (
        patch("ingest.context_forecasts.discover_firework", return_value=(run, hours)),
        patch(
            "ingest.context_forecasts.fetch_firework_native_hour",
            side_effect=RuntimeError("v8 acquisition deadline reached"),
        ),
    ):
        attach_v8_forecasts(manifest, store, previous, now, settings, NativeFieldCache(store))
    return manifest


def test_budget_skip_retains_complete_best(tmp_path: Any) -> None:
    store = LocalFrameStore(tmp_path)
    previous_best = _complete_best(NOW)
    previous = {"version": 8, "forecasts": {"best": previous_best, "firework": {"frames": []}, "hrrr": {"frames": []}}}
    manifest = _attach(store, NOW, previous)
    assert manifest["forecast"]["integratedStatus"] == "retained"
    assert manifest["forecast"]["frames"] == previous_best["frames"]
    assert manifest["forecasts"]["best"]["integratedStatus"] == "retained"


def test_budget_skip_without_previous_best_fails(tmp_path: Any) -> None:
    store = LocalFrameStore(tmp_path)
    with pytest.raises(RuntimeError, match="v8 acquisition deadline reached"):
        _attach(store, NOW, None)
