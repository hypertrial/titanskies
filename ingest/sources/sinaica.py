from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from ingest.config import Settings
from ingest.context_contracts import in_monitor_bounds, iso_utc
from ingest.http import fetch
from ingest.http_pool import map_isolated
from ingest.sources.aqi import AQI_METHOD_NOWCAST, aqi_category, nowcast_aqi

SINAICA_URL = "https://sinaica.inecc.gob.mx/"
SINAICA_HOSTS = frozenset({"sinaica.inecc.gob.mx"})
SINAICA_STATIONS_URL = "https://sinaica.inecc.gob.mx/data.php"
SINAICA_RPC_URL = "https://sinaica.inecc.gob.mx/lib/libd/cnxn.php"
SINAICA_GRAPH_URL = "https://sinaica.inecc.gob.mx/pags/datGrafs.php"
SINAICA_COLLAPSE_RATIO = 0.5
SINAICA_COLLAPSE_MIN = 10
MAX_HTML_BYTES = 2 * 1024 * 1024
METADATA_PARSER_VERSION = "sinaica-meta-v2"
SINAICA_COUNT_VERSION = "unique-stations-v1"
METADATA_TTL = timedelta(hours=12)
METADATA_CACHE_PATH = "context/sinaica-stations.json"
LOGGER = logging.getLogger("titanskies.sinaica")
DAT_RE = re.compile(r"var\s+dat\s*=\s*(\[[\s\S]*?\]);")
OPTION_RE = re.compile(r'<option[^>]*value=["\'](\d+)["\'][^>]*>([^<]+)</option>', re.I)
STATION_ID_RE = re.compile(r"^\d{1,6}$")
DEFAULT_ZONE = ZoneInfo("America/Mexico_City")
STATE_ZONES = {
    "baja california": ZoneInfo("America/Tijuana"),
    "baja california sur": ZoneInfo("America/Mazatlan"),
    "sonora": ZoneInfo("America/Hermosillo"),
    "sinaloa": ZoneInfo("America/Mazatlan"),
    "nayarit": ZoneInfo("America/Mazatlan"),
    "quintana roo": ZoneInfo("America/Cancun"),
    "chihuahua": ZoneInfo("America/Chihuahua"),
}
MUNICIPALITY_ZONES = {
    "tijuana": ZoneInfo("America/Tijuana"),
    "mexicali": ZoneInfo("America/Tijuana"),
    "ensenada": ZoneInfo("America/Tijuana"),
    "ciudad juarez": ZoneInfo("America/Ciudad_Juarez"),
    "juarez": ZoneInfo("America/Ciudad_Juarez"),
    "nuevo laredo": ZoneInfo("America/Matamoros"),
    "reynosa": ZoneInfo("America/Matamoros"),
    "matamoros": ZoneInfo("America/Matamoros"),
}


class _SelectParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stations: list[tuple[str, str]] = []
        self._capture = False
        self._value = ""
        self._label: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "option":
            return
        attributes = {key: value or "" for key, value in attrs}
        value = attributes.get("value", "")
        if STATION_ID_RE.match(value):
            self._capture = True
            self._value = value
            self._label = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._label.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "option" and self._capture:
            label = " ".join("".join(self._label).split())
            self.stations.append((self._value, label))
            self._capture = False


def parse_sinaica_stations(html: str) -> list[tuple[str, str]]:
    parser = _SelectParser()
    parser.feed(html)
    stations = [(identifier, name) for identifier, name in parser.stations if identifier != "0"]
    if not stations:
        stations = [(match.group(1), " ".join(match.group(2).split())) for match in OPTION_RE.finditer(html)]
    unique: dict[str, str] = {}
    for identifier, name in stations:
        if identifier == "0":
            continue
        if identifier not in unique or (not unique[identifier] and name):
            unique[identifier] = name
    return list(unique.items())


def parse_sinaica_json(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        return json.loads(stripped)
    match = DAT_RE.search(text)
    if not match:
        raise ValueError("SINAICA response did not contain JSON data")
    return json.loads(match.group(1))


def sinaica_timezone(state: str, municipality: str, lat: float, lon: float) -> ZoneInfo | None:
    municipality_key = _normalize(municipality)
    if municipality_key in MUNICIPALITY_ZONES:
        return MUNICIPALITY_ZONES[municipality_key]
    state_key = _normalize(state)
    if state_key in STATE_ZONES:
        return STATE_ZONES[state_key]
    if lat < 14 or lat > 33 or lon < -118.5 or lon > -86:
        if 32.0 <= lat <= 33.0 and -117.5 <= lon <= -114.5:
            return ZoneInfo("America/Tijuana")
        if 31.2 <= lat <= 32.0 and -116.0 <= lon <= -114.5:
            return ZoneInfo("America/Tijuana")
        return None
    return DEFAULT_ZONE


def parse_sinaica_hours(payload: Any, zone: ZoneInfo, now: datetime) -> dict[datetime, Decimal]:
    if not isinstance(payload, list):
        raise ValueError("SINAICA hours are not a list")
    hours: dict[datetime, Decimal] = {}
    horizon = now + timedelta(minutes=15)
    for row in payload:
        if not isinstance(row, dict):
            continue
        try:
            value = Decimal(str(row.get("valor")))
            day = datetime.strptime(str(row.get("fecha")), "%Y-%m-%d")
            hour = int(row["hora"])
        except (InvalidOperation, KeyError, TypeError, ValueError):
            continue
        if not value.is_finite() or value < 0 or hour < 0 or hour > 23:
            continue
        local = datetime(day.year, day.month, day.day, hour, tzinfo=zone)
        observed = local.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        if observed > horizon:
            continue
        hours[observed] = value
    return hours


def fetch_sinaica(
    settings: Settings,
    now: datetime,
    previous_count: int | None = None,
    store: Any | None = None,
) -> list[dict[str, Any]]:
    if not settings.sinaica_enabled:
        raise PermissionError("SINAICA is disabled")
    _require_host(settings.sinaica_base_url, SINAICA_HOSTS)
    html = fetch(
        settings.sinaica_stations_url,
        hosts=SINAICA_HOSTS,
        params={"tipo": "C"},
        timeout=60,
        max_bytes=MAX_HTML_BYTES,
        retries=1,
    ).decode("utf-8", "replace")
    discovered = parse_sinaica_stations(html)
    if len(discovered) < settings.sinaica_min_stations:
        raise ValueError("SINAICA station list collapsed")
    signature = _station_list_signature(discovered)
    eligible = _load_metadata_cache(store, signature, settings, now) if store is not None else None
    if eligible is None:
        loaded = map_isolated(
            discovered,
            lambda item: _station_metadata(settings, item[0], item[1]),
            workers=max(1, settings.sinaica_concurrency),
        )
        eligible = [station for station in loaded if isinstance(station, dict)]
        if len(eligible) < settings.sinaica_min_stations:
            raise ValueError("SINAICA PM2.5 station metadata collapsed")
        if store is not None:
            _store_metadata_cache(store, signature, eligible, now)
    monitors: list[dict[str, Any]] = []
    stale = 0
    hour_results = map_isolated(
        eligible,
        lambda station: _station_hours(settings, station, now),
        workers=max(1, settings.sinaica_concurrency),
    )
    for result in hour_results:
        if isinstance(result, BaseException):
            stale += 1
            continue
        monitor, is_stale = result
        if is_stale:
            stale += 1
        if monitor:
            monitors.append(monitor)
    if not monitors:
        raise ValueError("SINAICA published no current PM2.5 monitors")
    if stale / max(len(eligible), 1) > settings.sinaica_stale_rate:
        raise ValueError("SINAICA stale rate exceeded")
    if previous_count and previous_count >= SINAICA_COLLAPSE_MIN and len(monitors) < previous_count * SINAICA_COLLAPSE_RATIO:
        raise ValueError(f"SINAICA snapshot collapsed from {previous_count} to {len(monitors)}")
    return sorted(monitors, key=lambda item: item["id"])


def _station_metadata(settings: Settings, identifier: str, fallback_name: str) -> dict[str, Any] | None:
    if not STATION_ID_RE.match(identifier):
        return None
    try:
        params_body = fetch(
            settings.sinaica_rpc_url,
            hosts=SINAICA_HOSTS,
            method="POST",
            data={"estId": identifier, "metodo": "getParamsPorEstAjax", "tipoDatos": ""},
            timeout=45,
            max_bytes=256 * 1024,
            retries=0,
        ).decode("utf-8", "replace")
        parameters = parse_sinaica_json(params_body)
    except (RuntimeError, ValueError, json.JSONDecodeError):
        return None
    names = {str(item.get("id") or item.get("nombre") or "").upper() for item in parameters} if isinstance(parameters, list) else set()
    if not any(name.replace(" ", "") in {"PM2.5", "PM25", "PARTÍCULAS MENORES A 2.5 MICRAS", "PARTICULAS MENORES A 2.5 MICRAS"} or "2.5" in name for name in names):
        return None
    try:
        meta_body = fetch(
            settings.sinaica_rpc_url,
            hosts=SINAICA_HOSTS,
            method="POST",
            data={"metodo": "infoEstacion", "estacionId": identifier, "json": "true"},
            timeout=45,
            max_bytes=256 * 1024,
            retries=0,
        ).decode("utf-8", "replace")
        meta = parse_sinaica_json(meta_body)
    except (RuntimeError, ValueError, json.JSONDecodeError):
        return None
    if isinstance(meta, list):
        meta = meta[0] if meta else {}
    if not isinstance(meta, dict):
        return None
    try:
        lat = float(meta.get("lat") or meta.get("Latitud") or "")
        lon = float(meta.get("long") or meta.get("lon") or meta.get("Longitud") or "")
    except (TypeError, ValueError):
        return None
    if not in_monitor_bounds(lon, lat):
        return None
    state = str(meta.get("edo") or meta.get("estado") or "")
    municipality = str(meta.get("munc") or meta.get("municipio") or "")
    zone = sinaica_timezone(state, municipality, lat, lon)
    if zone is None:
        return None
    agency = _clean(meta.get("redNom") or meta.get("SMCANom") or "INECC/SINAICA")
    name = _clean(meta.get("nombre") or fallback_name or identifier)
    return {"id": identifier, "name": name, "agency": agency, "lat": lat, "lon": lon, "zone": zone}


def _station_hours(settings: Settings, station: dict[str, Any], now: datetime) -> tuple[dict[str, Any] | None, bool]:
    start = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    try:
        body = fetch(
            settings.sinaica_graph_url,
            hosts=SINAICA_HOSTS,
            method="POST",
            data={
                "estacionId": station["id"],
                "param": "PM2.5",
                "fechaIni": start,
                "rango": "2",
                "tipoDatos": "",
                "datoBase": "1",
            },
            timeout=60,
            max_bytes=MAX_HTML_BYTES,
            retries=0,
        ).decode("utf-8", "replace")
        payload = parse_sinaica_json(body)
        hours = parse_sinaica_hours(payload, station["zone"], now)
    except (RuntimeError, ValueError, json.JSONDecodeError):
        return None, True
    if not hours:
        return None, True
    end = now.replace(minute=0, second=0, microsecond=0)
    computed = nowcast_aqi(hours, end)
    latest = max(hours)
    stale = now - latest > timedelta(hours=2)
    if computed is None:
        return None, stale
    aqi, nowcast = computed
    monitor = {
        "id": f"sinaica:{station['id']}",
        "name": station["name"],
        "agency": station["agency"],
        "lat": station["lat"],
        "lon": station["lon"],
        "observedAt": iso_utc(latest),
        "aqi": aqi,
        "category": aqi_category(aqi),
        "concentration": float(hours[latest]),
        "nowcastConcentration": float(nowcast),
        "unit": "µg/m³",
        "source": "sinaica",
        "sourceUrl": SINAICA_URL,
        "aqiMethod": AQI_METHOD_NOWCAST,
        "indexSystem": "us-epa-pm25-aqi",
        "indexValue": aqi,
        "indexMethod": AQI_METHOD_NOWCAST,
        "country": "MX",
        "preliminary": True,
    }
    return monitor, stale


def _station_list_signature(discovered: list[tuple[str, str]]) -> str:
    payload = "\n".join(f"{identifier}\t{name}" for identifier, name in discovered)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _serialize_station(station: dict[str, Any]) -> dict[str, Any]:
    zone = station["zone"]
    return {
        "id": station["id"],
        "name": station["name"],
        "agency": station["agency"],
        "lat": station["lat"],
        "lon": station["lon"],
        "zone": getattr(zone, "key", str(zone)),
    }


def _deserialize_station(item: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return {
            "id": str(item["id"]),
            "name": str(item["name"]),
            "agency": str(item["agency"]),
            "lat": float(item["lat"]),
            "lon": float(item["lon"]),
            "zone": ZoneInfo(str(item["zone"])),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _load_metadata_cache(store: Any, signature: str, settings: Settings, now: datetime) -> list[dict[str, Any]] | None:
    try:
        raw = store.get_text(METADATA_CACHE_PATH)
        if not raw:
            return None
        payload = json.loads(raw)
        if payload.get("parserVersion") != METADATA_PARSER_VERSION or payload.get("signature") != signature:
            return None
        cached_at = datetime.fromisoformat(str(payload["cachedAt"]).replace("Z", "+00:00"))
        if now - cached_at > METADATA_TTL:
            return None
        stations = [station for item in payload.get("stations") or [] if isinstance(item, dict) for station in [_deserialize_station(item)] if station]
        if len(stations) < settings.sinaica_min_stations:
            return None
        return stations
    except Exception:
        LOGGER.warning("SINAICA metadata cache unread; refreshing", exc_info=True)
        return None


def _store_metadata_cache(store: Any, signature: str, stations: list[dict[str, Any]], now: datetime) -> None:
    try:
        store.put_json(
            METADATA_CACHE_PATH,
            {
                "parserVersion": METADATA_PARSER_VERSION,
                "signature": signature,
                "cachedAt": iso_utc(now),
                "stations": [_serialize_station(station) for station in stations],
            },
            cache_seconds=int(METADATA_TTL.total_seconds()),
            overwrite=True,
        )
    except Exception:
        LOGGER.warning("SINAICA metadata cache write failed", exc_info=True)


def _require_host(url: str, hosts: frozenset[str]) -> None:
    host = urlparse(url).hostname or ""
    if host not in hosts:
        raise ValueError(f"blocked SINAICA host {host}")


def _normalize(value: str) -> str:
    return " ".join(value.lower().replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u").replace("ü", "u").split())


def _clean(value: Any, limit: int = 120) -> str:
    return " ".join(str(value).split())[:limit]
