from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ingest.context_contracts import (
    CONTEXT_HEIGHT,
    CONTEXT_WIDTH,
    DETAIL_GRID,
    DETAIL_RASTER_PROCESSING_VERSION,
    DETAIL_TILE_HEIGHT,
    DETAIL_TILE_WIDTH,
    context_capabilities,
    iso_utc,
)
from ingest.forecast_native_cache import GeographicGrid, NativeField
from ingest.forecast_palette import (
    MASK_COMBINED,
    MASK_FIREWORK,
    MASK_HRRR,
    MASK_NONE,
    PALETTE_VERSION,
    V8_PALETTE_VERSION,
    encode_concentration,
    encode_mask_png,
    legend_png,
)
from ingest.forecast_raster import (
    BASE_RASTER_GRID,
    DETAIL_RASTER_GRID,
    V8_DETAIL_RASTER_GRID,
    HrrrGrid,
    RasterGrid,
    _sample_bilinear_valid,
    bilinear_valid,
    domain_mask,
    hrrr_edge_weights,
    lonlat_to_hrrr_xy,
    reproject_hrrr,
)
from ingest.perf import current_metrics

BEST_POLICY_VERSION = "feathered-max-v1"
FEATHER_DISTANCE_KM = 200
SOURCE_MASK_VERSION = "contribution-v2"
LEGACY_SOURCE_MASK_VERSION = "winner-v1"
PUBLIC_OUTLOOK_HORIZON_HOURS = 24
V7_OUTLOOK_HORIZON_HOURS = 36
LEGACY_OUTLOOK_HORIZON_HOURS = 72
COASTAL_BUFFER_KM = 200
COVERAGE_POLICY = "land-plus-coastal-water-v1"
COVERAGE_MASK_VERSION = "natural-earth-ocean-50m-v1"
DETAIL_COVERAGE_MASK_VERSION = "natural-earth-ocean-50m-v2"
V8_DETAIL_COVERAGE_MASK_VERSION = "natural-earth-ocean-50m-v3"
_COVERAGE_MASK_PATH = Path(__file__).resolve().parent.parent / "shared" / "smoke-coverage-mask-v1.png"
_DETAIL_COVERAGE_MASK_PATH = Path(__file__).resolve().parent.parent / "shared" / "smoke-coverage-mask-v2.png"
_V8_DETAIL_COVERAGE_MASK_PATH = Path(__file__).resolve().parent.parent / "shared" / "smoke-coverage-mask-v3.png"


@lru_cache(maxsize=3)
def coastal_coverage_mask(raster_grid: RasterGrid = BASE_RASTER_GRID) -> np.ndarray:
    detail = raster_grid in {DETAIL_RASTER_GRID, V8_DETAIL_RASTER_GRID}
    width, height = raster_grid.width, raster_grid.height
    path = _V8_DETAIL_COVERAGE_MASK_PATH if raster_grid == V8_DETAIL_RASTER_GRID else _DETAIL_COVERAGE_MASK_PATH if detail else _COVERAGE_MASK_PATH
    with Image.open(path) as image:
        if image.size != (width, height):
            raise ValueError(f"coastal coverage mask must be {width}x{height}")
        pixels = np.asarray(image.convert("L"), dtype=np.uint8)
    if not np.all((pixels == 0) | (pixels == 255)) or not np.any(pixels == 0) or not np.any(pixels == 255):
        raise ValueError("coastal coverage mask must contain both 0 and 255 only")
    coverage = pixels == 255
    coverage.setflags(write=False)
    return coverage


def canonical_hours(now: datetime, horizon: int = 72) -> list[datetime]:
    start = now.replace(minute=0, second=0, microsecond=0)
    return [start + timedelta(hours=offset) for offset in range(horizon + 1)]


def outlook_horizon_for_version(version: int) -> int:
    return int(context_capabilities(version)["outlookHorizonHours"])


def _usable(values: np.ndarray | None, valid: np.ndarray | None, shape: tuple[int, int]) -> np.ndarray:
    if values is None:
        return np.zeros(shape, dtype=bool)
    finite = np.isfinite(values) & (values >= 0)
    if valid is not None:
        finite = finite & valid
    return finite


def compose_best_values(
    firework_values: np.ndarray | None,
    hrrr_values: np.ndarray | None,
    *,
    firework_valid: np.ndarray | None = None,
    hrrr_valid: np.ndarray | None = None,
    inside_hrrr: np.ndarray | None = None,
    edge_weight: np.ndarray | None = None,
    coverage_mask: np.ndarray | None = None,
    raster_grid: RasterGrid = BASE_RASTER_GRID,
) -> tuple[np.ndarray, np.ndarray]:
    shape = (raster_grid.height, raster_grid.width)
    inside = inside_hrrr if inside_hrrr is not None else domain_mask(HrrrGrid(), raster_grid)
    weights = edge_weight if edge_weight is not None else hrrr_edge_weights(HrrrGrid(), FEATHER_DISTANCE_KM, raster_grid)
    fw_ok = _usable(firework_values, firework_valid, shape)
    hr_ok = _usable(hrrr_values, hrrr_valid, shape) & inside
    merged = np.full(shape, np.nan, dtype=np.float32)
    codes = np.full(shape, MASK_NONE, dtype=np.uint8)
    if firework_values is not None:
        merged[fw_ok] = firework_values[fw_ok]
        codes[fw_ok] = MASK_FIREWORK
    if hrrr_values is not None:
        both = fw_ok & hr_ok
        hrrr_only = hr_ok & ~fw_ok
        merged[hrrr_only] = hrrr_values[hrrr_only]
        codes[hrrr_only] = MASK_HRRR
        if firework_values is not None:
            enhanced = both & (hrrr_values > firework_values) & (weights > 0)
            merged[both] = firework_values[both] + weights[both] * np.maximum(
                hrrr_values[both] - firework_values[both], 0
            )
            codes[enhanced] = MASK_COMBINED
    if coverage_mask is not None:
        coverage = np.asarray(coverage_mask, dtype=bool)
        if coverage.shape != merged.shape:
            raise ValueError(f"coverage mask must be {CONTEXT_WIDTH}x{CONTEXT_HEIGHT}")
        merged[~coverage] = np.nan
        codes[~coverage] = MASK_NONE
    return merged, codes


def compose_best_frame(
    firework_values: np.ndarray | None,
    hrrr_values: np.ndarray | None,
    *,
    firework_valid: np.ndarray | None = None,
    hrrr_valid: np.ndarray | None = None,
    inside_hrrr: np.ndarray | None = None,
    edge_weight: np.ndarray | None = None,
    coverage_mask: np.ndarray | None = None,
    legacy_mask: bool = False,
    raster_grid: RasterGrid = BASE_RASTER_GRID,
) -> tuple[bytes, bytes, np.ndarray]:
    merged, codes = compose_best_values(
        firework_values,
        hrrr_values,
        firework_valid=firework_valid,
        hrrr_valid=hrrr_valid,
        inside_hrrr=inside_hrrr,
        edge_weight=edge_weight,
        coverage_mask=coverage_mask,
        raster_grid=raster_grid,
    )
    valid = np.isfinite(merged)
    return encode_concentration(merged, valid), encode_mask_png(codes, legacy=legacy_mask), codes


def _detail_tiles(values: np.ndarray, codes: np.ndarray) -> list[dict[str, Any]]:
    tiles = []
    for row, y0 in enumerate((0, DETAIL_TILE_HEIGHT - 1)):
        for column, x0 in enumerate((0, DETAIL_TILE_WIDTH - 1)):
            tile_values = values[y0:y0 + DETAIL_TILE_HEIGHT, x0:x0 + DETAIL_TILE_WIDTH]
            tile_codes = codes[y0:y0 + DETAIL_TILE_HEIGHT, x0:x0 + DETAIL_TILE_WIDTH]
            tiles.append({
                "column": column,
                "row": row,
                "texturePng": encode_concentration(tile_values, np.isfinite(tile_values)),
                "maskPng": encode_mask_png(tile_codes),
            })
    return tiles


def compose_detail_frame(
    firework_values: np.ndarray | None,
    hrrr_values: np.ndarray | None,
    *,
    firework_valid: np.ndarray | None = None,
    hrrr_valid: np.ndarray | None = None,
    coverage_mask: np.ndarray | None = None,
) -> tuple[bytes, bytes, list[dict[str, Any]], np.ndarray]:
    merged, codes = compose_best_values(
        firework_values,
        hrrr_values,
        firework_valid=firework_valid,
        hrrr_valid=hrrr_valid,
        coverage_mask=coverage_mask,
        raster_grid=DETAIL_RASTER_GRID,
    )
    base_values = merged[::2, ::2]
    base_codes = codes[::2, ::2]
    return (
        encode_concentration(base_values, np.isfinite(base_values)),
        encode_mask_png(base_codes),
        _detail_tiles(merged, codes),
        codes,
    )


def _v8_tile_grid(column: int, row: int) -> tuple[RasterGrid, int, int]:
    stride_x = 1023
    stride_y = 634
    x0, y0 = column * stride_x, row * stride_y
    dx = (V8_DETAIL_RASTER_GRID.east - V8_DETAIL_RASTER_GRID.west) / (V8_DETAIL_RASTER_GRID.width - 1)
    dy = (V8_DETAIL_RASTER_GRID.north - V8_DETAIL_RASTER_GRID.south) / (V8_DETAIL_RASTER_GRID.height - 1)
    return RasterGrid(
        1024,
        635,
        f"v8-tile-{column}-{row}",
        V8_DETAIL_RASTER_GRID.west + x0 * dx,
        V8_DETAIL_RASTER_GRID.north - (y0 + 634) * dy,
        V8_DETAIL_RASTER_GRID.west + (x0 + 1023) * dx,
        V8_DETAIL_RASTER_GRID.north - y0 * dy,
    ), x0, y0


def _tile_lonlat(raster_grid: RasterGrid) -> tuple[np.ndarray, np.ndarray]:
    lons = np.linspace(raster_grid.west, raster_grid.east, raster_grid.width, dtype=np.float32)
    lats = np.linspace(raster_grid.north, raster_grid.south, raster_grid.height, dtype=np.float32)
    return np.meshgrid(lons, lats)


_V8_LONGITUDES = np.linspace(V8_DETAIL_RASTER_GRID.west, V8_DETAIL_RASTER_GRID.east, V8_DETAIL_RASTER_GRID.width, dtype=np.float32)
_V8_LATITUDES = np.linspace(V8_DETAIL_RASTER_GRID.north, V8_DETAIL_RASTER_GRID.south, V8_DETAIL_RASTER_GRID.height, dtype=np.float32)


def _v8_tile_lonlat(x0: int, y0: int, width: int = 1024, height: int = 635) -> tuple[np.ndarray, np.ndarray]:
    return np.meshgrid(_V8_LONGITUDES[x0:x0 + width], _V8_LATITUDES[y0:y0 + height])


def v8_hrrr_target_coordinates(grid: HrrrGrid) -> tuple[np.ndarray, np.ndarray]:
    """Build one bounded Float32 target transform and reuse its tile views."""
    target_x = np.empty((V8_DETAIL_RASTER_GRID.height, V8_DETAIL_RASTER_GRID.width), dtype=np.float32)
    target_y = np.empty_like(target_x)
    for row in range(4):
        for column in range(4):
            tile_grid, x0, y0 = _v8_tile_grid(column, row)
            lon, lat = _v8_tile_lonlat(x0, y0, tile_grid.width, tile_grid.height)
            x, y = lonlat_to_hrrr_xy(lon, lat, grid)
            target_x[y0:y0 + tile_grid.height, x0:x0 + tile_grid.width] = x.astype(np.float32)
            target_y[y0:y0 + tile_grid.height, x0:x0 + tile_grid.width] = y.astype(np.float32)
    return target_x, target_y


def reproject_native(
    field: NativeField | None,
    raster_grid: RasterGrid,
    *,
    hrrr_xy: tuple[np.ndarray, np.ndarray] | None = None,
    target_lonlat: tuple[np.ndarray, np.ndarray] | None = None,
) -> tuple[np.ndarray | None, np.ndarray | None]:
    if field is None:
        return None, None
    if isinstance(field.grid, HrrrGrid):
        if hrrr_xy is not None:
            return bilinear_valid(field.values, field.valid, hrrr_xy[0], hrrr_xy[1])
        return reproject_hrrr(field.values, field.valid, field.grid, raster_grid, validity_aware=True)
    lon, lat = target_lonlat if target_lonlat is not None else _tile_lonlat(raster_grid)
    x = (lon - field.grid.lon0) / field.grid.dx
    y = (field.grid.lat0 - lat) / field.grid.dy
    return bilinear_valid(field.values, field.valid, x, y)


@lru_cache(maxsize=2)
def _v8_geographic_sample_axes(grid: GeographicGrid) -> tuple[np.ndarray, np.ndarray]:
    # A few KiB per grid, rather than full-grid neighbor/weight tables.
    return (_V8_LONGITUDES - grid.lon0) / grid.dx, (grid.lat0 - _V8_LATITUDES) / grid.dy


def _prepared_native(field: NativeField | None) -> tuple[np.ndarray, np.ndarray] | None:
    if field is None:
        return None
    values = np.asarray(field.values, dtype=np.float32)
    return values, np.asarray(field.valid, dtype=bool) & np.isfinite(values) & (values >= 0)


def _covered_native_samples(
    prepared: tuple[np.ndarray, np.ndarray] | None,
    x: np.ndarray,
    y: np.ndarray,
    coverage: np.ndarray,
) -> tuple[np.ndarray | None, np.ndarray | None]:
    if prepared is None:
        return None, None
    values = np.full(coverage.shape, np.nan, dtype=np.float32)
    valid = np.zeros(coverage.shape, dtype=bool)
    if x.size:
        values[coverage], valid[coverage] = _sample_bilinear_valid(*prepared, x, y)
    return values, valid


def compose_v8_frame(
    firework: NativeField | None,
    hrrr: NativeField | None,
    *,
    hrrr_target_xy: tuple[np.ndarray, np.ndarray] | None = None,
) -> tuple[bytes, bytes, list[dict[str, Any]]]:
    base_values = np.full((CONTEXT_HEIGHT, CONTEXT_WIDTH), np.nan, dtype=np.float32)
    base_codes = np.full((CONTEXT_HEIGHT, CONTEXT_WIDTH), MASK_NONE, dtype=np.uint8)
    base_filled = np.zeros((CONTEXT_HEIGHT, CONTEXT_WIDTH), dtype=bool)
    coverage = coastal_coverage_mask(V8_DETAIL_RASTER_GRID)
    tiles: list[dict[str, Any]] = []
    hrrr_grid = hrrr.grid if hrrr is not None and isinstance(hrrr.grid, HrrrGrid) else HrrrGrid()
    firework_prepared, hrrr_prepared = _prepared_native(firework), _prepared_native(hrrr)
    firework_axes = _v8_geographic_sample_axes(firework.grid) if firework is not None and isinstance(firework.grid, GeographicGrid) else None
    for row in range(4):
        for column in range(4):
            tile_grid, x0, y0 = _v8_tile_grid(column, row)
            tile_coverage = coverage[y0:y0 + tile_grid.height, x0:x0 + tile_grid.width]
            visible_y, visible_x = np.nonzero(tile_coverage)
            if not visible_x.size:
                fw_values, fw_valid = None, None
            elif firework_axes is not None:
                fw_values, fw_valid = _covered_native_samples(
                    firework_prepared, firework_axes[0][x0 + visible_x], firework_axes[1][y0 + visible_y], tile_coverage,
                )
            else:
                fw_values, fw_valid = reproject_native(firework, tile_grid, target_lonlat=_v8_tile_lonlat(x0, y0, tile_grid.width, tile_grid.height))
            tile_hrrr_xy = (
                hrrr_target_xy[0][y0:y0 + tile_grid.height, x0:x0 + tile_grid.width],
                hrrr_target_xy[1][y0:y0 + tile_grid.height, x0:x0 + tile_grid.width],
            ) if hrrr_target_xy is not None else None
            if not visible_x.size:
                hr_values, hr_valid = None, None
            elif tile_hrrr_xy is not None:
                hr_values, hr_valid = _covered_native_samples(
                    hrrr_prepared, tile_hrrr_xy[0][tile_coverage], tile_hrrr_xy[1][tile_coverage], tile_coverage,
                )
            else:
                hr_values, hr_valid = reproject_native(hrrr, tile_grid)
            if not visible_x.size:
                merged = np.full(tile_coverage.shape, np.nan, dtype=np.float32)
                codes = np.full(tile_coverage.shape, MASK_NONE, dtype=np.uint8)
                weights = None
            else:
                if tile_hrrr_xy is None:
                    inside = domain_mask(hrrr_grid, tile_grid)
                    weights = hrrr_edge_weights(hrrr_grid, FEATHER_DISTANCE_KM, tile_grid)
                else:
                    sample_x, sample_y = tile_hrrr_xy
                    inside = (sample_x >= 0) & (sample_y >= 0) & (sample_x <= hrrr_grid.nx - 1) & (sample_y <= hrrr_grid.ny - 1)
                    distance_km = (min(hrrr_grid.dx, hrrr_grid.dy) / 1000.0) * np.minimum.reduce(
                        (sample_x, hrrr_grid.nx - 1 - sample_x, sample_y, hrrr_grid.ny - 1 - sample_y)
                    )
                    t = np.clip(distance_km / FEATHER_DISTANCE_KM, 0.0, 1.0)
                    weights = np.where(inside, t * t * (3.0 - 2.0 * t), 0.0).astype(np.float32)
                merged, codes = compose_best_values(
                    fw_values,
                    hr_values,
                    firework_valid=fw_valid,
                    hrrr_valid=hr_valid,
                    inside_hrrr=inside,
                    edge_weight=weights,
                    coverage_mask=tile_coverage,
                    raster_grid=tile_grid,
                )
            local_x = np.flatnonzero((x0 + np.arange(tile_grid.width)) % 4 == 0)
            local_y = np.flatnonzero((y0 + np.arange(tile_grid.height)) % 4 == 0)
            base_x = (x0 + local_x) // 4
            base_y = (y0 + local_y) // 4
            sampled_values = merged[np.ix_(local_y, local_x)]
            sampled_codes = codes[np.ix_(local_y, local_x)]
            existing = base_filled[np.ix_(base_y, base_x)]
            if np.any(existing):
                old_values = base_values[np.ix_(base_y, base_x)]
                old_codes = base_codes[np.ix_(base_y, base_x)]
                if not np.array_equal(old_codes[existing], sampled_codes[existing]) or not np.array_equal(old_values[existing], sampled_values[existing], equal_nan=True):
                    metrics = current_metrics()
                    if metrics:
                        metrics.increment("seam_gate_failures")
                    raise ValueError("v8 detail seams do not agree")
            base_values[np.ix_(base_y, base_x)] = sampled_values
            base_codes[np.ix_(base_y, base_x)] = sampled_codes
            base_filled[np.ix_(base_y, base_x)] = True
            tiles.append({
                "column": column,
                "row": row,
                "texturePng": encode_concentration(merged, np.isfinite(merged), V8_PALETTE_VERSION),
                "maskPng": encode_mask_png(codes),
            })
            metrics = current_metrics()
            if metrics:
                metrics.increment("detail_tiles_encoded")
                metrics.increment("bilinear_tile_reprojections", (int(firework is not None) + int(hrrr is not None)) if visible_x.size else 0)
                arrays = [base_values, base_codes, base_filled, coverage, fw_values, fw_valid, hr_values, hr_valid, merged, codes, weights]
                metrics.observe_working_bytes(sum(array.nbytes for array in arrays if isinstance(array, np.ndarray)) + sum(array.nbytes for array in hrrr_target_xy or ()))
    if not np.all(base_filled):
        metrics = current_metrics()
        if metrics:
            metrics.increment("completeness_gate_failures")
        raise ValueError("v8 base raster is incomplete")
    return (
        encode_concentration(base_values, np.isfinite(base_values), V8_PALETTE_VERSION),
        encode_mask_png(base_codes),
        tiles,
    )


def _has_values(frame: dict[str, Any] | None) -> bool:
    return frame is not None and frame.get("values") is not None


def _present_contributors(fw: dict[str, Any] | None, hr: dict[str, Any] | None, firework: dict[str, Any] | None) -> list[dict[str, Any]]:
    contributors = []
    if hr:
        contributors.append({"source": "hrrr", "modelRun": hr.get("modelRun")})
    if fw:
        contributors.append({"source": "firework", "modelRun": fw.get("modelRun") or (firework or {}).get("modelRun")})
    return contributors


def _numeric_contributors(fw: dict[str, Any] | None, hr: dict[str, Any] | None, firework: dict[str, Any] | None) -> list[dict[str, Any]]:
    return _present_contributors(fw if _has_values(fw) else None, hr if _has_values(hr) else None, firework)


def _same_contributors(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    return [(item.get("source"), item.get("modelRun")) for item in left] == [(item.get("source"), item.get("modelRun")) for item in right]


def _source_versions_reusable(
    previous_best: dict[str, Any] | None,
    firework: dict[str, Any] | None,
    hrrr: dict[str, Any] | None,
    *,
    legacy_mask: bool = False,
    horizon_hours: int = PUBLIC_OUTLOOK_HORIZON_HOURS,
    raster_grid: RasterGrid = BASE_RASTER_GRID,
) -> bool:
    prior = previous_best or {}
    mask_version = LEGACY_SOURCE_MASK_VERSION if legacy_mask else SOURCE_MASK_VERSION
    detail = raster_grid == DETAIL_RASTER_GRID
    return (
        prior.get("selectionPolicy") == BEST_POLICY_VERSION
        and prior.get("horizonHours") == horizon_hours
        and prior.get("featherDistanceKm") == FEATHER_DISTANCE_KM
        and prior.get("paletteVersion") == PALETTE_VERSION
        and prior.get("sourceMaskVersion") == mask_version
        and prior.get("coveragePolicy") == COVERAGE_POLICY
        and prior.get("coastalBufferKm") == COASTAL_BUFFER_KM
        and prior.get("coverageMaskVersion") == (DETAIL_COVERAGE_MASK_VERSION if detail else COVERAGE_MASK_VERSION)
        and prior.get("rasterProcessingVersion") == (DETAIL_RASTER_PROCESSING_VERSION if detail else None)
        and prior.get("detailGrid") == (DETAIL_GRID if detail else None)
        and not prior.get("incompleteNumeric")
        and prior.get("fireworkProcessingVersion") == (firework or {}).get("processingVersion")
        and prior.get("hrrrProcessingVersion") == (hrrr or {}).get("processingVersion")
    )


def hours_needing_png(
    firework: dict[str, Any] | None,
    hrrr: dict[str, Any] | None,
    previous_best: dict[str, Any] | None,
    now: datetime,
    *,
    legacy_mask: bool = False,
    horizon_hours: int = PUBLIC_OUTLOOK_HORIZON_HOURS,
    raster_grid: RasterGrid = BASE_RASTER_GRID,
) -> set[str]:
    firework_frames = {frame["validTime"]: frame for frame in (firework or {}).get("frames", [])}
    hrrr_frames = {frame["validTime"]: frame for frame in (hrrr or {}).get("frames", [])}
    prior_frames = {frame["validTime"]: frame for frame in (previous_best or {}).get("frames", [])}
    reuse_ok = _source_versions_reusable(
        previous_best, firework, hrrr, legacy_mask=legacy_mask, horizon_hours=horizon_hours, raster_grid=raster_grid
    )
    needed: set[str] = set()
    for hour in canonical_hours(now, horizon_hours):
        key = iso_utc(hour)
        if key not in firework_frames and key not in hrrr_frames:
            continue
        contributors = _present_contributors(firework_frames.get(key), hrrr_frames.get(key), firework)
        prior = prior_frames.get(key) if reuse_ok else None
        if not (
            prior
            and prior.get("textureUrl")
            and prior.get("sourceMaskUrl")
            and (raster_grid != DETAIL_RASTER_GRID or len(prior.get("detailTiles") or []) == 4)
            and _same_contributors(prior.get("contributors") or [], contributors)
        ):
            needed.add(key)
    return needed


def compose_best_run(
    firework: dict[str, Any] | None,
    hrrr: dict[str, Any] | None,
    now: datetime,
    *,
    inside_hrrr: np.ndarray | None = None,
    coverage_mask: np.ndarray | None = None,
    previous_best: dict[str, Any] | None = None,
    legacy_mask: bool = False,
    horizon_hours: int = PUBLIC_OUTLOOK_HORIZON_HOURS,
    raster_grid: RasterGrid = BASE_RASTER_GRID,
) -> dict[str, Any]:
    firework_frames = {frame["validTime"]: frame for frame in (firework or {}).get("frames", [])}
    hrrr_frames = {frame["validTime"]: frame for frame in (hrrr or {}).get("frames", [])}
    prior_frames = {frame["validTime"]: frame for frame in (previous_best or {}).get("frames", [])}
    reuse_ok = _source_versions_reusable(
        previous_best, firework, hrrr, legacy_mask=legacy_mask, horizon_hours=horizon_hours, raster_grid=raster_grid
    )
    times = []
    for hour in canonical_hours(now, horizon_hours):
        key = iso_utc(hour)
        if key in firework_frames or key in hrrr_frames:
            times.append(key)
    if not times:
        raise ValueError("no overlapping forecast hours for Best Available")
    inside = inside_hrrr if inside_hrrr is not None else domain_mask(HrrrGrid(), raster_grid)
    coverage = coverage_mask if coverage_mask is not None else coastal_coverage_mask(raster_grid)
    frames = []
    incomplete = bool((previous_best or {}).get("incompleteNumeric")) if reuse_ok else False
    for valid in times:
        fw = firework_frames.get(valid)
        hr = hrrr_frames.get(valid)
        present = _present_contributors(fw, hr, firework)
        numeric = _numeric_contributors(fw, hr, firework)
        if present != numeric:
            incomplete = True
        prior = prior_frames.get(valid) if reuse_ok else None
        if (
            prior
            and prior.get("textureUrl")
            and prior.get("sourceMaskUrl")
            and (raster_grid != DETAIL_RASTER_GRID or len(prior.get("detailTiles") or []) == 4)
            and _same_contributors(prior.get("contributors") or [], present)
        ):
            frames.append({
                "validTime": valid,
                "modelRun": prior.get("modelRun") or (hr or fw or {}).get("modelRun"),
                "textureUrl": prior["textureUrl"],
                "sourceMaskUrl": prior["sourceMaskUrl"],
                "contributors": prior.get("contributors") or numeric,
                **({"detailTiles": prior["detailTiles"]} if raster_grid == DETAIL_RASTER_GRID else {}),
            })
            continue
        if raster_grid == DETAIL_RASTER_GRID:
            texture, mask, detail_tiles, _codes = compose_detail_frame(
                None if fw is None else fw.get("values"),
                None if hr is None else hr.get("values"),
                firework_valid=None if fw is None else fw.get("valid"),
                hrrr_valid=None if hr is None else hr.get("valid"),
                coverage_mask=coverage,
            )
            metrics = current_metrics()
            if metrics:
                metrics.increment("detail_frames_composed")
                metrics.increment("detail_tiles_encoded", len(detail_tiles))
        else:
            texture, mask, _codes = compose_best_frame(
                None if fw is None else fw.get("values"),
                None if hr is None else hr.get("values"),
                firework_valid=None if fw is None else fw.get("valid"),
                hrrr_valid=None if hr is None else hr.get("valid"),
                inside_hrrr=inside,
                coverage_mask=coverage,
                legacy_mask=legacy_mask,
                raster_grid=raster_grid,
            )
        frames.append({
            "validTime": valid,
            "modelRun": (hr or fw or {}).get("modelRun") or (firework or {}).get("modelRun"),
            "texturePng": texture,
            "maskPng": mask,
            "contributors": numeric,
            **({"detailTilePngs": detail_tiles} if raster_grid == DETAIL_RASTER_GRID else {}),
        })
    return {
        "modelId": "best",
        "variable": "best_available_smoke",
        "units": (hrrr or firework or {}).get("units") or "µg/m³",
        "nativeResolutionKm": None,
        "ingestMethod": "compose",
        "horizonHours": horizon_hours,
        "selectionPolicy": BEST_POLICY_VERSION,
        "featherDistanceKm": FEATHER_DISTANCE_KM,
        "paletteVersion": PALETTE_VERSION,
        "sourceMaskVersion": LEGACY_SOURCE_MASK_VERSION if legacy_mask else SOURCE_MASK_VERSION,
        "coveragePolicy": COVERAGE_POLICY,
        "coastalBufferKm": COASTAL_BUFFER_KM,
        "coverageMaskVersion": DETAIL_COVERAGE_MASK_VERSION if raster_grid == DETAIL_RASTER_GRID else COVERAGE_MASK_VERSION,
        **({
            "detailGrid": DETAIL_GRID,
            "rasterProcessingVersion": DETAIL_RASTER_PROCESSING_VERSION,
        } if raster_grid == DETAIL_RASTER_GRID else {}),
        "fireworkProcessingVersion": (firework or {}).get("processingVersion"),
        "hrrrProcessingVersion": (hrrr or {}).get("processingVersion"),
        "incompleteNumeric": incomplete,
        "modelRun": (hrrr or firework or {}).get("modelRun"),
        "legendPng": legend_png(),
        "frames": frames,
    }
