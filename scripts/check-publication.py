#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(os.environ.get("TITANSKIES_DATA_DIR", ".local/data"))
try:
    status = json.loads((root / "context/status.json").read_text(encoding="utf-8"))
    pointer = json.loads((root / "context/latest.json").read_text(encoding="utf-8"))
    attempted = datetime.fromisoformat(status["lastAttemptAt"].replace("Z", "+00:00"))
    healthy = pointer.get("version") == 8 and (datetime.now(timezone.utc) - attempted).total_seconds() <= 1800
except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
    healthy = False
raise SystemExit(0 if healthy else 1)
