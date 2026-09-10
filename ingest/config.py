from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _load_env_files() -> None:
    for name in (".env.local", ".env"):
        path = _ROOT / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip().strip("'").strip('"')


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    try:
        value = default if raw is None or not raw.strip() else int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    return min(maximum, max(minimum, value))


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return default if raw is None or not raw.strip() else float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _env_choice(name: str, default: str, choices: set[str]) -> str:
    raw = os.environ.get(name)
    value = default if raw is None or not raw.strip() else raw.strip().lower()
    if value not in choices:
        raise ValueError(f"{name} must be {' or '.join(sorted(choices))}")
    return value


@dataclass(frozen=True)
class Settings:
    context_source: str = "live"
    local_frame_dir: Path = _ROOT / ".local" / "data"
    local_cache_dir: Path = _ROOT / ".local" / "cache"
    data_url_prefix: str = "/data"
    retention_hours: int = 48
    lock_seconds: int = 420
    context_orphan_gc_enabled: bool = True
    airnow_api_key: str = ""
    airnow_base_url: str = "https://www.airnowapi.org/aq/data/"
    hrrr_smoke_enabled: bool = True
    hrrr_nomads_base: str = "https://nomads.ncep.noaa.gov"
    hrrr_concurrency: int = 4
    firework_concurrency: int = 6
    firework_wms_url: str = "https://geo.weather.gc.ca/geomet"
    bc_hourly_url: str = "https://www.env.gov.bc.ca/epd/bcairquality/aqo/csv/Hourly_Raw_Air_Data/Air_Quality/PM25.csv"
    bc_stations_url: str = "https://www.env.gov.bc.ca/epd/bcairquality/aqo/csv/bc_air_monitoring_stations.csv"
    sinaica_enabled: bool = True
    sinaica_base_url: str = "https://sinaica.inecc.gob.mx/"
    sinaica_stations_url: str = "https://sinaica.inecc.gob.mx/data.php"
    sinaica_rpc_url: str = "https://sinaica.inecc.gob.mx/lib/libd/cnxn.php"
    sinaica_graph_url: str = "https://sinaica.inecc.gob.mx/pags/datGrafs.php"
    sinaica_concurrency: int = 4
    sinaica_min_stations: int = 8
    sinaica_stale_rate: float = 0.85
    wfigs_incidents_url: str = "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0/query"
    wfigs_perimeters_url: str = "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query"
    cwfis_url: str = "https://geoserver.cwfif.nrcan.gc.ca/geoserver/wfs"
    cwfis_perimeters_url: str = "https://cwfis.cfs.nrcan.gc.ca/geoserver/public/ows"
    ingest_budget_seconds: int = 300
    http_concurrency: int = 12
    context_source_concurrency: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        _load_env_files()
        return cls(
            context_source=_env_choice("CONTEXT_SOURCE", "live", {"demo", "live"}),
            local_frame_dir=Path(os.environ.get("TITANSKIES_DATA_DIR") or _ROOT / ".local" / "data"),
            local_cache_dir=Path(os.environ.get("TITANSKIES_CACHE_DIR") or _ROOT / ".local" / "cache"),
            data_url_prefix="/data",
            retention_hours=_env_int("FRAME_RETENTION_HOURS", 48, 24, 168),
            lock_seconds=_env_int("INGEST_LOCK_SECONDS", 420, 60, 420),
            context_orphan_gc_enabled=_env_bool("CONTEXT_ORPHAN_GC_ENABLED", True),
            airnow_api_key=os.environ.get("AIRNOW_API_KEY", ""),
            airnow_base_url=os.environ.get("AIRNOW_BASE_URL", cls.airnow_base_url).rstrip("/") + "/",
            hrrr_smoke_enabled=_env_bool("HRRR_SMOKE_ENABLED", True),
            hrrr_nomads_base=os.environ.get("HRRR_NOMADS_BASE", cls.hrrr_nomads_base).rstrip("/"),
            hrrr_concurrency=_env_int("HRRR_CONCURRENCY", 4, 1, 8),
            firework_concurrency=_env_int("FIREWORK_CONCURRENCY", 6, 1, 8),
            firework_wms_url=os.environ.get("FIREWORK_WMS_URL", cls.firework_wms_url),
            bc_hourly_url=os.environ.get("BC_HOURLY_URL", cls.bc_hourly_url),
            bc_stations_url=os.environ.get("BC_STATIONS_URL", cls.bc_stations_url),
            sinaica_enabled=_env_bool("SINAICA_ENABLED", True),
            sinaica_base_url=os.environ.get("SINAICA_BASE_URL", cls.sinaica_base_url).rstrip("/") + "/",
            sinaica_stations_url=os.environ.get("SINAICA_STATIONS_URL", cls.sinaica_stations_url),
            sinaica_rpc_url=os.environ.get("SINAICA_RPC_URL", cls.sinaica_rpc_url),
            sinaica_graph_url=os.environ.get("SINAICA_GRAPH_URL", cls.sinaica_graph_url),
            sinaica_concurrency=_env_int("SINAICA_CONCURRENCY", 4, 1, 8),
            sinaica_min_stations=_env_int("SINAICA_MIN_STATIONS", 8, 1, 500),
            sinaica_stale_rate=_env_float("SINAICA_STALE_RATE", 0.85),
            wfigs_incidents_url=os.environ.get("WFIGS_INCIDENTS_URL", cls.wfigs_incidents_url),
            wfigs_perimeters_url=os.environ.get("WFIGS_PERIMETERS_URL", cls.wfigs_perimeters_url),
            cwfis_url=os.environ.get("CWFIS_URL", cls.cwfis_url),
            cwfis_perimeters_url=os.environ.get("CWFIS_PERIMETERS_URL", cls.cwfis_perimeters_url),
            ingest_budget_seconds=_env_int("INGEST_BUDGET_SECONDS", 300, 60, 300),
            http_concurrency=_env_int("INGEST_HTTP_CONCURRENCY", 12, 1, 16),
            context_source_concurrency=_env_int("CONTEXT_SOURCE_CONCURRENCY", 3, 1, 6),
        )
