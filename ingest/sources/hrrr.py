from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlencode

import numpy as np

from ingest.auth import redact
from ingest.config import Settings
from ingest.context_contracts import CONTEXT_BOUNDS, iso_utc
from ingest.forecast_palette import KG_PER_UG, PALETTE_UNITS
from ingest.forecast_raster import (
    BASE_RASTER_GRID,
    HRRR_LAT1,
    HRRR_LATIN1,
    HRRR_LATIN2,
    HRRR_LOV,
    HRRR_NX,
    HRRR_NY,
    HrrrGrid,
    PROCESSING_VERSION,
    RasterGrid,
    rasterize_hrrr,
)
from ingest.forecast_native_cache import NativeField
from ingest.http import fetch
from ingest.perf import current_budget, timed_provider_phase

HRRR_URL = "https://rapidrefresh.noaa.gov/hrrr/HRRRsmoke/"
HRRR_HOSTS = frozenset({"nomads.ncep.noaa.gov"})
HRRR_VARIABLE = "hrrr_smoke_concentration"
HRRR_GRIB_NAME = "MASSDEN"
HRRR_LEVEL = "8 m above ground"
HRRR_NATIVE_PROCESSING_VERSION = "hrrr-massden-native-ug-v9"
STANDARD_HOURS = 18
EXTENDED_HOURS = 48
EXTENDED_CYCLES = (0, 6, 12, 18)
IDX_MAX_BYTES = 256 * 1024
GRIB_MAX_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class HrrrCycle:
    run: datetime
    hours: tuple[int, ...]
    kind: str


@dataclass(frozen=True)
class HrrrField:
    values: np.ndarray
    valid: np.ndarray
    model_run: datetime
    valid_time: datetime
    grid: HrrrGrid


def nomads_dir(run: datetime) -> str:
    return f"/hrrr.{run:%Y%m%d}/conus"


def nomads_file(run: datetime, hour: int) -> str:
    return f"hrrr.t{run:%H}z.wrfsfcf{hour:02d}.grib2"


def nomads_idx_url(base: str, run: datetime, hour: int) -> str:
    return f"{base.rstrip('/')}/pub/data/nccf/com/hrrr/prod{nomads_dir(run)}/{nomads_file(run, hour)}.idx"


def nomads_filter_url(base: str, run: datetime, hour: int) -> str:
    params = {
        "file": nomads_file(run, hour),
        "lev_8_m_above_ground": "on",
        "var_MASSDEN": "on",
        "subregion": "",
        "leftlon": f"{CONTEXT_BOUNDS['west']:.0f}",
        "rightlon": f"{CONTEXT_BOUNDS['east']:.0f}",
        "toplat": f"{CONTEXT_BOUNDS['north']:.0f}",
        "bottomlat": f"{CONTEXT_BOUNDS['south']:.0f}",
        "dir": nomads_dir(run),
    }
    return f"{base.rstrip('/')}/cgi-bin/filter_hrrr_2d.pl?{urlencode(params)}"


def parse_hrrr_index(text: str) -> bool:
    haystack = text.upper()
    return "MASSDEN" in haystack and "8 M ABOVE GROUND" in haystack


def _get_text(url: str, *, timeout: int = 30, max_bytes: int = IDX_MAX_BYTES) -> str:
    return fetch(url, hosts=HRRR_HOSTS, timeout=timeout, max_bytes=max_bytes, retries=2).decode("utf-8", "replace")


def cycle_available(settings: Settings, run: datetime, hour: int) -> bool:
    try:
        return parse_hrrr_index(_get_text(nomads_idx_url(settings.hrrr_nomads_base, run, hour)))
    except RuntimeError:
        return False


def _complete_hours(settings: Settings, run: datetime, last_hour: int) -> bool:
    required = (0, last_hour) if last_hour <= 6 else (0, last_hour // 2, last_hour)
    return all(cycle_available(settings, run, hour) for hour in required)


def discover_hrrr(settings: Settings, now: datetime) -> tuple[HrrrCycle, HrrrCycle | None]:
    from ingest.http_pool import map_bounded

    runs = [(now - timedelta(hours=age)).replace(minute=0, second=0, microsecond=0) for age in range(0, 10)]

    def probe(run: datetime) -> tuple[datetime, bool, bool]:
        standard_ok = _complete_hours(settings, run, STANDARD_HOURS)
        extended_ok = run.hour in EXTENDED_CYCLES and _complete_hours(settings, run, EXTENDED_HOURS)
        return run, standard_ok, extended_ok

    results = map_bounded(runs, probe, workers=min(4, len(runs)))
    standard: HrrrCycle | None = None
    extended: HrrrCycle | None = None
    for run, standard_ok, extended_ok in results:
        if standard is None and standard_ok:
            standard = HrrrCycle(run, tuple(range(STANDARD_HOURS + 1)), "standard")
        if extended is None and extended_ok:
            extended = HrrrCycle(run, tuple(range(EXTENDED_HOURS + 1)), "extended")
        if standard and extended:
            break
    if standard is None:
        raise ValueError("HRRR has no complete standard cycle")
    return standard, extended


def decode_hrrr_massden(data: bytes) -> HrrrField:
    try:
        import eccodeslib  # noqa: F401  # bundled libeccodes for portable Linux wheels
    except ImportError:
        pass
    try:
        import eccodes
    except ImportError as exc:
        raise RuntimeError("eccodes is required to decode HRRR GRIB2") from exc
    gid = None
    try:
        gid = eccodes.codes_new_from_message(data)
        short_name = str(eccodes.codes_get(gid, "shortName")).upper()
        level = eccodes.codes_get(gid, "level")
        type_of_level = str(eccodes.codes_get(gid, "typeOfLevel"))
        parameter = tuple(
            int(eccodes.codes_get(gid, key)) if eccodes.codes_is_defined(gid, key) else -1
            for key in ("discipline", "parameterCategory", "parameterNumber")
        )
        if short_name not in {"MASSDEN", "MASS DEN"} and parameter != (0, 20, 0):
            raise ValueError(f"unexpected HRRR variable {short_name}")
        if int(level) != 8 or type_of_level != "heightAboveGround":
            raise ValueError(f"unexpected HRRR level {level} {type_of_level}")
        nx = int(eccodes.codes_get(gid, "Nx"))
        ny = int(eccodes.codes_get(gid, "Ny"))
        validate_hrrr_grid(HrrrGrid(nx=nx, ny=ny))
        values = np.asarray(eccodes.codes_get_values(gid), dtype=np.float32).reshape((ny, nx))
        missing = float(eccodes.codes_get(gid, "missingValue"))
        valid = np.isfinite(values) & (values != missing) & (values >= 0)
        values = np.where(valid, values / KG_PER_UG, np.nan)
        data_date = str(eccodes.codes_get(gid, "dataDate"))
        data_time = int(eccodes.codes_get(gid, "dataTime"))
        forecast_hour = int(eccodes.codes_get(gid, "forecastTime"))
        model_run = datetime.strptime(f"{data_date}{data_time:04d}", "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
        valid_time = model_run + timedelta(hours=forecast_hour)
        latin1 = float(eccodes.codes_get(gid, "Latin1InDegrees")) if eccodes.codes_is_defined(gid, "Latin1InDegrees") else HRRR_LATIN1
        latin2 = float(eccodes.codes_get(gid, "Latin2InDegrees")) if eccodes.codes_is_defined(gid, "Latin2InDegrees") else HRRR_LATIN2
        lov = float(eccodes.codes_get(gid, "LoVInDegrees")) if eccodes.codes_is_defined(gid, "LoVInDegrees") else HRRR_LOV
        if lov > 180:
            lov -= 360
        lat1 = float(eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees")) if eccodes.codes_is_defined(gid, "latitudeOfFirstGridPointInDegrees") else HRRR_LAT1
        lon1 = float(eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees")) if eccodes.codes_is_defined(gid, "longitudeOfFirstGridPointInDegrees") else -122.719528
        if lon1 > 180:
            lon1 -= 360
        grid = HrrrGrid(nx=nx, ny=ny, lat1=lat1, lon1=lon1, lov=lov, latin1=latin1, latin2=latin2)
        return HrrrField(values=values, valid=valid, model_run=model_run, valid_time=valid_time, grid=grid)
    except Exception as exc:
        raise ValueError(redact(f"invalid HRRR GRIB2: {exc}")) from None
    finally:
        if gid is not None:
            eccodes.codes_release(gid)


def fetch_hrrr_grib(settings: Settings, run: datetime, hour: int) -> bytes:
    url = nomads_filter_url(settings.hrrr_nomads_base, run, hour)
    data = fetch(url, hosts=HRRR_HOSTS, timeout=90, max_bytes=GRIB_MAX_BYTES, retries=2)
    if not data.startswith(b"GRIB"):
        raise ValueError("HRRR subset was not GRIB2")
    return data


def fetch_hrrr_hour(
    settings: Settings,
    model_run: str,
    valid_time: str,
    *,
    encode_png: bool = True,
    raster_grid: RasterGrid = BASE_RASTER_GRID,
) -> dict[str, Any]:
    run = datetime.fromisoformat(model_run.replace("Z", "+00:00"))
    valid = datetime.fromisoformat(valid_time.replace("Z", "+00:00"))
    hour = int((valid - run).total_seconds() // 3600)
    if hour < 0 or hour > 48:
        raise ValueError("HRRR forecast hour is outside 0-48")
    grib = fetch_hrrr_grib(settings, run, hour)
    field = decode_hrrr_massden(grib)
    if abs((field.valid_time - valid).total_seconds()) > 60:
        raise ValueError("HRRR valid time does not match requested forecast hour")
    if not np.any(field.valid):
        raise ValueError("HRRR MASSDEN contained no valid values")
    png, sample, sample_valid = rasterize_hrrr(
        field.values, field.valid, field.grid, encode_png=encode_png, raster_grid=raster_grid
    )
    payload = {
        "validTime": valid_time,
        "modelRun": model_run,
        "values": sample,
        "valid": sample_valid,
        "processingVersion": PROCESSING_VERSION,
    }
    if png is not None:
        payload["png"] = png
    return payload


def fetch_hrrr_native_hour(settings: Settings, model_run: str, valid_time: str) -> NativeField:
    run = datetime.fromisoformat(model_run.replace("Z", "+00:00"))
    valid = datetime.fromisoformat(valid_time.replace("Z", "+00:00"))
    hour = int((valid - run).total_seconds() // 3600)
    if hour < 0 or hour > 48:
        raise ValueError("HRRR forecast hour is outside 0-48")
    field = decode_hrrr_massden(fetch_hrrr_grib(settings, run, hour))
    if abs((field.valid_time - valid).total_seconds()) > 60:
        raise ValueError("HRRR valid time does not match requested forecast hour")
    if not np.any(field.valid):
        raise ValueError("HRRR MASSDEN contained no valid values")
    return NativeField(field.values, field.valid, field.grid, "hrrr", model_run, valid_time, "µg/m³", HRRR_NATIVE_PROCESSING_VERSION)


def planned_hours(standard: HrrrCycle, extended: HrrrCycle | None) -> list[tuple[HrrrCycle, int]]:
    jobs = [(standard, hour) for hour in standard.hours]
    if extended and standard.hours:
        last_standard_valid = max(standard.run + timedelta(hours=hour) for hour in standard.hours)
        jobs.extend(
            (extended, hour)
            for hour in extended.hours
            if extended.run + timedelta(hours=hour) > last_standard_valid
        )
    return sorted(jobs, key=lambda item: item[0].run + timedelta(hours=item[1]))


def fetch_hrrr(
    settings: Settings,
    now: datetime,
    discovery: tuple[HrrrCycle, HrrrCycle | None] | None = None,
    reuse: dict[tuple[str, int], dict[str, Any]] | None = None,
    required_times: set[str] | None = None,
    on_frame: Callable[[dict[str, Any]], None] | None = None,
    *,
    encode_png: bool = True,
    raster_grid: RasterGrid = BASE_RASTER_GRID,
) -> tuple[list[dict[str, Any]], bytes, dict[str, Any]]:
    from ingest.http import HttpFetchError
    from ingest.http_pool import map_isolated_batches

    standard, extended = discovery or discover_hrrr(settings, now)
    published: list[dict[str, Any]] = []
    stats: dict[str, Any] = {"downloaded": 0, "reused": 0, "bytes": 0, "standardRun": iso_utc(standard.run), "extendedRun": iso_utc(extended.run) if extended else None}
    stats_lock = threading.Lock()

    def render(cycle: HrrrCycle, hour: int) -> dict[str, Any]:
        key = (iso_utc(cycle.run), hour)
        if reuse and key in reuse:
            with stats_lock:
                stats["reused"] += 1
            return reuse[key]
        with timed_provider_phase("hrrr", "download"):
            grib = fetch_hrrr_grib(settings, cycle.run, hour)
        with timed_provider_phase("hrrr", "decode"):
            field = decode_hrrr_massden(grib)
        if abs((field.valid_time - (cycle.run + timedelta(hours=hour))).total_seconds()) > 60:
            raise ValueError("HRRR valid time does not match requested forecast hour")
        if not np.any(field.valid):
            raise ValueError("HRRR MASSDEN contained no valid values")
        with timed_provider_phase("hrrr", "decode"):
            png, sample, sample_valid = rasterize_hrrr(
                field.values, field.valid, field.grid, encode_png=encode_png, raster_grid=raster_grid
            )
        with stats_lock:
            stats["downloaded"] += 1
            stats["bytes"] += len(grib)
        payload = {
            "validTime": iso_utc(cycle.run + timedelta(hours=hour)),
            "modelRun": iso_utc(cycle.run),
            "values": sample,
            "valid": sample_valid,
            "processingVersion": PROCESSING_VERSION,
        }
        if png is not None:
            payload["png"] = png
        if on_frame:
            on_frame(payload)
        return payload

    all_jobs = planned_hours(standard, extended)
    jobs = all_jobs if required_times is None else [
        item for item in all_jobs if iso_utc(item[0].run + timedelta(hours=item[1])) in required_times
    ]
    results = map_isolated_batches(
        jobs,
        lambda item: render(*item),
        workers=settings.hrrr_concurrency,
        stop_after=lambda batch: any(
            isinstance(result, HttpFetchError) and result.provider_outage for result in batch
        ),
        before_batch=lambda: (
            HttpFetchError("ingest acquisition deadline reached", host="nomads.ncep.noaa.gov", provider_outage=True)
            if current_budget() and current_budget().acquisition_remaining() <= 0
            else None
        ),
    )
    errors = [result for result in results if isinstance(result, BaseException)]
    if errors:
        raise errors[0]
    loaded = {frame["validTime"]: frame for frame in results if isinstance(frame, dict)}
    frames = [
        loaded.get(
            iso_utc(cycle.run + timedelta(hours=hour)),
            {"validTime": iso_utc(cycle.run + timedelta(hours=hour)), "modelRun": iso_utc(cycle.run), "processingVersion": PROCESSING_VERSION},
        )
        for cycle, hour in all_jobs
    ]
    from ingest.forecast_palette import official_legend_png
    legend = official_legend_png()
    for frame in frames:
        published.append({
            "validTime": frame["validTime"],
            "modelRun": frame["modelRun"],
            "png": frame.get("png"),
            "values": frame.get("values"),
            "valid": frame.get("valid"),
            "textureUrl": frame.get("textureUrl"),
            "processingVersion": frame.get("processingVersion", PROCESSING_VERSION),
        })
    last_valid = datetime.fromisoformat(published[-1]["validTime"].replace("Z", "+00:00"))
    if last_valid < now:
        raise PermissionError("HRRR forecast has expired")
    return published, legend, stats


def hrrr_run_metadata(standard: HrrrCycle, extended: HrrrCycle | None) -> dict[str, Any]:
    return {
        "modelId": "hrrr",
        "variable": HRRR_VARIABLE,
        "units": PALETTE_UNITS,
        "nativeResolutionKm": 3,
        "ingestMethod": "nomads-grib2",
        "horizonHours": EXTENDED_HOURS if extended else STANDARD_HOURS,
        "modelRun": iso_utc(standard.run),
        "extendedModelRun": iso_utc(extended.run) if extended else None,
        "processingVersion": PROCESSING_VERSION,
    }


def validate_hrrr_grid(grid: HrrrGrid) -> None:
    if grid.nx < 100 or grid.ny < 100:
        raise ValueError("HRRR grid is unexpectedly small")
    if not math.isfinite(grid.lat1) or not math.isfinite(grid.lon1):
        raise ValueError("HRRR grid origin is invalid")
    if abs(grid.nx - HRRR_NX) > 400 or abs(grid.ny - HRRR_NY) > 400:
        raise ValueError("HRRR grid dimensions are outside the expected CONUS family")
