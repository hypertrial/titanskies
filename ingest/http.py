from __future__ import annotations

import random
import threading
import time
from typing import Any
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter

from ingest import __version__
from ingest.auth import redact
from ingest.config import Settings
from ingest.perf import bounded_timeout, current_budget, current_metrics, record_http

DEFAULT_TIMEOUT = 60
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
USER_AGENT = f"TitanSkies/{__version__} (+https://github.com/hypertrial/titanskies)"
GLOBAL_HTTP_LIMIT = 12
HTTP_LIMIT_MAX = 16
REGISTERED_ENDPOINT_HOSTS = frozenset(
    {
        "api.weather.gc.ca",
        "cwfis.cfs.nrcan.gc.ca",
        "geo.weather.gc.ca",
        "geoserver.cwfif.nrcan.gc.ca",
        "nomads.ncep.noaa.gov",
        "services3.arcgis.com",
        "sinaica.inecc.gob.mx",
        "www.airnowapi.org",
        "www.env.gov.bc.ca",
    }
)

_LOCAL = threading.local()
_POOL_SIZE = GLOBAL_HTTP_LIMIT
_SESSION_GENERATION = 0
_GLOBAL_SEMAPHORE = threading.BoundedSemaphore(GLOBAL_HTTP_LIMIT)
_HOST_COOLDOWN_UNTIL: dict[str, float] = {}
_HOST_COOLDOWN_LOCK = threading.Lock()
_PROVIDER_LIMITS = {
    "nomads.ncep.noaa.gov": 4,
    "geo.weather.gc.ca": 6,
    "www.airnowapi.org": 4,
    "sinaica.inecc.gob.mx": 4,
    "www.env.gov.bc.ca": 2,
}
_PROVIDER_SEMAPHORES: dict[str, threading.BoundedSemaphore] = {
    host: threading.BoundedSemaphore(limit) for host, limit in _PROVIDER_LIMITS.items()
}


class HttpFetchError(RuntimeError):
    def __init__(self, message: str, *, host: str, status: int | None = None, retryable: bool = False, provider_outage: bool = False):
        super().__init__(message)
        self.host = host
        self.status = status
        self.retryable = retryable
        self.provider_outage = provider_outage


def configure_http_limit(limit: int, settings: Settings | None = None) -> None:
    global _GLOBAL_SEMAPHORE, _POOL_SIZE, _SESSION_GENERATION
    _POOL_SIZE = max(1, min(HTTP_LIMIT_MAX, limit))
    _GLOBAL_SEMAPHORE = threading.BoundedSemaphore(_POOL_SIZE)
    _SESSION_GENERATION += 1
    hrrr = settings.hrrr_concurrency if settings is not None else _PROVIDER_LIMITS["nomads.ncep.noaa.gov"]
    firework = settings.firework_concurrency if settings is not None else _PROVIDER_LIMITS["geo.weather.gc.ca"]
    sinaica = settings.sinaica_concurrency if settings is not None else _PROVIDER_LIMITS["sinaica.inecc.gob.mx"]
    rebuilt = {
        "nomads.ncep.noaa.gov": threading.BoundedSemaphore(hrrr),
        "geo.weather.gc.ca": threading.BoundedSemaphore(firework),
        "www.airnowapi.org": threading.BoundedSemaphore(_PROVIDER_LIMITS["www.airnowapi.org"]),
        "sinaica.inecc.gob.mx": threading.BoundedSemaphore(sinaica),
        "www.env.gov.bc.ca": threading.BoundedSemaphore(_PROVIDER_LIMITS["www.env.gov.bc.ca"]),
    }
    _PROVIDER_SEMAPHORES.clear()
    _PROVIDER_SEMAPHORES.update(rebuilt)


def require_host(url: str, hosts: frozenset[str]) -> None:
    host = (urlparse(url).hostname or "").lower()
    if host not in hosts:
        raise ValueError(f"blocked host {host or url}")


def clean_text(value: Any, limit: int = 120) -> str:
    return " ".join(str(value).split())[:limit]


def _session() -> requests.Session:
    generation = getattr(_LOCAL, "generation", None)
    session = getattr(_LOCAL, "session", None)
    if session is None or generation != _SESSION_GENERATION:
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=_POOL_SIZE, pool_maxsize=_POOL_SIZE, max_retries=0)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({"User-Agent": USER_AGENT})
        _LOCAL.session = session
        _LOCAL.generation = _SESSION_GENERATION
    return session


def _note_cooldown(host: str, retry_after: str | None) -> None:
    delay = 1.0
    if retry_after:
        try:
            delay = min(8.0, max(0.2, float(retry_after)))
        except ValueError:
            delay = 1.0
    with _HOST_COOLDOWN_LOCK:
        _HOST_COOLDOWN_UNTIL[host] = max(_HOST_COOLDOWN_UNTIL.get(host, 0.0), time.monotonic() + delay)


def _wait_cooldown(host: str) -> None:
    with _HOST_COOLDOWN_LOCK:
        until = _HOST_COOLDOWN_UNTIL.get(host, 0.0)
    delay = until - time.monotonic()
    if delay > 0:
        bounded = bounded_timeout(delay)
        if bounded is None or bounded < delay:
            raise HttpFetchError("ingest acquisition deadline reached", host=host, provider_outage=True)
        time.sleep(delay)


def _provider_semaphore(host: str) -> threading.BoundedSemaphore | None:
    if host in _PROVIDER_SEMAPHORES:
        return _PROVIDER_SEMAPHORES[host]
    return None


class _Slot:
    def __init__(self, host: str):
        self._host = host
        self._global = _GLOBAL_SEMAPHORE
        self._provider = _provider_semaphore(host)
        self._global_acquired = False
        self._provider_acquired = False

    def __enter__(self) -> None:
        timeout = bounded_timeout(DEFAULT_TIMEOUT)
        if timeout is None or not self._global.acquire(timeout=timeout):
            raise HttpFetchError("ingest acquisition deadline reached", host=self._host, provider_outage=True)
        self._global_acquired = True
        if self._provider:
            timeout = bounded_timeout(DEFAULT_TIMEOUT)
            if timeout is None or not self._provider.acquire(timeout=timeout):
                self._global.release()
                self._global_acquired = False
                raise HttpFetchError("ingest acquisition deadline reached", host=self._host, provider_outage=True)
            self._provider_acquired = True

    def __exit__(self, *_exc: object) -> None:
        if self._provider_acquired and self._provider:
            self._provider.release()
        if self._global_acquired:
            self._global.release()


def allowed_host(url: str, hosts: frozenset[str]) -> str:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if not hosts or not hosts.issubset(REGISTERED_ENDPOINT_HOSTS):
        raise ValueError("blocked unregistered provider allowlist")
    if parsed.scheme != "https" or hostname not in hosts:
        raise ValueError(f"blocked host {hostname or url}")
    return hostname


def fetch(
    url: str,
    *,
    hosts: frozenset[str],
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | str | None = None,
    method: str = "GET",
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    retries: int = 1,
) -> bytes:
    host = allowed_host(url, hosts)
    last_error: Exception | None = None
    attempts = max(1, retries + 1)
    for attempt in range(attempts):
        try:
            bounded = bounded_timeout(timeout)
            if bounded is None:
                raise HttpFetchError("ingest acquisition deadline reached", host=host, retryable=False, provider_outage=True)
            return _request(url, hosts=hosts, params=params, data=data, method=method, timeout=bounded, max_bytes=max_bytes)
        except HttpFetchError as exc:
            last_error = exc
            if attempt + 1 >= attempts or not exc.retryable:
                raise
            delay = min(8.0, 0.4 * (2**attempt)) * (0.5 + random.random())
            bounded_delay = bounded_timeout(delay)
            if bounded_delay is None or bounded_delay < delay:
                raise HttpFetchError("ingest acquisition deadline reached", host=host, retryable=False, provider_outage=True) from exc
            metrics = current_metrics()
            if metrics:
                metrics.increment("provider_retries")
            time.sleep(delay)
    raise last_error or RuntimeError("request failed")


def _request(
    url: str,
    *,
    hosts: frozenset[str],
    params: dict[str, Any] | None,
    data: dict[str, Any] | str | None,
    method: str,
    timeout: float,
    max_bytes: int,
) -> bytes:
    host = allowed_host(url, hosts)
    _wait_cooldown(host)
    try:
        with _Slot(host):
            request_timeout = bounded_timeout(timeout)
            if request_timeout is None:
                raise HttpFetchError("ingest acquisition deadline reached", host=host, provider_outage=True)
            budget = current_budget()
            requests_timeout: float | tuple[float, float] = request_timeout
            if budget is not None:
                read_timeout = min(request_timeout, max(1.0, budget.finalization_reserve() / 2))
                requests_timeout = (request_timeout, read_timeout)
            with _session().request(
                method,
                url,
                params=params,
                data=data,
                timeout=requests_timeout,
                stream=True,
                allow_redirects=False,
                headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            ) as response:
                if response.status_code == 429:
                    record_http()
                    _note_cooldown(host, response.headers.get("Retry-After"))
                    raise HttpFetchError("429 Too Many Requests", host=host, status=429, retryable=True, provider_outage=True)
                if 300 <= response.status_code < 400:
                    record_http()
                    location = response.headers.get("Location", "")
                    # Never follow provider redirects: the allowlist applies to the
                    # requested URL and an upstream redirect must not become an SSRF
                    # bypass. NOMADS does, however, occasionally return a transient
                    # redirect with an empty Location header while a GRIB subset is
                    # being staged. Retrying the original, already-validated URL is
                    # safe and prevents one such response from invalidating an entire
                    # forecast publication.
                    transient_empty_redirect = response.status_code in {301, 302, 303, 307, 308} and not location.strip()
                    raise HttpFetchError(
                        redact(f"blocked redirect from {response.url} to {location}"),
                        host=host,
                        status=response.status_code,
                        retryable=transient_empty_redirect,
                        provider_outage=transient_empty_redirect,
                    )
                if response.status_code >= 400:
                    record_http()
                    status = response.status_code
                    retryable = status in {500, 502, 503, 504}
                    raise HttpFetchError(
                        f"{status} {response.reason}", host=host, status=status, retryable=retryable, provider_outage=retryable
                    )
                allowed_host(response.url, hosts)
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > max_bytes:
                    raise RuntimeError(f"response exceeds {max_bytes} bytes")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_content(64 * 1024):
                    if bounded_timeout(timeout) is None:
                        raise HttpFetchError("ingest acquisition deadline reached", host=host, retryable=False, provider_outage=True)
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_bytes:
                        raise RuntimeError(f"response exceeds {max_bytes} bytes")
                    chunks.append(chunk)
                if bounded_timeout(timeout) is None:
                    raise HttpFetchError("ingest acquisition deadline reached", host=host, retryable=False, provider_outage=True)
                body = b"".join(chunks)
                record_http(bytes_in=len(body), bytes_out=len(data) if isinstance(data, (bytes, str)) else 0)
                return body
    except requests.RequestException as exc:
        record_http()
        timeout_error = isinstance(exc, requests.Timeout)
        outage = timeout_error or isinstance(exc, requests.ConnectionError)
        metrics = current_metrics()
        if timeout_error and metrics:
            metrics.increment("provider_timeouts")
        raise HttpFetchError(redact(str(exc)), host=host, retryable=outage, provider_outage=outage) from None


def session_get(
    url: str,
    *,
    hosts: frozenset[str],
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    params: dict[str, Any] | None = None,
) -> requests.Response:
    body = fetch(url, hosts=hosts, params=params, timeout=timeout, max_bytes=max_bytes)
    response = requests.Response()
    response.status_code = 200
    response.url = url
    response.encoding = "utf-8"
    response._content = body
    return response
