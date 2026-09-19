from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

LOGGER = logging.getLogger("titanskies.perf")
DEFAULT_BUDGET_SECONDS = 300.0
FINALIZE_RESERVE_SECONDS = 60.0
HRRR_MIN_SECONDS = 90.0
FORECAST_WAVE_MIN_SECONDS = 90.0
COMPOSE_MIN_SECONDS = 20.0
SCAN_MIN_SECONDS = 45.0
SPLIT_P95_SECONDS = 600.0
SPLIT_RSS_BYTES = int(1.5 * 1024 * 1024 * 1024)


def should_split(*, elapsed_seconds: float, rss_bytes: int = 0) -> bool:
    """True only when a single-function run still misses the 600s / 1.5 GiB gates.

    The local ingest process remains the only writer of `context/latest.json`.
    """
    return elapsed_seconds >= SPLIT_P95_SECONDS or rss_bytes >= SPLIT_RSS_BYTES


@dataclass
class RunMetrics:
    started: float = field(default_factory=time.perf_counter)
    cpu_started: float = field(default_factory=time.process_time)
    phases: dict[str, float] = field(default_factory=dict)
    source_seconds: dict[str, float] = field(default_factory=dict)
    http_requests: int = 0
    storage_reads: int = 0
    storage_writes: int = 0
    storage_lists: int = 0
    storage_deletes: int = 0
    storage_read_bytes: int = 0
    storage_write_bytes: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    forecast_downloaded: int = 0
    forecast_reused: int = 0
    field_cache_hits: int = 0
    field_cache_misses: int = 0
    field_cache_corrupt: int = 0
    field_cache_bytes: int = 0
    native_field_cache_hits: int = 0
    native_field_cache_misses: int = 0
    native_field_cache_corrupt: int = 0
    native_field_cache_bytes: int = 0
    native_field_storage_reads: int = 0
    native_field_storage_read_bytes: int = 0
    native_field_storage_writes: int = 0
    native_field_storage_write_bytes: int = 0
    native_field_spool_hits: int = 0
    native_field_spool_peak_bytes: int = 0
    native_dimensions: dict[str, tuple[int, int]] = field(default_factory=dict)
    bilinear_tile_reprojections: int = 0
    peak_working_bytes: int = 0
    completeness_gate_failures: int = 0
    seam_gate_failures: int = 0
    budget_gate_failures: int = 0
    source_pngs_skipped: int = 0
    provider_retries: int = 0
    provider_timeouts: int = 0
    provider_circuit_opens: int = 0
    deadline_cancellations: int = 0
    cache_checkpoints: int = 0
    numeric_frames_planned: int = 0
    numeric_frames_skipped: int = 0
    detail_fetches: int = 0
    detail_cache_hits: int = 0
    detail_frames_composed: int = 0
    detail_tiles_encoded: int = 0
    detail_tiles_written: int = 0
    detail_tiles_reused: int = 0
    detail_failures: int = 0
    provider_phases: dict[str, float] = field(default_factory=dict)
    provider_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    publication_status: str = "not-published"
    forecast_expected_frames: int = 0
    forecast_candidate_frames: int = 0
    missing_forecast_hours: list[str] = field(default_factory=list)
    deadline_skips: list[str] = field(default_factory=list)
    processed_scans: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add_phase(self, name: str, seconds: float) -> None:
        with self._lock:
            self.phases[name] = round(self.phases.get(name, 0.0) + seconds, 3)

    def add_source(self, name: str, seconds: float) -> None:
        with self._lock:
            self.source_seconds[name] = round(self.source_seconds.get(name, 0.0) + seconds, 3)

    def add_provider_phase(self, provider: str, phase: str, seconds: float) -> None:
        with self._lock:
            key = f"{provider}.{phase}"
            self.provider_phases[key] = round(self.provider_phases.get(key, 0.0) + seconds, 3)

    def add_provider_count(self, provider: str, kind: str, amount: int) -> None:
        with self._lock:
            counts = self.provider_counts.setdefault(provider, {})
            counts[kind] = counts.get(kind, 0) + amount

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            current = getattr(self, name)
            setattr(self, name, current + amount)

    def set_native_dimensions(self, provider: str, width: int, height: int) -> None:
        with self._lock:
            self.native_dimensions[provider] = (width, height)

    def observe_working_bytes(self, amount: int) -> None:
        with self._lock:
            self.peak_working_bytes = max(self.peak_working_bytes, amount)

    def observe_native_spool_bytes(self, amount: int) -> None:
        with self._lock:
            self.native_field_spool_peak_bytes = max(self.native_field_spool_peak_bytes, amount)

    def skip(self, reason: str) -> None:
        with self._lock:
            self.deadline_skips.append(reason)

    def set_forecast_window(self, expected: list[str], actual: list[str]) -> None:
        with self._lock:
            actual_set = set(actual)
            self.forecast_expected_frames = len(expected)
            self.forecast_candidate_frames = len(actual)
            self.missing_forecast_hours = [value for value in expected if value not in actual_set]

    def elapsed(self) -> float:
        return round(time.perf_counter() - self.started, 3)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "elapsedSeconds": self.elapsed(),
                "cpuSeconds": round(time.process_time() - self.cpu_started, 3),
                "phaseSeconds": dict(self.phases),
                "sourceSeconds": dict(self.source_seconds),
                "httpRequests": self.http_requests,
                "storageReads": self.storage_reads,
                "storageWrites": self.storage_writes,
                "storageLists": self.storage_lists,
                "storageDeletes": self.storage_deletes,
                "storageReadBytes": self.storage_read_bytes,
                "storageWriteBytes": self.storage_write_bytes,
                "bytesIn": self.bytes_in,
                "bytesOut": self.bytes_out,
                "forecastDownloaded": self.forecast_downloaded,
                "forecastReused": self.forecast_reused,
                "fieldCacheHits": self.field_cache_hits,
                "fieldCacheMisses": self.field_cache_misses,
                "fieldCacheCorrupt": self.field_cache_corrupt,
                "fieldCacheBytes": self.field_cache_bytes,
                "nativeFieldCacheHits": self.native_field_cache_hits,
                "nativeFieldCacheMisses": self.native_field_cache_misses,
                "nativeFieldCacheCorrupt": self.native_field_cache_corrupt,
                "nativeFieldCacheBytes": self.native_field_cache_bytes,
                "nativeFieldStorageReads": self.native_field_storage_reads,
                "nativeFieldStorageReadBytes": self.native_field_storage_read_bytes,
                "nativeFieldStorageWrites": self.native_field_storage_writes,
                "nativeFieldStorageWriteBytes": self.native_field_storage_write_bytes,
                "nativeFieldSpoolHits": self.native_field_spool_hits,
                "nativeFieldSpoolPeakBytes": self.native_field_spool_peak_bytes,
                "nativeDimensions": {name: list(shape) for name, shape in self.native_dimensions.items()},
                "bilinearTileReprojections": self.bilinear_tile_reprojections,
                "peakWorkingBytes": self.peak_working_bytes,
                "completenessGateFailures": self.completeness_gate_failures,
                "seamGateFailures": self.seam_gate_failures,
                "budgetGateFailures": self.budget_gate_failures,
                "sourcePngsSkipped": self.source_pngs_skipped,
                "providerRetries": self.provider_retries,
                "providerTimeouts": self.provider_timeouts,
                "providerCircuitOpens": self.provider_circuit_opens,
                "deadlineCancellations": self.deadline_cancellations,
                "cacheCheckpoints": self.cache_checkpoints,
                "numericFramesPlanned": self.numeric_frames_planned,
                "numericFramesSkipped": self.numeric_frames_skipped,
                "detailFetches": self.detail_fetches,
                "detailCacheHits": self.detail_cache_hits,
                "detailFramesComposed": self.detail_frames_composed,
                "detailTilesEncoded": self.detail_tiles_encoded,
                "detailTilesWritten": self.detail_tiles_written,
                "detailTilesReused": self.detail_tiles_reused,
                "detailFailures": self.detail_failures,
                "providerPhaseSeconds": dict(self.provider_phases),
                "providerFrameCounts": {name: dict(counts) for name, counts in self.provider_counts.items()},
                "publicationStatus": self.publication_status,
                "forecastExpectedFrames": self.forecast_expected_frames,
                "forecastCandidateFrames": self.forecast_candidate_frames,
                "missingForecastHours": list(self.missing_forecast_hours),
                "forecastWaveSeconds": self.phases.get("forecastWave"),
                "deadlineSkips": list(self.deadline_skips),
                "processedScans": self.processed_scans,
                "splitRecommended": should_split(elapsed_seconds=self.elapsed()),
            }

    def log_summary(self, kind: str, extra: dict[str, Any] | None = None) -> None:
        payload = {"kind": kind, **self.snapshot(), **(extra or {})}
        LOGGER.info("ingest.summary %s", json.dumps(payload, separators=(",", ":")))


@dataclass
class RunBudget:
    seconds: float = DEFAULT_BUDGET_SECONDS
    started: float = field(default_factory=time.perf_counter)

    def remaining(self) -> float:
        return self.seconds - (time.perf_counter() - self.started)

    def finalization_reserve(self) -> float:
        reserve = _ACQUISITION_RESERVE.get()
        return min(reserve, self.seconds) if reserve is not None else min(FINALIZE_RESERVE_SECONDS, self.seconds * 0.2)

    def acquisition_remaining(self) -> float:
        return self.remaining() - self.finalization_reserve()

    def allow(self, estimated: float, *, reason: str) -> bool:
        if self.remaining() >= estimated:
            return True
        metrics = current_metrics()
        if metrics:
            metrics.skip(reason)
        LOGGER.warning("ingest deadline skip %s remaining=%.1f needed=%.1f", reason, self.remaining(), estimated)
        return False

    def timeout(self, requested: float, *, reserve_finalization: bool = True) -> float | None:
        remaining = self.acquisition_remaining() if reserve_finalization else self.remaining()
        if remaining <= 0:
            metrics = current_metrics()
            if metrics:
                metrics.increment("deadline_cancellations")
            return None
        return min(float(requested), remaining)


_METRICS: ContextVar[RunMetrics | None] = ContextVar("ingest_metrics", default=None)
_BUDGET: ContextVar[RunBudget | None] = ContextVar("ingest_budget", default=None)
_ASSET_MEMO: ContextVar[dict[str, str] | None] = ContextVar("ingest_asset_memo", default=None)
_ACQUISITION_RESERVE: ContextVar[float | None] = ContextVar("ingest_acquisition_reserve", default=None)


def current_metrics() -> RunMetrics | None:
    return _METRICS.get()


def current_budget() -> RunBudget | None:
    return _BUDGET.get()


def current_asset_memo() -> dict[str, str]:
    memo = _ASSET_MEMO.get()
    if memo is None:
        memo = {}
        _ASSET_MEMO.set(memo)
    return memo


def record_http(*, bytes_in: int = 0, bytes_out: int = 0) -> None:
    metrics = current_metrics()
    if metrics:
        metrics.increment("http_requests")
        if bytes_in:
            metrics.increment("bytes_in", bytes_in)
        if bytes_out:
            metrics.increment("bytes_out", bytes_out)


def bounded_timeout(requested: float, *, reserve_finalization: bool = True) -> float | None:
    budget = current_budget()
    return float(requested) if budget is None else budget.timeout(requested, reserve_finalization=reserve_finalization)


@contextmanager
def acquisition_reserve(seconds: float) -> Iterator[None]:
    token = _ACQUISITION_RESERVE.set(max(0.0, float(seconds)))
    try:
        yield
    finally:
        _ACQUISITION_RESERVE.reset(token)


def record_storage(kind: str, *, count: int = 1, bytes_in: int = 0, bytes_out: int = 0) -> None:
    metrics = current_metrics()
    if not metrics:
        return
    key = {"read": "storage_reads", "write": "storage_writes", "list": "storage_lists", "delete": "storage_deletes"}[kind]
    metrics.increment(key, count)
    if bytes_in:
        metrics.increment("storage_read_bytes", bytes_in)
    if bytes_out:
        metrics.increment("storage_write_bytes", bytes_out)


@contextmanager
def timed_phase(name: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        metrics = current_metrics()
        if metrics:
            metrics.add_phase(name, time.perf_counter() - started)


@contextmanager
def timed_source(name: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        metrics = current_metrics()
        if metrics:
            metrics.add_source(name, time.perf_counter() - started)


@contextmanager
def timed_provider_phase(provider: str, phase: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        metrics = current_metrics()
        if metrics:
            metrics.add_provider_phase(provider, phase, time.perf_counter() - started)


@contextmanager
def ingest_run(*, budget_seconds: float = DEFAULT_BUDGET_SECONDS) -> Iterator[tuple[RunMetrics, RunBudget]]:
    metrics = RunMetrics()
    budget = RunBudget(seconds=budget_seconds)
    memo: dict[str, str] = {}
    tokens = (_METRICS.set(metrics), _BUDGET.set(budget), _ASSET_MEMO.set(memo))
    try:
        yield metrics, budget
    finally:
        _METRICS.reset(tokens[0])
        _BUDGET.reset(tokens[1])
        _ASSET_MEMO.reset(tokens[2])
