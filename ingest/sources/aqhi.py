from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from ingest.context_contracts import AQHI_COUNT_VERSION, in_monitor_bounds, iso_utc
from ingest.http import fetch

AQHI_URL = "https://api.weather.gc.ca/collections/aqhi-observations-realtime"
AQHI_ITEMS_URL = f"{AQHI_URL}/items"
AQHI_HOSTS = frozenset({"api.weather.gc.ca"})
AQHI_MAX_BYTES = 512 * 1024
AQHI_LIMIT = 500
AQHI_FRESH_HOURS = 2
AQHI_COLLAPSE_RATIO = 0.5
AQHI_COLLAPSE_MIN = 25


def aqhi_display_value(value: float) -> int | str:
    if value > 10:
        return "10+"
    return max(1, min(10, math.floor(value + 0.5)))


def aqhi_category(value: float) -> str:
    display = aqhi_display_value(value)
    if display == "10+":
        return "Very high"
    assert isinstance(display, int)
    if display <= 3:
        return "Low"
    if display <= 6:
        return "Moderate"
    return "High"


def parse_aqhi(payload: Any, now: datetime) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise ValueError("ECCC AQHI response is not a FeatureCollection")
    features = payload.get("features")
    matched = payload.get("numberMatched")
    returned = payload.get("numberReturned")
    links = payload.get("links")
    if (
        not isinstance(features, list)
        or type(matched) is not int
        or type(returned) is not int
        or matched < 0
        or returned < 0
        or not isinstance(links, list)
    ):
        raise ValueError("ECCC AQHI response metadata is invalid")
    has_next = any(isinstance(link, dict) and link.get("rel") == "next" for link in links)
    if returned != len(features) or returned > AQHI_LIMIT or matched > returned or has_next:
        raise ValueError("ECCC AQHI response was truncated")

    newest: dict[str, tuple[dict[str, Any], bool]] = {}
    horizon = now + timedelta(minutes=15)
    oldest = now - timedelta(hours=AQHI_FRESH_HOURS)
    for feature in features:
        parsed = _parse_feature(feature, oldest, horizon)
        if parsed is None:
            continue
        item, amendment = parsed
        current = newest.get(item["id"])
        if current is None or item["observedAt"] > current[0]["observedAt"] or (
            item["observedAt"] == current[0]["observedAt"] and amendment and not current[1]
        ):
            newest[item["id"]] = (item, amendment)
    return sorted((item for item, _ in newest.values()), key=lambda item: item["id"])


def fetch_aqhi(now: datetime, previous_count: int | None = None) -> list[dict[str, Any]]:
    body = fetch(
        AQHI_ITEMS_URL,
        hosts=AQHI_HOSTS,
        params={"f": "json", "latest": "true", "limit": str(AQHI_LIMIT)},
        timeout=30,
        max_bytes=AQHI_MAX_BYTES,
        retries=1,
    )
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("ECCC AQHI response is not JSON") from exc
    monitors = parse_aqhi(payload, now)
    if not monitors:
        raise ValueError("ECCC published no current AQHI observations")
    if previous_count and previous_count >= AQHI_COLLAPSE_MIN and len(monitors) < previous_count * AQHI_COLLAPSE_RATIO:
        raise ValueError(f"ECCC AQHI snapshot collapsed from {previous_count} to {len(monitors)}")
    return monitors


def _parse_feature(feature: Any, oldest: datetime, horizon: datetime) -> tuple[dict[str, Any], bool] | None:
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        return None
    geometry = feature.get("geometry")
    properties = feature.get("properties")
    if not isinstance(geometry, dict) or geometry.get("type") != "Point" or not isinstance(properties, dict):
        return None
    coordinates = geometry.get("coordinates")
    if (
        not isinstance(coordinates, list)
        or len(coordinates) < 2
        or isinstance(coordinates[0], bool)
        or isinstance(coordinates[1], bool)
        or isinstance(properties.get("aqhi"), bool)
        or properties.get("latest") is not True
    ):
        return None
    if properties.get("aqhi_type") != "AQHI-Observation":
        return None
    observation_type = properties.get("observation_type")
    if observation_type not in {"original", "amendment"}:
        return None
    try:
        lon = float(coordinates[0])
        lat = float(coordinates[1])
        value = float(properties["aqhi"])
        observed = datetime.fromisoformat(str(properties["observation_datetime"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    observed = observed.astimezone(timezone.utc)
    if not math.isfinite(value) or value <= 0 or not in_monitor_bounds(lon, lat) or observed < oldest or observed > horizon:
        return None
    location_id = str(properties.get("location_id") or "").strip()
    name = " ".join(str(properties.get("location_name_en") or "").split())[:120]
    if not location_id or not name:
        return None
    item = {
        "id": f"aqhi:{location_id}",
        "name": name,
        "agency": "Environment and Climate Change Canada",
        "lat": lat,
        "lon": lon,
        "observedAt": iso_utc(observed),
        "indexSystem": "ca-aqhi",
        "indexValue": value,
        "indexMethod": "provider",
        "category": aqhi_category(value),
        "country": "CA",
        "source": "aqhi",
        "sourceUrl": AQHI_URL,
        "preliminary": True,
    }
    return item, observation_type == "amendment"
