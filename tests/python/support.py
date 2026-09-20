from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import numpy as np

from ingest.config import Settings
from ingest.forecast_native_cache import GeographicGrid, NativeField
from ingest.forecast_raster import HrrrGrid


def fixture_settings(root: Path) -> Settings:
    """Return isolated live settings without loading developer credentials."""
    return Settings(
        context_source="live",
        local_frame_dir=root,
        local_cache_dir=root / "cache",
        sinaica_enabled=False,
    )


def fixture_field(provider: str, model_run: str, valid: str | datetime) -> NativeField:
    grid = HrrrGrid() if provider == "hrrr" else GeographicGrid(1024, 635, -145, 72, 85 / 1023, 57 / 634)
    width, height = (grid.nx, grid.ny) if isinstance(grid, HrrrGrid) else (grid.width, grid.height)
    seed = int(hashlib.sha256(f"{provider}{model_run}{valid}".encode()).hexdigest()[:8], 16)
    x = np.linspace(-3, 3, width, dtype=np.float32)[None, :]
    y = np.linspace(-3, 3, height, dtype=np.float32)[:, None]
    values = np.asarray(
        (20 + seed % 30) * np.exp(-((x - (seed % 7) / 10) ** 2 + y**2)),
        dtype=np.float32,
    )
    valid_mask = np.broadcast_to(x > -2.95, values.shape).copy()
    return NativeField(values, valid_mask, grid)
