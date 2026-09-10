from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_CONTRACT = json.loads(
    (Path(__file__).resolve().parent.parent / "shared" / "context-contract-v8.json").read_text(encoding="utf-8")
)
_CAPABILITY_REGISTRY = json.loads(
    (Path(__file__).resolve().parent.parent / "shared" / "context-capabilities.json").read_text(encoding="utf-8")
)
_CONTRACTS = {int(_CONTRACT["version"]): _CONTRACT}
_CAPABILITIES = {
    int(item["version"]): item
    for item in _CAPABILITY_REGISTRY["versions"]
}

if set(_CAPABILITIES) != set(_CONTRACTS):
    raise RuntimeError("context capability versions do not match context contracts")

CONTEXT_VERSION = max(_CONTRACTS)
CONTEXT_VERSIONS = set(_CONTRACTS)
SOURCE_METADATA_MODELS = {"hrrr", "firework"}
CONTEXT_BOUNDS = {key: float(value) for key, value in _CONTRACT["bounds"].items()}
DISPLAY_BOUNDS = {key: float(value) for key, value in _CONTRACT["displayBounds"].items()}
MONITOR_REGIONS = tuple({key: float(value) for key, value in region.items()} for region in _CONTRACT["monitorRegions"])
CONTEXT_WIDTH = int(_CONTRACT["raster"]["width"])
CONTEXT_HEIGHT = int(_CONTRACT["raster"]["height"])
STALE_AFTER_HOURS = {key: int(value) for key, value in _CONTRACT["staleAfterHours"].items()}
CONTEXT_RASTER_BUDGET_BYTES = int(_CONTRACT["assetBudgets"]["rasterBytes"])
CONTEXT_JSON_BUDGET_BYTES = int(_CONTRACT["assetBudgets"]["jsonBytes"])
MONITOR_JSON_BUDGET_BYTES = int(_CONTRACT["assetBudgets"]["monitorJsonBytes"])
CONTEXT_RETAINED_BUDGET_BYTES = int(_CONTRACT["assetBudgets"]["retainedBytes"])
CONTEXT_FIELD_BUDGET_BYTES = int(_CONTRACT["assetBudgets"].get("fieldBytes", 4 * 1024 * 1024))
DETAIL_GRID = dict(_CONTRACT["detailGrid"])
DETAIL_WIDTH = int(DETAIL_GRID["width"])
DETAIL_HEIGHT = int(DETAIL_GRID["height"])
DETAIL_TILE_WIDTH = int(DETAIL_GRID["tileWidth"])
DETAIL_TILE_HEIGHT = int(DETAIL_GRID["tileHeight"])
DETAIL_RASTER_PROCESSING_VERSION = "regional-grid-2047x1269-v1"
V8_DETAIL_RASTER_PROCESSING_VERSION = "regional-grid-4093x2537-bilinear-v2"
REQUIRED_SOURCE_NAMES = tuple(_CAPABILITIES[CONTEXT_VERSION]["requiredSources"])
SOURCE_NAMES = tuple(_CAPABILITIES[CONTEXT_VERSION]["allowedSources"])
FORECAST_MODEL_IDS = ("best", "hrrr", "firework")
INTEGRATED_STATUSES = ("complete", "retained", "unavailable")
AIR_SOURCE_NAMES = ("airnow", "bcair", "sinaica", "aqhi")
AQHI_COUNT_VERSION = "eccc-aqhi-latest-v1"
SOURCE_STATUSES = {"ok", "stale", "error", "unavailable"}
AQI_CATEGORIES = {
    "good",
    "moderate",
    "unhealthy for sensitive groups",
    "unhealthy",
    "very unhealthy",
    "hazardous",
    "beyond the aqi",
}
AQI_METHODS = {"provider", "epa-nowcast-2024"}
AIR_SOURCES = {"airnow", "bcair", "sinaica", "aqhi"}
FIREWORK_BBOX = f"{CONTEXT_BOUNDS['west']:.0f},{CONTEXT_BOUNDS['south']:.0f},{CONTEXT_BOUNDS['east']:.0f},{CONTEXT_BOUNDS['north']:.0f}"


def iso_utc(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def in_bounds(lon: float, lat: float, bounds: dict[str, float]) -> bool:
    return (
        math.isfinite(lon)
        and math.isfinite(lat)
        and bounds["west"] <= lon <= bounds["east"]
        and bounds["south"] <= lat <= bounds["north"]
    )


def in_display_bounds(lon: float, lat: float) -> bool:
    return in_bounds(unwrap_lon(lon), lat, DISPLAY_BOUNDS)


def in_monitor_bounds(lon: float, lat: float) -> bool:
    return any(in_bounds(lon, lat, region) for region in MONITOR_REGIONS)


def unwrap_lon(lon: float) -> float:
    return lon - 360 if lon > 90 else lon


def validate_context_manifest(payload: dict[str, Any]) -> None:
    version = payload.get("version")
    if version not in CONTEXT_VERSIONS:
        raise ValueError("invalid context schema version")
    expected_contract = _CONTRACTS[version]
    capabilities = _CAPABILITIES[version]
    expected_bounds = {key: float(value) for key, value in expected_contract["bounds"].items()}
    expected_display = {key: float(value) for key, value in expected_contract["displayBounds"].items()}
    expected_regions = tuple(
        {key: float(value) for key, value in region.items()} for region in expected_contract["monitorRegions"]
    )
    if payload.get("mode") not in {"demo", "live"}:
        raise ValueError("invalid context mode")
    _require_iso(payload.get("generatedAt"), "generatedAt")
    if payload.get("bounds") != expected_bounds:
        raise ValueError("invalid context bounds")
    display = payload.get("displayBounds")
    if display is not None and display != expected_display:
        raise ValueError("invalid display bounds")
    regions = payload.get("monitorRegions")
    if regions is not None and tuple({key: float(value) for key, value in region.items()} for region in regions) != expected_regions:
        raise ValueError("invalid monitor regions")
    sources = payload.get("sources")
    required_sources = capabilities["requiredSources"]
    if not isinstance(sources, dict) or any(name not in sources for name in required_sources):
        raise ValueError("context source states are incomplete")
    allowed_sources = capabilities["allowedSources"]
    unknown = set(sources) - set(allowed_sources)
    if unknown:
        raise ValueError("unknown context source states")
    for name, state in sources.items():
        if not isinstance(state, dict) or state.get("status") not in SOURCE_STATUSES:
            raise ValueError(f"invalid {name} source state")
        _require_iso(state.get("checkedAt"), f"{name}.checkedAt")
        if not isinstance(state.get("provenance"), str) or not state["provenance"].startswith("http"):
            raise ValueError(f"missing {name} provenance")
        if state.get("observedAt") is not None:
            _require_iso(state["observedAt"], f"{name}.observedAt")
        if state.get("perimeterObservedAt") is not None:
            _require_iso(state["perimeterObservedAt"], f"{name}.perimeterObservedAt")

    for section in ("air", "fires", "forecast"):
        if not isinstance(payload.get(section), dict):
            raise ValueError(f"invalid {section} section")

    air = payload["air"]
    if air.get("monitorsUrl"):
        _require_url(air["monitorsUrl"], "AirNow monitors")
        _require_iso(air.get("observedAt"), "AirNow observation")
    for key in ("bcMonitorsUrl", "sinaicaMonitorsUrl"):
        if air.get(key):
            _require_url(air[key], "air monitors")
    sets = air.get("monitorSets")
    if sets is not None:
        if not isinstance(sets, dict):
            raise ValueError("invalid monitor sets")
        allowed_air_sources = AIR_SOURCES
        for name, item in sets.items():
            if name not in allowed_air_sources or not isinstance(item, dict):
                raise ValueError("invalid monitor set")
            _require_url(item.get("url"), f"{name} monitors")
            if item.get("observedAt") is not None:
                _require_iso(item["observedAt"], f"{name} observation")
            if item.get("countVersion") is not None and not isinstance(item["countVersion"], str):
                raise ValueError("invalid monitor count version")
            if item.get("count") is not None and (not isinstance(item["count"], int) or isinstance(item["count"], bool) or item["count"] < 0):
                raise ValueError("invalid monitor count")
        if capabilities["aqhi"]:
            aqhi_set = sets.get("aqhi")
            if isinstance(aqhi_set, dict) and (
                aqhi_set.get("countVersion") != AQHI_COUNT_VERSION
                or not isinstance(aqhi_set.get("count"), int)
                or aqhi_set["count"] <= 0
                or aqhi_set.get("observedAt") is None
            ):
                raise ValueError("invalid AQHI monitor set metadata")
    if capabilities["aqhi"] and sources["aqhi"]["status"] == "ok" and not isinstance((sets or {}).get("aqhi"), dict):
        raise ValueError("healthy AQHI source is missing its monitor set")
    fires = payload["fires"]
    for key in ("wfigsIncidentsUrl", "cwfisIncidentsUrl"):
        if fires.get(key):
            _require_url(fires[key], "incident data")
    for key in ("wfigsPerimeterTextureUrl", "cwfisPerimeterTextureUrl"):
        if fires.get(key):
            _require_url(fires[key], "perimeter texture")
    _validate_forecast_run(
        payload["forecast"],
        "forecast",
        version=version,
        require_mask=capabilities["sourceMaskRequired"],
        require_outlook_metadata=capabilities["outlookMetadataRequired"],
        require_detail=capabilities["detailTilesRequired"],
    )
    if capabilities["outlookMetadataRequired"]:
        public_outlook = payload["forecast"]
        horizon = int(capabilities["outlookHorizonHours"])
        if public_outlook.get("horizonHours") != horizon:
            raise ValueError(f"v{version} outlook horizon must be {horizon} hours")
        if public_outlook.get("integratedStatus") != "unavailable" and len(public_outlook.get("frames", [])) != horizon + 1:
            raise ValueError(f"v{version} complete outlook must contain {horizon + 1} hourly frames")
        if capabilities["canonicalHourlyOutlook"]:
            _require_canonical_outlook(payload["generatedAt"], public_outlook, horizon, "forecast")
    if capabilities["multiModelForecast"]:
        forecasts = payload.get("forecasts")
        if not isinstance(forecasts, dict):
            raise ValueError("invalid forecasts section")
        unknown_models = set(forecasts) - set(FORECAST_MODEL_IDS)
        if unknown_models:
            raise ValueError("unknown forecast models")
        for name in FORECAST_MODEL_IDS:
            run = forecasts.get(name)
            if not isinstance(run, dict):
                raise ValueError(f"missing {name} forecast")
            _validate_forecast_run(
                run,
                name,
                version=version,
                require_mask=name == "best",
                metadata_only=not capabilities["sourceTexturesPublic"] and name in SOURCE_METADATA_MODELS,
                require_outlook_metadata=capabilities["outlookMetadataRequired"] and name == "best",
                require_detail=capabilities["detailTilesRequired"] and name == "best",
            )
        if capabilities["outlookMetadataRequired"]:
            best = forecasts["best"]
            horizon = int(capabilities["outlookHorizonHours"])
            if best.get("horizonHours") != horizon:
                raise ValueError(f"v{version} best outlook horizon must be {horizon} hours")
            if best.get("integratedStatus") != "unavailable" and len(best.get("frames", [])) != horizon + 1:
                raise ValueError(f"v{version} best outlook must contain {horizon + 1} hourly frames")
            if capabilities["canonicalHourlyOutlook"]:
                _require_canonical_outlook(payload["generatedAt"], best, horizon, "best")


def _require_canonical_outlook(generated_at: str, run: dict[str, Any], horizon: int, label: str) -> None:
    frames = run.get("frames", [])
    if run.get("integratedStatus") == "unavailable" or not frames:
        return
    start = datetime.fromisoformat(generated_at.replace("Z", "+00:00")).replace(minute=0, second=0, microsecond=0)
    expected = [iso_utc(start + timedelta(hours=hour)) for hour in range(horizon + 1)]
    actual = [frame.get("validTime") for frame in frames]
    if actual != expected:
        raise ValueError(f"v7 {label} frames must match every canonical hour")


def published_context_version(settings: Any) -> int:
    del settings
    return CONTEXT_VERSION


def context_capabilities(version: int) -> dict[str, Any]:
    return dict(_CAPABILITIES[version])


def display_bounds_for_version(version: int) -> dict[str, float]:
    return {key: float(value) for key, value in _CONTRACTS[version]["displayBounds"].items()}


def monitor_regions_for_version(version: int) -> tuple[dict[str, float], ...]:
    return tuple({key: float(value) for key, value in region.items()} for region in _CONTRACTS[version]["monitorRegions"])


def detail_grid_for_version(version: int) -> dict[str, Any] | None:
    grid = _CONTRACTS[version].get("detailGrid")
    return dict(grid) if isinstance(grid, dict) else None


def detail_raster_processing_version_for_version(version: int) -> str | None:
    value = _CAPABILITIES[version]["detailRasterProcessingVersion"]
    return str(value) if value is not None else None


def uses_native_forecast_fields(version: int) -> bool:
    return detail_raster_processing_version_for_version(version) == V8_DETAIL_RASTER_PROCESSING_VERSION


def palette_version_for_version(version: int) -> str:
    return str(_CAPABILITIES[version]["paletteVersion"])


def coverage_mask_version_for_version(version: int) -> str:
    return str(_CAPABILITIES[version]["coverageMaskVersion"])


def detail_tile_count_for_version(version: int) -> int:
    grid = detail_grid_for_version(version)
    return int(grid["columns"]) * int(grid["rows"]) if grid else 0


def _validate_forecast_run(
    forecast: dict[str, Any],
    label: str,
    *,
    version: int,
    require_mask: bool = False,
    metadata_only: bool = False,
    require_outlook_metadata: bool = False,
    require_detail: bool = False,
) -> None:
    frames = forecast.get("frames", [])
    if not isinstance(frames, list):
        raise ValueError(f"invalid {label} frames")
    status = forecast.get("integratedStatus")
    if status is not None and status not in INTEGRATED_STATUSES:
        raise ValueError(f"invalid {label} integrated status")
    if not frames:
        return
    if require_outlook_metadata:
        if forecast.get("selectionPolicy") != "feathered-max-v1":
            raise ValueError(f"invalid {label} selection policy")
        if forecast.get("featherDistanceKm") != 200:
            raise ValueError(f"invalid {label} feather distance")
        if forecast.get("paletteVersion") != palette_version_for_version(version):
            raise ValueError(f"invalid {label} palette version")
        if forecast.get("sourceMaskVersion") != "contribution-v2":
            raise ValueError(f"invalid {label} source mask version")
        if forecast.get("coveragePolicy") != "land-plus-coastal-water-v1":
            raise ValueError(f"invalid {label} coverage policy")
        if forecast.get("coastalBufferKm") != 200:
            raise ValueError(f"invalid {label} coastal buffer")
        expected_coverage = coverage_mask_version_for_version(version)
        if forecast.get("coverageMaskVersion") != expected_coverage:
            raise ValueError(f"invalid {label} coverage mask version")
        if require_detail:
            if forecast.get("detailGrid") != detail_grid_for_version(version):
                raise ValueError(f"invalid {label} detail grid")
            if forecast.get("rasterProcessingVersion") != detail_raster_processing_version_for_version(version):
                raise ValueError(f"invalid {label} raster processing version")
    _require_iso(forecast.get("modelRun"), f"{label} model run")
    if not metadata_only:
        _require_url(forecast.get("legendUrl"), f"{label} legend")
    forbidden = ("numericUrl", "fieldUrl", "values", "valid", "png", "texturePng", "maskPng")
    valid_times = []
    for frame in frames:
        if not isinstance(frame, dict):
            raise ValueError(f"invalid {label} frame")
        valid_times.append(_require_iso(frame.get("validTime"), f"{label} valid time"))
        if frame.get("modelRun") is not None:
            _require_iso(frame.get("modelRun"), f"{label} frame model run")
        if any(frame.get(key) is not None for key in forbidden):
            raise ValueError(f"{label} frame includes private numeric fields")
        if metadata_only:
            if frame.get("textureUrl") or frame.get("sourceMaskUrl"):
                raise ValueError(f"{label} metadata frames must omit textures")
            continue
        _require_url(frame.get("textureUrl"), f"{label} texture")
        if require_mask or frame.get("sourceMaskUrl"):
            _require_url(frame.get("sourceMaskUrl"), f"{label} source mask")
        detail_tiles = frame.get("detailTiles")
        if forecast.get("detailGrid") is not None:
            grid = detail_grid_for_version(version)
            expected_count = detail_tile_count_for_version(version)
            if not isinstance(detail_tiles, list) or len(detail_tiles) != expected_count:
                raise ValueError(f"{label} frame must contain {expected_count} detail tiles")
            positions = []
            for tile in detail_tiles:
                if not isinstance(tile, dict):
                    raise ValueError(f"invalid {label} detail tile")
                position = (tile.get("column"), tile.get("row"))
                positions.append(position)
                _require_url(tile.get("textureUrl"), f"{label} detail texture")
                _require_url(tile.get("sourceMaskUrl"), f"{label} detail source mask")
            expected_positions = [
                (column, row)
                for row in range(int((grid or {})["rows"]))
                for column in range(int((grid or {})["columns"]))
            ]
            if positions != expected_positions:
                raise ValueError(f"{label} detail tiles must be row-major")
        elif detail_tiles is not None:
            raise ValueError(f"{label} detail tiles require detail grid metadata")
    if valid_times != sorted(set(valid_times)):
        raise ValueError(f"{label} frames must have unique ordered times")


def _require_iso(value: Any, label: str) -> float:
    if not isinstance(value, str):
        raise ValueError(f"missing {label}")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError as exc:
        raise ValueError(f"invalid {label}") from exc


def _require_url(value: Any, label: str) -> None:
    if not isinstance(value, str) or not (value.startswith("/") or value.startswith("https://")):
        raise ValueError(f"invalid {label} URL")
