#!/usr/bin/env python3
from __future__ import annotations

import json
import resource
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingest.config import Settings  # noqa: E402
from ingest.context_contracts import iso_utc  # noqa: E402
from ingest.sources.hrrr import discover_hrrr, fetch_hrrr_native_hour, planned_hours  # noqa: E402

MAX_SECONDS = 600
MAX_RSS_BYTES = 2 * 1024 * 1024 * 1024
MAX_FRAME_SECONDS = 12


def _rss_bytes() -> int:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss if sys.platform == "darwin" else rss * 1024


def main() -> None:
    settings = Settings.from_env()
    now = datetime.now(UTC)
    started = time.perf_counter()
    try:
        import eccodes  # noqa: F401

        decoder = "eccodes"
    except ImportError:
        print(json.dumps({"ok": False, "error": "eccodes is not importable in this runtime"}))
        raise SystemExit("HRRR decoder is unavailable") from None

    standard, extended = discover_hrrr(settings, now)
    jobs = planned_hours(standard, extended)
    cycle, hour = jobs[min(6, len(jobs) - 1)]
    model_run = iso_utc(cycle.run)
    valid_time = iso_utc(cycle.run + timedelta(hours=hour))
    download_started = time.perf_counter()
    field = fetch_hrrr_native_hour(settings, model_run, valid_time)
    downloaded = time.perf_counter() - download_started
    elapsed = time.perf_counter() - started
    rss_bytes = _rss_bytes()
    projected = elapsed + max(0, len(jobs) - 1) * max(downloaded, 0.5)
    valid_pixels = int(field.valid.sum())
    gates = {
        "sampleDuration": elapsed < MAX_FRAME_SECONDS,
        "projectedDuration": projected < MAX_SECONDS,
        "rss": rss_bytes < MAX_RSS_BYTES,
        "validPixels": valid_pixels > 1000,
    }
    report = {
        "ok": True,
        "decoder": decoder,
        "standardRun": cycle.run.isoformat(),
        "extendedRun": extended.run.isoformat() if extended else None,
        "plannedFrames": len(jobs),
        "sampleHour": hour,
        "nativeShape": list(field.values.shape),
        "validPixels": valid_pixels,
        "elapsedSeconds": round(elapsed, 3),
        "downloadSeconds": round(downloaded, 3),
        "rssBytes": rss_bytes,
        "projectedFullRunSeconds": round(projected, 3),
        "gates": gates,
    }
    print(json.dumps(report, indent=2))
    if not all(gates.values()):
        raise SystemExit("HRRR ingest failed resource feasibility gates")


if __name__ == "__main__":
    main()
