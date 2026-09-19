#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingest.config import watch_limits  # noqa: E402
from ingest.context_contracts import (  # noqa: E402
    CONTEXT_JSON_BUDGET_BYTES,
    CONTEXT_RASTER_BUDGET_BYTES,
    MONITOR_JSON_BUDGET_BYTES,
    validate_context_manifest,
)
from ingest.local_store import LocalFrameStore  # noqa: E402

root = Path(os.environ.get("TITANSKIES_DATA_DIR", ".local/data"))
store = LocalFrameStore(root)
manifest_pattern = re.compile(r"^context/manifests/([0-9a-f]{20})\.json$")
asset_pattern = re.compile(r"^/data/(context/assets/([0-9a-f]{20})/[a-z0-9][a-z0-9-]*\.(json|png))$")


def asset_paths(value: object) -> set[tuple[str, str, str]]:
    paths: set[tuple[str, str, str]] = set()

    def visit(item: object) -> None:
        if isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, dict):
            for key, child in item.items():
                if key == "url" or key.endswith("Url"):
                    match = asset_pattern.fullmatch(child) if isinstance(child, str) else None
                    if match is None:
                        raise ValueError("invalid asset URL")
                    paths.add((match.group(1), match.group(2), match.group(3)))
                else:
                    visit(child)

    visit(value)
    if not paths:
        raise ValueError("manifest has no assets")
    return paths


try:
    status_bytes = store.get_bytes("context/status.json", max_bytes=CONTEXT_JSON_BUDGET_BYTES)
    pointer_bytes = store.get_bytes("context/latest.json", max_bytes=CONTEXT_JSON_BUDGET_BYTES)
    if status_bytes is None or pointer_bytes is None:
        raise ValueError("publication metadata is missing")
    status = json.loads(status_bytes)
    pointer = json.loads(pointer_bytes)
    attempted = datetime.fromisoformat(status["lastAttemptAt"].replace("Z", "+00:00"))
    complete = datetime.fromisoformat(status["lastCompleteForecastAt"].replace("Z", "+00:00"))
    default_watch, min_watch, max_watch = watch_limits()
    watch = int(os.environ.get("CONTEXT_WATCH_SECONDS", str(default_watch)))
    maximum_age = max(1800, min(max_watch, max(min_watch, watch)) * 2)
    manifest_path = pointer.get("manifestPath")
    match = manifest_pattern.fullmatch(manifest_path or "")
    if (
        pointer.get("version") != 8
        or match is None
        or pointer.get("manifestUrl") != f"/data/{manifest_path}"
        or not isinstance(pointer.get("updatedAt"), str)
    ):
        raise ValueError("invalid pointer")
    datetime.fromisoformat(pointer["updatedAt"].replace("Z", "+00:00"))
    encoded = store.get_bytes(manifest_path, max_bytes=CONTEXT_JSON_BUDGET_BYTES)
    if encoded is None:
        raise ValueError("manifest is missing")
    if hashlib.sha256(encoded).hexdigest()[:20] != match.group(1):
        raise ValueError("manifest hash mismatch")
    manifest = json.loads(encoded)
    validate_context_manifest(manifest)
    for asset_path, expected_hash, extension in asset_paths(manifest):
        maximum_bytes = MONITOR_JSON_BUDGET_BYTES if extension == "json" else CONTEXT_RASTER_BUDGET_BYTES
        asset = store.get_bytes(asset_path, max_bytes=maximum_bytes)
        if asset is None:
            raise ValueError("asset is missing")
        if hashlib.sha256(asset).hexdigest()[:20] != expected_hash:
            raise ValueError("asset hash mismatch")
        if extension == "png" and not asset.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("invalid PNG")
        if extension == "json":
            json.loads(asset)
    frames = manifest["forecast"].get("frames", [])
    last_valid = datetime.fromisoformat(frames[-1]["validTime"].replace("Z", "+00:00"))
    now = datetime.now(UTC)
    healthy = (
        status.get("version") == 1
        and status.get("outcome") in {"fresh", "retained", "failed"}
        and status.get("contextVersion") == 8
        and manifest.get("version") == 8
        and manifest["forecast"].get("integratedStatus") != "unavailable"
        and (now - attempted).total_seconds() <= maximum_age
        and (now - complete).total_seconds() <= maximum_age
        and (last_valid - now).total_seconds() >= 6 * 3600
    )
except (OSError, IndexError, KeyError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
    healthy = False
raise SystemExit(0 if healthy else 1)
