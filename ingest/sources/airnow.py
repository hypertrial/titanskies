from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from ingest.config import Settings
from ingest.context_contracts import AQI_CATEGORIES, in_monitor_bounds, iso_utc
from ingest.http import fetch
from ingest.sources.aqi import AQI_METHOD_PROVIDER, aqi_category, is_pm25_mass_unit

AIRNOW_URL = "https://docs.airnowapi.org/"
AIRNOW_HOSTS = frozenset({"www.airnowapi.org"})
AIRNOW_LOOKBACK_HOURS = 6
AIRNOW_COLLAPSE_RATIO = 0.5
AIRNOW_COLLAPSE_MIN = 20
AIRNOW_TILES = (
    ("alaska", -180.0, 50.0, -130.0, 84.0),
    ("northwest", -130.0, 40.0, -110.0, 84.0),
    ("north-central", -110.0, 40.0, -90.0, 84.0),
    ("northeast", -90.0, 40.0, -45.0, 84.0),
    ("southwest", -170.0, 10.0, -110.0, 40.0),
    ("south-central", -110.0, 10.0, -90.0, 40.0),
    ("southeast", -90.0, 10.0, -45.0, 40.0),
    ("aleutians", 170.0, 50.0, 180.0, 84.0),
)


def parse_airnow(payload: Any, now: datetime | None = None) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ValueError("AirNow response is not a list")
    newest: dict[str, dict[str, Any]] = {}
    horizon = (now or datetime.now(UTC)) + timedelta(minutes=15)
    for record in payload:
        item = _parse_record(record, horizon)
        if item is None:
            continue
        identifier = item["id"]
        if identifier not in newest or item["observedAt"] > newest[identifier]["observedAt"]:
            newest[identifier] = item
    return sorted(newest.values(), key=lambda item: item["id"])


def fetch_airnow(settings: Settings, now: datetime, previous_count: int | None = None) -> list[dict[str, Any]]:
    if not settings.airnow_api_key:
        raise PermissionError("AIRNOW_API_KEY is not configured")
    host = urlparse(settings.airnow_base_url).hostname or ""
    if host not in AIRNOW_HOSTS:
        raise ValueError(f"blocked AirNow host {host}")
    start = now - timedelta(hours=AIRNOW_LOOKBACK_HOURS)
    from ingest.http_pool import map_bounded

    def _tile(item: tuple[str, float, float, float, float]) -> list[Any]:
        _name, west, south, east, north = item
        payload = _fetch_tile(settings, start, now, west, south, east, north)
        if not isinstance(payload, list):
            raise ValueError("AirNow tile response is not a list")
        return payload

    records: list[Any] = []
    for payload in map_bounded(list(AIRNOW_TILES), _tile, workers=min(4, len(AIRNOW_TILES))):
        records.extend(payload)
    monitors = parse_airnow(records, now)
    if not monitors:
        raise ValueError("AirNow published no current PM2.5 monitors")
    if previous_count and previous_count >= AIRNOW_COLLAPSE_MIN and len(monitors) < previous_count * AIRNOW_COLLAPSE_RATIO:
        raise ValueError(f"AirNow snapshot collapsed from {previous_count} to {len(monitors)}")
    return monitors


def _fetch_tile(
    settings: Settings,
    start: datetime,
    now: datetime,
    west: float,
    south: float,
    east: float,
    north: float,
) -> Any:
    params = {
        "startdate": start.strftime("%Y-%m-%dT%H"),
        "enddate": now.strftime("%Y-%m-%dT%H"),
        "parameters": "PM25",
        "BBOX": f"{west},{south},{east},{north}",
        "dataType": "B",
        "format": "application/json",
        "verbose": "1",
        "monitorType": "2",
        "includerawconcentrations": "1",
        "API_KEY": settings.airnow_api_key,
    }
    body = fetch(
        settings.airnow_base_url,
        hosts=AIRNOW_HOSTS,
        params=params,
        timeout=90,
        max_bytes=2 * 1024 * 1024,
        retries=3,
    )
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("AirNow tile is not JSON") from exc


def _parse_record(record: Any, horizon: datetime) -> dict[str, Any] | None:
    if not isinstance(record, dict) or str(record.get("Parameter", "")).upper() not in {"PM2.5", "PM25"}:
        return None
    try:
        lat = float(record["Latitude"])
        lon = float(record["Longitude"])
        aqi = int(record["AQI"])
        concentration = float(record["Value"] if "Value" in record else record["Concentration"])
    except (KeyError, TypeError, ValueError):
        return None
    unit = str(record.get("Unit") or "µg/m³")
    if not is_pm25_mass_unit(unit):
        return None
    if (
        not all(math.isfinite(value) for value in (lat, lon, concentration))
        or not in_monitor_bounds(lon, lat)
        or aqi < 0
        or concentration < 0
    ):
        return None
    observed_raw = record.get("UTC") or record.get("DateObserved")
    try:
        observed = datetime.fromisoformat(str(observed_raw).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=UTC)
    except ValueError:
        return None
    if observed > horizon:
        return None
    identifier = str(record.get("FullAQSCode") or record.get("SiteCode") or "").strip()
    if not identifier:
        identifier = f"{lat:.4f},{lon:.4f}"
    category = record.get("Category")
    category_name = category.get("Name") if isinstance(category, dict) else category
    category_name = str(category_name or aqi_category(aqi)).strip()
    if category_name.lower() not in AQI_CATEGORIES:
        category_name = aqi_category(aqi)
    country = _country(record, identifier)
    item = {
        "id": f"airnow:{identifier}",
        "name": _clean_text(record.get("SiteName") or record.get("ReportingArea") or "AirNow monitor"),
        "agency": _clean_text(record.get("AgencyName") or "AirNow reporting agency"),
        "lat": lat,
        "lon": lon,
        "observedAt": iso_utc(observed.astimezone(UTC)),
        "aqi": aqi,
        "category": category_name,
        "concentration": concentration,
        "unit": unit,
        "source": "airnow",
        "sourceUrl": AIRNOW_URL,
        "aqiMethod": AQI_METHOD_PROVIDER,
        "indexSystem": "us-epa-pm25-aqi",
        "indexValue": aqi,
        "indexMethod": AQI_METHOD_PROVIDER,
        "preliminary": True,
    }
    if country:
        item["country"] = country
    return item


def _country(record: dict[str, Any], identifier: str) -> str | None:
    raw = str(record.get("CountryCode") or "").strip().upper()
    countries = {"US": "US", "USA": "US", "840": "US", "CA": "CA", "CAN": "CA", "124": "CA", "MX": "MX", "MEX": "MX", "484": "MX"}
    if raw in countries:
        return countries[raw]
    state = str(record.get("StateCode") or "").strip().upper()
    if state == "CC":
        return "CA"
    if state == "MX":
        return "MX"
    digits = "".join(character for character in identifier if character.isdigit())
    if (len(digits) == 12 and digits.startswith("840")) or (
        len(digits) in {8, 9} and all(part.isdigit() for part in identifier.split("-"))
    ):
        return "US"
    return None


def _clean_text(value: Any, limit: int = 120) -> str:
    return " ".join(str(value).split())[:limit]
