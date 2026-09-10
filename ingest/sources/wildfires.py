from __future__ import annotations

import io
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from PIL import Image

from ingest.config import Settings
from ingest.context_contracts import CONTEXT_HEIGHT, CONTEXT_WIDTH, DISPLAY_BOUNDS, FIREWORK_BBOX, in_display_bounds, iso_utc
from ingest.sources.context_http import _get
from ingest.sources.context_raster import _clip_ring, _validated_png, image_png, rasterize_perimeters

WFIGS_URL = "https://data-nifc.opendata.arcgis.com/"
CWFIS_URL = "https://cwfis.cfs.nrcan.gc.ca/"
WFIGS_MAX_FEATURES = 10_000
WFIGS_CURRENT_FIELDS = "OBJECTID,IrwinID,IncidentName,IncidentTypeCategory,IncidentTypeKind,IncidentSize,FinalAcres,DiscoveryAcres,FireDiscoveryDateTime,ModifiedOnDateTime_dt,IncidentManagementOrganization,ContainmentDateTime"
WFIGS_LEGACY_FIELDS = "OBJECTID,IrwinID,IncidentName,IncidentTypeCategory,IncidentTypeKind,DailyAcres,CalculatedAcres,DiscoveryAcres,FireDiscoveryDateTime,ModifiedOnDateTime_dt,IncidentManagementOrganization,ContainmentDateTime"

def _arcgis_features(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        error = payload["error"]
        message = str(error.get("message") or "feature query failed")
        raw_details = error.get("details")
        details = "; ".join(str(item) for item in raw_details if item) if isinstance(raw_details, list) else str(raw_details or "")
        code = f" {error['code']}" if error.get("code") is not None else ""
        raise ValueError(f"ArcGIS{code}: {message}{f': {details}' if details else ''}")
    if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
        raise ValueError("invalid ArcGIS feature response")
    return payload["features"]


def _arcgis_feature_pages(url: str, params: dict[str, Any], *, out_fields: str, page_size: int) -> dict[str, list[dict[str, Any]]]:
    features: list[dict[str, Any]] = []
    object_ids: set[object] = set()
    offset = 0
    while True:
        payload = _get(url, params={
            **params,
            "outFields": out_fields,
            "orderByFields": "OBJECTID ASC",
            "resultOffset": str(offset),
            "resultRecordCount": str(page_size),
        }).json()
        page = _arcgis_features(payload)
        if not page:
            if payload.get("exceededTransferLimit") is True:
                raise ValueError("invalid ArcGIS pagination response")
            break
        for feature in page:
            object_id = (feature.get("attributes") or {}).get("OBJECTID")
            if object_id is None or object_id in object_ids:
                raise ValueError("invalid ArcGIS pagination response")
            object_ids.add(object_id)
        features.extend(page)
        if len(features) > WFIGS_MAX_FEATURES:
            raise ValueError("ArcGIS feature response exceeds limit")
        offset += len(page)
        if payload.get("exceededTransferLimit") is not True and len(page) < page_size:
            break
    return {"features": features}


def parse_wfigs(incidents_payload: Any, perimeters_payload: Any) -> tuple[list[dict[str, Any]], list[list[list[float]]]]:
    incidents: dict[str, dict[str, Any]] = {}
    for feature in _arcgis_features(incidents_payload):
        attrs = feature.get("attributes") or feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        fire_type = str(attrs.get("IncidentTypeCategory") or attrs.get("IncidentTypeKind") or "WF").upper()
        if "RX" in fire_type or "PRESCRIB" in fire_type:
            continue
        try:
            raw_lon = geometry.get("x", attrs.get("POO_Longitude"))
            raw_lat = geometry.get("y", attrs.get("POO_Latitude"))
            if raw_lon is None or raw_lat is None:
                continue
            lon = float(raw_lon)
            lat = float(raw_lat)
        except (TypeError, ValueError):
            continue
        if not in_display_bounds(lon, lat):
            continue
        identifier = str(attrs.get("IrwinID") or attrs.get("IncidentID") or attrs.get("OBJECTID") or "").strip("{}")
        if not identifier:
            continue
        acres = next((value for name in ("IncidentSize", "FinalAcres", "DailyAcres", "CalculatedAcres", "DiscoveryAcres") if (value := _number(attrs.get(name))) is not None), None)
        updated = _parse_source_time(attrs.get("ModifiedOnDateTime_dt"), attrs.get("FireDiscoveryDateTime"))
        if updated is None:
            continue
        incidents[identifier] = {
            "id": f"US:{identifier}",
            "name": str(attrs.get("IncidentName") or "Unnamed wildfire").title(),
            "country": "US",
            "lat": lat,
            "lon": lon,
            "status": str(attrs.get("IncidentManagementOrganization") or attrs.get("ContainmentDateTime") or "Reported wildfire"),
            "areaHectares": round(acres * 0.404685642, 3) if acres is not None else None,
            "sourceArea": acres,
            "sourceAreaUnit": "acres" if acres is not None else None,
            "updatedAt": updated,
            "sourceUrl": WFIGS_URL,
        }
    rings: list[list[list[float]]] = []
    for feature in _arcgis_features(perimeters_payload):
        attrs = feature.get("attributes") or feature.get("properties") or {}
        fire_type = str(attrs.get("attr_IncidentTypeCategory") or attrs.get("poly_IncidentTypeCategory") or attrs.get("IncidentTypeCategory") or "WF").upper()
        if "RX" in fire_type or "PRESCRIB" in fire_type:
            continue
        geometry = feature.get("geometry") or {}
        for ring in geometry.get("rings", []):
            clean = [point for value in ring if (point := _coordinate(value)) is not None]
            clipped = _clip_ring(clean)
            if len(clipped) >= 3:
                rings.append(clipped)
    return sorted(incidents.values(), key=lambda item: item["id"]), rings


def fetch_wfigs_parts(
    settings: Settings,
) -> tuple[list[dict[str, Any]], list[list[list[float]]] | None, str | None]:
    params = {
        "where": "1=1",
        "returnGeometry": "true",
        "geometry": f"{DISPLAY_BOUNDS['west']:.0f},{DISPLAY_BOUNDS['south']:.0f},{DISPLAY_BOUNDS['east']:.0f},{DISPLAY_BOUNDS['north']:.0f}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "outSR": "4326",
        "f": "json",
    }
    try:
        incidents_payload = _arcgis_feature_pages(
            settings.wfigs_incidents_url, params, out_fields=WFIGS_CURRENT_FIELDS, page_size=1_000,
        )
    except ValueError as exc:
        if "outFields" not in str(exc):
            raise
        incidents_payload = _arcgis_feature_pages(
            settings.wfigs_incidents_url, params, out_fields=WFIGS_LEGACY_FIELDS, page_size=1_000,
        )
    try:
        perimeters_payload = _arcgis_feature_pages(
            settings.wfigs_perimeters_url,
            params,
            out_fields="OBJECTID,attr_IncidentTypeCategory,attr_IncidentTypeKind",
            page_size=20,
        )
    except (RuntimeError, ValueError) as exc:
        incidents, _ = parse_wfigs(incidents_payload, {"features": []})
        return incidents, None, str(exc)
    incidents, rings = parse_wfigs(incidents_payload, perimeters_payload)
    return incidents, rings, None


def fetch_wfigs(settings: Settings) -> tuple[list[dict[str, Any]], list[list[list[float]]]]:
    incidents, rings, _ = fetch_wfigs_parts(settings)
    return incidents, rings or []


def parse_cwfis(payload: Any) -> tuple[list[dict[str, Any]], list[list[list[float]]]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
        raise ValueError("invalid CWFIS response")
    features = payload["features"]
    incidents = []
    rings: list[list[list[float]]] = []
    seen: set[str] = set()
    for feature in features:
        props = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        if int(props.get("fire_was_prescribed") or 0) == 1:
            continue
        identifier = str(props.get("national_fire_id") or props.get("agency_fire_id") or props.get("firename") or props.get("fireid") or props.get("id") or "")
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        coords = geometry.get("coordinates")
        kind = str(geometry.get("type") or "")
        point = _geometry_centroid(kind, coords)
        if point is None or not in_display_bounds(*point):
            continue
        lon, lat = point
        hectares = next(
            (value for key in ("fire_size", "hectares", "area", "sizeha") if (value := _number(props.get(key))) is not None),
            None,
        )
        updated = _parse_source_time(
            props.get("status_date"), props.get("record_start"), props.get("lastrepdate"),
            props.get("updated"), props.get("date"),
        )
        if updated is None:
            continue
        incidents.append({
            "id": f"CA:{identifier}",
            "name": str(props.get("agency_fire_id") or props.get("firename") or props.get("fireid") or "Reported wildfire"),
            "country": "CA",
            "lat": lat,
            "lon": lon,
            "status": str(props.get("stage_of_control_status") or props.get("stage_of_control") or props.get("stageofcontrol") or props.get("status") or "Reported wildfire"),
            "areaHectares": hectares,
            "sourceArea": hectares,
            "sourceAreaUnit": "hectares" if hectares is not None else None,
            "updatedAt": updated,
            "sourceUrl": CWFIS_URL,
        })
        rings.extend(_geojson_rings(kind, coords))
    return sorted(incidents, key=lambda item: item["id"]), rings


def fetch_cwfis(
    settings: Settings,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[list[list[float]]], datetime | None]:
    now = now or datetime.now(timezone.utc)
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": "public:cwfif_national_activefires",
        "outputFormat": "application/json",
        "srsName": "EPSG:4326",
        "CQL_FILTER": f"record_end AFTER {iso_utc(now)} AND fire_was_prescribed = 0",
    }
    payload = _get(settings.cwfis_url, params=params).json()
    incidents, rings = parse_cwfis(payload)
    observed: datetime | None
    try:
        observed = datetime.fromisoformat(str(payload["timeStamp"]).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        observed = observed.astimezone(timezone.utc)
        if observed.year <= 2000 or observed > now + timedelta(minutes=5):
            observed = None
    except (KeyError, OverflowError, TypeError, ValueError):
        observed = None
    return incidents, rings, observed


def fetch_cwfis_perimeter_texture(settings: Settings) -> bytes:
    response = _get(settings.cwfis_perimeters_url, params={
        "SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetMap",
        "LAYERS": "public:m3polygons", "STYLES": "", "FORMAT": "image/png",
        "TRANSPARENT": "TRUE", "SRS": "EPSG:4326", "BBOX": FIREWORK_BBOX,
        "WIDTH": str(CONTEXT_WIDTH), "HEIGHT": str(CONTEXT_HEIGHT),
    }, timeout=90)
    data = _validated_png(response.content, "CWFIS perimeter", (CONTEXT_WIDTH, CONTEXT_HEIGHT))
    source = Image.open(io.BytesIO(data)).convert("RGBA")
    alpha = source.getchannel("A")
    outline = Image.new("RGBA", source.size, (255, 188, 87, 0))
    outline.putalpha(alpha.point(lambda value: min(210, value)))
    return image_png(outline)

def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError):
        return None


def _parse_source_time(*values: Any) -> str | None:
    for value in values:
        try:
            parsed = datetime.fromtimestamp(value / 1000, tz=timezone.utc) if isinstance(value, (int, float)) and math.isfinite(value) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return iso_utc(parsed.astimezone(timezone.utc))
        except (OSError, OverflowError, TypeError, ValueError):
            continue
    return None


def _geometry_centroid(kind: str, coords: Any) -> tuple[float, float] | None:
    if kind == "Point" and isinstance(coords, list) and len(coords) >= 2:
        point = _coordinate(coords)
        return (point[0], point[1]) if point else None
    rings = _geojson_rings(kind, coords)
    points = [point for ring in rings for point in ring]
    if not points:
        return None
    return sum(point[0] for point in points) / len(points), sum(point[1] for point in points) / len(points)


def _geojson_rings(kind: str, coords: Any) -> list[list[list[float]]]:
    raw = coords if kind == "Polygon" else [polygon for polygon in (coords or [])] if kind == "MultiPolygon" else []
    rings: list[list[list[float]]] = []
    if kind == "Polygon":
        raw = [coords]
    for polygon in raw or []:
        if polygon and isinstance(polygon[0], list) and polygon[0] and isinstance(polygon[0][0], (int, float)):
            polygon = [polygon]
        for ring in polygon or []:
            clean = [point for value in ring if (point := _coordinate(value)) is not None]
            clipped = _clip_ring(clean)
            if len(clipped) >= 3:
                rings.append(clipped)
    return rings


def _coordinate(value: Any) -> list[float] | None:
    try:
        lon, lat = float(value[0]), float(value[1])
    except (IndexError, TypeError, ValueError):
        return None
    return [lon, lat] if math.isfinite(lon) and math.isfinite(lat) else None
