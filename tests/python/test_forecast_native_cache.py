from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import numpy as np

from ingest.local_store import LocalFrameStore
from ingest.forecast_native_cache import (
    NATIVE_FIELD_CACHE_INDEX_PATH,
    NATIVE_FIELD_CACHE_MAGIC,
    NATIVE_FIELD_CACHE_VERSION,
    GeographicGrid,
    NativeField,
    NativeFieldCache,
    decode_native_field,
    encode_native_field,
    native_field_identity,
)
from ingest.forecast_raster import HrrrGrid


IDENTITY = {
    "model_id": "firework",
    "model_run": "2026-08-21T12:00:00Z",
    "valid_time": "2026-08-21T18:00:00Z",
    "processing_version": "firework-wcs-native-ug-v6",
}


def _field(grid: GeographicGrid | HrrrGrid) -> NativeField:
    height = grid.height if isinstance(grid, GeographicGrid) else grid.ny
    width = grid.width if isinstance(grid, GeographicGrid) else grid.nx
    values = np.arange(height * width, dtype=np.float32).reshape(height, width)
    valid = np.ones((height, width), dtype=bool)
    valid[0, 1] = False
    values[0, 1] = np.nan
    return NativeField(values, valid, grid)


def test_native_geographic_field_round_trip_is_lossless() -> None:
    field = _field(GeographicGrid(width=7, height=5, lon0=-145.0, lat0=72.0, dx=0.1, dy=0.1))
    encoded = encode_native_field(field, **IDENTITY)
    decoded = decode_native_field(encoded, **IDENTITY)
    assert decoded.grid == field.grid
    assert np.array_equal(decoded.valid, field.valid)
    assert np.array_equal(decoded.values[field.valid].view(np.uint32), field.values[field.valid].view(np.uint32))
    assert np.isnan(decoded.values[0, 1])


def test_native_compression_levels_preserve_bits_and_read_existing_objects() -> None:
    field = _field(HrrrGrid(nx=100, ny=100))
    identity = {**IDENTITY, "model_id": "hrrr"}
    with patch("ingest.forecast_native_cache.NATIVE_FIELD_COMPRESSION_LEVEL", 6):
        old = encode_native_field(field, **identity)
    new = encode_native_field(field, **identity)
    assert old != new
    for data in (old, new):
        decoded = decode_native_field(data, **identity)
        np.testing.assert_array_equal(decoded.valid, field.valid)
        np.testing.assert_array_equal(decoded.values[field.valid].view(np.uint32), field.values[field.valid].view(np.uint32))


def test_native_hrrr_grid_round_trip_preserves_projection() -> None:
    identity = {**IDENTITY, "model_id": "hrrr", "processing_version": "hrrr-massden-native-ug-v9"}
    field = _field(HrrrGrid(nx=100, ny=100))
    decoded = decode_native_field(encode_native_field(field, **identity), **identity)
    assert decoded.grid == field.grid
    assert np.array_equal(decoded.valid, field.valid)


def test_native_identity_includes_processing_and_cache_versions() -> None:
    identity = native_field_identity(**IDENTITY)
    assert NATIVE_FIELD_CACHE_VERSION in identity
    assert native_field_identity(**{**IDENTITY, "processing_version": "other"}) != identity
    assert native_field_identity(**{**IDENTITY, "valid_time": "2026-08-21T19:00:00Z"}) != identity


def test_native_cache_checkpoint_reuse_and_gc(tmp_path) -> None:
    store = LocalFrameStore(tmp_path)
    cache = NativeFieldCache(store)
    field = _field(GeographicGrid(width=7, height=5, lon0=-145.0, lat0=72.0, dx=0.1, dy=0.1))
    retained = cache.put(field, **IDENTITY)
    stale_data = encode_native_field(field, **{**IDENTITY, "valid_time": "2026-08-21T19:00:00Z"})
    stale = f"cache/native-fields/{hashlib.sha256(stale_data).hexdigest()[:20]}/field.bin"
    store.put_bytes(stale, stale_data, "application/octet-stream", cache_seconds=60, overwrite=False)
    cache.checkpoint()
    assert store.get_text(NATIVE_FIELD_CACHE_INDEX_PATH)
    loaded = NativeFieldCache(store).get(**IDENTITY)
    assert loaded is not None and loaded.grid == field.grid
    cache.publish_index()
    cache.gc()
    assert store.get_bytes(retained) is not None
    assert store.get_bytes(stale) is None


def test_native_cache_rejects_corruption_identity_and_grid_shape() -> None:
    field = _field(GeographicGrid(width=7, height=5, lon0=-145.0, lat0=72.0, dx=0.1, dy=0.1))
    encoded = encode_native_field(field, **IDENTITY)
    corrupt = encoded[:-1] + bytes([encoded[-1] ^ 0xFF])
    for payload, identity in (
        (corrupt, IDENTITY),
        (encoded, {**IDENTITY, "model_run": "wrong"}),
    ):
        try:
            decode_native_field(payload, **identity)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid native field was accepted")

    mismatched = NativeField(field.values, field.valid, GeographicGrid(width=8, height=5, lon0=-145.0, lat0=72.0, dx=0.1, dy=0.1))
    try:
        encode_native_field(mismatched, **IDENTITY)
    except ValueError as exc:
        assert "shape" in str(exc)
    else:
        raise AssertionError("mismatched native grid was accepted")


def test_short_magic_native_cache_objects_miss_safely(tmp_path) -> None:
    store = LocalFrameStore(tmp_path)
    cache = NativeFieldCache(store)
    identity = native_field_identity(**IDENTITY)
    for length in range(4, 8):
        data = NATIVE_FIELD_CACHE_MAGIC + bytes(length - len(NATIVE_FIELD_CACHE_MAGIC))
        path = f"cache/native-fields/{hashlib.sha256(data).hexdigest()[:20]}/field.bin"
        store.put_bytes(path, data, "application/octet-stream", cache_seconds=60, overwrite=True)
        cache.index[identity] = path
        assert cache.get(**IDENTITY) is None
        assert identity not in cache.index


def test_native_cache_corruption_repair_survives_restart(tmp_path) -> None:
    store = LocalFrameStore(tmp_path)
    cache = NativeFieldCache(store)
    field = _field(GeographicGrid(width=7, height=5, lon0=-145.0, lat0=72.0, dx=0.1, dy=0.1))
    path = cache.put(field, **IDENTITY)
    cache.checkpoint()
    store.put_bytes(path, b"corrupt", "application/octet-stream", cache_seconds=0, overwrite=True)
    cache = NativeFieldCache(store)
    assert cache.get(**IDENTITY) is None
    cache.put(field, **IDENTITY)
    cache.checkpoint()
    repaired = NativeFieldCache(store).get(**IDENTITY)
    assert repaired is not None
    np.testing.assert_array_equal(repaired.valid, field.valid)


def test_native_cache_read_failure_is_a_miss_but_deadline_propagates(tmp_path) -> None:
    identity = native_field_identity(**IDENTITY)
    path = "cache/native-fields/ffffffffffffffffffff/field.bin"

    class FailingStore(LocalFrameStore):
        error = "blob response exceeds 16777216 bytes"

        def get_bytes(self, pathname, *, max_bytes=16 * 1024 * 1024):
            if pathname == path:
                raise RuntimeError(self.error)
            return super().get_bytes(pathname, max_bytes=max_bytes)

    store = FailingStore(tmp_path)
    cache = NativeFieldCache(store)
    cache.index[identity] = path
    assert cache.get(**IDENTITY) is None
    assert identity not in cache.index
    cache.index[identity] = path
    store.error = "ingest deadline reached during Blob request"
    try:
        cache.get(**IDENTITY)
    except RuntimeError as exc:
        assert "deadline" in str(exc)
    else:
        raise AssertionError("ingest deadline was treated as a cache miss")


def test_unreadable_native_cache_index_starts_empty(tmp_path) -> None:
    store = LocalFrameStore(tmp_path)
    store.put_bytes(NATIVE_FIELD_CACHE_INDEX_PATH, b"\xff", "application/json", cache_seconds=60, overwrite=True)
    assert NativeFieldCache(store).index == {}


def test_wrong_shaped_native_cache_index_starts_empty(tmp_path) -> None:
    store = LocalFrameStore(tmp_path)
    for payload in (b"[]", b"null", b'"value"', b'{"entries":[]}'):
        store.put_bytes(NATIVE_FIELD_CACHE_INDEX_PATH, payload, "application/json", cache_seconds=60, overwrite=True)
        assert NativeFieldCache(store).index == {}


def test_native_cache_drops_negative_and_nonfinite_samples() -> None:
    field = _field(GeographicGrid(width=4, height=3, lon0=-145.0, lat0=72.0, dx=0.1, dy=0.1))
    field.values[1, 1] = -2
    field.values[1, 2] = np.inf
    decoded = decode_native_field(encode_native_field(field, **IDENTITY), **IDENTITY)
    assert not decoded.valid[1, 1]
    assert not decoded.valid[1, 2]
    assert np.isnan(decoded.values[1, 1])
    assert np.isnan(decoded.values[1, 2])


def test_native_spool_reuses_bytes_but_revalidates_and_recovers_local_corruption(tmp_path) -> None:
    store = LocalFrameStore(tmp_path / "remote")
    field = _field(GeographicGrid(7, 5, -145, 72, 0.1, 0.1))
    writer = NativeFieldCache(store)
    path = writer.put(field, **IDENTITY)
    writer.checkpoint()
    cache = NativeFieldCache(store, spool_max_bytes=4096)
    try:
        with patch.object(store, "get_bytes", wraps=store.get_bytes) as read:
            first = cache.get(**IDENTITY)
            second = cache.get(**IDENTITY)
            assert first is not None and second is not None
            np.testing.assert_array_equal(first.values, second.values)
            assert read.call_count == 1
            # Never expose a mutable decoded field shared by subsequent users.
            first.values[:] = 100
            assert not np.all(cache.get(**IDENTITY).values == 100)
            spool = cache._spool
            assert spool is not None
            local = next(Path(spool.name).iterdir())
            local.write_bytes(b"corrupt")
            assert cache.get(**IDENTITY) is not None
            assert read.call_count == 2
            assert cache.index[native_field_identity(**IDENTITY)] == path
    finally:
        cache.close()
    assert not Path(spool.name).exists()


def test_native_spool_put_avoids_readback_and_full_spool_falls_back(tmp_path) -> None:
    store = LocalFrameStore(tmp_path)
    field = _field(GeographicGrid(7, 5, -145, 72, 0.1, 0.1))
    encoded = encode_native_field(field, **IDENTITY)
    cache = NativeFieldCache(store, spool_max_bytes=len(encoded))
    try:
        cache.put(field, **IDENTITY)
        other = {**IDENTITY, "valid_time": "2026-08-21T19:00:00Z"}
        cache.put(field, **other)
        with patch.object(store, "get_bytes", wraps=store.get_bytes) as read:
            assert cache.get(**IDENTITY) is not None
            assert read.call_count == 0
            assert cache.get(**other) is not None
            assert read.call_count == 1
        assert cache._spool_bytes == len(encoded)
    finally:
        cache.close()


def test_native_spool_unavailable_does_not_break_remote_repair(tmp_path) -> None:
    store = LocalFrameStore(tmp_path)
    field = _field(GeographicGrid(7, 5, -145, 72, 0.1, 0.1))
    cache = NativeFieldCache(store, spool_max_bytes=4096)
    try:
        with patch("ingest.forecast_native_cache.tempfile.TemporaryDirectory", side_effect=OSError("disk full")):
            path = cache.put(field, **IDENTITY)
            assert cache.get(**IDENTITY) is not None
        store.put_bytes(path, b"corrupt", "application/octet-stream", cache_seconds=0, overwrite=True)
        assert cache.get(**IDENTITY) is None
        cache.put(field, **IDENTITY)
        cache.checkpoint()
        assert NativeFieldCache(store).get(**IDENTITY) is not None
    finally:
        cache.close()


def test_concurrent_native_spool_admission_obeys_total_byte_limit(tmp_path) -> None:
    store = LocalFrameStore(tmp_path)
    field = _field(GeographicGrid(7, 5, -145, 72, 0.1, 0.1))
    cache = NativeFieldCache(store, spool_max_bytes=1500)
    identities = [{**IDENTITY, "valid_time": f"2026-08-21T{hour:02d}:00:00Z"} for hour in range(10, 20)]
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda identity: cache.put(field, **identity), identities))
        files = list(Path(cache._spool.name).iterdir())
        assert sum(file.stat().st_size for file in files) == cache._spool_bytes <= 1500
        assert all(cache.get(**identity) is not None for identity in identities)
    finally:
        cache.close()


def test_native_spool_does_not_hide_failed_remote_put(tmp_path) -> None:
    import pytest

    store = LocalFrameStore(tmp_path)
    cache = NativeFieldCache(store, spool_max_bytes=4096)
    field = _field(GeographicGrid(7, 5, -145, 72, 0.1, 0.1))
    try:
        with patch.object(store, "put_bytes", side_effect=OSError("remote unavailable")):
            with pytest.raises(OSError, match="remote unavailable"):
                cache.put(field, **IDENTITY)
        assert cache.get(**IDENTITY) is None
        assert cache._spool is None
    finally:
        cache.close()


def test_failed_ingest_cleans_native_spool_and_releases_lease(tmp_path) -> None:
    from ingest.context_pipeline import run_context_ingest
    from ingest.context_publish import CONTEXT_LOCK_PATH
    from tests.python.support import fixture_settings

    captured = []

    def fail_after_staging(*args):
        cache = args[-1]
        cache.put(_field(GeographicGrid(7, 5, -145, 72, 0.1, 0.1)), **IDENTITY)
        captured.append(Path(cache._spool.name))
        raise RuntimeError("composition failed")

    with patch("ingest.context_pipeline._live_manifest", side_effect=fail_after_staging):
        result = run_context_ingest(fixture_settings(tmp_path))
    assert not result["ok"] and "composition failed" in result["error"]
    assert len(captured) == 1 and not captured[0].exists()
    assert LocalFrameStore(tmp_path).get_bytes(CONTEXT_LOCK_PATH) is None
