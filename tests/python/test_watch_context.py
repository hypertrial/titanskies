from __future__ import annotations

from typing import Any
from unittest.mock import patch

from scripts.watch_context import main, watch_interval_seconds


def test_watch_interval_clamps_and_defaults() -> None:
    assert watch_interval_seconds(None) == 900
    assert watch_interval_seconds("") == 900
    assert watch_interval_seconds("nope") == 900
    assert watch_interval_seconds("10") == 60
    assert watch_interval_seconds("7200") == 3600


def test_demo_once_runs_ingest(monkeypatch: Any) -> None:
    monkeypatch.setattr("ingest.config.load_env_files", lambda: None)
    monkeypatch.setenv("CONTEXT_SOURCE", "demo")
    with patch("ingest.context_pipeline.run_context_ingest", return_value={"ok": True, "mode": "demo"}) as run:
        with patch("builtins.print"):
            assert main(["--once"]) == 0
        run.assert_called_once()


def test_demo_watch_publishes_immediately_before_waiting(monkeypatch: Any) -> None:
    monkeypatch.setattr("ingest.config.load_env_files", lambda: None)
    monkeypatch.setenv("CONTEXT_SOURCE", "demo")
    with patch("ingest.context_pipeline.run_context_ingest", return_value={"ok": True, "mode": "demo"}) as run:
        with patch("time.sleep", side_effect=KeyboardInterrupt):
            with patch("builtins.print"):
                assert main([]) == 0
        run.assert_called_once()


def test_watch_interval_is_measured_from_ingest_start(monkeypatch: Any) -> None:
    monkeypatch.setattr("ingest.config.load_env_files", lambda: None)
    monkeypatch.setenv("CONTEXT_SOURCE", "live")
    with patch("ingest.context_pipeline.run_context_ingest", return_value={"ok": True, "mode": "live"}) as run:
        with patch("time.monotonic", side_effect=[100.0, 160.0]):
            with patch("time.sleep", side_effect=KeyboardInterrupt) as sleep:
                with patch("builtins.print"):
                    assert main([]) == 0
        run.assert_called_once()
        sleep.assert_called_once_with(840.0)


def test_skipped_lease_retries_without_waiting_a_full_interval(monkeypatch: Any) -> None:
    monkeypatch.setattr("ingest.config.load_env_files", lambda: None)
    monkeypatch.setenv("CONTEXT_SOURCE", "live")
    with patch("ingest.context_pipeline.run_context_ingest", return_value={"ok": True, "skipped": True}) as run:
        with patch("time.monotonic", return_value=100.0):
            with patch("time.sleep", side_effect=KeyboardInterrupt) as sleep:
                with patch("builtins.print"):
                    assert main([]) == 0
        run.assert_called_once()
        sleep.assert_called_once_with(60)


def test_live_once_runs_ingest(monkeypatch: Any) -> None:
    monkeypatch.setattr("ingest.config.load_env_files", lambda: None)
    monkeypatch.setenv("CONTEXT_SOURCE", "live")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    with patch("ingest.context_pipeline.run_context_ingest", return_value={"ok": True, "mode": "live"}) as run:
        with patch("builtins.print"):
            assert main(["--once"]) == 0
        run.assert_called_once()


def test_live_once_failure_exits_nonzero(monkeypatch: Any) -> None:
    monkeypatch.setattr("ingest.config.load_env_files", lambda: None)
    monkeypatch.setenv("CONTEXT_SOURCE", "live")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    with patch("ingest.context_pipeline.run_context_ingest", return_value={"ok": False, "error": "GeoMet timeout"}):
        with patch("builtins.print"):
            assert main(["--once"]) == 1
