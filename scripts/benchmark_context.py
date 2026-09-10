#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import time
import sys
import resource
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingest.config import Settings
from ingest.context_pipeline import run_context_ingest
from ingest.context_contracts import CONTEXT_RETAINED_BUDGET_BYTES

MAX_RSS_BYTES = 768 * 1024 * 1024
MAX_ASSET_BYTES = 750_000


def _rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def _run(directory: Path, **overrides):
    settings = replace(
        Settings.from_env(),
        context_source="demo",
        local_frame_dir=directory,
        local_cache_dir=directory / "cache",
        **overrides,
    )
    started = time.perf_counter()
    result = run_context_ingest(settings, datetime(2024, 7, 15, 20, tzinfo=timezone.utc))
    elapsed = time.perf_counter() - started
    if not result.get("ok"):
        raise SystemExit(result)
    public = sum(path.stat().st_size for path in (directory / "context").rglob("*") if path.is_file())
    cache = sum(path.stat().st_size for path in (directory / "cache").rglob("*") if path.is_file()) if (directory / "cache").exists() else 0
    assets = [path.stat().st_size for path in (directory / "context" / "assets").rglob("*") if path.is_file()]
    return result, elapsed, public, cache, max(assets, default=0), _rss_bytes()


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        cold, cold_elapsed, public, cache, largest_asset, cold_rss = _run(root)
        warm, warm_elapsed, warm_public, warm_cache, warm_asset, warm_rss = _run(root)
        changed, changed_elapsed, changed_public, changed_cache, changed_asset, changed_rss = _run(root, hrrr_smoke_enabled=False)
        retained_bytes = max(public + cache, warm_public + warm_cache, changed_public + changed_cache)
        largest_asset = max(largest_asset, warm_asset, changed_asset)
        print(
            "context ingest "
            f"cold={cold_elapsed:.3f}s warm={warm_elapsed:.3f}s hrrrDisabled={changed_elapsed:.3f}s "
            f"public={public / 1024 / 1024:.2f} MiB cache={cache / 1024 / 1024:.2f} MiB "
            f"largestAsset={largest_asset / 1024:.1f} KiB peakRss={max(cold_rss, warm_rss, changed_rss) / 1024 / 1024:.1f} MiB "
            f"filesWritten={cold.get('storageWrites')} sourcePngsSkipped={cold.get('sourcePngsSkipped')} "
            f"fieldCacheHits={warm.get('fieldCacheHits')} splitRecommended={cold.get('splitRecommended')}"
        )
        if max(cold_elapsed, warm_elapsed, changed_elapsed) > 300 or retained_bytes > CONTEXT_RETAINED_BUDGET_BYTES:
            raise SystemExit("context benchmark exceeded its budget")
        if max(cold_rss, warm_rss, changed_rss) >= MAX_RSS_BYTES or largest_asset > MAX_ASSET_BYTES:
            raise SystemExit("v8 context benchmark exceeded its memory or asset budget")
        if cold.get("forecastFrameCount") != 37:
            raise SystemExit("v8 context benchmark did not publish 37 frames")
        if changed.get("ok") is not True:
            raise SystemExit(changed)


if __name__ == "__main__":
    main()
