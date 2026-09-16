from __future__ import annotations

import fcntl
import heapq
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, ContextManager, Iterator, Protocol

from ingest.config import Settings
from ingest.perf import record_storage

_DEFAULT_GET_MAX_BYTES = 16 * 1024 * 1024
_SAFE_STORE_PATH = re.compile(r"^[A-Za-z0-9._/-]{1,1024}$")
_OPERATION_DEADLINE: ContextVar[float | None] = ContextVar("storage_operation_deadline", default=None)


def validate_store_path(value: str) -> str:
    if not _SAFE_STORE_PATH.fullmatch(value) or value.startswith("/") or "//" in value:
        raise ValueError("invalid store path")
    if any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("invalid store path")
    return value


def _check_operation_deadline() -> None:
    deadline = _OPERATION_DEADLINE.get()
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("storage housekeeping deadline reached")


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
    def get_bytes(self, pathname: str, *, max_bytes: int = _DEFAULT_GET_MAX_BYTES) -> bytes | None: ...
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
    def operation_budget(self, seconds: float) -> ContextManager[None]: ...


def lease_expired(payload: dict[str, Any], now: datetime) -> bool:
    try:
        return datetime.fromisoformat(str(payload["expiresAt"]).replace("Z", "+00:00")) <= now
    except (KeyError, TypeError, ValueError):
        return True


class LocalFrameStore:
    """Atomic, content-address-friendly storage rooted on the local filesystem."""

    def __init__(self, root: Path, url_prefix: str = "/data"):
        self.root = root.resolve()
        self.url_prefix = "/" + url_prefix.strip("/")
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, pathname: str, *, allow_final_symlink: bool = False) -> Path:
        validate_store_path(pathname)
        relative = Path(pathname)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ValueError("invalid store path")
        path = self.root / relative
        current = self.root
        for index, part in enumerate(relative.parts):
            current /= part
            if current.is_symlink():
                if allow_final_symlink and index == len(relative.parts) - 1:
                    continue
                raise ValueError("invalid store path")
        containment_path = path.parent.resolve() if allow_final_symlink else path.resolve()
        if not containment_path.is_relative_to(self.root):
            raise ValueError("invalid store path")
        return path

    def get_text(self, pathname: str) -> str | None:
        data = self.get_bytes(pathname)
        return None if data is None else data.decode("utf-8")

    def get_bytes(self, pathname: str, *, max_bytes: int = _DEFAULT_GET_MAX_BYTES) -> bytes | None:
        path = self._path(pathname)
        if not path.is_file():
            record_storage("read")
            return None
        size = path.stat().st_size
        if size > max_bytes:
            raise RuntimeError(f"local object exceeds {max_bytes} bytes")
        data = path.read_bytes()
        record_storage("read", bytes_in=len(data))
        return data

    def put_bytes(self, pathname: str, data: bytes, content_type: str, *, cache_seconds: int, overwrite: bool) -> str:
        del content_type, cache_seconds
        _check_operation_deadline()
        path = self._path(pathname, allow_final_symlink=overwrite)
        if path.exists() and not overwrite:
            return self.url_for(pathname)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            record_storage("write", bytes_out=len(data))
        finally:
            temporary.unlink(missing_ok=True)
        return self.url_for(pathname)

    def put_json(self, pathname: str, payload: dict[str, Any], *, cache_seconds: int, overwrite: bool) -> str:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return self.put_bytes(pathname, encoded, "application/json", cache_seconds=cache_seconds, overwrite=overwrite)

    def get_authoritative_json(self, pathname: str) -> dict[str, Any] | None:
        raw = self.get_text(pathname)
        value = json.loads(raw) if raw else None
        if value is not None and not isinstance(value, dict):
            raise ValueError("invalid authoritative JSON")
        return value

    def list_page(self, prefix: str, cursor: str | None = None, limit: int = 250) -> StoragePage:
        if not 1 <= limit <= 250:
            raise ValueError("invalid page limit")
        root = self._path(prefix)
        if not root.exists():
            return StoragePage([], None)

        def candidates() -> Iterator[str]:
            for path in root.rglob("*"):
                _check_operation_deadline()
                if path.is_file():
                    relative = path.relative_to(self.root).as_posix()
                    if cursor is None or relative > cursor:
                        yield relative

        paths = heapq.nsmallest(limit + 1, candidates())
        record_storage("list")
        return StoragePage(paths[:limit], paths[limit - 1] if len(paths) > limit else None)

    def list_prefix(self, prefix: str) -> list[str]:
        root = self._path(prefix)
        if not root.exists():
            return []
        paths = [path.relative_to(self.root).as_posix() for path in root.rglob("*") if path.is_file()]
        record_storage("list")
        return paths

    def delete(self, pathname: str) -> None:
        _check_operation_deadline()
        path = self._path(pathname)
        existed = path.is_file()
        path.unlink(missing_ok=True)
        if existed:
            record_storage("delete")

    def delete_many(self, pathnames: list[str]) -> None:
        for pathname in pathnames:
            self.delete(pathname)

    def url_for(self, pathname: str) -> str:
        return f"{self.url_prefix}/{validate_store_path(pathname)}"

    def acquire_lease(self, pathname: str, owner: str, now: datetime, expires_at: datetime) -> IngestLease | None:
        path = self._path(pathname)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "owner": owner,
            "startedAt": now.isoformat().replace("+00:00", "Z"),
            "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
        }
        encoded = json.dumps(payload, separators=(",", ":"))
        guard = self.root / ".lease.guard"
        with guard.open("a+", encoding="utf-8") as guard_handle:
            fcntl.flock(guard_handle, fcntl.LOCK_EX)
            try:
                try:
                    current = json.loads(path.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    current = None
                except json.JSONDecodeError:
                    current = {}
                if current is not None and not lease_expired(current, now):
                    return None
                path.write_text(encoded, encoding="utf-8")
                return IngestLease(pathname, owner)
            finally:
                fcntl.flock(guard_handle, fcntl.LOCK_UN)

    def release_lease(self, lease: IngestLease) -> None:
        path = self._path(lease.pathname)
        guard = self.root / ".lease.guard"
        with guard.open("a+", encoding="utf-8") as guard_handle:
            fcntl.flock(guard_handle, fcntl.LOCK_EX)
            try:
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (FileNotFoundError, json.JSONDecodeError):
                    return
                if payload.get("owner") == lease.owner:
                    path.unlink(missing_ok=True)
            finally:
                fcntl.flock(guard_handle, fcntl.LOCK_UN)

    def operation_budget(self, seconds: float) -> ContextManager[None]:
        return storage_operation_budget(seconds)


def open_store(settings: Settings) -> FrameStore:
    if settings.storage_backend == "blob":
        from ingest.blob_store import BlobFrameStore

        return BlobFrameStore(settings)
    return LocalFrameStore(settings.local_frame_dir, settings.data_url_prefix)


def open_cache_store(settings: Settings) -> FrameStore:
    if settings.storage_backend == "blob":
        from ingest.blob_store import BlobFrameStore

        return BlobFrameStore(settings)
    return LocalFrameStore(settings.local_cache_dir, settings.data_url_prefix)
