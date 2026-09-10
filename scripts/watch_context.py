#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_WATCH_SECONDS = 900
MIN_WATCH_SECONDS = 60
MAX_WATCH_SECONDS = 3600


def watch_interval_seconds(raw: str | None = None) -> int:
    value = DEFAULT_WATCH_SECONDS if raw is None else raw
    if isinstance(value, str) and not value.strip():
        value = DEFAULT_WATCH_SECONDS
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = DEFAULT_WATCH_SECONDS
    return min(MAX_WATCH_SECONDS, max(MIN_WATCH_SECONDS, seconds))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh local live context on an interval")
    parser.add_argument("--once", action="store_true", help="Run a single ingest and exit")
    parser.add_argument("--interval", type=int, default=None, help="Seconds between live refreshes")
    args = parser.parse_args(argv)

    from ingest.config import Settings

    settings = Settings.from_env()

    from ingest.context_pipeline import run_context_ingest

    interval = watch_interval_seconds(
        str(args.interval) if args.interval is not None else os.environ.get("CONTEXT_WATCH_SECONDS")
    )

    while True:
        started = time.monotonic()
        result = run_context_ingest(settings)
        print(json.dumps(result, indent=2))
        if args.once:
            return 0 if result.get("ok") else 1
        delay = min(60, interval) if result.get("skipped") else max(0, interval - (time.monotonic() - started))
        try:
            time.sleep(delay)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
