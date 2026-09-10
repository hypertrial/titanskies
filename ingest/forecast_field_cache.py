from __future__ import annotations

import hashlib
import json
import re
import struct
import threading
import zlib
from typing import Any

import numpy as np
import requests

from ingest.local_store import FrameStore
from ingest.context_contracts import CONTEXT_BOUNDS, CONTEXT_FIELD_BUDGET_BYTES, DETAIL_HEIGHT, DETAIL_WIDTH
from ingest.forecast_raster import BASE_RASTER_GRID, DETAIL_RASTER_GRID, RasterGrid
from ingest.perf import current_metrics

FIELD_CACHE_VERSION = "field-cache-v2"
FIELD_CACHE_MAGIC = b"TSF2"
FIELD_CACHE_UNITS = "µg/m³"
FIELD_CACHE_PREFIX = "cache/fields"
FIELD_CACHE_INDEX_PATH = f"{FIELD_CACHE_PREFIX}/index.json"
FIELD_CACHE_COMPRESSED_MAX_BYTES = CONTEXT_FIELD_BUDGET_BYTES
FIELD_CACHE_UNCOMPRESSED_MAX_BYTES = DETAIL_WIDTH * DETAIL_HEIGHT * 4 + (DETAIL_WIDTH * DETAIL_HEIGHT + 7) // 8 + 65_536
_FIELD_PATH = re.compile(rf"^{FIELD_CACHE_PREFIX}/[0-9a-f]{{20}}/field.bin$")
_HEADER_MAX_BYTES = 8_192


def _grid_for_version(grid_version: str) -> RasterGrid:
    if grid_version == BASE_RASTER_GRID.version:
        return BASE_RASTER_GRID
    if grid_version == DETAIL_RASTER_GRID.version:
        return DETAIL_RASTER_GRID
    raise ValueError("field cache grid version is invalid")


def field_identity(
    model_id: str,
    model_run: str,
    valid_time: str,
    processing_version: str,
    grid_version: str = BASE_RASTER_GRID.version,
) -> str:
    return f"{model_id}|{model_run}|{valid_time}|{processing_version}|{grid_version}|{FIELD_CACHE_VERSION}"


def encode_field(
    values: np.ndarray,
    valid: np.ndarray,
    *,
    model_id: str,
    model_run: str,
    valid_time: str,
    processing_version: str,
    grid_version: str = BASE_RASTER_GRID.version,
) -> bytes:
    grid = _grid_for_version(grid_version)
    if values.ndim != 2 or valid.shape != values.shape or values.shape != (grid.height, grid.width):
        raise ValueError("field cache grid shape is invalid")
    height, width = values.shape
    values = np.asarray(values, dtype=np.float32)
    valid_mask = np.asarray(valid, dtype=bool) & np.isfinite(values) & (values >= 0)
    packed_valid = np.packbits(valid_mask.reshape(-1), bitorder="little").tobytes()
    packed_values = _xor_delta(np.ascontiguousarray(values[valid_mask], dtype="<f4"))
    raw = packed_valid + packed_values
    if len(raw) > FIELD_CACHE_UNCOMPRESSED_MAX_BYTES:
        raise ValueError("field cache uncompressed payload exceeds budget")
    payload = zlib.compress(raw, level=6)
    payload_hash = hashlib.sha256(payload).hexdigest()
    header = {
        "fieldCacheVersion": FIELD_CACHE_VERSION,
        "modelId": model_id,
        "modelRun": model_run,
        "validTime": valid_time,
        "processingVersion": processing_version,
        "gridVersion": grid_version,
        "units": FIELD_CACHE_UNITS,
        "width": width,
        "height": height,
        "bounds": dict(CONTEXT_BOUNDS),
        "dtype": "float32",
        "endianness": "little",
        "valueEncoding": "xor-delta-u32",
        "validCount": int(valid_mask.sum()),
        "uncompressedBytes": len(raw),
        "payloadSha256": payload_hash,
    }
    encoded_header = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    blob = FIELD_CACHE_MAGIC + struct.pack("<I", len(encoded_header)) + encoded_header + payload
    if len(blob) > FIELD_CACHE_COMPRESSED_MAX_BYTES:
        raise ValueError("field cache compressed payload exceeds budget")
    return blob


def decode_field(
    data: bytes,
    *,
    model_id: str,
    model_run: str,
    valid_time: str,
    processing_version: str,
    grid_version: str = BASE_RASTER_GRID.version,
) -> tuple[np.ndarray, np.ndarray]:
    if not data or len(data) < 8:
        raise ValueError("field cache object is invalid")
    if len(data) > FIELD_CACHE_COMPRESSED_MAX_BYTES:
        raise ValueError("field cache object exceeds compressed budget")
    if data[:4] != FIELD_CACHE_MAGIC:
        raise ValueError("field cache magic is invalid")
    header_len = struct.unpack_from("<I", data, 4)[0]
    if header_len < 2 or header_len > _HEADER_MAX_BYTES or 8 + header_len > len(data):
        raise ValueError("field cache header is invalid")
    try:
        header = json.loads(data[8:8 + header_len].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("field cache header is invalid") from exc
    payload = data[8 + header_len:]
    _validate_header(
        header,
        model_id=model_id,
        model_run=model_run,
        valid_time=valid_time,
        processing_version=processing_version,
        grid_version=grid_version,
    )
    if hashlib.sha256(payload).hexdigest() != header["payloadSha256"]:
        raise ValueError("field cache payload hash mismatch")
    uncompressed = int(header["uncompressedBytes"])
    if uncompressed > FIELD_CACHE_UNCOMPRESSED_MAX_BYTES:
        raise ValueError("field cache uncompressed payload exceeds budget")
    decompressor = zlib.decompressobj()
    raw = decompressor.decompress(payload, max_length=uncompressed)
    if len(raw) != uncompressed:
        raise ValueError("field cache payload size mismatch")
    extra = decompressor.decompress(b"", max_length=1)
    if extra:
        raise ValueError("field cache payload size mismatch")
    width = int(header["width"])
    height = int(header["height"])
    bit_count = width * height
    packed_len = (bit_count + 7) // 8
    packed_valid = raw[:packed_len]
    packed_values = raw[packed_len:]
    valid = np.unpackbits(np.frombuffer(packed_valid, dtype=np.uint8), bitorder="little", count=bit_count).astype(bool).reshape((height, width))
    expected = int(header["validCount"]) * 4
    if len(packed_values) != expected or int(valid.sum()) != int(header["validCount"]):
        raise ValueError("field cache validity count mismatch")
    values = np.full((height, width), np.nan, dtype=np.float32)
    decoded = _undo_xor_delta(packed_values, int(header["validCount"]))
    if decoded.size:
        if not np.all(np.isfinite(decoded) & (decoded >= 0)):
            raise ValueError("field cache contains invalid numeric values")
        values[valid] = decoded
    return values, valid


def _validate_header(
    header: dict[str, Any],
    *,
    model_id: str,
    model_run: str,
    valid_time: str,
    processing_version: str,
    grid_version: str,
) -> None:
    if header.get("fieldCacheVersion") != FIELD_CACHE_VERSION:
        raise ValueError("field cache version mismatch")
    if header.get("modelId") != model_id or header.get("modelRun") != model_run or header.get("validTime") != valid_time:
        raise ValueError("field cache identity mismatch")
    if header.get("processingVersion") != processing_version:
        raise ValueError("field cache processing version mismatch")
    if header.get("gridVersion") != grid_version:
        raise ValueError("field cache grid version mismatch")
    if header.get("units") != FIELD_CACHE_UNITS:
        raise ValueError("field cache units must be µg/m³")
    width = int(header.get("width") or 0)
    height = int(header.get("height") or 0)
    grid = _grid_for_version(grid_version)
    if (width, height) != (grid.width, grid.height):
        raise ValueError("field cache grid shape mismatch")
    if header.get("bounds") != CONTEXT_BOUNDS:
        raise ValueError("field cache bounds mismatch")
    if header.get("dtype") != "float32" or header.get("endianness") != "little":
        raise ValueError("field cache dtype mismatch")
    if header.get("valueEncoding") != "xor-delta-u32":
        raise ValueError("field cache value encoding mismatch")


def _xor_delta(values: np.ndarray) -> bytes:
    bits = np.ascontiguousarray(values, dtype="<f4").view("<u4")
    if bits.size == 0:
        return b""
    delta = np.empty_like(bits)
    delta[0] = bits[0]
    delta[1:] = bits[1:] ^ bits[:-1]
    return delta.tobytes()


def _undo_xor_delta(payload: bytes, valid_count: int) -> np.ndarray:
    expected = valid_count * 4
    if len(payload) != expected:
        raise ValueError("field cache validity count mismatch")
    delta = np.frombuffer(payload, dtype="<u4")
    if delta.size == 0:
        return np.empty((0,), dtype="<f4")
    bits = np.bitwise_xor.accumulate(delta)
    return bits.view("<f4")


class FieldCache:
    def __init__(self, store: FrameStore):
        self.store = store
        self._lock = threading.Lock()
        self.index: dict[str, str] = {}
        self.retained: dict[str, str] = {}
        try:
            raw = store.get_text(FIELD_CACHE_INDEX_PATH)
        except (OSError, UnicodeError, requests.RequestException, RuntimeError) as exc:
            if "deadline" in str(exc).lower():
                raise
            raw = None
        if not raw:
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, dict):
            return
        for key, path in entries.items():
            if isinstance(key, str) and isinstance(path, str) and _FIELD_PATH.fullmatch(path):
                self.index[key] = path

    def get(
        self,
        *,
        model_id: str,
        model_run: str,
        valid_time: str,
        processing_version: str,
        grid_version: str = BASE_RASTER_GRID.version,
    ) -> tuple[np.ndarray, np.ndarray] | None:
        identity = field_identity(model_id, model_run, valid_time, processing_version, grid_version)
        with self._lock:
            path = self.index.get(identity)
        metrics = current_metrics()
        if not path or not _FIELD_PATH.fullmatch(path):
            if metrics:
                metrics.increment("field_cache_misses")
            return None
        try:
            data = self.store.get_bytes(path, max_bytes=FIELD_CACHE_COMPRESSED_MAX_BYTES)
        except (OSError, requests.RequestException, RuntimeError) as exc:
            if "deadline" in str(exc).lower():
                raise
            with self._lock:
                self.index.pop(identity, None)
            if metrics:
                metrics.increment("field_cache_corrupt")
                metrics.increment("field_cache_misses")
            return None
        if not data:
            with self._lock:
                self.index.pop(identity, None)
            if metrics:
                metrics.increment("field_cache_misses")
            return None
        digest = path.split("/")[-2]
        if hashlib.sha256(data).hexdigest()[:20] != digest:
            with self._lock:
                self.index.pop(identity, None)
            if metrics:
                metrics.increment("field_cache_corrupt")
                metrics.increment("field_cache_misses")
            return None
        try:
            values, valid = decode_field(
                data,
                model_id=model_id,
                model_run=model_run,
                valid_time=valid_time,
                processing_version=processing_version,
                grid_version=grid_version,
            )
        except ValueError:
            with self._lock:
                self.index.pop(identity, None)
            if metrics:
                metrics.increment("field_cache_corrupt")
                metrics.increment("field_cache_misses")
            return None
        with self._lock:
            self.retained[identity] = path
        if metrics:
            metrics.increment("field_cache_hits")
            metrics.increment("field_cache_bytes", len(data))
        return values, valid

    def put(
        self,
        values: np.ndarray,
        valid: np.ndarray,
        *,
        model_id: str,
        model_run: str,
        valid_time: str,
        processing_version: str,
        grid_version: str = BASE_RASTER_GRID.version,
    ) -> str:
        encoded = encode_field(
            values,
            valid,
            model_id=model_id,
            model_run=model_run,
            valid_time=valid_time,
            processing_version=processing_version,
            grid_version=grid_version,
        )
        digest = hashlib.sha256(encoded).hexdigest()[:20]
        path = f"{FIELD_CACHE_PREFIX}/{digest}/field.bin"
        self.store.put_bytes(path, encoded, "application/octet-stream", cache_seconds=60 * 60 * 24 * 30, overwrite=False)
        identity = field_identity(model_id, model_run, valid_time, processing_version, grid_version)
        with self._lock:
            self.index[identity] = path
            self.retained[identity] = path
        metrics = current_metrics()
        if metrics:
            metrics.increment("field_cache_bytes", len(encoded))
        return path

    def remember(self, identity: str, path: str) -> None:
        if _FIELD_PATH.fullmatch(path):
            with self._lock:
                self.retained[identity] = path

    def checkpoint(self) -> None:
        with self._lock:
            entries = dict(self.index)
            self.store.put_json(FIELD_CACHE_INDEX_PATH, {"version": 1, "entries": entries}, cache_seconds=60, overwrite=True)
        metrics = current_metrics()
        if metrics:
            metrics.increment("cache_checkpoints")

    def publish_index(self) -> None:
        with self._lock:
            payload = {"version": 1, "entries": dict(self.retained)}
        self.store.put_json(FIELD_CACHE_INDEX_PATH, payload, cache_seconds=60, overwrite=True)

    def gc(self) -> None:
        with self._lock:
            keep = set(self.retained.values())
        keep.add(FIELD_CACHE_INDEX_PATH)
        stale = [path for path in self.store.list_prefix(FIELD_CACHE_PREFIX) if path not in keep and path != FIELD_CACHE_INDEX_PATH]
        normalized = []
        for path in stale:
            candidate = path.lstrip("/")
            if _FIELD_PATH.fullmatch(candidate):
                normalized.append(candidate)
            elif _FIELD_PATH.fullmatch(path):
                normalized.append(path)
        if normalized:
            self.store.delete_many(normalized)
