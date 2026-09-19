from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

_ROOT = Path(__file__).resolve().parent.parent
_BLOB_STORE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _validate_blob_settings(token: str, store_id: str, public_base_url: str) -> None:
    if len(token) < 16 or token != token.strip() or not token.isascii() or any(character.isspace() for character in token):
        raise ValueError("BLOB_READ_WRITE_TOKEN is required and malformed when STORAGE_BACKEND=blob")
    if not _BLOB_STORE_ID.fullmatch(store_id):
        raise ValueError("BLOB_STORE_ID is required and must contain only letters, digits, '_' or '-'")
    parsed = urlparse(public_base_url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or host != f"{store_id}.public.blob.vercel-storage.com".lower()
    ):
        raise ValueError("PUBLIC_BLOB_BASE_URL must match the configured public Vercel Blob store")


def load_env_files() -> None:
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


def watch_limits() -> tuple[int, int, int]:
    limits = json.loads((_ROOT / "shared" / "runtime-limits.json").read_text(encoding="utf-8"))["watchSeconds"]
    return int(limits["default"]), int(limits["min"]), int(limits["max"])


def watch_interval_seconds(raw: str | int | None = None) -> int:
    default, minimum, maximum = watch_limits()
    value = default if raw is None else raw
    if isinstance(value, str) and not value.strip():
        value = default
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = default
    return min(maximum, max(minimum, seconds))


def _env_choice(name: str, default: str, choices: set[str]) -> str:
    raw = os.environ.get(name)
    value = default if raw is None or not raw.strip() else raw.strip().lower()
    if value not in choices:
        raise ValueError(f"{name} must be {' or '.join(sorted(choices))}")
    return value


@dataclass(frozen=True)
class Settings:
    context_source: str = "live"
    storage_backend: str = "local"
    blob_token: str = ""
    blob_store_id: str = ""
    public_blob_base_url: str = ""
    local_frame_dir: Path = _ROOT / ".local" / "data"
    local_cache_dir: Path = _ROOT / ".local" / "cache"
    data_url_prefix: str = "/data"
    retention_hours: int = 48
    watch_seconds: int = 900
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
    wfigs_incidents_url: str = (
        "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Incident_Locations_Current/FeatureServer/0/query"
    )
    wfigs_perimeters_url: str = (
        "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query"
    )
    cwfis_url: str = "https://geoserver.cwfif.nrcan.gc.ca/geoserver/wfs"
    cwfis_perimeters_url: str = "https://cwfis.cfs.nrcan.gc.ca/geoserver/public/ows"
    ingest_budget_seconds: int = 300
    http_concurrency: int = 12
    context_source_concurrency: int = 3
    # Default 2 is measurement-gated; keep 1 until benchmark_context shows a wall-time win under 768 MiB RSS.
    compose_concurrency: int = 1

    def __post_init__(self) -> None:
        if self.storage_backend not in {"local", "blob"}:
            raise ValueError("STORAGE_BACKEND must be blob or local")
        if self.storage_backend == "blob":
            if os.environ.get("VERCEL_ENV", "").strip().lower() == "preview":
                raise ValueError("STORAGE_BACKEND=blob is unavailable in Vercel Preview")
            normalized_base = self.public_blob_base_url.rstrip("/")
            normalized_store_id = self.blob_store_id.removeprefix("store_")
            _validate_blob_settings(self.blob_token, normalized_store_id, normalized_base)
            object.__setattr__(self, "blob_store_id", normalized_store_id)
            object.__setattr__(self, "public_blob_base_url", normalized_base)

    @classmethod
    def from_env(cls) -> Settings:
        load_env_files()
        return cls(
            context_source=_env_choice("CONTEXT_SOURCE", "live", {"demo", "live"}),
            storage_backend=_env_choice("STORAGE_BACKEND", "local", {"blob", "local"}),
            blob_token=os.environ.get("BLOB_READ_WRITE_TOKEN", ""),
            blob_store_id=os.environ.get("BLOB_STORE_ID", ""),
            public_blob_base_url=os.environ.get("PUBLIC_BLOB_BASE_URL", "").rstrip("/"),
            local_frame_dir=Path(os.environ.get("TITANSKIES_DATA_DIR") or _ROOT / ".local" / "data"),
            local_cache_dir=Path(os.environ.get("TITANSKIES_CACHE_DIR") or _ROOT / ".local" / "cache"),
            data_url_prefix="/data",
            retention_hours=_env_int("FRAME_RETENTION_HOURS", 48, 24, 168),
            watch_seconds=watch_interval_seconds(os.environ.get("CONTEXT_WATCH_SECONDS")),
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
            compose_concurrency=_env_int("COMPOSE_CONCURRENCY", 1, 1, 4),
        )
