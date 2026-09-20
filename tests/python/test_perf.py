from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import patch

from ingest.http_pool import map_bounded, map_isolated
from ingest.perf import (
    SPLIT_RSS_BYTES,
    acquisition_reserve,
    bounded_timeout,
    current_asset_memo,
    current_budget,
    current_metrics,
    ingest_run,
    record_http,
    should_split,
)


def test_run_budget_skips_when_deadline_is_exhausted() -> None:
    with ingest_run(budget_seconds=0.01) as (metrics, budget):
        time.sleep(0.02)
        assert budget.allow(1.0, reason="hrrr") is False
        assert "hrrr" in metrics.deadline_skips
        snapshot = metrics.snapshot()
        assert snapshot["deadlineSkips"] == ["hrrr"]
        assert snapshot["splitRecommended"] is False


def test_request_timeout_preserves_a_finalization_reserve() -> None:
    with ingest_run(budget_seconds=300) as (_metrics, _budget):
        assert 239 <= (bounded_timeout(999) or 0) <= 240
        assert 299 <= (bounded_timeout(999, reserve_finalization=False) or 0) <= 300


def test_v8_request_timeout_preserves_its_composition_reserve() -> None:
    with ingest_run(budget_seconds=300) as (_metrics, budget), acquisition_reserve(120):
        budget.started -= 179
        assert 0.9 <= (bounded_timeout(90) or 0) <= 1.0


def test_request_timeout_never_exceeds_a_short_remaining_budget() -> None:
    with (
        ingest_run(budget_seconds=1) as (_metrics, budget),
        acquisition_reserve(0),
        patch("ingest.perf.time.perf_counter", return_value=100.0),
    ):
        budget.started = 99.05
        timeout = bounded_timeout(30)
        assert timeout is not None
        assert 0 < timeout < 0.1


def test_split_gate_uses_duration_and_rss_thresholds() -> None:
    assert should_split(elapsed_seconds=599.9) is False
    assert should_split(elapsed_seconds=600) is True
    assert should_split(elapsed_seconds=10, rss_bytes=SPLIT_RSS_BYTES) is True


def test_map_isolated_captures_per_item_failures() -> None:
    barrier = threading.Barrier(2, timeout=2)

    def worker(item: str) -> str:
        barrier.wait()
        if item == "bad":
            raise RuntimeError("boom")
        return item.upper()

    results = map_isolated(["ok", "bad"], worker, workers=2)
    assert results[0] == "OK"
    assert isinstance(results[1], RuntimeError)


def test_run_metrics_are_visible_to_worker_threads() -> None:
    def worker(_item: int) -> int:
        record_http(bytes_in=4)
        return _item

    with ingest_run() as (metrics, _budget):
        map_bounded([1, 2, 3], worker, workers=3)
        snapshot = metrics.snapshot()
    assert snapshot["httpRequests"] == 3
    assert snapshot["bytesIn"] == 12


def test_overlapping_runs_keep_worker_context_isolated() -> None:
    first_active = threading.Event()
    second_active = threading.Event()
    first_sampled = threading.Event()
    second_done = threading.Event()
    results: dict[str, tuple[bool, bool, str | None]] = {}

    def sample(metrics: Any, budget: Any) -> tuple[bool, bool, str | None]:
        return current_metrics() is metrics, current_budget() is budget, current_asset_memo().get("run")

    def first() -> None:
        with ingest_run(budget_seconds=111) as (metrics, budget):
            current_asset_memo()["run"] = "first"
            first_active.set()
            assert second_active.wait(2)
            isolated = map_isolated([0, 1], lambda _item: sample(metrics, budget), workers=2)[0]
            assert not isinstance(isolated, BaseException)
            results["first_during"] = isolated
            first_sampled.set()
            assert second_done.wait(2)
            results["first_after"] = map_bounded([0, 1], lambda _item: sample(metrics, budget), workers=2)[0]

    def second() -> None:
        assert first_active.wait(2)
        with ingest_run(budget_seconds=222) as (metrics, budget):
            current_asset_memo()["run"] = "second"
            second_active.set()
            results["second"] = map_bounded([0, 1], lambda _item: sample(metrics, budget), workers=2)[0]
            assert first_sampled.wait(2)
        second_done.set()

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()

    assert results == {
        "first_during": (True, True, "first"),
        "second": (True, True, "second"),
        "first_after": (True, True, "first"),
    }


def test_provider_frame_metrics_are_reported() -> None:
    with ingest_run() as (metrics, _budget):
        metrics.add_provider_count("firework", "planned", 25)
        metrics.add_provider_count("firework", "reused", 10)
        metrics.set_forecast_window(["00", "01", "02"], ["00", "02"])
        snapshot = metrics.snapshot()
    assert snapshot["providerFrameCounts"]["firework"] == {"planned": 25, "reused": 10}
    assert snapshot["forecastExpectedFrames"] == 3
    assert snapshot["forecastCandidateFrames"] == 2
    assert snapshot["missingForecastHours"] == ["01"]


def test_map_bounded_preserves_order_under_concurrency() -> None:
    barrier = threading.Barrier(3, timeout=2)

    def worker(item: int) -> int:
        barrier.wait()
        return item * 2

    assert map_bounded([1, 2, 3], worker, workers=3) == [2, 4, 6]
