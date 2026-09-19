from __future__ import annotations

import re
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

DEFAULT_GET_MAX_BYTES = 16 * 1024 * 1024
_SAFE_STORE_PATH = re.compile(r"^[A-Za-z0-9._/-]{1,1024}$")
_OPERATION_DEADLINE: ContextVar[float | None] = ContextVar("storage_operation_deadline", default=None)


def validate_store_path(value: str) -> str:
    if not _SAFE_STORE_PATH.fullmatch(value) or value.startswith("/") or "//" in value:
        raise ValueError("invalid store path")
    if any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("invalid store path")
    return value


def operation_deadline_remaining() -> float | None:
    deadline = _OPERATION_DEADLINE.get()
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("storage housekeeping deadline reached")
    return remaining


def check_operation_deadline() -> None:
    operation_deadline_remaining()


def has_operation_deadline() -> bool:
    return _OPERATION_DEADLINE.get() is not None


@contextmanager
def storage_operation_budget(seconds: float) -> Iterator[None]:
    token = _OPERATION_DEADLINE.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _OPERATION_DEADLINE.reset(token)


@dataclass(frozen=True)
class StoragePage:
    paths: list[str]
    cursor: str | None


@dataclass(frozen=True)
class IngestLease:
    pathname: str
    owner: str
    etag: str | None = None


class FrameStore(Protocol):
    def get_text(self, pathname: str) -> str | None: ...
    def get_bytes(self, pathname: str, *, max_bytes: int = DEFAULT_GET_MAX_BYTES) -> bytes | None: ...
    def exists(self, pathname: str) -> bool: ...
    def put_bytes(self, pathname: str, data: bytes, content_type: str, *, cache_seconds: int, overwrite: bool) -> str: ...
    def put_json(self, pathname: str, payload: dict[str, Any], *, cache_seconds: int, overwrite: bool) -> str: ...
    def get_authoritative_json(self, pathname: str) -> dict[str, Any] | None: ...
    def list_page(self, prefix: str, cursor: str | None = None, limit: int = 250) -> StoragePage: ...
    def list_prefix(self, prefix: str) -> list[str]: ...
    def delete(self, pathname: str) -> None: ...
    def delete_many(self, pathnames: list[str]) -> None: ...
    def url_for(self, pathname: str) -> str: ...
    def acquire_lease(self, pathname: str, owner: str, now: datetime, expires_at: datetime) -> IngestLease | None: ...
    def release_lease(self, lease: IngestLease) -> None: ...
    def operation_budget(self, seconds: float) -> AbstractContextManager[None]: ...


def lease_expired(payload: dict[str, Any], now: datetime) -> bool:
    try:
        return datetime.fromisoformat(str(payload["expiresAt"]).replace("Z", "+00:00")) <= now
    except (KeyError, TypeError, ValueError):
        return True
