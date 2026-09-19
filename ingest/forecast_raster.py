from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ingest.context_contracts import CONTEXT_BOUNDS, CONTEXT_HEIGHT, CONTEXT_WIDTH, DETAIL_HEIGHT, DETAIL_WIDTH
from ingest.forecast_palette import encode_official_concentration

# NCEP HRRR CONUS Lambert conformal (spherical Earth used by the model grid).
HRRR_NX = 1799
HRRR_NY = 1059
HRRR_DX_M = 3000.0
HRRR_DY_M = 3000.0
HRRR_LAT1 = 21.138123
HRRR_LON1 = -122.719528
HRRR_LOV = -97.5
HRRR_LATIN1 = 38.5
HRRR_LATIN2 = 38.5
HRRR_RADIUS_M = 6_371_229.0
HRRR_RESOLUTION_KM = 3
PROCESSING_VERSION = "hrrr-massden-ug-v8"


@dataclass(frozen=True)
class RasterGrid:
    width: int
    height: int
    version: str
    west: float = CONTEXT_BOUNDS["west"]
    south: float = CONTEXT_BOUNDS["south"]
    east: float = CONTEXT_BOUNDS["east"]
    north: float = CONTEXT_BOUNDS["north"]


BASE_RASTER_GRID = RasterGrid(CONTEXT_WIDTH, CONTEXT_HEIGHT, "context-1024x635-v1")
DETAIL_RASTER_GRID = RasterGrid(DETAIL_WIDTH, DETAIL_HEIGHT, "regional-grid-2047x1269-v1")
V8_DETAIL_RASTER_GRID = RasterGrid(4093, 2537, "regional-grid-4093x2537-bilinear-v2")


@dataclass(frozen=True)
class HrrrGrid:
    nx: int = HRRR_NX
    ny: int = HRRR_NY
    dx: float = HRRR_DX_M
    dy: float = HRRR_DY_M
    lat1: float = HRRR_LAT1
    lon1: float = HRRR_LON1
    lov: float = HRRR_LOV
    latin1: float = HRRR_LATIN1
    latin2: float = HRRR_LATIN2
    radius: float = HRRR_RADIUS_M


DEFAULT_HRRR_GRID = HrrrGrid()


def _lcc_params(grid: HrrrGrid) -> tuple[float, float, float, float, float]:
    latin1 = np.deg2rad(grid.latin1)
    latin2 = np.deg2rad(grid.latin2)
    if abs(grid.latin1 - grid.latin2) < 1e-8:
        n = np.sin(latin1)
    else:
        n = np.log(np.cos(latin1) / np.cos(latin2)) / np.log(np.tan(np.pi / 4 + latin2 / 2) / np.tan(np.pi / 4 + latin1 / 2))
    f_const = np.cos(latin1) * (np.tan(np.pi / 4 + latin1 / 2) ** n) / n
    rho0 = grid.radius * f_const / (np.tan(np.pi / 4 + latin1 / 2) ** n)
    lat1 = np.deg2rad(grid.lat1)
    lon1 = np.deg2rad(grid.lon1)
    lov = np.deg2rad(grid.lov)
    rho1 = grid.radius * f_const / (np.tan(np.pi / 4 + lat1 / 2) ** n)
    theta1 = n * (lon1 - lov)
    x0 = rho1 * np.sin(theta1)
    y0 = rho0 - rho1 * np.cos(theta1)
    return float(n), float(f_const), float(rho0), float(x0), float(y0)


def lonlat_to_hrrr_xy(lon: np.ndarray, lat: np.ndarray, grid: HrrrGrid = DEFAULT_HRRR_GRID) -> tuple[np.ndarray, np.ndarray]:
    n, f_const, rho0, x0, y0 = _lcc_params(grid)
    lon_r = np.deg2rad(np.asarray(lon, dtype=np.float64))
    lat_r = np.deg2rad(np.asarray(lat, dtype=np.float64))
    lov = np.deg2rad(grid.lov)
    rho = grid.radius * f_const / (np.tan(np.pi / 4 + lat_r / 2) ** n)
    theta = n * (lon_r - lov)
    x = rho * np.sin(theta)
    y = rho0 - rho * np.cos(theta)
    return (x - x0) / grid.dx, (y - y0) / grid.dy


def hrrr_xy_to_lonlat(x: np.ndarray, y: np.ndarray, grid: HrrrGrid = DEFAULT_HRRR_GRID) -> tuple[np.ndarray, np.ndarray]:
    n, f_const, rho0, x0, y0 = _lcc_params(grid)
    x_m = np.asarray(x, dtype=np.float64) * grid.dx + x0
    y_m = np.asarray(y, dtype=np.float64) * grid.dy + y0
    lov = np.deg2rad(grid.lov)
    theta = np.arctan2(x_m, rho0 - y_m)
    rho = np.hypot(x_m, rho0 - y_m)
    if n < 0:
        rho = -rho
    lat = 2 * np.arctan((grid.radius * f_const / rho) ** (1 / n)) - np.pi / 2
    lon = lov + theta / n
    return np.rad2deg(lon), np.rad2deg(lat)


_CONTEXT_LONLAT: dict[RasterGrid, tuple[np.ndarray, np.ndarray]] = {}
_HRRR_XY_CACHE: dict[tuple[HrrrGrid, RasterGrid], tuple[np.ndarray, np.ndarray]] = {}


def context_lonlat_grid(raster_grid: RasterGrid = BASE_RASTER_GRID) -> tuple[np.ndarray, np.ndarray]:
    cached = _CONTEXT_LONLAT.get(raster_grid)
    if cached is None:
        lons = np.linspace(raster_grid.west, raster_grid.east, raster_grid.width)
        lats = np.linspace(raster_grid.north, raster_grid.south, raster_grid.height)
        cached = np.meshgrid(lons, lats)
        _CONTEXT_LONLAT[raster_grid] = cached
    return cached


def hrrr_sample_xy(grid: HrrrGrid, raster_grid: RasterGrid = BASE_RASTER_GRID) -> tuple[np.ndarray, np.ndarray]:
    key = (grid, raster_grid)
    cached = _HRRR_XY_CACHE.get(key)
    if cached is not None:
        return cached
    lon, lat = context_lonlat_grid(raster_grid)
    xy = lonlat_to_hrrr_xy(lon, lat, grid)
    _HRRR_XY_CACHE[key] = xy
    return xy


def _bilinear(values: np.ndarray, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ny, nx = values.shape
    x0 = np.floor(x).astype(np.int32)
    y0 = np.floor(y).astype(np.int32)
    inside = (x >= 0) & (y >= 0) & (x <= nx - 1) & (y <= ny - 1)
    x0 = np.clip(x0, 0, nx - 2)
    y0 = np.clip(y0, 0, ny - 2)
    x1 = x0 + 1
    y1 = y0 + 1
    sx = x - x0
    sy = y - y0
    v00 = values[y0, x0]
    v10 = values[y0, x1]
    v01 = values[y1, x0]
    v11 = values[y1, x1]
    sample = v00 * (1 - sx) * (1 - sy) + v10 * sx * (1 - sy) + v01 * (1 - sx) * sy + v11 * sx * sy
    valid = inside & np.isfinite(sample) & (sample >= 0)
    sample = np.where(valid, sample, np.nan)
    return sample.astype(np.float32), valid


def bilinear_valid(
    values: np.ndarray,
    valid_mask: np.ndarray | None,
    x: np.ndarray,
    y: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    field = np.asarray(values, dtype=np.float32)
    valid_source = np.isfinite(field) & (field >= 0)
    if valid_mask is not None:
        valid_source &= np.asarray(valid_mask, dtype=bool)
    return _sample_bilinear_valid(field, valid_source, x, y)


def _sample_bilinear_valid(
    field: np.ndarray,
    valid_source: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Sample prepared Float32 values without rescanning native validity per tile."""
    ny, nx = field.shape
    inside = (x >= 0) & (y >= 0) & (x <= nx - 1) & (y <= ny - 1)
    x0 = np.clip(np.floor(x).astype(np.int32), 0, max(0, nx - 2))
    y0 = np.clip(np.floor(y).astype(np.int32), 0, max(0, ny - 2))
    x1 = np.minimum(x0 + 1, nx - 1)
    y1 = np.minimum(y0 + 1, ny - 1)
    sx = x - x0
    sy = y - y0
    total = np.zeros(x.shape, dtype=np.float64)
    weighted = np.zeros(x.shape, dtype=np.float64)
    # Preserve neighbor and arithmetic order, but hold only one weight at a time.
    for right, bottom in ((False, False), (True, False), (False, True), (True, True)):
        weight = (sx if right else 1 - sx) * (sy if bottom else 1 - sy)
        rows, columns = (y1 if bottom else y0), (x1 if right else x0)
        usable = valid_source[rows, columns]
        contribution = np.where(usable, weight, 0.0)
        total += contribution
        weighted += contribution * np.where(usable, field[rows, columns], 0.0)
    valid = inside & (total > 0)
    sample = np.full(x.shape, np.nan, dtype=np.float32)
    sample[valid] = (weighted[valid] / total[valid]).astype(np.float32)
    return sample, valid


def reproject_hrrr(
    values: np.ndarray,
    valid: np.ndarray | None = None,
    grid: HrrrGrid | None = None,
    raster_grid: RasterGrid = BASE_RASTER_GRID,
    validity_aware: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    grid = grid or HrrrGrid(nx=values.shape[1], ny=values.shape[0])
    x, y = hrrr_sample_xy(grid, raster_grid)
    if validity_aware:
        return bilinear_valid(values, valid, x, y)
    field = np.asarray(values, dtype=np.float32)
    if valid is not None:
        field = np.where(valid, field, np.nan)
    return _bilinear(field, x, y)


def domain_mask(grid: HrrrGrid = DEFAULT_HRRR_GRID, raster_grid: RasterGrid = BASE_RASTER_GRID) -> np.ndarray:
    x, y = hrrr_sample_xy(grid, raster_grid)
    return (x >= 0) & (y >= 0) & (x <= grid.nx - 1) & (y <= grid.ny - 1)


def hrrr_edge_weights(
    grid: HrrrGrid = DEFAULT_HRRR_GRID,
    feather_km: float = 200.0,
    raster_grid: RasterGrid = BASE_RASTER_GRID,
) -> np.ndarray:
    x, y = hrrr_sample_xy(grid, raster_grid)
    inside = (x >= 0) & (y >= 0) & (x <= grid.nx - 1) & (y <= grid.ny - 1)
    distance_km = (min(grid.dx, grid.dy) / 1000.0) * np.minimum.reduce((x, grid.nx - 1 - x, y, grid.ny - 1 - y))
    t = np.clip(distance_km / feather_km, 0.0, 1.0)
    weights = t * t * (3.0 - 2.0 * t)
    return np.where(inside, weights, 0.0).astype(np.float32)


def rasterize_hrrr(
    values: np.ndarray,
    valid: np.ndarray | None = None,
    grid: HrrrGrid | None = None,
    *,
    encode_png: bool = True,
    raster_grid: RasterGrid = BASE_RASTER_GRID,
) -> tuple[bytes | None, np.ndarray, np.ndarray]:
    sample, sample_valid = reproject_hrrr(values, valid, grid, raster_grid)
    png = encode_official_concentration(sample, sample_valid) if encode_png else None
    return png, sample, sample_valid


def rasterize_hrrr_png(values: np.ndarray, valid: np.ndarray | None = None, grid: HrrrGrid | None = None) -> tuple[bytes, np.ndarray]:
    png, _sample, sample_valid = rasterize_hrrr(values, valid, grid)
    if png is None:
        raise RuntimeError("HRRR raster encoding produced no PNG")
    return png, sample_valid
