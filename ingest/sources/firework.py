from __future__ import annotations

import io
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlencode
from xml.etree import ElementTree

import numpy as np
from PIL import Image

from ingest.config import Settings
from ingest.context_contracts import CONTEXT_BOUNDS, CONTEXT_HEIGHT, CONTEXT_WIDTH, FIREWORK_BBOX, iso_utc
from ingest.forecast_native_cache import GeographicGrid, NativeField
from ingest.forecast_palette import KG_PER_UG
from ingest.forecast_raster import BASE_RASTER_GRID, RasterGrid
from ingest.http import HttpFetchError
from ingest.perf import current_budget, timed_provider_phase
from ingest.sources.context_http import _firework_bytes, _get
from ingest.sources.context_raster import _validated_png

FIREWORK_URL = "https://www.weather.gc.ca/firework/"
FIREWORK_LAYER = "RAQDPS.Sfc_PM2.5-WildfireSmokePlume"
FIREWORK_STYLE = "FW-SFC-PM-DIFF_KGM3"
FIREWORK_PROCESSING_VERSION = "firework-wcs-ug-v6"
FIREWORK_NATIVE_PROCESSING_VERSION = "firework-wcs-native-ug-v7"
_TIFF_MAGIC = {b"II*\x00", b"MM\x00*"}

def parse_firework_capabilities(text: str) -> tuple[str, list[str]]:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise ValueError("malformed FireWork capabilities") from exc
    target = None
    for layer in root.findall(".//{*}Layer"):
        if layer.findtext("{*}Name") == FIREWORK_LAYER:
            target = layer
            break
    if target is None:
        raise ValueError("FireWork layer unavailable")
    dimensions: dict[str, str] = {}
    for element in target.findall("{*}Dimension") + target.findall("{*}Extent"):
        dimensions[str(element.attrib.get("name", "")).lower()] = (element.text or "").strip()
    time_values = _dimension_values(dimensions.get("time", ""))
    run_values = _dimension_values(dimensions.get("reference_time", "") or dimensions.get("reference-time", ""))
    if not time_values:
        raise ValueError("FireWork valid times unavailable")
    run = max(run_values or [time_values[0]], key=_iso_sort_key)
    ordered = sorted(set(time_values), key=_iso_sort_key)
    return _normalize_iso(run), [_normalize_iso(value) for value in ordered]


def firework_getmap_url(
    base_url: str,
    model_run: str,
    valid_time: str,
    sld_body: str | None = None,
    style: str | None = None,
) -> str:
    params = {
        "SERVICE": "WMS",
        "VERSION": "1.1.1",
        "REQUEST": "GetMap",
        "LAYERS": FIREWORK_LAYER,
        "STYLES": style or "",
        "FORMAT": "image/png",
        "TRANSPARENT": "TRUE",
        "SRS": "EPSG:4326",
        "BBOX": FIREWORK_BBOX,
        "WIDTH": str(CONTEXT_WIDTH),
        "HEIGHT": str(CONTEXT_HEIGHT),
        "TIME": valid_time,
        "DIM_REFERENCE_TIME": model_run,
    }
    if sld_body:
        params["SLD_BODY"] = sld_body
    return f"{base_url}?{urlencode(params)}"


def firework_wcs_url(
    base_url: str,
    model_run: str,
    valid_time: str,
    raster_grid: RasterGrid = BASE_RASTER_GRID,
) -> str:
    lon_res = (CONTEXT_BOUNDS["east"] - CONTEXT_BOUNDS["west"]) / (raster_grid.width - 1)
    lat_res = (CONTEXT_BOUNDS["north"] - CONTEXT_BOUNDS["south"]) / (raster_grid.height - 1)
    params = [
        ("SERVICE", "WCS"),
        ("VERSION", "2.0.1"),
        ("REQUEST", "GetCoverage"),
        ("COVERAGEID", FIREWORK_LAYER),
        ("FORMAT", "image/tiff"),
        ("SUBSETTINGCRS", "EPSG:4326"),
        ("SUBSET", f"x({CONTEXT_BOUNDS['west']},{CONTEXT_BOUNDS['east']})"),
        ("SUBSET", f"y({CONTEXT_BOUNDS['south']},{CONTEXT_BOUNDS['north']})"),
        ("RESOLUTION", f"x({lon_res})"),
        ("RESOLUTION", f"y({lat_res})"),
        ("TIME", valid_time),
        ("DIM_REFERENCE_TIME", model_run),
    ]
    return f"{base_url}?{urlencode(params)}"


def firework_native_wcs_url(base_url: str, model_run: str, valid_time: str) -> str:
    params = [
        ("SERVICE", "WCS"),
        ("VERSION", "2.0.1"),
        ("REQUEST", "GetCoverage"),
        ("COVERAGEID", FIREWORK_LAYER),
        ("FORMAT", "image/tiff"),
        ("SUBSETTINGCRS", "EPSG:4326"),
        ("SUBSET", f"x({CONTEXT_BOUNDS['west']},{CONTEXT_BOUNDS['east']})"),
        ("SUBSET", f"y({CONTEXT_BOUNDS['south']},{CONTEXT_BOUNDS['north']})"),
        ("TIME", valid_time),
        ("DIM_REFERENCE_TIME", model_run),
    ]
    return f"{base_url}?{urlencode(params)}"


def _tiff_nodata(page: Any) -> float | None:
    tag = page.tags.get("GDAL_NODATA") if getattr(page, "tags", None) else None
    if tag is None:
        return None
    try:
        return float(str(tag.value).split()[0])
    except (TypeError, ValueError):
        return None


def decode_firework_coverage(data: bytes, raster_grid: RasterGrid = BASE_RASTER_GRID) -> np.ndarray:
    if not data or data[:4] not in _TIFF_MAGIC:
        raise ValueError("FireWork coverage was not a GeoTIFF")
    try:
        import tifffile
    except ImportError as exc:
        raise ValueError("tifffile is required to decode FireWork WCS") from exc
    try:
        with tifffile.TiffFile(io.BytesIO(data)) as tiff:
            page = tiff.pages[0]
            width = int(page.imagewidth)
            height = int(page.imagelength)
            if width < 1 or height < 1 or width > raster_grid.width * 4 or height > raster_grid.height * 4:
                raise ValueError("FireWork coverage exceeds the context grid")
            values = page.asarray()
            nodata = _tiff_nodata(page)
            scale = page.tags.get("ModelPixelScaleTag")
            tiepoint = page.tags.get("ModelTiepointTag")
            pixel_offset = 0.0 if (page.geotiff_tags or {}).get("GTRasterTypeGeoKey", 1) == 2 else 0.5
    except MemoryError as exc:
        raise ValueError("invalid FireWork coverage") from exc
    except (OSError, ValueError, KeyError) as exc:
        raise ValueError("invalid FireWork coverage") from exc
    array = np.asarray(values, dtype=np.float32)
    while array.ndim > 2:
        array = array[0]
    if array.ndim != 2:
        raise ValueError("FireWork coverage is not a 2D field")
    if nodata is not None:
        array = np.where(array == nodata, np.nan, array)
    if scale is not None and tiepoint is not None:
        scale_values = tuple(float(value) for value in scale.value)
        tiepoint_values = tuple(float(value) for value in tiepoint.value)
        if len(scale_values) < 2 or len(tiepoint_values) < 6 or scale_values[0] <= 0 or scale_values[1] <= 0:
            raise ValueError("invalid FireWork coverage georeference")
        pixel_x, pixel_y = tiepoint_values[0], tiepoint_values[1]
        geo_x, geo_y = tiepoint_values[3], tiepoint_values[4]
        target_lons = np.linspace(CONTEXT_BOUNDS["west"], CONTEXT_BOUNDS["east"], raster_grid.width)
        target_lats = np.linspace(CONTEXT_BOUNDS["north"], CONTEXT_BOUNDS["south"], raster_grid.height)
        source_cols = np.rint(pixel_x + (target_lons - geo_x) / scale_values[0] - pixel_offset).astype(np.int32)
        source_rows = np.rint(pixel_y + (geo_y - target_lats) / scale_values[1] - pixel_offset).astype(np.int32)
        valid_cols = np.flatnonzero((source_cols >= 0) & (source_cols < array.shape[1]))
        valid_rows = np.flatnonzero((source_rows >= 0) & (source_rows < array.shape[0]))
        georeferenced = np.full((raster_grid.height, raster_grid.width), np.nan, dtype=np.float32)
        georeferenced[np.ix_(valid_rows, valid_cols)] = array[np.ix_(source_rows[valid_rows], source_cols[valid_cols])]
        array = georeferenced
    elif array.shape != (raster_grid.height, raster_grid.width):
        rows = np.linspace(0, array.shape[0] - 1, raster_grid.height).round().astype(np.int32)
        cols = np.linspace(0, array.shape[1] - 1, raster_grid.width).round().astype(np.int32)
        array = array[rows][:, cols]
    converted = np.where(np.isfinite(array) & (array >= 0), array / KG_PER_UG, np.nan)
    return converted.astype(np.float32)


def decode_firework_native_coverage(data: bytes) -> NativeField:
    if not data or data[:4] not in _TIFF_MAGIC:
        raise ValueError("FireWork coverage was not a GeoTIFF")
    try:
        import tifffile
        with tifffile.TiffFile(io.BytesIO(data)) as tiff:
            page = tiff.pages[0]
            array = np.asarray(page.asarray(), dtype=np.float32)
            while array.ndim > 2:
                array = array[0]
            nodata = _tiff_nodata(page)
            scale = tuple(float(value) for value in page.tags["ModelPixelScaleTag"].value)
            tiepoint = tuple(float(value) for value in page.tags["ModelTiepointTag"].value)
            geotiff = page.geotiff_tags or {}
            pixel_offset = 0.0 if geotiff.get("GTRasterTypeGeoKey", 1) == 2 else 0.5
            model_type = int(geotiff.get("GTModelTypeGeoKey", -1))
            geographic_type = int(geotiff.get("GeographicTypeGeoKey", -1))
            if model_type != 2 or geographic_type != 4326 or page.tags.get("ModelTransformationTag") is not None:
                raise ValueError("FireWork native coverage must use an EPSG:4326 north-up affine grid")
    except (ImportError, KeyError, MemoryError, OSError, TypeError, ValueError) as exc:
        raise ValueError("invalid FireWork native coverage") from exc
    if array.ndim != 2 or min(array.shape) < 2 or array.size > 2_100_000:
        raise ValueError("FireWork native coverage dimensions are invalid")
    if len(scale) < 2 or len(tiepoint) < 6 or scale[0] <= 0 or scale[1] <= 0:
        raise ValueError("invalid FireWork native coverage georeference")
    if nodata is not None:
        array = np.where(array == nodata, np.nan, array)
    converted = np.where(np.isfinite(array) & (array >= 0), array / KG_PER_UG, np.nan).astype(np.float32)
    grid = GeographicGrid(
        width=array.shape[1],
        height=array.shape[0],
        lon0=tiepoint[3] + (pixel_offset - tiepoint[0]) * scale[0],
        lat0=tiepoint[4] - (pixel_offset - tiepoint[1]) * scale[1],
        dx=scale[0],
        dy=scale[1],
    )
    return NativeField(converted, np.isfinite(converted) & (converted >= 0), grid)


def fetch_firework_native_hour(settings: Settings, model_run: str, valid: str) -> NativeField:
    with timed_provider_phase("firework", "download"):
        coverage = _firework_bytes(firework_native_wcs_url(settings.firework_wms_url, model_run, valid))
    with timed_provider_phase("firework", "decode"):
        field = decode_firework_native_coverage(coverage)
        if not np.any(field.valid & np.isfinite(field.values) & (field.values >= 0)):
            raise ValueError("FireWork native coverage has no valid concentrations")
        return NativeField(field.values, field.valid, field.grid, "firework", model_run, valid, "µg/m³", FIREWORK_NATIVE_PROCESSING_VERSION)


def discover_firework(settings: Settings) -> tuple[str, list[str]]:
    capabilities = _get(settings.firework_wms_url, params={
        "SERVICE": "WMS", "REQUEST": "GetCapabilities", "VERSION": "1.3.0",
        "LAYERS": FIREWORK_LAYER,
    }).text
    return parse_firework_capabilities(capabilities)


def _validated_firework_sld_png(data: bytes, expected_size: tuple[int, int]) -> bytes:
    _validated_png(data, "FireWork forecast", expected_size)
    with Image.open(io.BytesIO(data)) as image:
        rgba = image.convert("RGBA")
        alpha_max = rgba.getchannel("A").getextrema()[1]
        rgb_max = max(rgba.getchannel(channel).getextrema()[1] for channel in "RGB")
    if alpha_max > 0 and rgb_max == 0:
        raise ValueError("invalid FireWork forecast image")
    return data


def _colorize_firework(values: np.ndarray) -> tuple[bytes, np.ndarray]:
    from ingest.forecast_palette import encode_official_concentration

    valid = np.isfinite(values) & (values >= 0)
    return encode_official_concentration(values, valid), valid


def fetch_firework_hour(
    settings: Settings,
    model_run: str,
    valid: str,
    *,
    encode_png: bool = True,
    raster_grid: RasterGrid = BASE_RASTER_GRID,
) -> dict[str, Any]:
    base = settings.firework_wms_url
    try:
        with timed_provider_phase("firework", "download"):
            coverage = _firework_bytes(firework_wcs_url(base, model_run, valid, raster_grid))
        with timed_provider_phase("firework", "decode"):
            values = decode_firework_coverage(coverage, raster_grid)
        valid_mask = np.isfinite(values) & (values >= 0)
        payload: dict[str, Any] = {"validTime": valid, "modelRun": model_run, "values": values, "valid": valid_mask}
        if encode_png:
            payload["png"], payload["valid"] = _colorize_firework(values)
        return payload
    except HttpFetchError as exc:
        if exc.provider_outage:
            raise
        if not encode_png:
            raise
    except (OSError, RuntimeError, ValueError):
        if not encode_png:
            raise
    if encode_png:
        with timed_provider_phase("firework", "download"):
            data = _firework_bytes(firework_getmap_url(base, model_run, valid, style=FIREWORK_STYLE))
        with timed_provider_phase("firework", "decode"):
            png = _validated_firework_sld_png(data, (CONTEXT_WIDTH, CONTEXT_HEIGHT))
        return {"validTime": valid, "modelRun": model_run, "png": png}
    raise RuntimeError("FireWork numeric coverage unavailable")


def firework_native_times(valid_times: list[str]) -> list[str]:
    return valid_times[:73]


def firework_acquisition_times(valid_times: list[str], now: datetime, *, encode_png: bool) -> list[str]:
    native = firework_native_times(valid_times)
    if encode_png:
        return native
    end = now + timedelta(hours=72)
    return [
        value
        for value in native
        if now - timedelta(hours=1) <= datetime.fromisoformat(value.replace("Z", "+00:00")) <= end
    ]


def fetch_firework(
    settings: Settings,
    now: datetime,
    discovery: tuple[str, list[str]] | None = None,
    reuse: dict[str, dict[str, Any]] | None = None,
    required_times: set[str] | None = None,
    on_frame: Callable[[dict[str, Any]], None] | None = None,
    *,
    encode_png: bool = True,
    raster_grid: RasterGrid = BASE_RASTER_GRID,
) -> tuple[str, list[dict[str, Any]], bytes]:
    from ingest.forecast_palette import official_legend_png
    model_run, valid_times = discovery or discover_firework(settings)
    native = firework_native_times(valid_times)
    acquisition = set(firework_acquisition_times(native, now, encode_png=encode_png))
    required = acquisition if required_times is None else acquisition & required_times

    def load(valid: str) -> dict[str, Any]:
        if reuse and valid in reuse:
            return reuse[valid]
        frame = fetch_firework_hour(
            settings, model_run, valid, encode_png=encode_png, raster_grid=raster_grid
        )
        if on_frame:
            on_frame(frame)
        return frame

    from ingest.http_pool import map_isolated_batches

    results = map_isolated_batches(
        [valid for valid in native if valid in required],
        load,
        workers=max(1, getattr(settings, "firework_concurrency", 1)),
        stop_after=lambda batch: any(
            isinstance(result, HttpFetchError) and result.provider_outage for result in batch
        ),
        before_batch=lambda: (
            HttpFetchError("ingest acquisition deadline reached", host="geo.weather.gc.ca", provider_outage=True)
            if current_budget() and current_budget().acquisition_remaining() <= 0
            else None
        ),
    )
    errors = [result for result in results if isinstance(result, BaseException)]
    if errors:
        raise errors[0]
    loaded = {frame["validTime"]: frame for frame in results if isinstance(frame, dict)}
    frames = [loaded.get(valid, {"validTime": valid, "modelRun": model_run}) for valid in native]
    if not frames:
        raise ValueError("FireWork has no current forecast frames")
    return model_run, frames, official_legend_png()

def _dimension_values(value: str) -> list[str]:
    values: list[str] = []
    for token in filter(None, (part.strip() for part in value.split(","))):
        if "/" not in token:
            values.append(token)
            continue
        parts = token.split("/")
        if len(parts) != 3:
            continue
        try:
            start = datetime.fromisoformat(parts[0].replace("Z", "+00:00"))
            end = datetime.fromisoformat(parts[1].replace("Z", "+00:00"))
            step_match = re.fullmatch(r"PT(\d+)H", parts[2])
            step = timedelta(hours=int(step_match.group(1))) if step_match else timedelta(hours=1)
        except ValueError:
            continue
        current = start
        while current <= end and len(values) < 500:
            values.append(iso_utc(current))
            current += step
    return values


def _normalize_iso(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return iso_utc(parsed.astimezone(timezone.utc))


def _iso_sort_key(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
