from __future__ import annotations

import hashlib
import json
import re
import struct
import tempfile
import threading
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ingest.context_contracts import CONTEXT_BOUNDS, CONTEXT_FIELD_BUDGET_BYTES
from ingest.forecast_raster import HrrrGrid
from ingest.local_store import FrameStore
from ingest.perf import current_metrics

NATIVE_FIELD_CACHE_VERSION = "native-field-cache-v1"
NATIVE_FIELD_CACHE_PREFIX = "cache/native-fields"
NATIVE_FIELD_CACHE_INDEX_PATH = f"{NATIVE_FIELD_CACHE_PREFIX}/index.json"
NATIVE_FIELD_CACHE_MAGIC = b"TSN1"
NATIVE_FIELD_COMPRESSION_LEVEL = 1
NATIVE_FIELD_CACHE_MAX_BYTES = CONTEXT_FIELD_BUDGET_BYTES
NATIVE_FIELD_CACHE_MAX_RAW_BYTES = 16 * 1024 * 1024
NATIVE_SPOOL_MAX_BYTES = 256 * 1024 * 1024
_FIELD_PATH = re.compile(rf"^{NATIVE_FIELD_CACHE_PREFIX}/[0-9a-f]{{20}}/field.bin$")
_HEADER_MAX_BYTES = 8_192


@dataclass(frozen=True)
class GeographicGrid:
    width: int
    height: int
    lon0: float
    lat0: float
    dx: float
    dy: float


@dataclass(frozen=True)
class NativeField:
    values: np.ndarray
    valid: np.ndarray
    grid: GeographicGrid | HrrrGrid
    model_id: str = ""
    model_run: str = ""
    valid_time: str = ""
    units: str = "µg/m³"
    processing_version: str = ""


def _grid_payload(grid: GeographicGrid | HrrrGrid) -> dict[str, Any]:
    return {"projection": "epsg4326" if isinstance(grid, GeographicGrid) else "hrrr-lcc", **asdict(grid)}


def _decode_grid(payload: Any, width: int, height: int) -> GeographicGrid | HrrrGrid:
    if not isinstance(payload, dict):
        raise ValueError("native field grid is invalid")
    values = {key: value for key, value in payload.items() if key != "projection"}
    try:
        if payload.get("projection") == "epsg4326":
            grid: GeographicGrid | HrrrGrid = GeographicGrid(**values)
        elif payload.get("projection") == "hrrr-lcc":
            grid = HrrrGrid(**values)
        else:
            raise ValueError("native field projection is invalid")
    except (TypeError, ValueError) as exc:
        raise ValueError("native field grid is invalid") from exc
    grid_width = grid.width if isinstance(grid, GeographicGrid) else grid.nx
    grid_height = grid.height if isinstance(grid, GeographicGrid) else grid.ny
    if (grid_width, grid_height) != (width, height):
        raise ValueError("native field grid shape mismatch")
    numeric = [float(value) for key, value in values.items() if key not in {"width", "height", "nx", "ny"}]
    if not numeric or not all(np.isfinite(numeric)):
        raise ValueError("native field grid metadata is invalid")
    if isinstance(grid, GeographicGrid) and (grid.dx <= 0 or grid.dy <= 0):
        raise ValueError("native geographic grid resolution is invalid")
    if isinstance(grid, GeographicGrid):
        east = grid.lon0 + (grid.width - 1) * grid.dx
        south = grid.lat0 - (grid.height - 1) * grid.dy
        if (
            grid.dx > 5
            or grid.dy > 5
            or not (-180.5 <= grid.lon0 <= 180.5 and -180.5 <= east <= 180.5)
            or not (-90.5 <= south <= 90.5 and -90.5 <= grid.lat0 <= 90.5)
            or east < CONTEXT_BOUNDS["west"]
            or grid.lon0 > CONTEXT_BOUNDS["east"]
            or grid.lat0 < CONTEXT_BOUNDS["south"]
            or south > CONTEXT_BOUNDS["north"]
        ):
            raise ValueError("native geographic grid bounds are invalid")
    if isinstance(grid, HrrrGrid) and (
        not (100 <= grid.nx <= 2_500 and 100 <= grid.ny <= 1_800)
        or not (1_000 <= grid.dx <= 10_000 and 1_000 <= grid.dy <= 10_000)
        or not (-90 <= grid.lat1 <= 90 and -180 <= grid.lon1 <= 180 and -180 <= grid.lov <= 180)
        or not (-90 <= grid.latin1 <= 90 and -90 <= grid.latin2 <= 90)
        or not (6_000_000 <= grid.radius <= 7_000_000)
    ):
        raise ValueError("native HRRR grid metadata is invalid")
    return grid


def native_field_identity(model_id: str, model_run: str, valid_time: str, processing_version: str) -> str:
    return f"{model_id}|{model_run}|{valid_time}|{processing_version}|{NATIVE_FIELD_CACHE_VERSION}"


def encode_native_field(
    field: NativeField,
    *,
    model_id: str,
    model_run: str,
    valid_time: str,
    processing_version: str,
) -> bytes:
    declared = (field.model_id, field.model_run, field.valid_time, field.processing_version)
    expected = (model_id, model_run, valid_time, processing_version)
    if any(declared) and declared != expected:
        raise ValueError("native field metadata identity mismatch")
    if field.units != "µg/m³":
        raise ValueError("native field units are invalid")
    values = np.asarray(field.values, dtype=np.float32)
    valid = np.asarray(field.valid, dtype=bool)
    if values.ndim != 2 or valid.shape != values.shape:
        raise ValueError("native field shape is invalid")
    height, width = values.shape
    _decode_grid(_grid_payload(field.grid), width, height)
    usable = valid & np.isfinite(values) & (values >= 0)
    packed_valid = np.packbits(usable.reshape(-1), bitorder="little").tobytes()
    packed_values = np.ascontiguousarray(values[usable], dtype="<f4").tobytes()
    raw = packed_valid + packed_values
    if len(raw) > NATIVE_FIELD_CACHE_MAX_RAW_BYTES:
        raise ValueError("native field payload exceeds uncompressed budget")
    payload = zlib.compress(raw, level=NATIVE_FIELD_COMPRESSION_LEVEL)
    header = {
        "cacheVersion": NATIVE_FIELD_CACHE_VERSION,
        "modelId": model_id,
        "modelRun": model_run,
        "validTime": valid_time,
        "processingVersion": processing_version,
        "units": "µg/m³",
        "width": width,
        "height": height,
        "grid": _grid_payload(field.grid),
        "validCount": int(usable.sum()),
        "rawBytes": len(raw),
        "payloadSha256": hashlib.sha256(payload).hexdigest(),
    }
    encoded_header = json.dumps(header, separators=(",", ":"), sort_keys=True).encode()
    blob = NATIVE_FIELD_CACHE_MAGIC + struct.pack("<I", len(encoded_header)) + encoded_header + payload
    if len(blob) > NATIVE_FIELD_CACHE_MAX_BYTES:
        raise ValueError("native field payload exceeds compressed budget")
    return blob


def decode_native_field(
    data: bytes,
    *,
    model_id: str,
    model_run: str,
    valid_time: str,
    processing_version: str,
) -> NativeField:
    if not data or len(data) < 8 or len(data) > NATIVE_FIELD_CACHE_MAX_BYTES or data[:4] != NATIVE_FIELD_CACHE_MAGIC:
        raise ValueError("native field cache object is invalid")
    header_len = struct.unpack_from("<I", data, 4)[0]
    if header_len < 2 or header_len > _HEADER_MAX_BYTES or 8 + header_len > len(data):
        raise ValueError("native field header is invalid")
    try:
        header = json.loads(data[8 : 8 + header_len])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("native field header is invalid") from exc
    expected = (NATIVE_FIELD_CACHE_VERSION, model_id, model_run, valid_time, processing_version, "µg/m³")
    actual = tuple(header.get(key) for key in ("cacheVersion", "modelId", "modelRun", "validTime", "processingVersion", "units"))
    if actual != expected:
        raise ValueError("native field cache identity mismatch")
    width, height = int(header.get("width") or 0), int(header.get("height") or 0)
    if width < 2 or height < 2 or width * height > 2_100_000:
        raise ValueError("native field dimensions are invalid")
    grid = _decode_grid(header.get("grid"), width, height)
    payload = data[8 + header_len :]
    if hashlib.sha256(payload).hexdigest() != header.get("payloadSha256"):
        raise ValueError("native field payload hash mismatch")
    raw_bytes = int(header.get("rawBytes") or 0)
    if raw_bytes < 1 or raw_bytes > NATIVE_FIELD_CACHE_MAX_RAW_BYTES:
        raise ValueError("native field payload size is invalid")
    decompressor = zlib.decompressobj()
    try:
        raw = decompressor.decompress(payload, raw_bytes + 1)
        if decompressor.unconsumed_tail:
            raise ValueError("native field payload exceeds declared size")
        raw += decompressor.flush()
    except zlib.error as exc:
        raise ValueError("native field payload is corrupt") from exc
    if len(raw) != raw_bytes or decompressor.unused_data or not decompressor.eof:
        raise ValueError("native field payload size mismatch")
    bit_count = width * height
    mask_bytes = (bit_count + 7) // 8
    valid = (
        np.unpackbits(np.frombuffer(raw[:mask_bytes], dtype=np.uint8), bitorder="little", count=bit_count)
        .astype(bool)
        .reshape(height, width)
    )
    valid_count = int(header.get("validCount") or 0)
    packed_values = raw[mask_bytes:]
    if valid_count != int(valid.sum()) or len(packed_values) != valid_count * 4:
        raise ValueError("native field validity count mismatch")
    decoded = np.frombuffer(packed_values, dtype="<f4")
    if not np.all(np.isfinite(decoded) & (decoded >= 0)):
        raise ValueError("native field contains invalid values")
    values = np.full((height, width), np.nan, dtype=np.float32)
    values[valid] = decoded
    return NativeField(values, valid, grid, model_id, model_run, valid_time, "µg/m³", processing_version)


class NativeFieldCache:
    def __init__(self, store: FrameStore, *, spool_max_bytes: int = 0):
        self.store = store
        self._lock = threading.Lock()
        self._spool: tempfile.TemporaryDirectory[str] | None = None
        self._spool_limit = max(0, spool_max_bytes)
        self._spool_bytes = 0
        self._spooled: dict[str, int] = {}
        self.index: dict[str, str] = {}
        self.retained: dict[str, str] = {}
        try:
            raw = store.get_text(NATIVE_FIELD_CACHE_INDEX_PATH)
        except (OSError, UnicodeError, RuntimeError) as exc:
            if "deadline" in str(exc).lower():
                raise
            raw = None
        try:
            root = json.loads(raw or "{}")
        except json.JSONDecodeError:
            root = {}
        entries = root.get("entries", {}) if isinstance(root, dict) else {}
        if isinstance(entries, dict):
            self.index = {
                key: path for key, path in entries.items() if isinstance(key, str) and isinstance(path, str) and _FIELD_PATH.fullmatch(path)
            }

    def close(self) -> None:
        with self._lock:
            if self._spool is not None:
                try:
                    self._spool.cleanup()
                except OSError:
                    pass  # Temporary storage cleanup must not mask publication or lease release.
                self._spool = None
            self._spooled.clear()
            self._spool_bytes = 0
            self._spool_limit = 0

    def _discard_spooled(self, path: str) -> None:
        with self._lock:
            self._spool_bytes -= self._spooled.pop(path, 0)
            if self._spool is not None:
                try:
                    (Path(self._spool.name) / path.split("/")[-2]).unlink(missing_ok=True)
                except OSError:
                    self._spool_limit = 0

    def _read_spooled(self, path: str) -> bytes | None:
        with self._lock:
            if path not in self._spooled or self._spool is None:
                return None
            try:
                with (Path(self._spool.name) / path.split("/")[-2]).open("rb") as handle:
                    return handle.read(self._spooled[path] + 1)
            except OSError:
                pass
        self._discard_spooled(path)
        return None

    def _remember(self, path: str, data: bytes) -> None:
        # Admit once, without eviction: staging scans all hours before composition.
        # An LRU smaller than that scan would evict useful objects before reuse.
        with self._lock:
            if path in self._spooled or self._spool_bytes + len(data) > self._spool_limit:
                return
            try:
                if self._spool is None:
                    self._spool = tempfile.TemporaryDirectory(prefix="native-fields-")
                target = Path(self._spool.name) / path.split("/")[-2]
                target.write_bytes(data)
                self._spooled[path] = len(data)
                self._spool_bytes += len(data)
                metrics = current_metrics()
                if metrics:
                    metrics.observe_native_spool_bytes(self._spool_bytes)
            except OSError:
                self._spool_limit = 0
                if self._spool is not None:
                    try:
                        (Path(self._spool.name) / path.split("/")[-2]).unlink(missing_ok=True)
                    except OSError:
                        pass

    def get(self, *, model_id: str, model_run: str, valid_time: str, processing_version: str, remember: bool = True) -> NativeField | None:
        identity = native_field_identity(model_id, model_run, valid_time, processing_version)
        path = self.index.get(identity)
        metrics = current_metrics()
        if not path:
            if metrics:
                metrics.increment("native_field_cache_misses")
            return None

        def validate(data: bytes | None) -> NativeField:
            if not data or hashlib.sha256(data).hexdigest()[:20] != path.split("/")[-2]:
                raise ValueError
            return decode_native_field(
                data, model_id=model_id, model_run=model_run, valid_time=valid_time, processing_version=processing_version
            )

        try:
            data = self._read_spooled(path)
            field = None
            if data is not None:
                try:
                    field = validate(data)
                except ValueError:
                    self._discard_spooled(path)
                else:
                    if metrics:
                        metrics.increment("native_field_spool_hits")
            if field is None:
                if metrics:
                    metrics.increment("native_field_storage_reads")
                data = self.store.get_bytes(path, max_bytes=NATIVE_FIELD_CACHE_MAX_BYTES)
                if metrics:
                    metrics.increment("native_field_storage_read_bytes", len(data or b""))
                field = validate(data)
                assert data is not None
                if remember:
                    self._remember(path, data)
        except (OSError, RuntimeError, ValueError) as exc:
            if "deadline" in str(exc).lower():
                raise
            with self._lock:
                self.index.pop(identity, None)
            if metrics:
                metrics.increment("native_field_cache_corrupt")
                metrics.increment("native_field_cache_misses")
            return None
        with self._lock:
            self.retained[identity] = path
        if metrics:
            metrics.increment("native_field_cache_hits")
            metrics.increment("native_field_cache_bytes", len(data or b""))
            width = field.grid.width if isinstance(field.grid, GeographicGrid) else field.grid.nx
            height = field.grid.height if isinstance(field.grid, GeographicGrid) else field.grid.ny
            metrics.set_native_dimensions(model_id, width, height)
        return field

    def put(self, field: NativeField, *, model_id: str, model_run: str, valid_time: str, processing_version: str) -> str:
        data = encode_native_field(
            field, model_id=model_id, model_run=model_run, valid_time=valid_time, processing_version=processing_version
        )
        digest = hashlib.sha256(data).hexdigest()[:20]
        path = f"{NATIVE_FIELD_CACHE_PREFIX}/{digest}/field.bin"
        # A validated refetch must repair corrupt bytes already at this digest.
        # The same content-addressed path always receives the same valid bytes.
        self.store.put_bytes(path, data, "application/octet-stream", cache_seconds=60 * 60 * 24 * 30, overwrite=True)
        self._remember(path, data)
        identity = native_field_identity(model_id, model_run, valid_time, processing_version)
        with self._lock:
            self.index[identity] = path
            self.retained[identity] = path
        metrics = current_metrics()
        if metrics:
            metrics.increment("native_field_storage_writes")
            metrics.increment("native_field_storage_write_bytes", len(data))
            metrics.increment("native_field_cache_bytes", len(data))
            metrics.set_native_dimensions(model_id, field.values.shape[1], field.values.shape[0])
        return path

    def checkpoint(self) -> None:
        with self._lock:
            entries = dict(self.index)
        self.store.put_json(NATIVE_FIELD_CACHE_INDEX_PATH, {"version": 1, "entries": entries}, cache_seconds=60, overwrite=True)
        metrics = current_metrics()
        if metrics:
            metrics.increment("cache_checkpoints")

    def publish_index(self) -> None:
        with self._lock:
            entries = dict(self.retained)
        self.store.put_json(NATIVE_FIELD_CACHE_INDEX_PATH, {"version": 1, "entries": entries}, cache_seconds=60, overwrite=True)

    def gc(self) -> None:
        with self._lock:
            keep = set(self.retained.values()) | {NATIVE_FIELD_CACHE_INDEX_PATH}
        stale = []
        for path in self.store.list_prefix(NATIVE_FIELD_CACHE_PREFIX):
            candidate = path.lstrip("/")
            if candidate not in keep and _FIELD_PATH.fullmatch(candidate):
                stale.append(candidate)
        if stale:
            self.store.delete_many(stale)
