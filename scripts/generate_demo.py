#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ingest.config import Settings
from ingest.context_pipeline import run_context_ingest


def _valid_pointer(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return payload.get("version") == 8 and isinstance(payload.get("manifestUrl"), str)


def _output_path(value: str) -> Path:
    public = (ROOT / "public").resolve()
    output = (ROOT / value).resolve()
    if output == public or not output.is_relative_to(public):
        raise ValueError("demo output must be a subdirectory beneath public/")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the bundled synthetic v8 demo publication")
    parser.add_argument("--hours", type=int, default=37, help="Compatibility option; v8 always publishes 37 hourly frames")
    parser.add_argument("--out", default="public/demo")
    parser.add_argument("--check-existing", action="store_true")
    args = parser.parse_args()
    try:
        out = _output_path(args.out)
    except ValueError as exc:
        parser.error(str(exc))
    latest = out / "context" / "latest.json"
    if args.check_existing and _valid_pointer(latest):
        print("demo publication present")
        return

    if out.exists():
        shutil.rmtree(out)
    settings = Settings(
        context_source="demo",
        local_frame_dir=out,
        local_cache_dir=out / ".cache",
        data_url_prefix="/demo",
    )
    result = run_context_ingest(settings, datetime(2024, 7, 15, 20, 0, tzinfo=timezone.utc))
    if not result.get("ok"):
        raise SystemExit(f"demo generation failed: {result}")
    shutil.rmtree(out / ".cache", ignore_errors=True)
    shutil.rmtree(out / "locks", ignore_errors=True)
    (out / ".lease.guard").unlink(missing_ok=True)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
