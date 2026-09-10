from __future__ import annotations

from unittest.mock import Mock, patch

from ingest.http import GLOBAL_HTTP_LIMIT, HTTP_LIMIT_MAX, HttpFetchError, configure_http_limit, fetch, session_get
from ingest.http_pool import map_bounded, map_isolated, map_isolated_batches
from ingest.perf import ingest_run


def test_http_defaults_and_clamp() -> None:
    assert GLOBAL_HTTP_LIMIT == 12
    assert HTTP_LIMIT_MAX == 16
    configure_http_limit(99)
    configure_http_limit(12)


def test_http_429_sets_host_cooldown() -> None:
    first = Mock()
    first.__enter__ = Mock(return_value=first)
    first.__exit__ = Mock(return_value=False)
    first.status_code = 429
    first.headers = {"Retry-After": "0.2"}
    first.url = "https://nomads.ncep.noaa.gov/smoke"
    first.iter_content = Mock(return_value=iter([]))

    second = Mock()
    second.__enter__ = Mock(return_value=second)
    second.__exit__ = Mock(return_value=False)
    second.status_code = 200
    second.headers = {}
    second.url = "https://nomads.ncep.noaa.gov/smoke"
    second.iter_content = Mock(return_value=iter([b"GRIB"]))

    session = Mock()
    session.request.side_effect = [first, second]
    with patch("ingest.http._session", return_value=session), patch("ingest.http.time.sleep") as slept:
        body = fetch("https://nomads.ncep.noaa.gov/smoke", hosts=frozenset({"nomads.ncep.noaa.gov"}), retries=1)
    assert body == b"GRIB"
    assert slept.called


def test_http_error_exposes_provider_outage_state() -> None:
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.status_code = 503
    response.reason = "Unavailable"
    response.headers = {}
    response.url = "https://nomads.ncep.noaa.gov/smoke"
    session = Mock()
    session.request.return_value = response
    with patch("ingest.http._session", return_value=session):
        try:
            fetch("https://nomads.ncep.noaa.gov/smoke", hosts=frozenset({"nomads.ncep.noaa.gov"}), retries=0)
        except HttpFetchError as exc:
            assert exc.status == 503
            assert exc.provider_outage is True
        else:
            raise AssertionError("expected provider failure")


def test_transient_redirect_retries_the_original_allowed_url() -> None:
    redirect = Mock()
    redirect.__enter__ = Mock(return_value=redirect)
    redirect.__exit__ = Mock(return_value=False)
    redirect.status_code = 302
    redirect.headers = {"Location": ""}
    redirect.url = "https://nomads.ncep.noaa.gov/smoke"

    success = Mock()
    success.__enter__ = Mock(return_value=success)
    success.__exit__ = Mock(return_value=False)
    success.status_code = 200
    success.headers = {}
    success.url = "https://nomads.ncep.noaa.gov/smoke"
    success.iter_content = Mock(return_value=iter([b"GRIB"]))

    session = Mock()
    session.request.side_effect = [redirect, success]
    with patch("ingest.http._session", return_value=session), patch("ingest.http.time.sleep"):
        body = fetch("https://nomads.ncep.noaa.gov/smoke", hosts=frozenset({"nomads.ncep.noaa.gov"}), retries=1)

    assert body == b"GRIB"
    assert session.request.call_count == 2
    assert {call.args[1] for call in session.request.call_args_list} == {"https://nomads.ncep.noaa.gov/smoke"}
    assert all(call.kwargs["allow_redirects"] is False for call in session.request.call_args_list)


def test_persistent_empty_redirect_opens_the_provider_outage_circuit() -> None:
    responses = []
    for _ in range(2):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.status_code = 302
        response.headers = {"Location": ""}
        response.url = "https://nomads.ncep.noaa.gov/smoke"
        responses.append(response)

    session = Mock()
    session.request.side_effect = responses
    with patch("ingest.http._session", return_value=session), patch("ingest.http.time.sleep"):
        try:
            fetch("https://nomads.ncep.noaa.gov/smoke", hosts=frozenset({"nomads.ncep.noaa.gov"}), retries=1)
        except HttpFetchError as exc:
            assert exc.status == 302
            assert exc.retryable is True
            assert exc.provider_outage is True
        else:
            raise AssertionError("expected persistent empty redirect failure")

    assert session.request.call_count == 2


def test_redirect_target_is_rejected_without_retry_or_following() -> None:
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.status_code = 307
    response.headers = {"Location": "https://attacker.example/forecast"}
    response.url = "https://nomads.ncep.noaa.gov/smoke"

    session = Mock()
    session.request.return_value = response
    with patch("ingest.http._session", return_value=session), patch("ingest.http.time.sleep"):
        try:
            fetch("https://nomads.ncep.noaa.gov/smoke", hosts=frozenset({"nomads.ncep.noaa.gov"}), retries=1)
        except HttpFetchError as exc:
            assert exc.status == 307
            assert exc.retryable is False
            assert exc.provider_outage is False
        else:
            raise AssertionError("expected persistent redirect failure")

    session.request.assert_called_once()
    assert session.request.call_args.args[1] == "https://nomads.ncep.noaa.gov/smoke"
    assert session.request.call_args.kwargs["allow_redirects"] is False


def test_session_get_uses_https_allowlist_and_response_limit() -> None:
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.status_code = 200
    response.headers = {}
    response.url = "https://example.test/data"
    response.iter_content = Mock(return_value=iter([b"1234", b"5678"]))
    session = Mock()
    session.request.return_value = response

    with patch("ingest.http._session", return_value=session):
        try:
            session_get("https://example.test/data", hosts=frozenset({"example.test"}), max_bytes=7)
        except RuntimeError as exc:
            assert "exceeds 7 bytes" in str(exc)
        else:
            raise AssertionError("oversized response was accepted")

    try:
        session_get("http://example.test/data", hosts=frozenset({"example.test"}))
    except ValueError as exc:
        assert "blocked host" in str(exc)
    else:
        raise AssertionError("insecure provider URL was accepted")


def test_not_modified_without_location_is_not_a_provider_outage() -> None:
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.status_code = 304
    response.headers = {}
    response.url = "https://nomads.ncep.noaa.gov/smoke"

    session = Mock()
    session.request.return_value = response
    with patch("ingest.http._session", return_value=session):
        try:
            fetch("https://nomads.ncep.noaa.gov/smoke", hosts=frozenset({"nomads.ncep.noaa.gov"}), retries=1)
        except HttpFetchError as exc:
            assert exc.status == 304
            assert exc.retryable is False
            assert exc.provider_outage is False
        else:
            raise AssertionError("expected unexpected not-modified response to fail")

    session.request.assert_called_once()


def test_batched_isolation_stops_before_scheduling_later_work() -> None:
    calls: list[int] = []

    def worker(item: int) -> int:
        calls.append(item)
        if item == 0:
            raise HttpFetchError("provider down", host="example.test", retryable=True, provider_outage=True)
        return item

    results = map_isolated_batches(
        list(range(8)),
        worker,
        workers=2,
        stop_after=lambda batch: any(isinstance(item, HttpFetchError) and item.provider_outage for item in batch),
    )
    assert len(calls) == 2
    assert len(results) == 2


def test_parallel_helpers_preserve_legitimate_none_results() -> None:
    def worker(item: int) -> int | None:
        return None if item == 0 else item

    assert map_bounded([0, 1], worker, workers=2) == [None, 1]
    assert map_isolated([0, 1], worker, workers=2) == [None, 1]


def test_retry_is_suppressed_when_backoff_would_cross_deadline() -> None:
    outage = HttpFetchError("down", host="nomads.ncep.noaa.gov", retryable=True, provider_outage=True)
    with (
        patch("ingest.http._request", side_effect=outage) as request,
        patch("ingest.http.bounded_timeout", side_effect=[1.0, 0.1]),
        patch("ingest.http.random.random", return_value=0.5),
    ):
        try:
            fetch("https://nomads.ncep.noaa.gov/smoke", hosts=frozenset({"nomads.ncep.noaa.gov"}), retries=2)
        except HttpFetchError as exc:
            assert "deadline" in str(exc)
        else:
            raise AssertionError("deadline did not suppress retry")
    assert request.call_count == 1


def test_expired_acquisition_budget_never_starts_request() -> None:
    with ingest_run(budget_seconds=0.01) as (metrics, budget), patch("ingest.http._request") as request:
        budget.started -= 1
        try:
            fetch("https://nomads.ncep.noaa.gov/smoke", hosts=frozenset({"nomads.ncep.noaa.gov"}))
        except HttpFetchError:
            pass
        else:
            raise AssertionError("expired acquisition started")
        assert metrics.deadline_cancellations == 1
    request.assert_not_called()


def test_request_does_not_wait_past_deadline_for_a_concurrency_slot() -> None:
    semaphore = Mock()
    semaphore.acquire.return_value = False
    with patch("ingest.http._GLOBAL_SEMAPHORE", semaphore), patch("ingest.http._session") as session:
        try:
            fetch("https://example.test/data", hosts=frozenset({"example.test"}), retries=0)
        except HttpFetchError as exc:
            assert "deadline" in str(exc)
        else:
            raise AssertionError("request waited past its concurrency-slot deadline")
    semaphore.acquire.assert_called_once_with(timeout=60.0)
    session.assert_not_called()


def test_request_timeout_is_reclamped_after_waiting_for_a_slot() -> None:
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.status_code = 200
    response.headers = {}
    response.url = "https://example.test/data"
    response.iter_content = Mock(return_value=iter([b"ok"]))
    session = Mock()
    session.request.return_value = response
    with (
        patch("ingest.http._session", return_value=session),
        patch("ingest.http.bounded_timeout", side_effect=[30.0, 20.0, 10.0]),
    ):
        assert fetch("https://example.test/data", hosts=frozenset({"example.test"}), retries=0) == b"ok"
    assert session.request.call_args.kwargs["timeout"] == 10.0



def test_batches_reuse_worker_sessions_and_copy_each_job_context():
    import threading
    from contextvars import ContextVar
    from ingest.http import _session
    context = ContextVar("pool_test", default="missing")
    barrier = threading.Barrier(2)
    sessions = []
    def worker(item):
        before = context.get()
        session = _session()
        sessions.append(session)
        barrier.wait(timeout=5)
        context.set("worker mutation")
        return (item, before, id(session))
    def before_batch():
        context.set("parent")
    try:
        results = map_isolated_batches(list(range(6)), worker, workers=2, before_batch=before_batch)
        assert [r[:2] for r in results] == [(i, "parent") for i in range(6)]
        assert len({r[2] for r in results}) == 2
    finally:
        for session in sessions:
            session.close()


def test_batch_deadline_and_callback_failure_do_not_schedule_more_work():
    calls = []
    checks = []
    def before_batch():
        checks.append(1)
        if len(checks) == 2:
            return RuntimeError("deadline")
    result = map_isolated_batches(list(range(9)), lambda i: calls.append(i), workers=3, before_batch=before_batch)
    assert sorted(calls) == [0, 1, 2]
    assert result[:3] == [None, None, None]
    assert isinstance(result[-1], RuntimeError)
    calls.clear()
    def stop(_batch):
        raise ValueError("stop callback failed")
    import pytest
    with pytest.raises(ValueError, match="stop callback failed"):
        map_isolated_batches(list(range(9)), lambda i: calls.append(i), workers=3, stop_after=stop)
    assert sorted(calls) == [0, 1, 2]


def test_batched_workers_reuse_real_http_connections():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from ingest.http import _session
    connections = set()
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def do_GET(self):
            connections.add(self.client_address)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    barrier = threading.Barrier(2)
    sessions = []
    def worker(_item):
        session = _session()
        sessions.append(session)
        barrier.wait(timeout=5)
        with session.get(f"http://127.0.0.1:{server.server_port}/", timeout=5) as response:
            return response.content
    try:
        assert map_isolated_batches(list(range(6)), worker, workers=2) == [b"ok"] * 6
        assert len(connections) == 2
    finally:
        for session in sessions:
            session.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
