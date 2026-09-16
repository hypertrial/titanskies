from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
import random
import threading
import time
import uuid
from datetime import datetime
from typing import Any, ContextManager, Iterator
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter

from ingest.config import Settings
from ingest.http import USER_AGENT
from ingest.local_store import IngestLease, StoragePage, validate_store_path
from ingest.perf import bounded_timeout, current_metrics, record_storage

_PUBLIC_FALLBACK_STATUSES = frozenset({401, 403, 404, 405})
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_DELETE_BATCH = 64
_BLOB_API_BASE = "https://vercel.com/api/blob"
_BLOB_API_HOST = "vercel.com"
_BLOB_API_PATH = "/api/blob"
_BLOB_API_VERSION = "12"
_DEFAULT_GET_MAX_BYTES = 16 * 1024 * 1024
_LOCAL = threading.local()


_OPERATION_DEADLINE: ContextVar[float | None] = ContextVar("blob_operation_deadline", default=None)


def _check_operation_deadline() -> float | None:
    deadline = _OPERATION_DEADLINE.get()
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("storage housekeeping deadline reached")
    return remaining


def _pathname(value: str) -> str:
    return validate_store_path(value)


@contextmanager
def storage_operation_budget(seconds: float) -> Iterator[None]:
    token = _OPERATION_DEADLINE.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _OPERATION_DEADLINE.reset(token)


def _blob_session() -> requests.Session:
    session = getattr(_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=16, pool_maxsize=16, max_retries=0)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({"User-Agent": USER_AGENT})
        _LOCAL.session = session
    return session


def _retrying_request(
    method: str,
    url: str,
    *,
    retries: int = 2,
    allow_conflict: bool = False,
    allow_redirects: bool = False,
    **kwargs: Any,
) -> requests.Response:
    last_error: Exception | None = None
    session = _blob_session()
    caller = {"GET": session.get, "PUT": session.put, "POST": session.post}[method.upper()]
    kwargs.setdefault("allow_redirects", allow_redirects)
    requested_timeout = float(kwargs.get("timeout", 30))
    headers = kwargs.get("headers") or {}
    if any(str(key).lower() == "authorization" for key in headers):
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or host != _BLOB_API_HOST or not (
            parsed.path == _BLOB_API_PATH or parsed.path.startswith(f"{_BLOB_API_PATH}/")
        ):
            raise RuntimeError("blob credentials are limited to the Vercel Blob API")
    if _OPERATION_DEADLINE.get() is not None:
        retries = 0
    for attempt in range(max(1, retries + 1)):
        timeout = bounded_timeout(requested_timeout, reserve_finalization=False)
        if timeout is None:
            raise RuntimeError("ingest deadline reached during Blob request")
        operation_remaining = _check_operation_deadline()
        # Requests bounds connect/read separately, not total wall time. Check
        # the deadline again after headers and during every response chunk.
        kwargs["timeout"] = min(timeout, operation_remaining / 2) if operation_remaining else timeout
        try:
            response = caller(url, **kwargs)
            try:
                _check_operation_deadline()
            except TimeoutError:
                response.close()
                raise
            if allow_conflict and response.status_code in {409, 412}:
                return response
            if response.status_code in _RETRYABLE_STATUSES and attempt < retries:
                delay = min(8.0, 0.4 * (2 ** attempt)) * (0.5 + random.random())
                remaining_delay = bounded_timeout(delay, reserve_finalization=False)
                if remaining_delay is None or remaining_delay < delay:
                    return response
                metrics = current_metrics()
                if metrics:
                    metrics.increment("provider_retries")
                response.close()
                time.sleep(delay)
                continue
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= retries:
                raise
            delay = min(8.0, 0.4 * (2 ** attempt)) * (0.5 + random.random())
            remaining_delay = bounded_timeout(delay, reserve_finalization=False)
            if remaining_delay is None or remaining_delay < delay:
                raise
            metrics = current_metrics()
            if metrics:
                metrics.increment("provider_retries")
            time.sleep(delay)
    if last_error:
        raise last_error
    raise RuntimeError("blob request failed")


def _read_limited(response: requests.Response, max_bytes: int) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length and int(content_length) > max_bytes:
        raise RuntimeError(f"blob response exceeds {max_bytes} bytes")
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_content(64 * 1024):
        _check_operation_deadline()
        if bounded_timeout(1, reserve_finalization=False) is None:
            raise RuntimeError("ingest deadline reached during Blob response")
        if not chunk:
            continue
        size += len(chunk)
        if size > max_bytes:
            raise RuntimeError(f"blob response exceeds {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def _expired(payload: dict[str, Any], now: datetime) -> bool:
    try:
        return datetime.fromisoformat(str(payload["expiresAt"]).replace("Z", "+00:00")) <= now
    except (KeyError, TypeError, ValueError):
        return True

class BlobFrameStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base = _BLOB_API_BASE

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.settings.blob_token}",
            "x-api-version": _BLOB_API_VERSION,
            "x-api-blob-request-id": f"{self.settings.blob_store_id}:{uuid.uuid4().hex}",
            "x-api-blob-request-attempt": "0",
            "x-vercel-blob-store-id": self.settings.blob_store_id,
        }
        if extra:
            headers.update(extra)
        return headers

    def get_text(self, pathname: str) -> str | None:
        data = self.get_bytes(pathname)
        return None if data is None else data.decode("utf-8")

    def put_json(self, pathname: str, payload: dict[str, Any], *, cache_seconds: int, overwrite: bool) -> str:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return self.put_bytes(pathname, encoded, "application/json", cache_seconds=cache_seconds, overwrite=overwrite)

    def get_bytes(self, pathname: str, *, max_bytes: int = _DEFAULT_GET_MAX_BYTES) -> bytes | None:
        pathname = _pathname(pathname)
        headers = {"User-Agent": USER_AGENT}
        response = _retrying_request(
            "GET",
            self.url_for(pathname),
            headers=headers,
            timeout=30,
            stream=True,
        )
        try:
            if response.status_code in _PUBLIC_FALLBACK_STATUSES:
                record_storage("read")
                return None
            response.raise_for_status()
            body = _read_limited(response, max_bytes)
            record_storage("read", bytes_in=len(body))
            return body
        finally:
            response.close()

    def put_bytes(self, pathname: str, data: bytes, content_type: str, *, cache_seconds: int, overwrite: bool) -> str:
        pathname = _pathname(pathname)
        response = _retrying_request(
            "PUT",
            self.base,
            params={"pathname": pathname},
            data=data,
            headers=self._headers(
                {
                    "x-vercel-blob-access": "public",
                    "x-content-type": content_type,
                    "x-add-random-suffix": "0",
                    "x-allow-overwrite": "1" if overwrite else "0",
                    "x-cache-control-max-age": str(cache_seconds),
                }
            ),
            timeout=60,
            allow_conflict=not overwrite,
            stream=True,
        )
        try:
            record_storage("write", bytes_out=len(data))
            if response.status_code in {409, 412} and not overwrite:
                return self.url_for(pathname)
            response.raise_for_status()
            payload = json.loads(_read_limited(response, 64 * 1024))
            if not isinstance(payload, dict):
                raise RuntimeError("invalid Blob write response")
            expected_url = self.url_for(pathname)
            returned_url = payload.get("url")
            if returned_url != expected_url or payload.get("pathname") not in {None, pathname}:
                raise RuntimeError("Blob write response does not match the configured store")
            return expected_url
        finally:
            response.close()

    def get_authoritative_json(self, pathname: str) -> dict[str, Any] | None:
        pathname = _pathname(pathname)
        response = _retrying_request(
            "GET", self.url_for(pathname),
            headers={"Cache-Control": "no-cache", "User-Agent": USER_AGENT}, timeout=5, retries=0, stream=True,
        )
        try:
            if response.status_code == 404:
                return None
            response.raise_for_status()
            value = json.loads(_read_limited(response, 2 * 1024 * 1024))
            if not isinstance(value, dict):
                raise ValueError("invalid authoritative JSON")
            return value
        finally:
            response.close()

    def list_page(self, prefix: str, cursor: str | None = None, limit: int = 250) -> StoragePage:
        prefix = _pathname(prefix)
        if not 1 <= limit <= 250:
            raise ValueError("invalid page limit")
        params = {"prefix": prefix, "limit": str(limit)}
        if cursor is not None:
            params["cursor"] = cursor
        response = _retrying_request("GET", self.base, params=params, headers=self._headers(), timeout=5, stream=True)
        try:
            if cursor is not None and response.status_code in (400, 410):
                raise ValueError("rejected Blob list cursor")
            response.raise_for_status()
            payload = json.loads(_read_limited(response, 2 * 1024 * 1024))
            if not isinstance(payload, dict):
                raise ValueError("invalid Blob page")
            blobs = payload.get("blobs")
            if not isinstance(blobs, list) or len(blobs) > limit:
                raise ValueError("invalid Blob page")
            paths = [blob.get("pathname") for blob in blobs if isinstance(blob, dict)]
            if len(paths) != len(blobs) or any(not isinstance(path, str) or not path.startswith(prefix + "/") for path in paths):
                raise ValueError("invalid Blob paths")
            next_cursor = payload.get("cursor") if payload.get("hasMore") else None
            if payload.get("hasMore") and (not isinstance(next_cursor, str) or not next_cursor or len(next_cursor) > 8192 or next_cursor == cursor):
                raise ValueError("invalid Blob list cursor")
            record_storage("list")
            return StoragePage([str(path) for path in paths], next_cursor)
        finally:
            response.close()

    def list_prefix(self, prefix: str) -> list[str]:
        prefix = _pathname(prefix)
        pathnames: list[str] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            params = {"prefix": prefix, "limit": "1000"}
            if cursor:
                params["cursor"] = cursor
            response = _retrying_request("GET", self.base, params=params, headers=self._headers(), timeout=30, stream=True)
            try:
                response.raise_for_status()
                record_storage("list")
                payload = json.loads(_read_limited(response, 4 * 1024 * 1024))
            finally:
                response.close()
            if not isinstance(payload, dict) or not isinstance(payload.get("blobs"), list) or len(payload["blobs"]) > 1000:
                raise RuntimeError("invalid Blob listing")
            page = [item.get("pathname") for item in payload["blobs"] if isinstance(item, dict)]
            if len(page) != len(payload["blobs"]) or any(not isinstance(path, str) or not path.startswith(prefix + "/") for path in page):
                raise RuntimeError("invalid Blob listing")
            pathnames.extend(str(path) for path in page)
            if not payload.get("hasMore"):
                return pathnames
            next_cursor = payload.get("cursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen:
                raise RuntimeError("invalid Blob list cursor")
            seen.add(next_cursor)
            cursor = next_cursor

    def delete(self, pathname: str) -> None:
        self.delete_many([pathname])

    def delete_many(self, pathnames: list[str]) -> None:
        urls = [self.url_for(pathname) for pathname in pathnames]
        for offset in range(0, len(urls), _DELETE_BATCH):
            batch = urls[offset : offset + _DELETE_BATCH]
            response = _retrying_request(
                "POST",
                f"{self.base}/delete",
                json={"urls": batch},
                headers=self._headers({"content-type": "application/json"}),
                timeout=30,
            )
            try:
                response.raise_for_status()
                record_storage("delete", count=len(batch))
            finally:
                response.close()

    def url_for(self, pathname: str) -> str:
        pathname = _pathname(pathname)
        if self.settings.public_blob_base_url:
            return f"{self.settings.public_blob_base_url}/{pathname}"
        raise RuntimeError("PUBLIC_BLOB_BASE_URL is required for Blob storage")

    def _create_lease(self, pathname: str, payload: dict[str, Any]) -> IngestLease | None:
        pathname = _pathname(pathname)
        response = _retrying_request(
            "PUT",
            self.base,
            params={"pathname": pathname},
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers=self._headers(
                {
                    "x-vercel-blob-access": "public",
                    "x-content-type": "application/json",
                    "x-add-random-suffix": "0",
                    "x-allow-overwrite": "0",
                    "x-cache-control-max-age": "60",
                }
            ),
            timeout=30,
            retries=0,
            allow_conflict=True,
            stream=True,
        )
        try:
            if response.status_code in {409, 412}:
                return None
            response.raise_for_status()
            result = json.loads(_read_limited(response, 64 * 1024))
            if not isinstance(result, dict):
                raise RuntimeError("invalid Blob lease response")
            etag = response.headers.get("etag") or result.get("etag")
            if not isinstance(etag, str) or not etag:
                raise RuntimeError("Blob lease response is missing its ETag")
            return IngestLease(pathname, str(payload["owner"]), etag)
        finally:
            response.close()

    def _read_lease(self, pathname: str) -> tuple[dict[str, Any], str | None] | None:
        pathname = _pathname(pathname)
        response = _retrying_request(
            "GET",
            self.url_for(pathname),
            headers={"Cache-Control": "no-cache", "User-Agent": USER_AGENT},
            timeout=30,
            retries=0,
            stream=True,
        )
        try:
            if response.status_code == 404:
                return None
            response.raise_for_status()
            payload = json.loads(_read_limited(response, 64 * 1024))
            if not isinstance(payload, dict):
                raise RuntimeError("invalid Blob lease response")
            return payload, response.headers.get("etag")
        finally:
            response.close()

    def _delete_lease(self, pathname: str, etag: str | None) -> bool:
        pathname = _pathname(pathname)
        if not etag:
            return False
        headers = self._headers({"content-type": "application/json"})
        headers["x-if-match"] = etag
        response = _retrying_request(
            "POST",
            f"{self.base}/delete",
            json={"urls": [self.url_for(pathname)]},
            headers=headers,
            timeout=30,
            retries=0,
            allow_conflict=True,
        )
        try:
            if response.status_code in {409, 412}:
                return False
            response.raise_for_status()
            return True
        finally:
            response.close()

    def acquire_lease(
        self,
        pathname: str,
        owner: str,
        now: datetime,
        expires_at: datetime,
    ) -> IngestLease | None:
        payload = {
            "owner": owner,
            "startedAt": now.isoformat().replace("+00:00", "Z"),
            "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
        }
        lease = self._create_lease(pathname, payload)
        if lease:
            return lease
        current = self._read_lease(pathname)
        if current is None:
            return self._create_lease(pathname, payload)
        current_payload, etag = current
        if not _expired(current_payload, now) or not self._delete_lease(pathname, etag):
            return None
        return self._create_lease(pathname, payload)

    def release_lease(self, lease: IngestLease) -> None:
        if lease.etag:
            self._delete_lease(lease.pathname, lease.etag)
            return
        current = self._read_lease(lease.pathname)
        if current is None:
            return
        payload, etag = current
        if payload.get("owner") == lease.owner:
            self._delete_lease(lease.pathname, etag or lease.etag)

    def operation_budget(self, seconds: float) -> ContextManager[None]:
        return storage_operation_budget(seconds)
