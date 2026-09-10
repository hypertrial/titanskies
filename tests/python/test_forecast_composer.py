from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from PIL import Image
import io

from ingest import forecast_composer
from ingest.context_contracts import CONTEXT_HEIGHT, CONTEXT_WIDTH
from ingest.forecast_composer import (
    BEST_POLICY_VERSION,
    COASTAL_BUFFER_KM,
    COVERAGE_MASK_VERSION,
    COVERAGE_POLICY,
    FEATHER_DISTANCE_KM,
    SOURCE_MASK_VERSION,
    compose_best_frame,
    compose_best_run,
    compose_best_values,
    compose_v8_frame,
    canonical_hours,
    coastal_coverage_mask,
    hours_needing_png,
)
from ingest.forecast_native_cache import GeographicGrid, NativeField
from ingest.forecast_palette import MASK_COMBINED, MASK_FIREWORK, MASK_HRRR, MASK_NONE, PALETTE_VERSION, colorize_concentration, decode_mask_png
from ingest.forecast_raster import RasterGrid, domain_mask, hrrr_edge_weights


def test_v8_tiles_share_exact_edges_and_base_is_every_fourth_global_sample() -> None:
    yy, xx = np.mgrid[:40, :64]
    values = (2 + xx * 2 + yy * 3).astype(np.float32)
    native = NativeField(
        values,
        np.ones_like(values, dtype=bool),
        GeographicGrid(64, 40, -145, 72, 100 / 63, 62 / 39),
    )
    base_png, base_mask_png, tiles = compose_v8_frame(native, None)
    assert [(tile["column"], tile["row"]) for tile in tiles] == [
        (column, row) for row in range(4) for column in range(4)
    ]
    decoded = [np.asarray(Image.open(io.BytesIO(tile["texturePng"])).convert("RGBA")) for tile in tiles]
    decoded_masks = [decode_mask_png(tile["maskPng"]) for tile in tiles]
    assert all(image.shape == (635, 1024, 4) for image in decoded)
    for row in range(4):
        for column in range(3):
            left = decoded[row * 4 + column]
            right = decoded[row * 4 + column + 1]
            assert np.array_equal(left[:, -1], right[:, 0])
            assert np.array_equal(decoded_masks[row * 4 + column][:, -1], decoded_masks[row * 4 + column + 1][:, 0])
    for row in range(3):
        for column in range(4):
            north = decoded[row * 4 + column]
            south = decoded[(row + 1) * 4 + column]
            assert np.array_equal(north[-1], south[0])
            assert np.array_equal(decoded_masks[row * 4 + column][-1], decoded_masks[(row + 1) * 4 + column][0])

    full = np.empty((2537, 4093, 4), dtype=np.uint8)
    full_mask = np.empty((2537, 4093), dtype=np.uint8)
    for tile, image, mask in zip(tiles, decoded, decoded_masks):
        x0 = tile["column"] * 1023
        y0 = tile["row"] * 634
        full[y0:y0 + 635, x0:x0 + 1024] = image
        full_mask[y0:y0 + 635, x0:x0 + 1024] = mask
    base = np.asarray(Image.open(io.BytesIO(base_png)).convert("RGBA"))
    assert np.array_equal(base, full[::4, ::4])
    assert np.array_equal(decode_mask_png(base_mask_png), full_mask[::4, ::4])


def _field(value: float, valid: np.ndarray | None = None) -> np.ndarray:
    values = np.full((CONTEXT_HEIGHT, CONTEXT_WIDTH), value, dtype=np.float32)
    if valid is not None:
        values = np.where(valid, values, np.nan)
    return values


def _outlook_metadata() -> dict[str, object]:
    return {
        "selectionPolicy": BEST_POLICY_VERSION,
        "featherDistanceKm": FEATHER_DISTANCE_KM,
        "paletteVersion": PALETTE_VERSION,
        "sourceMaskVersion": SOURCE_MASK_VERSION,
        "coveragePolicy": COVERAGE_POLICY,
        "coastalBufferKm": COASTAL_BUFFER_KM,
        "coverageMaskVersion": COVERAGE_MASK_VERSION,
        "horizonHours": 24,
    }


def test_canonical_hours_floor_partial_hour_and_keep_25_frames() -> None:
    now = datetime(2024, 7, 15, 20, 37, 45, tzinfo=timezone.utc)
    hours = canonical_hours(now, 24)
    assert len(hours) == 25
    assert hours[0] == datetime(2024, 7, 15, 20, tzinfo=timezone.utc)
    assert hours[-1] == datetime(2024, 7, 16, 20, tzinfo=timezone.utc)


@pytest.mark.parametrize("source", ["firework", "hrrr"])
def test_36_hour_outlook_accepts_one_numeric_provider_per_canonical_hour(source: str) -> None:
    now = datetime(2024, 7, 15, 20, 37, tzinfo=timezone.utc)
    start = now.replace(minute=0)
    frames = [
        {
            "validTime": (start + timedelta(hours=hour)).isoformat().replace("+00:00", "Z"),
            "modelRun": start.isoformat().replace("+00:00", "Z"),
            "values": np.full((2, 2), 10 + hour, dtype=np.float32),
            "valid": np.ones((2, 2), dtype=bool),
        }
        for hour in range(37)
    ]
    run = {
        "modelRun": start.isoformat().replace("+00:00", "Z"),
        "processingVersion": "test-version",
        "units": "µg/m³",
        "frames": frames,
    }
    best = compose_best_run(
        run if source == "firework" else None,
        run if source == "hrrr" else None,
        now,
        inside_hrrr=np.ones((2, 2), dtype=bool),
        coverage_mask=np.ones((2, 2), dtype=bool),
        horizon_hours=36,
        raster_grid=RasterGrid(2, 2, "test-grid"),
    )

    assert best["horizonHours"] == 36
    assert len(best["frames"]) == 37
    assert best["incompleteNumeric"] is False
    assert all([item["source"] for item in frame["contributors"]] == [source] for frame in best["frames"])


def test_feathered_max_smoothly_introduces_a_higher_hrrr_value() -> None:
    inside = np.ones((CONTEXT_HEIGHT, CONTEXT_WIDTH), dtype=bool)
    weights = np.zeros((CONTEXT_HEIGHT, CONTEXT_WIDTH), dtype=np.float32)
    weights[0, 1] = 0.5
    weights[0, 2] = 1.0
    merged, codes = compose_best_values(_field(20), _field(100), inside_hrrr=inside, edge_weight=weights)
    assert tuple(merged[0, :3]) == (20, 60, 100)
    assert tuple(codes[0, :3]) == (MASK_FIREWORK, MASK_COMBINED, MASK_COMBINED)


def test_native_hrrr_edge_weights_reach_zero_and_full() -> None:
    distance = np.array([0.0, 100.0, 200.0], dtype=np.float32)
    t = np.clip(distance / FEATHER_DISTANCE_KM, 0, 1)
    weights = t * t * (3 - 2 * t)
    assert tuple(weights) == (0.0, 0.5, 1.0)
    native = hrrr_edge_weights()
    assert float(native.min()) == 0.0
    assert float(native.max()) == 1.0


def test_constant_fields_stay_continuous_and_color_identically_at_the_hrrr_edge() -> None:
    inside = np.zeros((CONTEXT_HEIGHT, CONTEXT_WIDTH), dtype=bool)
    inside[:, 1:] = True
    distance = np.maximum(np.arange(CONTEXT_WIDTH, dtype=np.float32) - 1, 0)
    t = np.clip(distance / FEATHER_DISTANCE_KM, 0, 1)
    row_weights = t * t * (3 - 2 * t)
    weights = np.broadcast_to(row_weights, inside.shape)
    merged, _codes = compose_best_values(_field(20), _field(100), inside_hrrr=inside, edge_weight=weights)
    assert merged[0, 0] == merged[0, 1] == 20
    assert float(np.max(np.diff(merged[0, :202]))) < 1
    colors = colorize_concentration(merged, np.isfinite(merged))
    assert tuple(colors[0, 0]) == tuple(colors[0, 1])


def test_feathered_max_marks_positive_hrrr_contributions() -> None:
    inside = domain_mask()
    weights = hrrr_edge_weights()
    firework = _field(20)
    hrrr = _field(40, inside)
    texture, mask, codes = compose_best_frame(firework, hrrr, inside_hrrr=inside)
    assert np.all(codes[inside & (weights > 0)] == MASK_COMBINED)
    assert np.all(codes[inside & (weights == 0)] == MASK_FIREWORK)
    assert MASK_FIREWORK in set(codes[~inside].tolist()) if np.any(~inside) else True
    assert decode_mask_png(mask).shape == (CONTEXT_HEIGHT, CONTEXT_WIDTH)
    assert texture.startswith(b"\x89PNG")


def test_feathered_max_uses_firework_when_it_is_higher() -> None:
    inside = domain_mask()
    firework = _field(80)
    hrrr = _field(10, inside)
    _texture, _mask, codes = compose_best_frame(firework, hrrr, inside_hrrr=inside)
    assert np.all(codes[inside] == MASK_FIREWORK)
    assert np.all(codes[~inside] == MASK_FIREWORK) if np.any(~inside) else True


def test_feathered_max_ties_go_to_firework() -> None:
    inside = domain_mask()
    values = _field(25)
    _texture, _mask, codes = compose_best_frame(values, np.where(inside, values, np.nan), inside_hrrr=inside)
    assert np.all(codes[inside] == MASK_FIREWORK)


def test_missing_is_not_treated_as_zero() -> None:
    inside = domain_mask()
    firework = np.full((CONTEXT_HEIGHT, CONTEXT_WIDTH), np.nan, dtype=np.float32)
    firework[~inside] = 15
    hrrr = np.full((CONTEXT_HEIGHT, CONTEXT_WIDTH), np.nan, dtype=np.float32)
    hrrr[inside] = 0
    _texture, _mask, codes = compose_best_frame(firework, hrrr, inside_hrrr=inside)
    assert np.all(codes[inside] == MASK_HRRR)
    if np.any(~inside):
        assert np.all(codes[~inside] == MASK_FIREWORK)


def test_invalid_hrrr_hole_falls_back_to_firework() -> None:
    inside = np.ones((CONTEXT_HEIGHT, CONTEXT_WIDTH), dtype=bool)
    weights = np.ones((CONTEXT_HEIGHT, CONTEXT_WIDTH), dtype=np.float32)
    firework = _field(30)
    hrrr = _field(90)
    hrrr[2, 3] = np.nan
    merged, codes = compose_best_values(firework, hrrr, inside_hrrr=inside, edge_weight=weights)
    assert merged[2, 3] == 30
    assert codes[2, 3] == MASK_FIREWORK


@pytest.mark.parametrize("sources", ["both", "firework", "hrrr"])
def test_coastal_coverage_suppresses_all_source_combinations(sources) -> None:
    coverage = np.ones((CONTEXT_HEIGHT, CONTEXT_WIDTH), dtype=bool)
    coverage[2, 3] = False
    firework = None if sources == "hrrr" else _field(20)
    hrrr = None if sources == "firework" else _field(40)
    merged, codes = compose_best_values(
        firework,
        hrrr,
        inside_hrrr=np.ones(coverage.shape, dtype=bool),
        edge_weight=np.ones(coverage.shape, dtype=np.float32),
        coverage_mask=coverage,
    )
    assert np.isnan(merged[2, 3])
    assert codes[2, 3] == MASK_NONE


def test_coastal_coverage_is_encoded_as_transparent_no_data() -> None:
    coverage = np.ones((CONTEXT_HEIGHT, CONTEXT_WIDTH), dtype=bool)
    coverage[2, 3] = False
    texture, mask, _codes = compose_best_frame(_field(20), None, coverage_mask=coverage)
    rgba = np.asarray(Image.open(io.BytesIO(texture)).convert("RGBA"))
    assert tuple(rgba[2, 3]) == (0, 0, 0, 0)
    assert decode_mask_png(mask)[2, 3] == MASK_NONE


def test_coastal_coverage_loader_is_binary_sized_cached_and_read_only() -> None:
    coverage = coastal_coverage_mask()
    assert coverage.shape == (CONTEXT_HEIGHT, CONTEXT_WIDTH)
    assert coverage.dtype == np.bool_
    assert np.any(coverage) and np.any(~coverage)
    assert not coverage.flags.writeable
    assert coastal_coverage_mask() is coverage


@pytest.mark.parametrize("values", [
    np.zeros((2, 2), dtype=np.uint8),
    np.zeros((CONTEXT_HEIGHT, CONTEXT_WIDTH), dtype=np.uint8),
    np.full((CONTEXT_HEIGHT, CONTEXT_WIDTH), 127, dtype=np.uint8),
])
def test_coastal_coverage_loader_rejects_invalid_assets(tmp_path, monkeypatch, values) -> None:
    path = tmp_path / "coverage.png"
    Image.fromarray(values, mode="L").save(path)
    monkeypatch.setattr(forecast_composer, "_COVERAGE_MASK_PATH", path)
    coastal_coverage_mask.cache_clear()
    try:
        with pytest.raises(ValueError, match="coastal coverage mask"):
            coastal_coverage_mask()
    finally:
        coastal_coverage_mask.cache_clear()


def test_coastal_coverage_loader_rejects_a_missing_asset(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(forecast_composer, "_COVERAGE_MASK_PATH", tmp_path / "missing.png")
    coastal_coverage_mask.cache_clear()
    try:
        with pytest.raises(FileNotFoundError):
            coastal_coverage_mask()
    finally:
        coastal_coverage_mask.cache_clear()


def test_hrrr_only_after_firework_is_absent() -> None:
    inside = domain_mask()
    hrrr = _field(12, inside)
    _texture, _mask, codes = compose_best_frame(None, hrrr, inside_hrrr=inside)
    assert set(np.unique(codes[inside])) == {MASK_HRRR}
    if np.any(~inside):
        assert set(np.unique(codes[~inside])) <= {MASK_NONE}


def test_compose_run_uses_hrrr_then_firework_hours() -> None:
    now = datetime(2024, 7, 15, 20, tzinfo=timezone.utc)
    inside = domain_mask()
    firework = {
        "modelRun": "2024-07-15T20:00:00Z",
        "units": "µg/m³",
        "frames": [
            {"validTime": "2024-07-15T20:00:00Z", "modelRun": "2024-07-15T20:00:00Z", "values": _field(20)},
            {"validTime": "2024-07-16T20:00:00Z", "modelRun": "2024-07-15T20:00:00Z", "values": _field(20)},
        ],
    }
    hrrr = {
        "modelRun": "2024-07-15T20:00:00Z",
        "units": "µg/m³",
        "frames": [
            {"validTime": "2024-07-15T20:00:00Z", "modelRun": "2024-07-15T20:00:00Z", "values": _field(40, inside)},
        ],
    }
    best = compose_best_run(firework, hrrr, now)
    times = [frame["validTime"] for frame in best["frames"]]
    assert "2024-07-15T20:00:00Z" in times
    assert "2024-07-16T20:00:00Z" in times
    assert best["selectionPolicy"] == "feathered-max-v1"
    assert best["featherDistanceKm"] == 200
    assert best["paletteVersion"] == "titanskies-smoke-display-v1"
    assert best["sourceMaskVersion"] == "contribution-v2"
    assert best["coveragePolicy"] == COVERAGE_POLICY
    assert best["coastalBufferKm"] == COASTAL_BUFFER_KM
    assert best["coverageMaskVersion"] == COVERAGE_MASK_VERSION


def test_compose_run_reuses_unchanged_hours_and_invalidates_policy_changes() -> None:
    now = datetime(2024, 7, 15, 20, tzinfo=timezone.utc)
    firework = {
        "modelRun": "2024-07-15T20:00:00Z",
        "units": "µg/m³",
        "frames": [
            {"validTime": "2024-07-15T20:00:00Z", "modelRun": "2024-07-15T20:00:00Z", "values": _field(20)},
            {"validTime": "2024-07-16T20:00:00Z", "modelRun": "2024-07-15T20:00:00Z", "values": _field(20)},
        ],
    }
    hrrr = {
        "modelRun": "2024-07-15T20:00:00Z",
        "units": "µg/m³",
        "frames": [
            {"validTime": "2024-07-15T20:00:00Z", "modelRun": "2024-07-15T20:00:00Z", "values": _field(40)},
        ],
    }
    previous = {
        **_outlook_metadata(),
        "frames": [
            {
                "validTime": "2024-07-15T20:00:00Z",
                "modelRun": "2024-07-15T20:00:00Z",
                "textureUrl": "/old-best.png",
                "sourceMaskUrl": "/old-mask.png",
                "contributors": [
                    {"source": "hrrr", "modelRun": "2024-07-15T20:00:00Z"},
                    {"source": "firework", "modelRun": "2024-07-15T20:00:00Z"},
                ],
            }
        ],
    }
    assert hours_needing_png(firework, hrrr, previous, now) == {"2024-07-16T20:00:00Z"}
    best = compose_best_run(firework, hrrr, now, previous_best=previous)
    reused = next(frame for frame in best["frames"] if frame["validTime"] == "2024-07-15T20:00:00Z")
    changed = next(frame for frame in best["frames"] if frame["validTime"] == "2024-07-16T20:00:00Z")
    assert reused["textureUrl"] == "/old-best.png"
    assert "texturePng" not in reused
    assert "texturePng" in changed
    stale_values = {
        "selectionPolicy": "domain-priority-v1",
        "featherDistanceKm": 150,
        "paletteVersion": "old-display-palette",
        "sourceMaskVersion": "winner-v1",
        "coveragePolicy": "legacy-coverage",
        "coastalBufferKm": 150,
        "coverageMaskVersion": "legacy-mask",
        "horizonHours": 72,
    }
    for field, value in stale_values.items():
        stale = {**previous, field: value}
        assert "2024-07-15T20:00:00Z" in hours_needing_png(firework, hrrr, stale, now)
        rebuilt = compose_best_run(firework, hrrr, now, previous_best=stale)
        first = next(frame for frame in rebuilt["frames"] if frame["validTime"] == "2024-07-15T20:00:00Z")
        assert first.get("textureUrl") != "/old-best.png"
        assert "texturePng" in first


def test_compose_run_invalidates_source_processing_version_changes() -> None:
    now = datetime(2024, 7, 15, 20, tzinfo=timezone.utc)
    firework = {
        "modelRun": "2024-07-15T20:00:00Z",
        "processingVersion": "firework-wcs-ug-v1",
        "frames": [
            {"validTime": "2024-07-15T20:00:00Z", "modelRun": "2024-07-15T20:00:00Z", "values": _field(20)},
        ],
    }
    hrrr = {
        "modelRun": "2024-07-15T20:00:00Z",
        "processingVersion": "hrrr-massden-ug-v3",
        "frames": [
            {"validTime": "2024-07-15T20:00:00Z", "modelRun": "2024-07-15T20:00:00Z", "values": _field(40)},
        ],
    }
    previous = {
        **_outlook_metadata(),
        "fireworkProcessingVersion": "firework-wcs-ug-v1",
        "hrrrProcessingVersion": "hrrr-massden-ug-v3",
        "frames": [
            {
                "validTime": "2024-07-15T20:00:00Z",
                "modelRun": "2024-07-15T20:00:00Z",
                "textureUrl": "/old-best.png",
                "sourceMaskUrl": "/old-mask.png",
                "contributors": [
                    {"source": "hrrr", "modelRun": "2024-07-15T20:00:00Z"},
                    {"source": "firework", "modelRun": "2024-07-15T20:00:00Z"},
                ],
            }
        ],
    }
    assert hours_needing_png(firework, hrrr, previous, now) == set()
    stale_firework = {**firework, "processingVersion": "firework-sld-kg-v1"}
    assert "2024-07-15T20:00:00Z" in hours_needing_png(stale_firework, hrrr, previous, now)
    rebuilt = compose_best_run(stale_firework, hrrr, now, previous_best=previous)
    first = next(frame for frame in rebuilt["frames"] if frame["validTime"] == "2024-07-15T20:00:00Z")
    assert first.get("textureUrl") != "/old-best.png"
    assert "texturePng" in first
    assert rebuilt["fireworkProcessingVersion"] == "firework-sld-kg-v1"
    assert rebuilt["hrrrProcessingVersion"] == "hrrr-massden-ug-v3"


def test_wms_only_firework_is_never_a_numeric_contributor() -> None:
    now = datetime(2024, 7, 15, 20, tzinfo=timezone.utc)
    firework = {
        "modelRun": "2024-07-15T20:00:00Z",
        "processingVersion": "firework-wcs-ug-v2",
        "frames": [{
            "validTime": "2024-07-15T20:00:00Z",
            "modelRun": "2024-07-15T20:00:00Z",
            "png": b"\x89PNG",
            "textureUrl": "/firework.png",
        }],
    }
    best = compose_best_run(firework, None, now)
    assert best["incompleteNumeric"] is True
    assert best["frames"][0]["contributors"] == []
    assert "texturePng" in best["frames"][0]
    assert best["frames"][0].get("textureUrl") != "/firework.png"


def test_valueless_firework_is_not_a_best_contributor() -> None:
    now = datetime(2024, 7, 15, 20, tzinfo=timezone.utc)
    inside = domain_mask()
    firework = {
        "modelRun": "2024-07-15T20:00:00Z",
        "processingVersion": "firework-wcs-ug-v1",
        "frames": [{"validTime": "2024-07-15T20:00:00Z", "modelRun": "2024-07-15T20:00:00Z"}],
    }
    hrrr = {
        "modelRun": "2024-07-15T20:00:00Z",
        "processingVersion": "hrrr-massden-ug-v3",
        "frames": [{"validTime": "2024-07-15T20:00:00Z", "modelRun": "2024-07-15T20:00:00Z", "values": _field(40, inside)}],
    }
    best = compose_best_run(firework, hrrr, now)
    assert best["incompleteNumeric"] is True
    assert [item["source"] for item in best["frames"][0]["contributors"]] == ["hrrr"]


def test_incomplete_numeric_best_is_not_reused() -> None:
    now = datetime(2024, 7, 15, 20, tzinfo=timezone.utc)
    firework = {
        "modelRun": "2024-07-15T20:00:00Z",
        "processingVersion": "firework-wcs-ug-v1",
        "frames": [{"validTime": "2024-07-15T20:00:00Z", "modelRun": "2024-07-15T20:00:00Z", "values": _field(20)}],
    }
    hrrr = {
        "modelRun": "2024-07-15T20:00:00Z",
        "processingVersion": "hrrr-massden-ug-v3",
        "frames": [{"validTime": "2024-07-15T20:00:00Z", "modelRun": "2024-07-15T20:00:00Z", "values": _field(40)}],
    }
    previous = {
        **_outlook_metadata(),
        "fireworkProcessingVersion": "firework-wcs-ug-v1",
        "hrrrProcessingVersion": "hrrr-massden-ug-v3",
        "incompleteNumeric": True,
        "frames": [
            {
                "validTime": "2024-07-15T20:00:00Z",
                "modelRun": "2024-07-15T20:00:00Z",
                "textureUrl": "/old-best.png",
                "sourceMaskUrl": "/old-mask.png",
                "contributors": [
                    {"source": "hrrr", "modelRun": "2024-07-15T20:00:00Z"},
                    {"source": "firework", "modelRun": "2024-07-15T20:00:00Z"},
                ],
            }
        ],
    }
    assert "2024-07-15T20:00:00Z" in hours_needing_png(firework, hrrr, previous, now)
    rebuilt = compose_best_run(firework, hrrr, now, previous_best=previous)
    first = next(frame for frame in rebuilt["frames"] if frame["validTime"] == "2024-07-15T20:00:00Z")
    assert first.get("textureUrl") != "/old-best.png"
    assert rebuilt["incompleteNumeric"] is False


def test_indexed_mask_png_roundtrips_to_the_same_rgba_labels() -> None:
    from ingest.forecast_palette import encode_mask_png, mask_rgba

    codes = np.zeros((CONTEXT_HEIGHT, CONTEXT_WIDTH), dtype=np.uint8)
    codes[10, 10] = MASK_FIREWORK
    codes[20, 20] = MASK_HRRR
    codes[30, 30] = MASK_COMBINED
    encoded = encode_mask_png(codes)
    image = Image.open(io.BytesIO(encoded))
    assert image.mode == "P"
    assert image.info.get("transparency") == 0
    decoded = decode_mask_png(encoded)
    assert decoded[10, 10] == MASK_FIREWORK
    assert decoded[20, 20] == MASK_HRRR
    assert decoded[30, 30] == MASK_COMBINED
    assert decoded[0, 0] == MASK_NONE
    rgba = np.asarray(image.convert("RGBA"))
    assert tuple(rgba[10, 10]) == mask_rgba(MASK_FIREWORK)
    assert tuple(rgba[20, 20]) == mask_rgba(MASK_HRRR)
    assert tuple(rgba[30, 30]) == mask_rgba(MASK_COMBINED)
    assert rgba[0, 0, 3] == 0

    legacy = decode_mask_png(encode_mask_png(codes, legacy=True))
    assert legacy[30, 30] == MASK_HRRR


@pytest.mark.parametrize("providers", ["both", "firework", "hrrr"])
def test_sparse_v8_composition_matches_dense_numeric_reference(providers):
    from unittest.mock import patch
    from tests.python.support import fixture_field
    from ingest.forecast_composer import reproject_native, v8_hrrr_target_coordinates, _v8_tile_grid, _v8_tile_lonlat
    from ingest.forecast_raster import V8_DETAIL_RASTER_GRID
    from ingest.forecast_palette import encode_concentration
    fw = fixture_field("firework", "run", "valid") if providers != "hrrr" else None
    hr = fixture_field("hrrr", "run", "valid") if providers != "firework" else None
    for field in (fw, hr):
        if field is not None:
            field.values[12:15, 20:25] = np.nan
            field.values[20:23, 15:18] = -1
            field.valid[30:33, 30:35] = False
    xy = v8_hrrr_target_coordinates(hr.grid) if hr else None
    expected = []
    coverage = coastal_coverage_mask(V8_DETAIL_RASTER_GRID)
    for row in range(4):
        for col in range(4):
            grid, x0, y0 = _v8_tile_grid(col, row)
            f, fv = reproject_native(fw, grid, target_lonlat=_v8_tile_lonlat(x0, y0))
            tile_xy = tuple(a[y0:y0+635, x0:x0+1024] for a in xy) if xy else None
            h, hv = reproject_native(hr, grid, hrrr_xy=tile_xy)
            if tile_xy:
                x, y = tile_xy
                inside = (x >= 0) & (y >= 0) & (x <= hr.grid.nx-1) & (y <= hr.grid.ny-1)
                distance = min(hr.grid.dx, hr.grid.dy)/1000 * np.minimum.reduce((x, hr.grid.nx-1-x, y, hr.grid.ny-1-y))
                t = np.clip(distance/FEATHER_DISTANCE_KM, 0, 1)
                weights = np.where(inside, t*t*(3-2*t), 0).astype(np.float32)
            else:
                inside, weights = domain_mask(raster_grid=grid), hrrr_edge_weights(raster_grid=grid)
            expected.append(compose_best_values(f, h, firework_valid=fv, hrrr_valid=hv, inside_hrrr=inside,
                edge_weight=weights, coverage_mask=coverage[y0:y0+635,x0:x0+1024], raster_grid=grid))
    calls = []
    def capture(values, valid, palette_version):
        i = len(calls)
        if i < 16:
            np.testing.assert_array_equal(values.view(np.uint32), expected[i][0].view(np.uint32))
        calls.append(1)
        return encode_concentration(values, valid, palette_version)
    with patch("ingest.forecast_composer.encode_concentration", side_effect=capture):
        _, _, tiles = compose_v8_frame(fw, hr, hrrr_target_xy=xy)
    assert len(calls) == 17
    for tile, (_, mask) in zip(tiles, expected):
        np.testing.assert_array_equal(decode_mask_png(tile["maskPng"]), mask)
    # The completely suppressed tile remains present and fully transparent.
    assert not np.asarray(Image.open(io.BytesIO(tiles[12]["texturePng"])).convert("RGBA"))[..., 3].any()


def test_indexed_png_fast_encoding_preserves_indexes_palette_and_alpha():
    from unittest.mock import patch
    from ingest.forecast_palette import encode_indexes, _COLOR_LUTS, V8_PALETTE_VERSION
    lut = _COLOR_LUTS[V8_PALETTE_VERSION]
    indexes = np.arange(len(lut), dtype=np.uint8).reshape(1, -1).repeat(4, axis=0)
    save = Image.Image.save
    def legacy(image, output, **kwargs):
        kwargs["optimize"] = True
        return save(image, output, **kwargs)
    with patch.object(Image.Image, "save", legacy):
        old = encode_indexes(indexes, lut)
    new = encode_indexes(indexes, lut)
    a, b = Image.open(io.BytesIO(old)), Image.open(io.BytesIO(new))
    assert a.mode == b.mode == "P"
    assert a.size == b.size
    assert a.getpalette() == b.getpalette()
    assert a.info["transparency"] == b.info["transparency"]
    np.testing.assert_array_equal(np.asarray(a), np.asarray(b))
    np.testing.assert_array_equal(np.asarray(a.convert("RGBA")), np.asarray(b.convert("RGBA")))
