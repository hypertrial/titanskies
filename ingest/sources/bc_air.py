from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from ingest.config import Settings
from ingest.context_contracts import in_monitor_bounds, iso_utc
from ingest.http import clean_text, fetch, require_host
from ingest.sources.aqi import AQI_METHOD_NOWCAST, NOWCAST_HOURS, aqi_category, is_pm25_mass_unit, nowcast_aqi

BC_AIR_URL = "https://www2.gov.bc.ca/gov/content/environment/air-land-water/air/air-quality/current-air-quality-data"
BC_HOSTS = frozenset({"www.env.gov.bc.ca"})
BC_PST = timezone(timedelta(hours=-8))
BC_COLLAPSE_RATIO = 0.5
BC_COLLAPSE_MIN = 8
# Rolling multi-week provincial PM2.5 file; 4.96 MB on 2026-08-15.
HOURLY_MAX_BYTES = 16 * 1024 * 1024
STATIONS_MAX_BYTES = 1 * 1024 * 1024


def parse_bc_stations(text: str) -> dict[str, dict[str, Any]]:
    stations: dict[str, dict[str, Any]] = {}
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        identifier = str(row.get("EMS_ID") or row.get("EMS ID") or "").strip()
        if not identifier:
            continue
        try:
            lat = float(row.get("LATITUDE") or row.get("Latitude") or "")
            lon = float(row.get("LONGITUDE") or row.get("Longitude") or "")
        except ValueError:
            continue
        if not in_monitor_bounds(lon, lat):
            continue
        stations[identifier] = {
            "name": clean_text(row.get("STATION_NAME") or row.get("Station Name") or identifier),
            "agency": clean_text(row.get("STATION_OWNER") or row.get("OWNER") or "British Columbia ENV"),
            "lat": lat,
            "lon": lon,
        }
    return stations


def parse_bc_hourly(text: str, stations: dict[str, dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    series: dict[str, dict[datetime, Decimal]] = defaultdict(dict)
    names: dict[str, str] = {}
    coords: dict[str, tuple[float, float]] = {}
    end = now.replace(minute=0, second=0, microsecond=0)
    oldest = end - timedelta(hours=NOWCAST_HOURS)
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        identifier = str(row.get("EMS_ID") or row.get("EMS ID") or "").strip()
        if not identifier:
            continue
        parameter = str(row.get("PARAMETER") or row.get("Parameter") or "").upper().replace(".", "")
        if parameter not in {"PM25", "PM2.5"}:
            continue
        unit = str(row.get("UNITS") or row.get("Unit") or "ug/m3")
        if not is_pm25_mass_unit(unit):
            continue
        raw = row.get("RAW_VALUE") or row.get("REPORTED_VALUE") or row.get("VALUE")
        try:
            value = Decimal(str(raw).strip())
        except (InvalidOperation, AttributeError):
            continue
        if not value.is_finite() or value < 0:
            continue
        observed = _parse_pst(row.get("DATE_PST") or row.get("DATE") or "")
        if observed is None or observed > now + timedelta(minutes=15) or observed < oldest:
            continue
        series[identifier][observed.replace(minute=0, second=0, microsecond=0)] = value
        names[identifier] = clean_text(row.get("STATION_NAME") or row.get("Station Name") or identifier)
        try:
            lat = float(row.get("LATITUDE") or row.get("Latitude") or "")
            lon = float(row.get("LONGITUDE") or row.get("Longitude") or "")
        except ValueError:
            continue
        if in_monitor_bounds(lon, lat):
            coords[identifier] = (lat, lon)
    monitors: list[dict[str, Any]] = []
    for identifier, hours in series.items():
        station = stations.get(identifier, {})
        lat_lon = coords.get(identifier)
        lat = float(station.get("lat") or (lat_lon[0] if lat_lon else float("nan")))
        lon = float(station.get("lon") or (lat_lon[1] if lat_lon else float("nan")))
        if not in_monitor_bounds(lon, lat):
            continue
        computed = nowcast_aqi(hours, end)
        if computed is None:
            continue
        aqi, nowcast = computed
        latest_hour = max(hours)
        latest_value = hours[latest_hour]
        monitors.append(
            {
                "id": f"bcair:{identifier}",
                "name": station.get("name") or names.get(identifier) or identifier,
                "agency": station.get("agency") or "British Columbia ENV",
                "lat": lat,
                "lon": lon,
                "observedAt": iso_utc(latest_hour.astimezone(UTC)),
                "aqi": aqi,
                "category": aqi_category(aqi),
                "concentration": float(latest_value),
                "nowcastConcentration": float(nowcast),
                "unit": "µg/m³",
                "source": "bcair",
                "sourceUrl": BC_AIR_URL,
                "aqiMethod": AQI_METHOD_NOWCAST,
                "indexSystem": "us-epa-pm25-aqi",
                "indexValue": aqi,
                "indexMethod": AQI_METHOD_NOWCAST,
                "country": "CA",
                "preliminary": True,
            }
        )
    return sorted(monitors, key=lambda item: item["id"])


def fetch_bc_air(settings: Settings, now: datetime, previous_count: int | None = None) -> list[dict[str, Any]]:
    require_host(settings.bc_hourly_url, BC_HOSTS)
    require_host(settings.bc_stations_url, BC_HOSTS)
    from ingest.http_pool import map_bounded

    hourly, stations_text = map_bounded(
        [
            (settings.bc_hourly_url, 90, HOURLY_MAX_BYTES),
            (settings.bc_stations_url, 60, STATIONS_MAX_BYTES),
        ],
        lambda item: fetch(item[0], hosts=BC_HOSTS, timeout=item[1], max_bytes=item[2], retries=1).decode("utf-8", "replace"),
        workers=2,
    )
    stations = parse_bc_stations(stations_text)
    monitors = parse_bc_hourly(hourly, stations, now)
    if not monitors:
        raise ValueError("BC Air published no current PM2.5 monitors")
    if previous_count and previous_count >= BC_COLLAPSE_MIN and len(monitors) < previous_count * BC_COLLAPSE_RATIO:
        raise ValueError(f"BC Air snapshot collapsed from {previous_count} to {len(monitors)}")
    return monitors


def _parse_pst(value: str) -> datetime | None:
    text = value.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M", "%d/%m/%Y %H:%M"):
        try:
            parsed = datetime.strptime(text, fmt).replace(tzinfo=BC_PST)
            return parsed.astimezone(UTC)
        except ValueError:
            continue
    return None
