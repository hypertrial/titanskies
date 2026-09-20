#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import heapq
import io
import json
import math
import urllib.request
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageOps

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "public" / "geo"
OUT_GEO = OUT_DIR / "north-america-v3.json"
LANDMARK_MANIFEST = ROOT / "scripts" / "geo_landmarks.json"
OUT_TEXTURE = OUT_DIR / "earth-dark-v2.webp"
OUT_COVERAGE_MASK = ROOT / "shared" / "smoke-coverage-mask-v1.png"
OUT_DETAIL_COVERAGE_MASK = ROOT / "shared" / "smoke-coverage-mask-v2.png"
OUT_V8_DETAIL_COVERAGE_MASK = ROOT / "shared" / "smoke-coverage-mask-v3.png"

NATURAL_EARTH_COMMIT = "ca96624a56bd078437bca8184e78163e5039ad19"
RAW_BASE = f"https://raw.githubusercontent.com/nvkelso/natural-earth-vector/{NATURAL_EARTH_COMMIT}/geojson"
CONTEXT_BOUNDS = (-145.0, 10.0, -45.0, 72.0)
CONTEXT_WIDTH = 1024
CONTEXT_HEIGHT = 635
DETAIL_WIDTH = 2047
DETAIL_HEIGHT = 1269
V8_DETAIL_WIDTH = 4093
V8_DETAIL_HEIGHT = 2537
DISPLAY_BOUNDS = (-170.0, 10.0, -45.0, 84.0)
ALEUTIAN_BOUNDS = (170.0, 50.0, 180.0, 84.0)
COUNTRY_CODES = {"CAN", "USA", "MEX"}
MAP_LABEL_TIERS = (
    "overview",
    "primary",
    "secondary",
    "detail-major",
    "detail-regional",
    "detail-local",
    "local",
)
LANDMARK_CATEGORIES = {"natural", "park", "cultural"}
COASTAL_BUFFER_KM = 200
COVERAGE_POLICY = "land-plus-coastal-water-v1"
COVERAGE_MASK_VERSION = "natural-earth-ocean-50m-v1"
EARTH_RADIUS_KM = 6_371.0088

SOURCES = {
    "coastlines": (
        f"{RAW_BASE}/ne_50m_coastline.geojson",
        "271f1c4c1908312bac6b29d158ea1356544beafc129f260005300913aa5ea283",
    ),
    "ocean": (
        f"{RAW_BASE}/ne_50m_ocean.geojson",
        "5b810162af4b20805838cfd766ef1a445c350cf6aafc8b589515daef30033adf",
    ),
    "country_borders": (
        f"{RAW_BASE}/ne_50m_admin_0_boundary_lines_land.geojson",
        "2faac4f6b34386f3d21b6e018cf151f241f00e5c936d44dd17d7d9bfb147fa48",
    ),
    "region_borders": (
        f"{RAW_BASE}/ne_50m_admin_1_states_provinces_lines.geojson",
        "72cca93c850d412628a5da4bc5ebfe21ba4d376eb34611bde6b623ee73f0fdcf",
    ),
    "cities": (
        f"{RAW_BASE}/ne_10m_populated_places_simple.geojson",
        "fd3fa867a320cbd5c5b6bb5bc550afeec2939fb2cef688e508007282a55ac42f",
    ),
    "raster": (
        "https://naciscdn.org/naturalearth/50m/raster/GRAY_50M_SR_W.zip",
        "25230eaa5c82c9e913b9acede197e29e575817e36df9925231c1a1b92a101d8c",
    ),
}

COUNTRY_LABELS = [
    {"name": "CANADA", "lon": -106.0, "lat": 56.5},
    {"name": "UNITED STATES", "lon": -101.0, "lat": 38.5},
    {"name": "MEXICO", "lon": -102.0, "lat": 23.5},
]

CITIES: list[dict[str, Any]] = [
    {"nameascii": "Vancouver", "country": "CAN", "admin1": "British Columbia", "priority": 1, "mobile": True},
    {"nameascii": "Edmonton", "country": "CAN", "admin1": "Alberta", "priority": 2},
    {"nameascii": "Calgary", "country": "CAN", "admin1": "Alberta", "priority": 2},
    {"nameascii": "Winnipeg", "country": "CAN", "admin1": "Manitoba", "priority": 2},
    {"nameascii": "Toronto", "country": "CAN", "admin1": "Ontario", "priority": 1, "mobile": True},
    {"nameascii": "Ottawa", "country": "CAN", "admin1": "Ontario", "priority": 2},
    {"nameascii": "Montreal", "country": "CAN", "admin1": "Québec", "priority": 1, "display": "Montréal"},
    {"nameascii": "Saskatoon", "country": "CAN", "admin1": "Saskatchewan", "priority": 2},
    {"nameascii": "Moncton", "country": "CAN", "admin1": "New Brunswick", "priority": 2},
    {"nameascii": "Halifax", "country": "CAN", "admin1": "Nova Scotia", "priority": 2},
    {"nameascii": "Charlottetown", "country": "CAN", "admin1": "Prince Edward Island", "priority": 2},
    {"nameascii": "St. John's", "country": "CAN", "admin1": "Newfoundland and Labrador", "priority": 2},
    {"nameascii": "Whitehorse", "country": "CAN", "admin1": "Yukon", "priority": 2, "mobile": True},
    {"nameascii": "Yellowknife", "country": "CAN", "admin1": "Northwest Territories", "priority": 2, "mobile": True},
    {"nameascii": "Iqaluit", "country": "CAN", "admin1": "Nunavut", "priority": 2, "mobile": True},
    {"nameascii": "Seattle", "country": "USA", "admin1": "Washington", "priority": 1},
    {"nameascii": "San Francisco", "country": "USA", "admin1": "California", "priority": 2},
    {"nameascii": "Los Angeles", "country": "USA", "admin1": "California", "priority": 1, "mobile": True},
    {"nameascii": "Denver", "country": "USA", "admin1": "Colorado", "priority": 1},
    {"nameascii": "Dallas", "country": "USA", "admin1": "Texas", "priority": 1},
    {"nameascii": "Houston", "country": "USA", "admin1": "Texas", "priority": 2},
    {"nameascii": "Chicago", "country": "USA", "admin1": "Illinois", "priority": 1, "mobile": True},
    {"nameascii": "Atlanta", "country": "USA", "admin1": "Georgia", "priority": 1},
    {"nameascii": "Miami", "country": "USA", "admin1": "Florida", "priority": 2},
    {"nameascii": "Washington, D.C.", "country": "USA", "admin1": "District of Columbia", "priority": 1, "display": "Washington"},
    {"nameascii": "New York", "country": "USA", "admin1": "New York", "priority": 1, "mobile": True},
    {"nameascii": "Boston", "country": "USA", "admin1": "Massachusetts", "priority": 2},
    {"nameascii": "Anchorage", "country": "USA", "admin1": "Alaska", "priority": 1, "mobile": True},
    {"nameascii": "Fairbanks", "country": "USA", "admin1": "Alaska", "priority": 2},
    {"nameascii": "Juneau", "country": "USA", "admin1": "Alaska", "priority": 2},
    {"nameascii": "Nome", "country": "USA", "admin1": "Alaska", "priority": 2},
    {"nameascii": "Birmingham", "country": "USA", "admin1": "Alabama", "priority": 2},
    {"nameascii": "Phoenix", "country": "USA", "admin1": "Arizona", "priority": 1},
    {"nameascii": "Little Rock", "country": "USA", "admin1": "Arkansas", "priority": 2},
    {"nameascii": "Hartford", "country": "USA", "admin1": "Connecticut", "priority": 2},
    {"nameascii": "Wilmington", "country": "USA", "admin1": "Delaware", "priority": 2},
    {"nameascii": "Boise", "country": "USA", "admin1": "Idaho", "priority": 2},
    {"nameascii": "Indianapolis", "country": "USA", "admin1": "Indiana", "priority": 2},
    {"nameascii": "Des Moines", "country": "USA", "admin1": "Iowa", "priority": 2},
    {"nameascii": "Wichita", "country": "USA", "admin1": "Kansas", "priority": 2},
    {"nameascii": "Louisville", "country": "USA", "admin1": "Kentucky", "priority": 2},
    {"nameascii": "New Orleans", "country": "USA", "admin1": "Louisiana", "priority": 2},
    {"nameascii": "Portland", "country": "USA", "admin1": "Maine", "priority": 2, "display": "Portland, ME"},
    {"nameascii": "Baltimore", "country": "USA", "admin1": "Maryland", "priority": 2},
    {"nameascii": "Detroit", "country": "USA", "admin1": "Michigan", "priority": 2},
    {"nameascii": "Minneapolis", "country": "USA", "admin1": "Minnesota", "priority": 2},
    {"nameascii": "Jackson", "country": "USA", "admin1": "Mississippi", "priority": 2},
    {"nameascii": "Kansas City", "country": "USA", "admin1": "Missouri", "priority": 2},
    {"nameascii": "Billings", "country": "USA", "admin1": "Montana", "priority": 2},
    {"nameascii": "Omaha", "country": "USA", "admin1": "Nebraska", "priority": 2},
    {"nameascii": "Las Vegas", "country": "USA", "admin1": "Nevada", "priority": 2},
    {"nameascii": "Manchester", "country": "USA", "admin1": "New Hampshire", "priority": 2},
    {"nameascii": "Newark", "country": "USA", "admin1": "New Jersey", "priority": 2},
    {"nameascii": "Albuquerque", "country": "USA", "admin1": "New Mexico", "priority": 2},
    {"nameascii": "Charlotte", "country": "USA", "admin1": "North Carolina", "priority": 2},
    {"nameascii": "Fargo", "country": "USA", "admin1": "North Dakota", "priority": 2},
    {"nameascii": "Columbus", "country": "USA", "admin1": "Ohio", "priority": 2},
    {"nameascii": "Oklahoma City", "country": "USA", "admin1": "Oklahoma", "priority": 2},
    {"nameascii": "Portland", "country": "USA", "admin1": "Oregon", "priority": 2, "display": "Portland, OR"},
    {"nameascii": "Philadelphia", "country": "USA", "admin1": "Pennsylvania", "priority": 2},
    {"nameascii": "Providence", "country": "USA", "admin1": "Rhode Island", "priority": 2},
    {"nameascii": "Charleston", "country": "USA", "admin1": "South Carolina", "priority": 2, "display": "Charleston, SC"},
    {"nameascii": "Sioux Falls", "country": "USA", "admin1": "South Dakota", "priority": 2},
    {"nameascii": "Nashville", "country": "USA", "admin1": "Tennessee", "priority": 2},
    {"nameascii": "Salt Lake City", "country": "USA", "admin1": "Utah", "priority": 2},
    {"nameascii": "Burlington", "country": "USA", "admin1": "Vermont", "priority": 2},
    {"nameascii": "Richmond", "country": "USA", "admin1": "Virginia", "priority": 2},
    {"nameascii": "Charleston", "country": "USA", "admin1": "West Virginia", "priority": 2, "display": "Charleston, WV"},
    {"nameascii": "Milwaukee", "country": "USA", "admin1": "Wisconsin", "priority": 2},
    {"nameascii": "Cheyenne", "country": "USA", "admin1": "Wyoming", "priority": 2},
    {"nameascii": "Tijuana", "country": "MEX", "admin1": "Baja California", "priority": 2},
    {"nameascii": "Guadalajara", "country": "MEX", "admin1": "Jalisco", "priority": 2},
    {"nameascii": "Monterrey", "country": "MEX", "admin1": "Nuevo León", "priority": 1},
    {"nameascii": "Mexico City", "country": "MEX", "admin1": "Distrito Federal", "priority": 1, "mobile": True},
]

OVERVIEW_CITY_KEYS = {
    ("Anchorage", "USA", "Alaska"),
    ("Vancouver", "CAN", "British Columbia"),
    ("Los Angeles", "USA", "California"),
    ("Chicago", "USA", "Illinois"),
    ("New York", "USA", "New York"),
    ("Mexico City", "MEX", "Distrito Federal"),
}


def city_display_name(city: dict[str, Any]) -> str:
    return str(city.get("display") or city["nameascii"])


def city_key(city: dict[str, Any]) -> tuple[str, str, str]:
    return (str(city["nameascii"]), str(city["country"]), str(city["admin1"]))


def _fetch(url: str, expected_sha256: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "TitanSkies/0.1.0 (+https://github.com/hypertrial/titanskies)"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected_sha256:
        raise RuntimeError(f"checksum mismatch for {url}: expected {expected_sha256}, got {digest}")
    return data


def _line_strings(geometry: dict[str, Any]) -> Iterable[list[list[float]]]:
    coordinates = geometry.get("coordinates", [])
    if geometry.get("type") == "LineString":
        yield coordinates
    elif geometry.get("type") == "MultiLineString":
        yield from coordinates


def _clean_line(line: list[list[float]]) -> list[list[float]]:
    return [[round(float(point[0]), 4), round(float(point[1]), 4)] for point in line]


def _clip_segment(
    start: list[float], end: list[float], bounds: tuple[float, float, float, float] = DISPLAY_BOUNDS
) -> tuple[list[float], list[float]] | None:
    west, south, east, north = bounds
    x0, y0 = float(start[0]), float(start[1])
    dx, dy = float(end[0]) - x0, float(end[1]) - y0
    lower, upper = 0.0, 1.0
    for p, q in ((-dx, x0 - west), (dx, east - x0), (-dy, y0 - south), (dy, north - y0)):
        if p == 0:
            if q < 0:
                return None
            continue
        ratio = q / p
        if p < 0:
            lower = max(lower, ratio)
        else:
            upper = min(upper, ratio)
        if lower > upper:
            return None
    return ([x0 + lower * dx, y0 + lower * dy], [x0 + upper * dx, y0 + upper * dy])


def _clip_line(line: list[list[float]], bounds: tuple[float, float, float, float] = DISPLAY_BOUNDS) -> list[list[list[float]]]:
    clipped: list[list[list[float]]] = []
    current: list[list[float]] = []
    for index in range(1, len(line)):
        segment = _clip_segment(line[index - 1], line[index], bounds)
        if segment is None:
            if len(current) > 1:
                clipped.append(_clean_line(current))
            current = []
            continue
        start, end = segment
        if not current or current[-1] != start:
            if len(current) > 1:
                clipped.append(_clean_line(current))
            current = [start]
        current.append(end)
    if len(current) > 1:
        clipped.append(_clean_line(current))
    return clipped


def _context_lines(payload: dict[str, Any]) -> list[list[list[float]]]:
    result = []
    for feature in payload.get("features", []):
        for line in _line_strings(feature.get("geometry", {})):
            result.extend(_clip_line(line, DISPLAY_BOUNDS))
            result.extend(_clip_line(line, ALEUTIAN_BOUNDS))
    return result


def _region_lines(payload: dict[str, Any]) -> list[list[list[float]]]:
    result = []
    for feature in payload.get("features", []):
        if feature.get("properties", {}).get("ADM0_A3") not in COUNTRY_CODES:
            continue
        for line in _line_strings(feature.get("geometry", {})):
            result.extend(_clip_line(line, DISPLAY_BOUNDS))
            result.extend(_clip_line(line, ALEUTIAN_BOUNDS))
    return result


def _cities(payload: dict[str, Any]) -> list[dict[str, Any]]:
    allowlist = {city_key(city): city for city in CITIES}
    if len(allowlist) != len(CITIES):
        raise RuntimeError("city allowlist keys must be unique")
    displays = [city_display_name(city) for city in CITIES]
    if len(displays) != len(set(displays)):
        raise RuntimeError("city display names must be unique")
    selected: dict[tuple[str, str, str], dict[str, Any]] = {}
    for feature in payload.get("features", []):
        properties = feature.get("properties", {})
        name = properties.get("nameascii") or properties.get("name")
        code = properties.get("adm0_a3")
        admin1 = properties.get("adm1name")
        spec = allowlist.get((name, code, admin1))
        if spec is None:
            continue
        lon, lat = feature.get("geometry", {}).get("coordinates", [None, None])[:2]
        if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
            continue
        if not (_clip_segment([lon, lat], [lon, lat], DISPLAY_BOUNDS) or _clip_segment([lon, lat], [lon, lat], ALEUTIAN_BOUNDS)):
            continue
        key = city_key(spec)
        selected[key] = {
            "name": city_display_name(spec),
            "searchName": spec["nameascii"],
            "region": spec["admin1"],
            "country": spec["country"],
            "lon": round(float(lon), 4),
            "lat": round(float(lat), 4),
            "priority": spec["priority"],
            "mobile": bool(spec.get("mobile")),
        }
    missing = [f"{city['nameascii']} ({city['country']}/{city['admin1']})" for city in CITIES if city_key(city) not in selected]
    if missing:
        raise RuntimeError(f"missing expected cities: {', '.join(missing)}")
    return sorted(selected.values(), key=lambda city: (city["priority"], city["country"], city["name"]))


def _point_in_display_bounds(lon: float, lat: float) -> bool:
    point = [lon, lat]
    return bool(_clip_segment(point, point, DISPLAY_BOUNDS) or _clip_segment(point, point, ALEUTIAN_BOUNDS))


def _detail_city_tier(min_zoom: float) -> str:
    if min_zoom <= 4:
        return "detail-major"
    if min_zoom <= 5:
        return "detail-regional"
    if min_zoom <= 6:
        return "detail-local"
    return "local"


def _landmark_tier(rank: int) -> str:
    if rank <= 5:
        return "detail-major"
    if rank <= 10:
        return "detail-regional"
    if rank <= 15:
        return "detail-local"
    return "local"


def _landmarks() -> list[dict[str, Any]]:
    records = json.loads(LANDMARK_MANIFEST.read_text(encoding="utf-8"))
    if not isinstance(records, list) or len(records) != 60:
        raise RuntimeError("landmark manifest must contain exactly 60 records")
    ids: set[str] = set()
    country_ranks: dict[str, set[int]] = {code: set() for code in COUNTRY_CODES}
    category_counts: dict[tuple[str, str], int] = {}
    result: list[dict[str, Any]] = []
    for record in records:
        qid = str(record.get("qid", ""))
        country = str(record.get("country", ""))
        category = str(record.get("category", ""))
        rank = record.get("rank")
        lon = record.get("lon")
        lat = record.get("lat")
        if not qid.startswith("Q") or not qid[1:].isdigit() or qid in ids:
            raise RuntimeError(f"invalid or duplicate landmark Wikidata ID: {qid}")
        if country not in COUNTRY_CODES or category not in LANDMARK_CATEGORIES:
            raise RuntimeError(f"invalid landmark classification: {qid}")
        if not isinstance(rank, int) or not 1 <= rank <= 20 or rank in country_ranks[country]:
            raise RuntimeError(f"invalid or duplicate landmark rank: {qid}")
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (lon, lat)):
            raise RuntimeError(f"invalid landmark coordinate: {qid}")
        if not _point_in_display_bounds(float(lon), float(lat)):
            raise RuntimeError(f"landmark outside display bounds: {qid}")
        ids.add(qid)
        country_ranks[country].add(rank)
        category_counts[(country, category)] = category_counts.get((country, category), 0) + 1
        result.append(
            {
                "id": f"wikidata:{qid}",
                "name": str(record["name"]),
                "lon": round(float(lon), 6),
                "lat": round(float(lat), 6),
                "country": country,
                "kind": "landmark",
                "category": category,
                "tier": _landmark_tier(rank),
                "_sort": (rank, qid),
            }
        )
    for country in COUNTRY_CODES:
        if country_ranks[country] != set(range(1, 21)):
            raise RuntimeError(f"landmark ranks must be 1 through 20 for {country}")
        expected = {"natural": 7, "park": 7, "cultural": 6}
        actual = {category: category_counts.get((country, category), 0) for category in LANDMARK_CATEGORIES}
        if actual != expected:
            raise RuntimeError(f"invalid landmark category counts for {country}: {actual}")
    return result


def _map_labels(payload: dict[str, Any]) -> list[dict[str, Any]]:
    allowlist = {city_key(city): city for city in CITIES}
    candidates: list[dict[str, Any]] = []
    curated_seen: set[tuple[str, str, str]] = set()
    natural_earth_ids: set[int] = set()
    for feature in payload.get("features", []):
        properties = feature.get("properties", {})
        country = properties.get("adm0_a3")
        if country not in COUNTRY_CODES:
            continue
        lon, lat = feature.get("geometry", {}).get("coordinates", [None, None])[:2]
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (lon, lat)):
            continue
        if not _point_in_display_bounds(float(lon), float(lat)):
            continue
        ne_id = properties.get("ne_id")
        if not isinstance(ne_id, int) or ne_id in natural_earth_ids:
            raise RuntimeError(f"invalid or duplicate Natural Earth city ID: {ne_id}")
        natural_earth_ids.add(ne_id)
        nameascii = properties.get("nameascii") or properties.get("name")
        key = (nameascii, country, properties.get("adm1name"))
        curated = allowlist.get(key)
        if curated:
            curated_seen.add(key)
            tier = "overview" if key in OVERVIEW_CITY_KEYS else ("primary" if curated["priority"] == 1 else "secondary")
            name = city_display_name(curated)
            curated_order = 0
        else:
            min_zoom_value = properties.get("min_zoom")
            min_zoom = float(min_zoom_value if min_zoom_value is not None else 99)
            tier = "local" if properties.get("adm1name") == "Hawaii" else _detail_city_tier(min_zoom)
            name = properties.get("name") or properties.get("nameascii")
            curated_order = 2
        if not isinstance(name, str) or not name:
            raise RuntimeError(f"Natural Earth city {ne_id} has no usable name")
        min_zoom_value = properties.get("min_zoom")
        scalerank_value = properties.get("scalerank")
        min_zoom = float(min_zoom_value if min_zoom_value is not None else 99)
        scalerank = int(scalerank_value if scalerank_value is not None else 99)
        pop_max = int(properties.get("pop_max") or 0)
        candidates.append(
            {
                "id": f"ne:{ne_id}",
                "name": name,
                "lon": round(float(lon), 4),
                "lat": round(float(lat), 4),
                "country": country,
                "kind": "city",
                "tier": tier,
                "_class": curated_order,
                "_sort": (scalerank, min_zoom, -pop_max, f"ne:{ne_id}"),
            }
        )
    missing = [key for key in allowlist if key not in curated_seen]
    if missing:
        raise RuntimeError(f"curated cities missing from map labels: {missing}")
    if len(candidates) != 1211:
        raise RuntimeError(f"expected 1,211 Natural Earth map labels, found {len(candidates)}")

    landmarks = _landmarks()
    for landmark in landmarks:
        landmark["_class"] = 1
        candidates.append(landmark)

    tier_order = {tier: index for index, tier in enumerate(MAP_LABEL_TIERS)}
    candidates.sort(
        key=lambda label: (
            tier_order[label["tier"]],
            label["_class"],
            *label["_sort"],
            label["id"],
        )
    )
    result: list[dict[str, Any]] = []
    for collision_rank, label in enumerate(candidates):
        public_label = {key: value for key, value in label.items() if not key.startswith("_")}
        public_label["collisionRank"] = collision_rank
        result.append(public_label)
    return result


def _write_texture(raster_zip: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(raster_zip)) as archive:
        with archive.open("GRAY_50M_SR_W.tif") as source:
            with Image.open(source) as raw:
                gray = raw.convert("L").resize((4096, 2048), Image.Resampling.LANCZOS)
    gray = ImageEnhance.Contrast(gray).enhance(0.90)
    texture = ImageOps.colorize(gray, black="#04101a", white="#687a7f", mid="#1a323c")
    OUT_TEXTURE.parent.mkdir(parents=True, exist_ok=True)
    texture.save(OUT_TEXTURE, "WEBP", quality=78, method=6)


def _polygons(geometry: dict[str, Any]) -> Iterable[list[list[list[float]]]]:
    coordinates = geometry.get("coordinates", [])
    if geometry.get("type") == "Polygon":
        yield coordinates
    elif geometry.get("type") == "MultiPolygon":
        yield from coordinates


def _rasterize_ocean(
    payload: dict[str, Any],
    width: int | None = None,
    height: int | None = None,
) -> tuple[np.ndarray, int, int]:
    width = CONTEXT_WIDTH if width is None else width
    height = CONTEXT_HEIGHT if height is None else height
    west, south, east, north = CONTEXT_BOUNDS
    lon_step = (east - west) / (width - 1)
    lat_step = (north - south) / (height - 1)
    vertical_km = EARTH_RADIUS_KM * math.radians(lat_step)
    northern_horizontal_km = EARTH_RADIUS_KM * math.radians(lon_step) * math.cos(math.radians(north))
    x_halo = math.ceil(COASTAL_BUFFER_KM / northern_horizontal_km) + 2
    y_halo = math.ceil(COASTAL_BUFFER_KM / vertical_km) + 2
    width += 2 * x_halo
    height += 2 * y_halo
    expanded_west = west - x_halo * lon_step
    expanded_north = north + y_halo * lat_step

    def point(value: list[float]) -> tuple[float, float]:
        return ((float(value[0]) - expanded_west) / lon_step, (expanded_north - float(value[1])) / lat_step)

    image = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(image)
    for feature in payload.get("features", []):
        for polygon in _polygons(feature.get("geometry", {})):
            if not polygon:
                continue
            draw.polygon([point(value) for value in polygon[0]], fill=1)
            for hole in polygon[1:]:
                draw.polygon([point(value) for value in hole], fill=0)
    return np.asarray(image, dtype=bool), x_halo, y_halo


def _coastal_coverage(
    ocean: np.ndarray,
    x_halo: int,
    y_halo: int,
    width: int | None = None,
    height: int | None = None,
) -> np.ndarray:
    width = CONTEXT_WIDTH if width is None else width
    height = CONTEXT_HEIGHT if height is None else height
    ocean_height, ocean_width = ocean.shape
    coastal = np.zeros_like(ocean)
    coastal[1:, :] |= ocean[1:, :] & ~ocean[:-1, :]
    coastal[:-1, :] |= ocean[:-1, :] & ~ocean[1:, :]
    coastal[:, 1:] |= ocean[:, 1:] & ~ocean[:, :-1]
    coastal[:, :-1] |= ocean[:, :-1] & ~ocean[:, 1:]
    coastal[1:, 1:] |= ocean[1:, 1:] & ~ocean[:-1, :-1]
    coastal[1:, :-1] |= ocean[1:, :-1] & ~ocean[:-1, 1:]
    coastal[:-1, 1:] |= ocean[:-1, 1:] & ~ocean[1:, :-1]
    coastal[:-1, :-1] |= ocean[:-1, :-1] & ~ocean[1:, 1:]

    distances = np.full(ocean.shape, np.inf, dtype=np.float32)
    seed_y, seed_x = np.nonzero(coastal)
    distances[seed_y, seed_x] = 0.0
    queue = [(0.0, int(y), int(x)) for y, x in zip(seed_y, seed_x, strict=True)]
    heapq.heapify(queue)
    west, south, east, north = CONTEXT_BOUNDS
    lon_step = (east - west) / (width - 1)
    lat_step = (north - south) / (height - 1)
    expanded_north = north + y_halo * lat_step
    vertical_km = EARTH_RADIUS_KM * math.radians(lat_step)
    neighbors = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))

    while queue:
        distance, y, x = heapq.heappop(queue)
        if distance != float(distances[y, x]):
            continue
        for dy, dx in neighbors:
            next_y, next_x = y + dy, x + dx
            if not (0 <= next_y < ocean_height and 0 <= next_x < ocean_width and ocean[next_y, next_x]):
                continue
            mean_lat = expanded_north - (y + next_y) * 0.5 * lat_step
            horizontal_km = EARTH_RADIUS_KM * math.radians(lon_step) * math.cos(math.radians(mean_lat))
            step_km = math.hypot(vertical_km if dy else 0.0, horizontal_km if dx else 0.0)
            candidate = float(np.float32(distance + step_km))
            if candidate <= COASTAL_BUFFER_KM and candidate < float(distances[next_y, next_x]):
                distances[next_y, next_x] = candidate
                heapq.heappush(queue, (candidate, next_y, next_x))

    keep = ~ocean | np.isfinite(distances)
    cropped = keep[y_halo : y_halo + height, x_halo : x_halo + width]
    if cropped.shape != (height, width) or not np.any(cropped) or np.all(cropped):
        raise RuntimeError("invalid coastal coverage mask")
    return cropped


def _write_coverage_mask(
    ocean_payload: dict[str, Any],
    *,
    width: int = CONTEXT_WIDTH,
    height: int = CONTEXT_HEIGHT,
    path: Path = OUT_COVERAGE_MASK,
) -> None:
    ocean, x_halo, y_halo = _rasterize_ocean(ocean_payload, width, height)
    coverage = _coastal_coverage(ocean, x_halo, y_halo, width, height)
    image = Image.fromarray(np.where(coverage, 255, 0).astype(np.uint8), mode="L")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG", optimize=True, compress_level=9)


def _validate(payload: dict[str, Any]) -> None:
    if payload.get("version") != 3:
        raise RuntimeError("unexpected geo asset version")
    if not payload.get("coastlines") or not payload.get("countryBorders") or not payload.get("regionBorders"):
        raise RuntimeError("geographic linework is empty")
    if {city["country"] for city in payload.get("cities", [])} != COUNTRY_CODES:
        raise RuntimeError("city data must cover Canada, USA, and Mexico")
    names = [city["name"] for city in payload.get("cities", [])]
    if len(names) != len(set(names)):
        raise RuntimeError("city names must be unique")
    if len(payload.get("cities", [])) != 74:
        raise RuntimeError("searchable city catalog must contain exactly 74 cities")
    labels = payload.get("mapLabels", [])
    city_labels = [label for label in labels if label.get("kind") == "city"]
    landmark_labels = [label for label in labels if label.get("kind") == "landmark"]
    if len(city_labels) != 1211 or len(landmark_labels) != 60:
        raise RuntimeError("map label catalog must contain 1,211 cities and 60 landmarks")
    label_ids = [label.get("id") for label in labels]
    if len(label_ids) != len(set(label_ids)):
        raise RuntimeError("map label IDs must be unique")
    if [label.get("collisionRank") for label in labels] != list(range(len(labels))):
        raise RuntimeError("map label collision ranks must be contiguous and deterministic")
    for label in labels:
        if label.get("tier") not in MAP_LABEL_TIERS or label.get("country") not in COUNTRY_CODES:
            raise RuntimeError(f"invalid map label classification: {label.get('id')}")
        if label.get("kind") == "landmark" and label.get("category") not in LANDMARK_CATEGORIES:
            raise RuntimeError(f"invalid map landmark category: {label.get('id')}")
        lon, lat = label.get("lon"), label.get("lat")
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (lon, lat)):
            raise RuntimeError(f"invalid map label coordinate: {label.get('id')}")
        if not _point_in_display_bounds(float(lon), float(lat)):
            raise RuntimeError(f"map label outside display bounds: {label.get('id')}")
    for key in ("coastlines", "countryBorders", "regionBorders"):
        for line in payload[key]:
            for point in line:
                if len(point) != 2 or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in point):
                    raise RuntimeError(f"invalid coordinate in {key}")
                if not (_clip_segment(point, point, DISPLAY_BOUNDS) or _clip_segment(point, point, ALEUTIAN_BOUNDS)):
                    raise RuntimeError(f"coordinate outside context bounds in {key}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage-only", action="store_true")
    parser.add_argument("--geo-only", action="store_true")
    args = parser.parse_args()
    if args.coverage_only:
        ocean_payload = json.loads(_fetch(*SOURCES["ocean"]))
        _write_coverage_mask(ocean_payload)
        _write_coverage_mask(
            ocean_payload,
            width=DETAIL_WIDTH,
            height=DETAIL_HEIGHT,
            path=OUT_DETAIL_COVERAGE_MASK,
        )
        _write_coverage_mask(
            ocean_payload,
            width=V8_DETAIL_WIDTH,
            height=V8_DETAIL_HEIGHT,
            path=OUT_V8_DETAIL_COVERAGE_MASK,
        )
        print(f"wrote {OUT_COVERAGE_MASK} ({OUT_COVERAGE_MASK.stat().st_size:,} bytes)")
        print(f"wrote {OUT_DETAIL_COVERAGE_MASK} ({OUT_DETAIL_COVERAGE_MASK.stat().st_size:,} bytes)")
        print(f"wrote {OUT_V8_DETAIL_COVERAGE_MASK} ({OUT_V8_DETAIL_COVERAGE_MASK.stat().st_size:,} bytes)")
        return
    source_names = ("coastlines", "country_borders", "region_borders", "cities") if args.geo_only else SOURCES
    downloads = {name: _fetch(*SOURCES[name]) for name in source_names}
    payload = {
        "version": 3,
        "source": {
            "name": "Natural Earth",
            "vectorVersion": "5.1.x",
            "rasterVersion": "3.2.0",
            "license": "Public domain",
            "url": "https://www.naturalearthdata.com/",
            "commit": NATURAL_EARTH_COMMIT,
            "sha256": {name: digest for name, (_, digest) in SOURCES.items()},
            "landmarks": {
                "name": "Wikidata",
                "catalogVersion": "landmarks-v1",
                "license": "CC0 1.0",
                "url": "https://www.wikidata.org/",
                "manifest": "scripts/geo_landmarks.json",
            },
        },
        "contextBounds": list(CONTEXT_BOUNDS),
        "displayBounds": list(DISPLAY_BOUNDS),
        "coastlines": _context_lines(json.loads(downloads["coastlines"])),
        "countryBorders": _context_lines(json.loads(downloads["country_borders"])),
        "regionBorders": _region_lines(json.loads(downloads["region_borders"])),
        "countryLabels": COUNTRY_LABELS,
        "cities": _cities(json.loads(downloads["cities"])),
        "mapLabels": _map_labels(json.loads(downloads["cities"])),
    }
    _validate(payload)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_GEO.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    if args.geo_only:
        print(f"wrote {OUT_GEO} ({OUT_GEO.stat().st_size:,} bytes)")
        return
    _write_texture(downloads["raster"])
    _write_coverage_mask(json.loads(downloads["ocean"]))
    _write_coverage_mask(
        json.loads(downloads["ocean"]),
        width=DETAIL_WIDTH,
        height=DETAIL_HEIGHT,
        path=OUT_DETAIL_COVERAGE_MASK,
    )
    _write_coverage_mask(
        json.loads(downloads["ocean"]),
        width=V8_DETAIL_WIDTH,
        height=V8_DETAIL_HEIGHT,
        path=OUT_V8_DETAIL_COVERAGE_MASK,
    )
    print(f"wrote {OUT_GEO} ({OUT_GEO.stat().st_size:,} bytes)")
    print(f"wrote {OUT_TEXTURE} ({OUT_TEXTURE.stat().st_size:,} bytes)")
    print(f"wrote {OUT_COVERAGE_MASK} ({OUT_COVERAGE_MASK.stat().st_size:,} bytes)")
    print(f"wrote {OUT_DETAIL_COVERAGE_MASK} ({OUT_DETAIL_COVERAGE_MASK.stat().st_size:,} bytes)")
    print(f"wrote {OUT_V8_DETAIL_COVERAGE_MASK} ({OUT_V8_DETAIL_COVERAGE_MASK.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
