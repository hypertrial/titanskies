from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np

from ingest.local_store import FrameStore
from ingest.config import Settings
from ingest.context_contracts import CONTEXT_BOUNDS, CONTEXT_VERSION, V8_DETAIL_RASTER_PROCESSING_VERSION, coverage_mask_version_for_version, detail_grid_for_version, display_bounds_for_version, iso_utc, monitor_regions_for_version
from ingest.forecast_composer import BEST_POLICY_VERSION, COASTAL_BUFFER_KM, COVERAGE_POLICY, FEATHER_DISTANCE_KM, SOURCE_MASK_VERSION, compose_v8_frame, v8_hrrr_target_coordinates
from ingest.forecast_native_cache import GeographicGrid, NativeField
from ingest.forecast_palette import PALETTE_UNITS, V8_PALETTE_VERSION, legend_png
from ingest.forecast_raster import HrrrGrid
from ingest.perf import current_metrics
from ingest.sources.airnow import AIRNOW_URL
from ingest.sources.aqhi import AQHI_COUNT_VERSION, AQHI_URL, aqhi_category
from ingest.sources.bc_air import BC_AIR_URL
from ingest.sources.context_raster import rasterize_perimeters
from ingest.sources.firework import FIREWORK_NATIVE_PROCESSING_VERSION, FIREWORK_PROCESSING_VERSION
from ingest.sources.wildfires import CWFIS_URL, WFIGS_URL
from ingest.sources.hrrr import HRRR_NATIVE_PROCESSING_VERSION, HRRR_VARIABLE
from ingest.sources.sinaica import SINAICA_URL
from ingest.context_publish import _put_asset, _put_json_asset, _put_monitor_asset, _publish_integrated_frame
from ingest.context_sources import _state

def _demo_manifest(
    store: FrameStore,
    now: datetime,
    settings: Settings | None = None,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = settings or Settings.from_env()
    published_display_bounds = display_bounds_for_version(CONTEXT_VERSION)
    published_monitor_regions = monitor_regions_for_version(CONTEXT_VERSION)
    source_times = {
        "airnow": now - timedelta(minutes=35),
        "bcair": now - timedelta(minutes=40),
        "sinaica": now - timedelta(minutes=45),
        "wfigs": now - timedelta(hours=1),
        "cwfis": now - timedelta(hours=2),
        "firework": now.replace(minute=0, second=0, microsecond=0),
        "hrrr": now.replace(minute=0, second=0, microsecond=0),
    }
    source_times["aqhi"] = now - timedelta(minutes=30)
    airnow_monitors, bc_monitors, sinaica_monitors, aqhi_monitors = _demo_monitors(
        source_times["airnow"], source_times["bcair"], source_times["sinaica"], source_times.get("aqhi")
    )
    airnow_url = _put_monitor_asset(store, "airnow-monitors.json", airnow_monitors)
    bc_url = _put_monitor_asset(store, "bc-monitors.json", bc_monitors)
    sinaica_url = _put_monitor_asset(store, "sinaica-monitors.json", sinaica_monitors)
    aqhi_url = _put_monitor_asset(store, "aqhi-monitors.json", aqhi_monitors) if aqhi_monitors else None
    us_incidents, ca_incidents = _demo_incidents(source_times["wfigs"], source_times["cwfis"])
    us_rings = [_box(item["lon"], item["lat"], 0.35) for item in us_incidents]
    ca_rings = [_box(item["lon"], item["lat"], 0.45) for item in ca_incidents]
    firework_run = iso_utc(source_times["firework"])
    hrrr_run = iso_utc(source_times["hrrr"])
    legend_url = _put_asset(store, "forecast-legend.png", legend_png(palette_version=V8_PALETTE_VERSION), "image/png")
    firework_frames = []
    for hour in range(73):
        valid = iso_utc(source_times["firework"] + timedelta(hours=hour))
        firework_frames.append({"validTime": valid, "modelRun": firework_run})
    include_hrrr = settings.hrrr_smoke_enabled
    hrrr_frames = []
    if include_hrrr:
        for hour in range(49):
            valid = iso_utc(source_times["hrrr"] + timedelta(hours=hour))
            hrrr_frames.append({"validTime": valid, "modelRun": hrrr_run})
    firework = {
        "modelId": "firework",
        "variable": "firework_wildfire_pm25",
        "modelRun": firework_run,
        "units": "µg/m³ PM2.5 from wildfire smoke",
        "nativeResolutionKm": 10,
        "ingestMethod": "geomet-wcs",
        "processingVersion": FIREWORK_NATIVE_PROCESSING_VERSION,
        "horizonHours": 72,
        "frames": firework_frames,
    }
    hrrr = {
        "modelId": "hrrr",
        "variable": HRRR_VARIABLE,
        "modelRun": hrrr_run,
        "units": PALETTE_UNITS + " smoke concentration at 8 m AGL",
        "nativeResolutionKm": 3,
        "ingestMethod": "nomads-grib2",
        "processingVersion": HRRR_NATIVE_PROCESSING_VERSION,
        "horizonHours": 48,
        "frames": hrrr_frames,
    } if include_hrrr else {"frames": []}
    composed = _demo_v8_composed(
        store,
        now,
        firework_run,
        hrrr_run,
        include_hrrr,
        ((previous or {}).get("forecasts") or {}).get("best"),
    )
    metrics = current_metrics()
    if metrics:
        expected = [iso_utc(now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=hour)) for hour in range(37)]
        metrics.set_forecast_window(expected, [str(frame.get("validTime")) for frame in composed.get("frames", [])])
    best = {
        "modelId": "best",
        "variable": "best_available_smoke",
        "units": composed["units"],
        "nativeResolutionKm": None,
        "ingestMethod": "compose",
        "horizonHours": 36,
        "selectionPolicy": composed["selectionPolicy"],
        "featherDistanceKm": composed["featherDistanceKm"],
        "sourceMaskVersion": composed["sourceMaskVersion"],
        "coveragePolicy": composed["coveragePolicy"],
        "coastalBufferKm": composed["coastalBufferKm"],
        "coverageMaskVersion": composed["coverageMaskVersion"],
        "fireworkProcessingVersion": composed.get("fireworkProcessingVersion"),
        "hrrrProcessingVersion": composed.get("hrrrProcessingVersion"),
        "incompleteNumeric": composed.get("incompleteNumeric", False),
        "integratedStatus": "complete",
        "paletteVersion": V8_PALETTE_VERSION,
        "detailGrid": composed["detailGrid"],
        "rasterProcessingVersion": composed["rasterProcessingVersion"],
        "modelRun": composed["modelRun"],
        "legendUrl": legend_url,
        "frames": composed["frames"],
    }

    monitor_sets = {
        "airnow": {"url": airnow_url, "observedAt": iso_utc(source_times["airnow"]), "count": len(airnow_monitors)},
        "bcair": {"url": bc_url, "observedAt": iso_utc(source_times["bcair"]), "count": len(bc_monitors)},
        "sinaica": {"url": sinaica_url, "observedAt": iso_utc(source_times["sinaica"]), "count": len(sinaica_monitors)},
    }
    if aqhi_url:
        monitor_sets["aqhi"] = {
            "url": aqhi_url,
            "observedAt": iso_utc(source_times["aqhi"]),
            "count": len(aqhi_monitors),
            "countVersion": AQHI_COUNT_VERSION,
        }

    return {
        "version": CONTEXT_VERSION,
        "mode": "demo",
        "generatedAt": iso_utc(now),
        "bounds": dict(CONTEXT_BOUNDS),
        "displayBounds": published_display_bounds,
        "monitorRegions": list(published_monitor_regions),
        "sources": {
            name: _state(name, now, observed, status="unavailable", error="HRRR_SMOKE_ENABLED is off") if name == "hrrr" and not include_hrrr else _state(name, now, observed)
            for name, observed in source_times.items()
        },
        "air": {
            "observedAt": iso_utc(source_times["airnow"]),
            "monitorsUrl": airnow_url,
            "bcMonitorsUrl": bc_url,
            "sinaicaMonitorsUrl": sinaica_url,
            "monitorSets": monitor_sets,
        },
        "fires": {
            "wfigsIncidentsUrl": _put_json_asset(store, "wfigs-incidents.json", {"incidents": us_incidents}),
            "wfigsPerimeterTextureUrl": _put_asset(store, "wfigs-perimeters.png", rasterize_perimeters(us_rings), "image/png"),
            "cwfisIncidentsUrl": _put_json_asset(store, "cwfis-incidents.json", {"incidents": ca_incidents}),
            "cwfisPerimeterTextureUrl": _put_asset(store, "cwfis-perimeters.png", rasterize_perimeters(ca_rings), "image/png"),
        },
        "forecast": best,
        "forecasts": {"best": best, "hrrr": hrrr, "firework": firework},
    }


def _demo_v8_composed(
    store: FrameStore,
    now: datetime,
    firework_run: str,
    hrrr_run: str,
    include_hrrr: bool,
    previous_best: dict[str, Any] | None,
) -> dict[str, Any]:
    firework_grid = GeographicGrid(257, 159, -145.0, 72.0, 100.0 / 256, 62.0 / 158)
    hrrr_grid = HrrrGrid(nx=600, ny=400, dx=9_000, dy=9_000)
    hrrr_target_xy = None
    fw_y, fw_x = np.mgrid[:firework_grid.height, :firework_grid.width]
    hr_y, hr_x = np.mgrid[:hrrr_grid.ny, :hrrr_grid.nx]
    frames = []
    reusable = bool(
        previous_best
        and previous_best.get("paletteVersion") == V8_PALETTE_VERSION
        and previous_best.get("detailGrid") == detail_grid_for_version(8)
        and previous_best.get("rasterProcessingVersion") == V8_DETAIL_RASTER_PROCESSING_VERSION
        and previous_best.get("coverageMaskVersion") == coverage_mask_version_for_version(8)
        and previous_best.get("fireworkProcessingVersion") == FIREWORK_NATIVE_PROCESSING_VERSION
        and previous_best.get("hrrrProcessingVersion") == (HRRR_NATIVE_PROCESSING_VERSION if include_hrrr else None)
    )
    prior_frames = {frame.get("validTime"): frame for frame in (previous_best or {}).get("frames", [])}
    start = now.replace(minute=0, second=0, microsecond=0)
    for hour in range(37):
        valid = iso_utc(start + timedelta(hours=hour))
        drift = hour * 1.8
        firework_values = (
            3
            + 360 * np.exp(-(((fw_x - 72 - drift) / 19) ** 2 + ((fw_y - 61) / 25) ** 2))
            + 145 * np.exp(-(((fw_x - 157 + drift * 0.35) / 34) ** 2 + ((fw_y - 88) / 22) ** 2))
        ).astype(np.float32)
        firework = NativeField(
            firework_values,
            # Keep the first hour HRRR-only so the deterministic demo exercises
            # changing source attribution during temporal interpolation.
            np.full_like(firework_values, hour > 0, dtype=bool),
            firework_grid,
            "firework",
            firework_run,
            valid,
            "µg/m³",
            FIREWORK_NATIVE_PROCESSING_VERSION,
        )
        hrrr = None
        contributors = []
        if include_hrrr:
            hrrr_values = (
                2
                + 720 * np.exp(-(((hr_x - 238 - drift * 0.8) / 31) ** 2 + ((hr_y - 210) / 39) ** 2))
                + 210 * np.exp(-(((hr_x - 355 + drift * 0.25) / 52) ** 2 + ((hr_y - 265) / 28) ** 2))
            ).astype(np.float32)
            hrrr = NativeField(
                hrrr_values,
                np.ones_like(hrrr_values, dtype=bool),
                hrrr_grid,
                "hrrr",
                hrrr_run,
                valid,
                "µg/m³",
                HRRR_NATIVE_PROCESSING_VERSION,
            )
            contributors.append({"source": "hrrr", "modelRun": hrrr_run})
        if hour > 0:
            contributors.append({"source": "firework", "modelRun": firework_run})
        prior = prior_frames.get(valid) if reusable else None
        if prior and prior.get("contributors") == contributors and len(prior.get("detailTiles") or []) == 16:
            frames.append(_publish_integrated_frame(store, prior))
            continue
        if include_hrrr and hrrr_target_xy is None:
            hrrr_target_xy = v8_hrrr_target_coordinates(hrrr_grid)
        texture, mask, tiles = compose_v8_frame(firework, hrrr, hrrr_target_xy=hrrr_target_xy)
        frames.append(_publish_integrated_frame(store, {
            "validTime": valid,
            "modelRun": hrrr_run if include_hrrr else firework_run,
            "texturePng": texture,
            "maskPng": mask,
            "detailTilePngs": tiles,
            "contributors": contributors,
        }))
        del firework, hrrr, firework_values, texture, mask, tiles
    return {
        "units": "µg/m³",
        "selectionPolicy": BEST_POLICY_VERSION,
        "featherDistanceKm": FEATHER_DISTANCE_KM,
        "sourceMaskVersion": SOURCE_MASK_VERSION,
        "coveragePolicy": COVERAGE_POLICY,
        "coastalBufferKm": COASTAL_BUFFER_KM,
        "coverageMaskVersion": coverage_mask_version_for_version(8),
        "fireworkProcessingVersion": FIREWORK_NATIVE_PROCESSING_VERSION,
        "hrrrProcessingVersion": HRRR_NATIVE_PROCESSING_VERSION if include_hrrr else None,
        "detailGrid": detail_grid_for_version(8),
        "rasterProcessingVersion": V8_DETAIL_RASTER_PROCESSING_VERSION,
        "incompleteNumeric": False,
        "modelRun": hrrr_run if include_hrrr else firework_run,
        "frames": frames,
    }


def _demo_monitors(airnow_time: datetime, bc_time: datetime, sinaica_time: datetime, aqhi_time: datetime | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    from ingest.sources.aqi import AQI_METHOD_NOWCAST, AQI_METHOD_PROVIDER, aqi_category

    def item(source: str, identifier: str, name: str, agency: str, lat: float, lon: float, aqi: int, concentration: float, observed: datetime, method: str, source_url: str, country: str | None = None) -> dict[str, Any]:
        country = country or ("CA" if source == "bcair" else "MX" if source == "sinaica" else "US")
        payload = {
            "id": identifier, "name": name, "agency": agency, "lat": lat, "lon": lon,
            "observedAt": iso_utc(observed), "aqi": aqi, "category": aqi_category(aqi),
            "concentration": concentration, "unit": "µg/m³", "source": source,
            "sourceUrl": source_url, "aqiMethod": method, "indexSystem": "us-epa-pm25-aqi",
            "indexValue": aqi, "indexMethod": method, "country": country, "preliminary": True,
        }
        if method == AQI_METHOD_NOWCAST:
            payload["nowcastConcentration"] = concentration
        return payload

    airnow = [
        item("airnow", "airnow:demo-vancouver", "Vancouver", "Metro Vancouver", 49.28, -123.12, 74, 22.4, airnow_time, AQI_METHOD_PROVIDER, AIRNOW_URL, "CA"),
        item("airnow", "airnow:demo-edmonton", "Edmonton", "Alberta Environment", 53.55, -113.49, 112, 39.1, airnow_time, AQI_METHOD_PROVIDER, AIRNOW_URL, "CA"),
        item("airnow", "airnow:demo-seattle", "Seattle", "Puget Sound Clean Air Agency", 47.61, -122.33, 91, 31.0, airnow_time, AQI_METHOD_PROVIDER, AIRNOW_URL),
        item("airnow", "airnow:demo-portland", "Portland", "Oregon DEQ", 45.52, -122.68, 128, 46.4, airnow_time, AQI_METHOD_PROVIDER, AIRNOW_URL),
        item("airnow", "airnow:demo-san-francisco", "San Francisco", "Bay Area AQMD", 37.77, -122.42, 54, 13.6, airnow_time, AQI_METHOD_PROVIDER, AIRNOW_URL),
        item("airnow", "airnow:demo-denver", "Denver", "Colorado DPHE", 39.74, -104.99, 67, 19.2, airnow_time, AQI_METHOD_PROVIDER, AIRNOW_URL),
        item("airnow", "airnow:demo-dallas", "Dallas", "Texas CEQ", 32.78, -96.8, 44, 10.5, airnow_time, AQI_METHOD_PROVIDER, AIRNOW_URL),
        item("airnow", "airnow:demo-chicago", "Chicago", "Illinois EPA", 41.88, -87.63, 103, 36.1, airnow_time, AQI_METHOD_PROVIDER, AIRNOW_URL),
        item("airnow", "airnow:demo-toronto", "Toronto", "Ontario MECP", 43.65, -79.38, 58, 14.8, airnow_time, AQI_METHOD_PROVIDER, AIRNOW_URL, "CA"),
        item("airnow", "airnow:demo-new-york", "New York", "New York DEC", 40.71, -74.01, 39, 9.2, airnow_time, AQI_METHOD_PROVIDER, AIRNOW_URL),
        item("airnow", "airnow:demo-atlanta", "Atlanta", "Georgia EPD", 33.75, -84.39, 47, 11.2, airnow_time, AQI_METHOD_PROVIDER, AIRNOW_URL),
        item("airnow", "airnow:demo-washington", "Washington", "District of Columbia DOEE", 38.91, -77.04, 63, 17.4, airnow_time, AQI_METHOD_PROVIDER, AIRNOW_URL),
        item("airnow", "airnow:demo-anchorage", "Anchorage", "Alaska DEC", 61.22, -149.90, 82, 27.0, airnow_time, AQI_METHOD_PROVIDER, AIRNOW_URL),
        item("airnow", "airnow:demo-fairbanks", "Fairbanks", "Alaska DEC", 64.84, -147.72, 97, 34.0, airnow_time, AQI_METHOD_PROVIDER, AIRNOW_URL),
        item("airnow", "airnow:demo-nome", "Nome", "Alaska DEC", 64.50, -165.41, 54, 13.8, airnow_time, AQI_METHOD_PROVIDER, AIRNOW_URL),
        item("airnow", "airnow:demo-adak", "Adak", "Alaska DEC", 51.88, -176.66, 41, 9.8, airnow_time, AQI_METHOD_PROVIDER, AIRNOW_URL),
        item("airnow", "airnow:demo-attu", "Attu", "Alaska DEC", 52.83, 173.18, 38, 9.1, airnow_time, AQI_METHOD_PROVIDER, AIRNOW_URL),
        item("airnow", "airnow:demo-vancouver-dup", "Vancouver Clark Drive", "Metro Vancouver", 49.281, -123.121, 76, 23.1, airnow_time, AQI_METHOD_PROVIDER, AIRNOW_URL, "CA"),
        item("airnow", "airnow:demo-cdmx", "Mexico City", "SEDEMA", 19.43, -99.13, 121, 43.2, airnow_time, AQI_METHOD_PROVIDER, AIRNOW_URL, "MX"),
    ]
    bcair = [
        item("bcair", "bcair:demo-vancouver", "Vancouver Clark Drive", "Metro Vancouver", 49.28, -123.12, 71, 21.2, bc_time, AQI_METHOD_NOWCAST, BC_AIR_URL),
        item("bcair", "bcair:demo-prince-george", "Prince George", "British Columbia ENV", 53.92, -122.75, 88, 29.4, bc_time, AQI_METHOD_NOWCAST, BC_AIR_URL),
        item("bcair", "bcair:demo-kamloops", "Kamloops", "British Columbia ENV", 50.67, -120.34, 64, 18.1, bc_time, AQI_METHOD_NOWCAST, BC_AIR_URL),
        item("bcair", "bcair:demo-fort-st-john", "Fort St. John", "British Columbia ENV", 56.25, -120.85, 79, 24.8, bc_time, AQI_METHOD_NOWCAST, BC_AIR_URL),
        item("bcair", "bcair:demo-penticton", "Penticton Debeck Road", "British Columbia ENV", 49.50, -119.60, 58, 15.4, bc_time, AQI_METHOD_NOWCAST, BC_AIR_URL),
    ]
    sinaica = [
        item("sinaica", "sinaica:demo-cdmx", "Benito Juárez", "SEDEMA", 19.372, -99.159, 118, 41.8, sinaica_time, AQI_METHOD_NOWCAST, SINAICA_URL),
        item("sinaica", "sinaica:demo-guadalajara", "Guadalajara Centro", "SEMADET", 20.67, -103.35, 94, 32.2, sinaica_time, AQI_METHOD_NOWCAST, SINAICA_URL),
        item("sinaica", "sinaica:demo-monterrey", "Monterrey Obispado", "SIMA", 25.67, -100.31, 86, 28.6, sinaica_time, AQI_METHOD_NOWCAST, SINAICA_URL),
        item("sinaica", "sinaica:demo-tijuana", "Tijuana Centro", "SPA Baja California", 32.53, -117.02, 72, 21.8, sinaica_time, AQI_METHOD_NOWCAST, SINAICA_URL),
    ]
    aqhi = [] if aqhi_time is None else [
        {
            "id": f"aqhi:demo-{name.lower().replace(' ', '-')}", "name": name,
            "agency": "Environment and Climate Change Canada", "lat": lat, "lon": lon,
            "observedAt": iso_utc(aqhi_time), "indexSystem": "ca-aqhi", "indexValue": value,
            "indexMethod": "provider", "category": aqhi_category(value), "country": "CA",
            "source": "aqhi", "sourceUrl": AQHI_URL, "preliminary": True,
        }
        for name, lat, lon, value in (
            ("Vancouver", 49.2827, -123.1207, 4.4), ("Vancouver East", 49.2820, -123.1198, 5.1),
            ("Prince George", 53.9171, -122.7497, 7.3), ("Edmonton", 53.5461, -113.4938, 3.2),
            ("Toronto", 43.6532, -79.3832, 5.7), ("Montreal", 45.5019, -73.5674, 2.4),
            ("Halifax", 44.6488, -63.5752, 1.8), ("Whitehorse", 60.7212, -135.0568, 10.4),
        )
    ]
    return airnow, bcair, sinaica, aqhi


def _demo_incidents(us_time: datetime, ca_time: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    def item(identifier: str, name: str, country: str, lon: float, lat: float, status: str, area: float, observed: datetime) -> dict[str, Any]:
        acres = round(area / 0.404685642, 1) if country == "US" else area
        return {"id": identifier, "name": name, "country": country, "lon": lon, "lat": lat, "status": status, "areaHectares": area, "sourceArea": acres, "sourceAreaUnit": "acres" if country == "US" else "hectares", "updatedAt": iso_utc(observed), "sourceUrl": WFIGS_URL if country == "US" else CWFIS_URL}
    return (
        [item("US:demo-1", "Pine Ridge Fire", "US", -121.45, 40.7, "Active", 8200, us_time), item("US:demo-2", "Canyon Fire", "US", -113.8, 43.4, "32% contained", 3100, us_time), item("US:demo-3", "Mesa Fire", "US", -106.2, 35.8, "Active", 1700, us_time)],
        [item("CA:demo-1", "BC-2024-187", "CA", -122.0, 52.4, "Out of control", 12600, ca_time), item("CA:demo-2", "AB-2024-091", "CA", -115.6, 55.2, "Being held", 4800, ca_time), item("CA:demo-3", "SK-2024-044", "CA", -106.7, 57.1, "Out of control", 6900, ca_time)],
    )


def _box(lon: float, lat: float, size: float) -> list[list[float]]:
    return [[lon - size, lat - size], [lon + size, lat - size], [lon + size, lat + size], [lon - size, lat + size]]
