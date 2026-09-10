#!/usr/bin/env python3
from __future__ import annotations

import json
import resource
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingest.config import Settings
from ingest.forecast_raster import rasterize_hrrr_png
from ingest.sources.hrrr import decode_hrrr_massden, discover_hrrr, fetch_hrrr_grib, planned_hours

MAX_SECONDS = 600
MAX_RSS_BYTES = 2 * 1024 * 1024 * 1024
MAX_FRAME_SECONDS = 12


def _rss_bytes() -> int:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss if sys.platform == "darwin" else rss * 1024


def main() -> None:
    settings = Settings.from_env()
    now = datetime.now(timezone.utc)
    started = time.perf_counter()
    try:
        import eccodes  # noqa: F401
        decoder = "eccodes"
    except ImportError:
        print(json.dumps({"ok": False, "error": "eccodes is not importable in this runtime"}))
        raise SystemExit("HRRR decoder is unavailable")

    standard, extended = discover_hrrr(settings, now)
    jobs = planned_hours(standard, extended)
    cycle, hour = jobs[min(6, len(jobs) - 1)]
    download_started = time.perf_counter()
    grib = fetch_hrrr_grib(settings, cycle.run, hour)
    downloaded = time.perf_counter() - download_started
    field = decode_hrrr_massden(grib)
    png, valid = rasterize_hrrr_png(field.values, field.valid, field.grid)
    elapsed = time.perf_counter() - started
    rss_bytes = _rss_bytes()
    projected = elapsed + max(0, len(jobs) - 1) * max(downloaded, 0.5)
    report = {
        "ok": True,
        "decoder": decoder,
        "standardRun": cycle.run.isoformat(),
        "extendedRun": extended.run.isoformat() if extended else None,
        "plannedFrames": len(jobs),
        "sampleHour": hour,
        "gribBytes": len(grib),
        "pngBytes": len(png),
        "validPixels": int(valid.sum()),
        "elapsedSeconds": round(elapsed, 3),
        "downloadSeconds": round(downloaded, 3),
        "rssBytes": rss_bytes,
        "projectedFullRunSeconds": round(projected, 3),
        "gates": {
            "sampleDuration": elapsed < MAX_FRAME_SECONDS,
            "projectedDuration": projected < MAX_SECONDS,
            "rss": rss_bytes < MAX_RSS_BYTES,
            "png": 0 < len(png) < 750_000,
            "validPixels": int(valid.sum()) > 1000,
        },
    }
    print(json.dumps(report, indent=2))
    if not all(report["gates"].values()):
        raise SystemExit("HRRR ingest failed resource feasibility gates")


if __name__ == "__main__":
    main()
