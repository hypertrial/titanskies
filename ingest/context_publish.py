from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import threading
from datetime import datetime
from typing import Any

from PIL import Image

from ingest.auth import redact
from ingest.context_contracts import (
    CONTEXT_JSON_BUDGET_BYTES,
    CONTEXT_RASTER_BUDGET_BYTES,
    MONITOR_JSON_BUDGET_BYTES,
    iso_utc,
    validate_context_manifest,
)
from ingest.http import GLOBAL_HTTP_LIMIT
from ingest.http_pool import map_bounded
from ingest.local_store import FrameStore
from ingest.perf import current_asset_memo, current_budget, current_metrics

LOGGER = logging.getLogger("titanskies.context")
CONTEXT_LATEST_PATH = "context/latest.json"
CONTEXT_STATUS_PATH = "context/status.json"
CONTEXT_LOCK_PATH = "locks/context.json"
CONTEXT_GC_INVENTORY = "context/gc-inventory.json"
_ASSET_MEMO_LOCK = threading.Lock()
_VOLATILE_DEMO_METRICS = {
    "elapsedSeconds",
    "cpuSeconds",
    "deadlineSkips",
    "forecastWaveSeconds",
    "phaseSeconds",
    "providerPhaseSeconds",
    "sourceSeconds",
    "splitRecommended",
}


def load_json(store: FrameStore, pathname: str) -> dict[str, Any] | None:
    try:
        raw = store.get_bytes(pathname, max_bytes=CONTEXT_JSON_BUDGET_BYTES)
        value = json.loads(raw.decode("utf-8")) if raw else None
        return value if isinstance(value, dict) else None
    except (json.JSONDecodeError, RuntimeError, UnicodeError, ValueError):
        return None


def context_status_metrics(manifest: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    if manifest.get("mode") != "demo":
        return metrics
    return {key: value for key, value in metrics.items() if key not in _VOLATILE_DEMO_METRICS}


def context_status_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    generated_at = datetime.fromisoformat(str(manifest["generatedAt"]).replace("Z", "+00:00"))
    return context_status_payload(None, generated_at, "fresh", manifest=manifest)


def context_status_payload(
    previous: dict[str, Any] | None,
    now: datetime,
    outcome: str,
    *,
    manifest: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    error: BaseException | None = None,
) -> dict[str, Any]:
    if outcome not in {"fresh", "retained", "failed"}:
        raise ValueError("invalid context status outcome")
    forecast = (manifest or {}).get("forecast") or {}
    frames = forecast.get("frames") or []
    fresh = outcome == "fresh"
    return {
        "version": 1,
        "lastAttemptAt": iso_utc(now),
        "lastSuccessfulPublicationAt": iso_utc(now) if manifest else (previous or {}).get("lastSuccessfulPublicationAt"),
        "lastCompleteForecastAt": iso_utc(now) if fresh else (previous or {}).get("lastCompleteForecastAt"),
        "outcome": outcome,
        "contextVersion": (manifest or {}).get("version") or (previous or {}).get("contextVersion"),
        "forecastFrameCount": len(frames) if manifest else (previous or {}).get("forecastFrameCount", 0),
        "forecastLastValidTime": (frames[-1].get("validTime") if frames else None)
        if manifest
        else (previous or {}).get("forecastLastValidTime"),
        "consecutiveNonFresh": 0 if fresh else int((previous or {}).get("consecutiveNonFresh") or 0) + 1,
        "failureCategory": type(error).__name__ if error else None,
        "metrics": metrics or {},
    }


def write_context_status(store: FrameStore, payload: dict[str, Any]) -> None:
    try:
        store.put_json(CONTEXT_STATUS_PATH, payload, cache_seconds=60, overwrite=True)
    except Exception:
        LOGGER.warning("context status publication failed", exc_info=True)


def json_bytes(payload: Any, budget: int = CONTEXT_JSON_BUDGET_BYTES) -> bytes:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True, allow_nan=False).encode("utf-8")
    if len(encoded) > budget:
        raise ValueError(f"context JSON exceeds {budget} bytes")
    return encoded


def _blob_store(store: FrameStore) -> bool:
    return type(store).__name__ == "BlobFrameStore"


def put_asset(store: FrameStore, name: str, data: bytes, content_type: str, budget: int | None = None) -> str:
    limit = budget if budget is not None else (CONTEXT_RASTER_BUDGET_BYTES if content_type == "image/png" else CONTEXT_JSON_BUDGET_BYTES)
    if len(data) > limit:
        raise ValueError(f"{name} exceeds {limit} bytes")
    digest = hashlib.sha256(data).hexdigest()[:20]
    pathname = f"context/assets/{digest}/{name}"
    memo = current_asset_memo()
    with _ASSET_MEMO_LOCK:
        cached = memo.get(pathname)
    if cached:
        return cached
    # Content-addressed blob keys embed the digest. Skip the pre-GET and treat PUT conflict as the existing object.
    if _blob_store(store):
        url = store.put_bytes(pathname, data, content_type, cache_seconds=60 * 60 * 24 * 30, overwrite=False)
    else:
        overwrite = False
        try:
            existing = store.get_bytes(pathname, max_bytes=limit)
        except (RuntimeError, ValueError):
            existing = None
            overwrite = True
        if existing == data:
            url = store.url_for(pathname)
        else:
            url = store.put_bytes(
                pathname,
                data,
                content_type,
                cache_seconds=60 * 60 * 24 * 30,
                overwrite=overwrite or existing is not None,
            )
    with _ASSET_MEMO_LOCK:
        memo[pathname] = url
    return url


def put_json_asset(store: FrameStore, name: str, payload: Any, budget: int = CONTEXT_JSON_BUDGET_BYTES) -> str:
    return put_asset(store, name, json_bytes(payload, budget), "application/json", budget)


def put_monitor_asset(store: FrameStore, name: str, monitors: list[dict[str, Any]]) -> str:
    payload = {"monitors": monitors, "count": len(monitors), "sourceCount": len(monitors)}
    return put_json_asset(store, name, payload, MONITOR_JSON_BUDGET_BYTES)


def asset_path_from_url(url: str) -> str | None:
    if "context/assets/" not in url:
        return None
    path = "context/assets/" + url.split("context/assets/", 1)[1].split("?", 1)[0].split("#", 1)[0]
    return path if _ASSET_PATH.fullmatch(path) else None


def seed_asset_memo(store: FrameStore, previous: dict[str, Any] | None, *, workers: int | None = None) -> bool:
    memo = current_asset_memo()
    paths: list[str] = []
    seen: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for nested in value.values():
                collect(nested)
        elif isinstance(value, list):
            for nested in value:
                collect(nested)
        elif isinstance(value, str):
            path = asset_path_from_url(value)
            if path and path not in seen:
                seen.add(path)
                paths.append(path)

    collect(previous)
    verify = os.environ.get("CONTEXT_VERIFY_ASSETS") == "1"
    # Content-addressed blob keys embed the digest; HEAD 200 is sufficient trust unless CONTEXT_VERIFY_ASSETS=1.
    if _blob_store(store) and not verify:

        def present(path: str) -> bool:
            try:
                return store.exists(path)
            except (OSError, RuntimeError, ValueError):
                return False

        concurrency = max(1, workers or GLOBAL_HTTP_LIMIT)
        results = map_bounded(paths, present, workers=concurrency)
        if not all(results):
            memo.clear()
            return False
        for path in paths:
            memo[path] = store.url_for(path)
        return True

    valid = True
    for path in paths:
        try:
            data = store.get_bytes(path, max_bytes=max(CONTEXT_RASTER_BUDGET_BYTES, MONITOR_JSON_BUDGET_BYTES))
        except (OSError, RuntimeError, ValueError):
            data = None
        digest = path.split("/", 3)[2]
        if data is None or hashlib.sha256(data).hexdigest()[:20] != digest:
            valid = False
        else:
            memo[path] = store.url_for(path)
    if not valid:
        memo.clear()
    return valid


def previous_context(store: FrameStore) -> tuple[dict[str, Any] | None, str | None]:
    pointer = load_json(store, CONTEXT_LATEST_PATH)
    if not pointer:
        return None, None
    path = pointer.get("manifestPath")
    if not isinstance(path, str) or not _MANIFEST_PATH.fullmatch(path):
        return None, None
    try:
        previous = load_json(store, path)
        if previous is not None:
            validate_context_manifest(previous)
        return previous, path
    except ValueError:
        return None, path


_ASSET_PATH = re.compile(r"^context/assets/[0-9a-f]{20}/[A-Za-z0-9._-]+$")
_MANIFEST_PATH = re.compile(r"^context/manifests/[0-9a-f]{20}\.json$")


def _publish_source_frames(store: FrameStore, frames: list[dict[str, Any]], name: str, encode_png: bool) -> list[dict[str, Any]]:
    published = []
    for frame in frames:
        item = dict(frame)
        png = item.get("png")
        if encode_png and png and not item.get("textureUrl"):
            item["textureUrl"] = put_asset(store, name, png, "image/png")
        if not encode_png:
            item.pop("png", None)
            item.pop("textureUrl", None)
            item.pop("sourceMaskUrl", None)
        published.append(item)
    return published


def _load_asset_bytes(store: FrameStore, url: str) -> bytes | None:
    if "context/assets/" not in url:
        return None
    path = "context/assets/" + url.split("context/assets/", 1)[1].split("?", 1)[0].split("#", 1)[0]
    if not _ASSET_PATH.fullmatch(path):
        return None
    try:
        data = store.get_bytes(path)
    except (OSError, RuntimeError) as exc:
        LOGGER.warning("retained asset unread %s: %s", path, redact(str(exc)))
        return None
    if data is None or len(data) > CONTEXT_RASTER_BUDGET_BYTES:
        return None
    return data


def legend_url(store: FrameStore, _previous_best: dict[str, Any] | None, legend: bytes) -> str:
    return put_asset(store, "best-legend.png", legend, "image/png")


def publish_integrated_frames(store: FrameStore, frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def publish(frame: dict[str, Any]) -> dict[str, Any]:
        return publish_integrated_frame(store, frame)

    return map_bounded(frames, publish, workers=min(6, max(1, len(frames))))


def publish_integrated_frame(store: FrameStore, frame: dict[str, Any]) -> dict[str, Any]:
    if frame.get("textureUrl") and frame.get("sourceMaskUrl") and "texturePng" not in frame:
        metrics = current_metrics()
        if metrics and frame.get("detailTiles"):
            metrics.increment("detail_tiles_reused", len(frame["detailTiles"]))
        return {
            "validTime": frame["validTime"],
            "modelRun": frame["modelRun"],
            "textureUrl": frame["textureUrl"],
            "sourceMaskUrl": frame["sourceMaskUrl"],
            "contributors": frame["contributors"],
            **({"detailTiles": frame["detailTiles"]} if frame.get("detailTiles") else {}),
        }
    _validate_forecast_png(frame["texturePng"], "base smoke texture")
    _validate_forecast_png(frame["maskPng"], "base smoke mask")
    published = {
        "validTime": frame["validTime"],
        "modelRun": frame["modelRun"],
        "textureUrl": put_asset(store, "best.png", frame["texturePng"], "image/png"),
        "sourceMaskUrl": put_asset(store, "best-mask.png", frame["maskPng"], "image/png"),
        "contributors": frame["contributors"],
    }
    staged_tiles = frame.get("detailTilePngs") or []
    positions = [(tile.get("column"), tile.get("row")) for tile in staged_tiles]
    if staged_tiles:
        columns = max(column for column, _row in positions if isinstance(column, int)) + 1
        rows = max(row for _column, row in positions if isinstance(row, int)) + 1
        expected = [(column, row) for row in range(rows) for column in range(columns)]
        if positions != expected or len(staged_tiles) not in {4, 16}:
            raise ValueError("detail smoke tiles must form an ordered 2x2 or 4x4 grid")
    detail_tiles = []
    for tile in staged_tiles:
        _validate_forecast_png(tile["texturePng"], "detail smoke texture")
        _validate_forecast_png(tile["maskPng"], "detail smoke mask")
        detail_tiles.append(
            {
                "column": tile["column"],
                "row": tile["row"],
                "textureUrl": put_asset(store, f"best-{tile['column']}-{tile['row']}.png", tile["texturePng"], "image/png"),
                "sourceMaskUrl": put_asset(store, f"best-mask-{tile['column']}-{tile['row']}.png", tile["maskPng"], "image/png"),
            }
        )
    if detail_tiles:
        published["detailTiles"] = detail_tiles
        metrics = current_metrics()
        if metrics:
            metrics.increment("detail_tiles_written", len(detail_tiles))
    return published


def _validate_forecast_png(data: bytes, label: str) -> None:
    if not isinstance(data, bytes) or len(data) > CONTEXT_RASTER_BUDGET_BYTES:
        raise ValueError(f"{label} exceeds its publication budget")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG" or image.size != (1024, 635):
                raise ValueError
            image.verify()
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} must be a valid 1024x635 PNG") from exc


def cleanup_context_assets(
    store: FrameStore,
    manifest: dict[str, Any],
    manifest_path: str,
    previous: dict[str, Any] | None,
    previous_manifest_path: str | None,
    *,
    discover_unknown: bool = True,
) -> None:
    if previous_manifest_path and previous is None:
        raise ValueError("previous publication references unavailable")
    referenced: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for nested in value.values():
                collect(nested)
        elif isinstance(value, list):
            for nested in value:
                collect(nested)
        elif isinstance(value, str) and "context/assets/" in value:
            referenced.add("context/assets/" + value.split("context/assets/", 1)[1])

    collect(manifest)
    collect(previous)
    retained_manifests = {path for path in (manifest_path, previous_manifest_path) if path}
    inventory = load_json(store, CONTEXT_GC_INVENTORY) or {}
    known_assets = {str(item) for item in inventory.get("assets") or [] if isinstance(item, str)}
    known_manifests = {str(item) for item in inventory.get("manifests") or [] if isinstance(item, str)}
    if not known_assets and discover_unknown:
        for pathname in store.list_prefix("context/assets"):
            normalized = pathname.lstrip("/")
            if "context/assets/" in normalized:
                known_assets.add("context/assets/" + normalized.split("context/assets/", 1)[1])
    if not known_manifests and discover_unknown:
        for pathname in store.list_prefix("context/manifests"):
            normalized = pathname.lstrip("/")
            if "context/manifests/" in normalized:
                known_manifests.add("context/manifests/" + normalized.split("context/manifests/", 1)[1])
    stale_assets = sorted(path for path in known_assets if path not in referenced)
    stale_manifests = sorted(path for path in known_manifests if path not in retained_manifests)
    budget = current_budget()
    if budget and not budget.allow(5, reason="cleanup"):
        store.put_json(
            CONTEXT_GC_INVENTORY,
            {"assets": sorted(known_assets | referenced), "manifests": sorted(known_manifests | retained_manifests)},
            cache_seconds=60,
            overwrite=True,
        )
        return
    try:
        if stale_assets:
            store.delete_many(stale_assets)
        if stale_manifests:
            store.delete_many(stale_manifests)
        store.put_json(
            CONTEXT_GC_INVENTORY, {"assets": sorted(referenced), "manifests": sorted(retained_manifests)}, cache_seconds=60, overwrite=True
        )
    except Exception:
        LOGGER.warning("context cleanup failed; retaining extra assets", exc_info=True)
        store.put_json(
            CONTEXT_GC_INVENTORY,
            {"assets": sorted(known_assets | referenced), "manifests": sorted(known_manifests | retained_manifests)},
            cache_seconds=60,
            overwrite=True,
        )
