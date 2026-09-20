from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingest.config import Settings  # noqa: E402
from ingest.cron import CronHandler, handle_cron_get  # noqa: E402


def _run(settings: Settings) -> dict[str, Any]:
    from ingest.context_pipeline import run_context_ingest

    return run_context_ingest(settings)


class handler(CronHandler):
    def do_GET(self) -> None:  # noqa: N802
        handle_cron_get(self, _run)


def main() -> None:
    from ingest.config import load_env_files
    from ingest.context_pipeline import run_context_ingest

    load_env_files()
    os.environ.setdefault("CONTEXT_SOURCE", "demo")
    os.environ.setdefault("STORAGE_BACKEND", "local")
    result = run_context_ingest()
    print(json.dumps(result, indent=2))
    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
