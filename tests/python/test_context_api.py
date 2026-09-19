from __future__ import annotations

import json
import logging
from email.message import Message
from io import BytesIO
from typing import Any

import pytest

from api.context import handler

SECRET = "secret-secret-secret"


def invoke(monkeypatch: Any, *, method: str = "GET", authorization: str | None = None, run: Any = None) -> Any:
    monkeypatch.setattr("ingest.cron.load_env_files", lambda: None)
    monkeypatch.setattr("ingest.config.load_env_files", lambda: None)
    if run is not None:
        monkeypatch.setattr("api.context._run", run)
    instance = handler.__new__(handler)
    headers = Message()
    if authorization is not None:
        headers["Authorization"] = authorization
    instance.headers = headers
    instance.wfile = BytesIO()
    instance.request_version = "HTTP/1.1"
    instance.client_address = ("127.0.0.1", 0)
    instance.command = method
    instance.requestline = f"{method} /api/context HTTP/1.1"
    instance.close_connection = False
    getattr(instance, f"do_{method}")()
    head, _, body = instance.wfile.getvalue().partition(b"\r\n\r\n")
    status = int(head.split(b"\r\n", 1)[0].split()[1])
    return status, json.loads(body) if body else None


@pytest.fixture(autouse=True)
def cron_secret(monkeypatch: Any) -> None:
    monkeypatch.setenv("CRON_SECRET", SECRET)


@pytest.mark.parametrize("authorization", [None, "", "Bearer wrong", "Bearer short"])
def test_cron_rejects_invalid_auth_without_running(monkeypatch: Any, authorization: Any) -> None:
    calls: list[object] = []
    status, payload = invoke(monkeypatch, authorization=authorization, run=lambda settings: calls.append(settings))
    assert status == 401
    assert payload == {"ok": False, "error": "Unauthorized"}
    assert calls == []


def test_cron_get_runs_once_and_returns_result(monkeypatch: Any) -> None:
    calls: list[object] = []

    def run(settings: Any) -> dict[str, Any]:
        calls.append(settings)
        return {"ok": True, "mode": "demo"}

    status, payload = invoke(
        monkeypatch,
        authorization=f"Bearer {SECRET}",
        run=run,
    )
    assert status == 200
    assert payload == {"ok": True, "mode": "demo"}
    assert len(calls) == 1


def test_cron_head_authenticates_without_ingest(monkeypatch: Any) -> None:
    calls: list[object] = []
    status, payload = invoke(
        monkeypatch,
        method="HEAD",
        authorization=f"Bearer {SECRET}",
        run=lambda settings: calls.append(settings),
    )
    assert status == 200
    assert payload is None
    assert calls == []


def test_cron_pipeline_failure_is_structured(monkeypatch: Any) -> None:
    status, payload = invoke(
        monkeypatch,
        authorization=f"Bearer {SECRET}",
        run=lambda _settings: {"ok": False, "error": "provider failed"},
    )
    assert status == 500
    assert payload == {"ok": False, "error": "provider failed"}


def test_cron_exception_is_redacted_in_response_and_logs(monkeypatch: Any, caplog: Any) -> None:
    def fail(_settings: Any) -> None:
        raise RuntimeError("CRON_SECRET=should-not-leak BLOB_READ_WRITE_TOKEN=also-secret")

    caplog.set_level(logging.ERROR)
    status, payload = invoke(monkeypatch, authorization=f"Bearer {SECRET}", run=fail)
    assert status == 500
    assert payload == {"ok": False, "error": "internal error"}
    assert "should-not-leak" not in caplog.text
    assert "also-secret" not in caplog.text


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
def test_cron_rejects_other_methods(monkeypatch: Any, method: Any) -> None:
    status, payload = invoke(monkeypatch, method=method, authorization=f"Bearer {SECRET}")
    assert status == 405
    assert payload == {"ok": False, "error": "Method not allowed"}
