from __future__ import annotations

from dataclasses import replace

import numpy as np

from ingest.forecast_raster import (
    HRRR_LAT1,
    HRRR_LON1,
    HrrrGrid,
    bilinear_valid,
    context_lonlat_grid,
    domain_mask,
    hrrr_edge_weights,
    hrrr_sample_xy,
    hrrr_xy_to_lonlat,
    lonlat_to_hrrr_xy,
    reproject_hrrr,
)


def test_validity_aware_bilinear_sampling_renormalizes_valid_neighbours() -> None:
    values = np.array([[0.0, 10.0], [20.0, 30.0]], dtype=np.float32)
    x = np.array([[0.5]], dtype=np.float32)
    y = np.array([[0.5]], dtype=np.float32)
    sample, valid = bilinear_valid(values, np.ones_like(values, dtype=bool), x, y)
    assert valid[0, 0]
    assert sample[0, 0] == 15.0

    partial = np.array([[True, True], [True, False]])
    sample, valid = bilinear_valid(values, partial, x, y)
    assert valid[0, 0]
    assert np.isclose(sample[0, 0], 10.0)

    single = np.array([[False, False], [False, True]])
    sample, valid = bilinear_valid(values, single, x, y)
    assert valid[0, 0]
    assert sample[0, 0] == 30.0


def test_validity_aware_bilinear_sampling_never_extrapolates_or_invents_zero() -> None:
    values = np.array([[4.0, 8.0], [12.0, 16.0]], dtype=np.float32)
    x = np.array([[-0.01, 0.5, 1.01]], dtype=np.float32)
    y = np.array([[0.5, 0.5, 0.5]], dtype=np.float32)
    sample, valid = bilinear_valid(values, np.zeros_like(values, dtype=bool), x, y)
    assert not valid.any()
    assert np.isnan(sample).all()


def test_hrrr_origin_round_trips() -> None:
    x, y = lonlat_to_hrrr_xy(np.array([HRRR_LON1]), np.array([HRRR_LAT1]))
    assert abs(x[0]) < 1e-4
    assert abs(y[0]) < 1e-4
    lon, lat = hrrr_xy_to_lonlat(np.array([0.0]), np.array([0.0]))
    assert abs(lon[0] - HRRR_LON1) < 1e-4
    assert abs(lat[0] - HRRR_LAT1) < 1e-4


def test_interior_conus_point_is_inside_hrrr_domain() -> None:
    x, y = lonlat_to_hrrr_xy(np.array([-97.5]), np.array([38.5]))
    assert 0 < x[0] < 1798
    assert 0 < y[0] < 1058


def test_canada_point_is_outside_hrrr_domain() -> None:
    mask = domain_mask(HrrrGrid())
    # Edmonton is north of the HRRR CONUS domain.
    from ingest.sources.context_raster import lon_lat_to_pixel
    x, y = lon_lat_to_pixel(-113.49, 53.55)
    assert mask[y, x] == False


def test_reproject_preserves_a_known_value() -> None:
    grid = HrrrGrid(nx=21, ny=21)
    values = np.zeros((21, 21), dtype=np.float32)
    values[10, 10] = 42
    lon, lat = hrrr_xy_to_lonlat(np.array([10.0]), np.array([10.0]), grid)
    sample, valid = reproject_hrrr(values, grid=grid)
    from ingest.sources.context_raster import lon_lat_to_pixel
    x, y = lon_lat_to_pixel(float(lon[0]), float(lat[0]))
    assert valid[y, x]
    assert sample[y, x] > 5


def test_hrrr_edge_weights_are_zero_at_edge_and_full_interior() -> None:
    inside = domain_mask()
    weights = hrrr_edge_weights(feather_km=200)
    assert np.all(weights[~inside] == 0)
    # Public raster sample centers need not land exactly on the native-grid edge.
    assert weights[inside].min() < 1e-6
    assert weights[inside].max() == 1
    assert np.any((weights > 0) & (weights < 1))


def test_hrrr_sample_transform_is_cached_per_grid() -> None:
    first = hrrr_sample_xy(HrrrGrid())
    second = hrrr_sample_xy(HrrrGrid())
    assert first[0] is second[0]
    assert first[1] is second[1]


def test_hrrr_sample_transform_cache_includes_every_projection_parameter() -> None:
    original = HrrrGrid()
    changed = replace(original, latin1=30.0, latin2=60.0, radius=6_400_000.0)
    first = hrrr_sample_xy(original)
    second = hrrr_sample_xy(changed)
    expected = lonlat_to_hrrr_xy(*context_lonlat_grid(), changed)
    assert first[0] is not second[0]
    assert np.allclose(second[0], expected[0])
    assert np.allclose(second[1], expected[1])


def _dense_reference(
    values: np.ndarray,
    valid_mask: np.ndarray | None,
    x: np.ndarray,
    y: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    field = np.asarray(values, dtype=np.float32)
    valid_source = np.isfinite(field) & (field >= 0)
    if valid_mask is not None:
        valid_source &= np.asarray(valid_mask, dtype=bool)
    ny, nx = field.shape
    inside = (x >= 0) & (y >= 0) & (x <= nx - 1) & (y <= ny - 1)
    x0 = np.clip(np.floor(x).astype(np.int32), 0, max(0, nx - 2))
    y0 = np.clip(np.floor(y).astype(np.int32), 0, max(0, ny - 2))
    x1 = np.minimum(x0 + 1, nx - 1)
    y1 = np.minimum(y0 + 1, ny - 1)
    sx = x - x0
    sy = y - y0
    weights = (
        (1 - sx) * (1 - sy),
        sx * (1 - sy),
        (1 - sx) * sy,
        sx * sy,
    )
    indexes = ((y0, x0), (y0, x1), (y1, x0), (y1, x1))
    total = np.zeros(x.shape, dtype=np.float64)
    weighted = np.zeros(x.shape, dtype=np.float64)
    for weight, (rows, columns) in zip(weights, indexes):
        usable = valid_source[rows, columns]
        contribution = np.where(usable, weight, 0.0)
        total += contribution
        weighted += contribution * np.where(usable, field[rows, columns], 0.0)
    valid = inside & (total > 0)
    sample = np.full(x.shape, np.nan, dtype=np.float32)
    sample[valid] = (weighted[valid] / total[valid]).astype(np.float32)
    return sample, valid


def test_sparse_bilinear_matches_dense_reference_bitwise():
    from ingest.forecast_raster import _sample_bilinear_valid
    rng = np.random.default_rng(817)
    field = rng.normal(20, 15, (13, 17)).astype(np.float32)
    field[0, :3] = [np.nan, np.inf, -np.inf]
    supplied = rng.random(field.shape) > 0.3
    valid_source = supplied & np.isfinite(field) & (field >= 0)
    for dtype in (np.float32, np.float64):
        x = rng.uniform(-1, 17, (15, 19)).astype(dtype)
        y = rng.uniform(-1, 13, (15, 19)).astype(dtype)
        x[0, :4] = [0, 16, -0.01, 16.01]
        y[0, :4] = [0, 12, 0, 12]
        for coverage in (np.zeros(x.shape, bool), np.ones(x.shape, bool), rng.random(x.shape) > 0.4):
            expected, expected_valid = _dense_reference(field, supplied, x, y)
            sampled, valid = _sample_bilinear_valid(field, valid_source, x[coverage], y[coverage])
            np.testing.assert_array_equal(valid, expected_valid[coverage])
            np.testing.assert_array_equal(sampled.view(np.uint32), expected[coverage].view(np.uint32))
        actual, valid = bilinear_valid(field, supplied, x, y)
        expected, expected_valid = _dense_reference(field, supplied, x, y)
        np.testing.assert_array_equal(actual.view(np.uint32), expected.view(np.uint32))
        np.testing.assert_array_equal(valid, expected_valid)
