"""Bounded, fail-closed discovery of public objects abandoned by failed runs."""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from typing import Any

from ingest.local_store import FrameStore, IngestLease
from ingest.context_contracts import validate_context_manifest
from ingest.context_publish import CONTEXT_LATEST_PATH, _asset_path_from_url
from ingest.perf import current_budget, current_metrics

STATE_PATH = "context/gc-reconcile-v1.json"
PREFIXES = ("context/assets", "context/manifests")
PAGE_SIZE = 250
MAX_PENDING = 4096
DELETE_BATCH = PAGE_SIZE * len(PREFIXES)
DEFAULT_RETENTION_HOURS = 48
GRACE_SECONDS = DEFAULT_RETENTION_HOURS * 60 * 60
OPERATION_SECONDS = 5
FINAL_RESERVE_SECONDS = 15
_MANIFEST_PATH = re.compile(r"^context/manifests/[0-9a-f]{20}\.json$")
LOGGER = logging.getLogger("titanskies.context.gc")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _count(kind: str, amount: int = 1) -> None:
    metrics = current_metrics()
    if metrics:
        metrics.add_provider_count("orphanGc", kind, amount)


def _object_path(path: Any) -> bool:
    return isinstance(path, str) and (
        _asset_path_from_url(path) == path or bool(_MANIFEST_PATH.fullmatch(path))
    )


def _protected(manifest: dict[str, Any], manifest_path: str, previous: dict[str, Any] | None, previous_path: str | None) -> set[str]:
    if previous_path and previous is None:
        raise ValueError("previous publication references unavailable")
    validate_context_manifest(manifest)
    if previous is not None:
        validate_context_manifest(previous)
    protected = {manifest_path}
    if previous_path:
        protected.add(previous_path)

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for nested in value.values():
                collect(nested)
        elif isinstance(value, list):
            for nested in value:
                collect(nested)
        elif isinstance(value, str):
            path = _asset_path_from_url(value)
            if path:
                protected.add(path)
    collect(manifest)
    collect(previous)
    return protected


def _state(value: dict[str, Any] | None, now: float) -> dict[str, Any]:
    if value is None:
        return {"version": 1, "nextPrefix": 0, "cursors": [None, None], "pending": {}, "lastSweep": [None, None]}
    if value.get("version") != 1 or type(value.get("nextPrefix")) is not int or value["nextPrefix"] not in (0, 1):
        raise ValueError("invalid reconciliation version or prefix")
    cursors, sweeps, pending = value.get("cursors"), value.get("lastSweep"), value.get("pending")
    if not isinstance(cursors, list) or len(cursors) != 2 or any(c is not None and (not isinstance(c, str) or not c or len(c) > 8192) for c in cursors):
        raise ValueError("invalid reconciliation cursors")
    def timestamp(v: Any) -> bool:
        return type(v) in (float, int) and math.isfinite(v) and 0 <= v <= now
    if not isinstance(sweeps, list) or len(sweeps) != 2 or any(v is not None and not timestamp(v) for v in sweeps):
        raise ValueError("invalid reconciliation sweep times")
    if not isinstance(pending, dict) or len(pending) > MAX_PENDING or any(not _object_path(p) or not timestamp(t) for p, t in pending.items()):
        raise ValueError("invalid reconciliation candidates")
    return value


def reconcile_orphans(
    store: FrameStore,
    lease: IngestLease,
    pointer: dict[str, Any],
    manifest: dict[str, Any],
    previous: dict[str, Any] | None,
    previous_path: str | None,
    *,
    retention_hours: int = DEFAULT_RETENTION_HOURS,
) -> None:
    """Only called after pointer success. All failures leave publication intact.

    The five-second budget is cooperative: Requests cannot interrupt a blocked
    socket at an exact wall-clock instant. No retries or background work escape
    the budget, and deadline checks run between requests and response chunks.
    """
    budget = current_budget()
    seconds = min(OPERATION_SECONDS, budget.remaining() - FINAL_RESERVE_SECONDS) if budget else OPERATION_SECONDS
    if seconds <= 0:
        _count("deferred")
        return
    try:
        with store.operation_budget(seconds):
            now = _now().timestamp()
            try:
                state = _state(store.get_authoritative_json(STATE_PATH), now)
            except (json.JSONDecodeError, RuntimeError, TypeError, UnicodeError, ValueError):
                state = _state(None, now)
                store.put_json(STATE_PATH, state, cache_seconds=0, overwrite=True)
            protected = _protected(manifest, pointer["manifestPath"], previous, previous_path)
            pending = state["pending"]
            for path in list(pending):
                if path in protected:
                    del pending[path]
            # Reset grace for anything that became referenced before proceeding.
            store.put_json(STATE_PATH, state, cache_seconds=0, overwrite=True)
            grace_seconds = retention_hours * 60 * 60
            eligible = sorted((p for p, seen in pending.items() if now - seen >= grace_seconds), key=lambda p: (pending[p], p))[:DELETE_BATCH]
            if eligible:
                actual_pointer = store.get_authoritative_json(CONTEXT_LATEST_PATH)
                actual_lease = store.get_authoritative_json(lease.pathname)
                expires = datetime.fromisoformat(str((actual_lease or {}).get("expiresAt", "")).replace("Z", "+00:00"))
                if actual_pointer != pointer or not actual_lease or actual_lease.get("owner") != lease.owner or expires.timestamp() - _now().timestamp() < OPERATION_SECONDS + FINAL_RESERVE_SECONDS:
                    _count("deferred")
                    return
                store.delete_many(eligible)
                for path in eligible:
                    del pending[path]
                _count("deleted", len(eligible))
                store.put_json(STATE_PATH, state, cache_seconds=0, overwrite=True)
            for _ in range(2):
                available = MAX_PENDING - len(pending)
                if not available:
                    _count("saturated")
                    break
                index = state["nextPrefix"]
                cursor = state["cursors"][index]
                try:
                    page = store.list_page(PREFIXES[index], cursor, min(PAGE_SIZE, available))
                except ValueError:
                    # Invalid/expired cursors restart discovery, never deletion.
                    state["cursors"][index] = None
                    store.put_json(STATE_PATH, state, cache_seconds=0, overwrite=True)
                    raise
                if len(page.paths) > min(PAGE_SIZE, available) or any(not _object_path(p) or not p.startswith(PREFIXES[index] + "/") for p in page.paths):
                    raise ValueError("unsafe reconciliation listing")
                if page.cursor is not None and (not isinstance(page.cursor, str) or not page.cursor or len(page.cursor) > 8192 or page.cursor == cursor):
                    raise ValueError("repeated reconciliation cursor")
                _count("scanned", len(page.paths))
                for path in page.paths:
                    if path not in protected and path not in pending:
                        pending[path] = now
                        _count("queued")
                state["cursors"][index] = page.cursor
                state["nextPrefix"] = 1 - index
                if page.cursor is None:
                    state["lastSweep"][index] = now
                    _count("sweeps")
                # Candidate discovery and cursor advancement are one atomic write.
                store.put_json(STATE_PATH, state, cache_seconds=0, overwrite=True)
            _count("pending", len(pending))
    except Exception:
        _count("failed")
        LOGGER.warning("orphan reconciliation deferred; publication preserved", exc_info=True)
