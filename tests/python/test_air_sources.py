from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ingest.local_store import LocalFrameStore
from ingest.http import allowed_host, fetch
from ingest.sources.airnow import fetch_airnow, parse_airnow
from ingest.sources.aqhi import AQHI_COUNT_VERSION, AQHI_HOSTS, aqhi_category, aqhi_display_value, fetch_aqhi, parse_aqhi
from ingest.sources.bc_air import HOURLY_MAX_BYTES, STATIONS_MAX_BYTES, fetch_bc_air, parse_bc_hourly, parse_bc_stations
from ingest.sources.sinaica import fetch_sinaica, parse_sinaica_hours, parse_sinaica_json, parse_sinaica_stations, sinaica_timezone, _station_hours, _station_metadata


def test_airnow_filters_pm25_deduplicates_and_namespaces_ids() -> None:
    base = {"Parameter": "PM2.5", "Latitude": 47.6, "Longitude": -122.3, "AQI": 82, "Value": 27.1, "Unit": "UG/M3", "UTC": "2026-08-13T17:00:00Z", "FullAQSCode": "53-033-001", "SiteName": "Seattle", "AgencyName": "PSCAA", "Category": {"Name": "Moderate"}}
    now = datetime(2026, 8, 13, 18, tzinfo=timezone.utc)
    records = parse_airnow([base, {**base, "UTC": "2026-08-13T18:00:00Z", "AQI": 91, "Value": 31.0}, {**base, "Parameter": "OZONE"}, {**base, "Latitude": 95}], now)
    assert len(records) == 1
    assert records[0]["id"] == "airnow:53-033-001"
    assert records[0]["aqi"] == 91
    assert records[0]["concentration"] == 31.0
    assert records[0]["unit"] == "UG/M3"
    assert records[0]["source"] == "airnow"
    assert records[0]["aqiMethod"] == "provider"
    assert records[0]["indexSystem"] == "us-epa-pm25-aqi"
    assert records[0]["indexValue"] == 91
    assert records[0]["country"] == "US"


def test_airnow_uses_explicit_country_and_leaves_unknown_unclassified() -> None:
    now = datetime(2026, 8, 13, 18, tzinfo=timezone.utc)
    base = {"Parameter": "PM2.5", "Latitude": 49.28, "Longitude": -123.12, "AQI": 40, "Value": 9.5, "Unit": "UG/M3", "UTC": "2026-08-13T17:00:00Z", "SiteName": "Site"}
    records = parse_airnow([
        {**base, "FullAQSCode": "CA-ONE", "CountryCode": "CA"},
        {**base, "FullAQSCode": "MX-ONE", "CountryCode": "MEX", "Latitude": 19.4, "Longitude": -99.1},
        {**base, "FullAQSCode": "UNKNOWN", "Latitude": 48.0},
    ], now)
    assert records[0]["country"] == "CA"
    assert records[1]["country"] == "MX"
    assert "country" not in records[2]


def _aqhi_feature(identifier: str = "JAGPB", *, value: float = 4.4, observed: str = "2026-08-19T17:00:00Z", latest: bool = True, kind: str = "original") -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [-120.846667, 56.2525]},
        "properties": {
            "location_id": identifier,
            "location_name_en": "Fort St. John",
            "observation_datetime": observed,
            "observation_type": kind,
            "aqhi_type": "AQHI-Observation",
            "aqhi": value,
            "latest": latest,
        },
    }


def _aqhi_payload(features: list[dict], *, matched: int | None = None, next_link: bool = False) -> dict:
    links = [{"rel": "next", "href": "https://api.weather.gc.ca/next"}] if next_link else []
    return {
        "type": "FeatureCollection",
        "features": features,
        "numberMatched": len(features) if matched is None else matched,
        "numberReturned": len(features),
        "links": links,
    }


def test_aqhi_parses_current_observations_and_public_scale() -> None:
    now = datetime(2026, 8, 19, 18, tzinfo=timezone.utc)
    monitors = parse_aqhi(_aqhi_payload([_aqhi_feature(value=4.4)]), now)
    assert monitors == [{
        "id": "aqhi:JAGPB", "name": "Fort St. John", "agency": "Environment and Climate Change Canada",
        "lat": 56.2525, "lon": -120.846667, "observedAt": "2026-08-19T17:00:00Z",
        "indexSystem": "ca-aqhi", "indexValue": 4.4, "indexMethod": "provider", "category": "Moderate",
        "country": "CA", "source": "aqhi", "sourceUrl": "https://api.weather.gc.ca/collections/aqhi-observations-realtime",
        "preliminary": True,
    }]
    assert aqhi_display_value(0.2) == 1
    assert aqhi_display_value(9.6) == 10
    assert aqhi_display_value(10.01) == "10+"
    assert [aqhi_category(value) for value in (1, 4, 7, 10.01)] == ["Low", "Moderate", "High", "Very high"]
    assert [
        (aqhi_display_value(value), aqhi_category(value))
        for value in (3.49, 3.5, 6.49, 6.5, 10.0, 10.01)
    ] == [(3, "Low"), (4, "Moderate"), (6, "Moderate"), (7, "High"), (10, "High"), ("10+", "Very high")]


def test_aqhi_filters_invalid_and_stale_features_and_prefers_amendments() -> None:
    now = datetime(2026, 8, 19, 18, tzinfo=timezone.utc)
    original = _aqhi_feature(value=2.0)
    amendment = _aqhi_feature(value=7.2, kind="amendment")
    invalid = [
        _aqhi_feature("stale", observed="2026-08-19T15:59:59Z"),
        _aqhi_feature("future", observed="2026-08-19T18:16:00Z"),
        _aqhi_feature("not-latest", latest=False),
        _aqhi_feature("zero", value=0),
        _aqhi_feature("unknown-kind", kind="unknown"),
        {**_aqhi_feature("bad-geometry"), "geometry": {"type": "Point", "coordinates": [-120, 95]}},
        {**_aqhi_feature("wrong-geometry"), "geometry": {"type": "Polygon", "coordinates": []}},
        {**_aqhi_feature("boolean-value"), "properties": {**_aqhi_feature()["properties"], "location_id": "boolean-value", "aqhi": True}},
    ]
    monitors = parse_aqhi(_aqhi_payload([original, amendment, *invalid]), now)
    assert len(monitors) == 1
    assert monitors[0]["indexValue"] == 7.2
    assert monitors[0]["category"] == "High"


def test_aqhi_rejects_truncated_response() -> None:
    now = datetime(2026, 8, 19, 18, tzinfo=timezone.utc)
    for payload in (_aqhi_payload([_aqhi_feature()], matched=2), _aqhi_payload([_aqhi_feature()], next_link=True)):
        try:
            parse_aqhi(payload, now)
        except ValueError as exc:
            assert "truncated" in str(exc)
        else:
            raise AssertionError("truncated AQHI response was accepted")


def test_aqhi_rejects_malformed_response_metadata() -> None:
    now = datetime(2026, 8, 19, 18, tzinfo=timezone.utc)
    for payload in (
        {**_aqhi_payload([_aqhi_feature()]), "links": {}},
        {**_aqhi_payload([_aqhi_feature()]), "numberMatched": True},
        {**_aqhi_payload([_aqhi_feature()]), "numberReturned": -1},
    ):
        try:
            parse_aqhi(payload, now)
        except ValueError as exc:
            assert "metadata" in str(exc)
        else:
            raise AssertionError("malformed AQHI metadata was accepted")


def test_aqhi_fetch_is_bounded_and_rejects_collapse() -> None:
    now = datetime(2026, 8, 19, 18, tzinfo=timezone.utc)
    body = json.dumps(_aqhi_payload([_aqhi_feature()])).encode()
    calls = []

    def fake_fetch(url, **kwargs):
        calls.append((url, kwargs))
        return body

    with patch("ingest.sources.aqhi.fetch", fake_fetch):
        assert len(fetch_aqhi(now)) == 1
        try:
            fetch_aqhi(now, previous_count=40)
        except ValueError as exc:
            assert "collapsed" in str(exc)
        else:
            raise AssertionError("collapsed AQHI response was accepted")
    assert calls[0][1]["params"] == {"f": "json", "latest": "true", "limit": "500"}
    assert calls[0][1]["max_bytes"] == 512 * 1024
    assert AQHI_COUNT_VERSION == "eccc-aqhi-latest-v1"


def test_aqhi_host_allowlist_fails_closed() -> None:
    assert allowed_host("https://api.weather.gc.ca/collections/aqhi-observations-realtime", AQHI_HOSTS) == "api.weather.gc.ca"
    for url in ("http://api.weather.gc.ca/collections/aqhi-observations-realtime", "https://weather.gc.ca.example.test/aqhi"):
        try:
            allowed_host(url, AQHI_HOSTS)
        except ValueError as exc:
            assert "blocked host" in str(exc)
        else:
            raise AssertionError("unofficial AQHI host was accepted")


def test_airnow_rejects_negative_concentration() -> None:
    now = datetime(2026, 8, 13, 18, tzinfo=timezone.utc)
    records = parse_airnow([{
        "Parameter": "PM2.5", "Latitude": 47.6, "Longitude": -122.3, "AQI": 40, "Value": -1.2,
        "Unit": "UG/M3", "UTC": "2026-08-13T17:00:00Z", "FullAQSCode": "53-NEG", "SiteName": "Bad",
    }], now)
    assert records == []


def test_airnow_rejects_non_finite_values_and_accepts_canonical_units() -> None:
    now = datetime(2026, 8, 13, 18, tzinfo=timezone.utc)
    base = {
        "Parameter": "PM2.5", "Latitude": 47.6, "Longitude": -122.3, "AQI": 40,
        "Value": 9.5, "UTC": "2026-08-13T17:00:00Z", "FullAQSCode": "53-UNIT", "SiteName": "Seattle",
    }
    for unit in (None, "µg/m³", "μg/m³", "UG/M3"):
        record = dict(base)
        if unit is not None:
            record["Unit"] = unit
        assert len(parse_airnow([record], now)) == 1
    for field, value in (("Value", "NaN"), ("Value", "Infinity"), ("Latitude", "NaN"), ("Longitude", "-Infinity")):
        assert parse_airnow([{**base, field: value}], now) == []


def test_airnow_keeps_alaska_and_aleutian_sites() -> None:
    now = datetime(2026, 8, 13, 18, tzinfo=timezone.utc)
    records = parse_airnow([
        {"Parameter": "PM25", "Latitude": 64.5, "Longitude": -165.4, "AQI": 40, "Value": 9.5, "Unit": "UG/M3", "UTC": "2026-08-13T17:00:00Z", "FullAQSCode": "02-NOME", "SiteName": "Nome"},
        {"Parameter": "PM25", "Latitude": 52.8, "Longitude": 173.2, "AQI": 32, "Value": 7.6, "Unit": "UG/M3", "UTC": "2026-08-13T17:00:00Z", "FullAQSCode": "02-ATTU", "SiteName": "Attu"},
        {"Parameter": "PM25", "Latitude": 64.5, "Longitude": -165.4, "AQI": 40, "Value": 9.5, "Unit": "UG/M3", "UTC": "2026-08-13T19:00:00Z", "FullAQSCode": "02-FUTURE", "SiteName": "Future"},
    ], now)
    assert {item["id"] for item in records} == {"airnow:02-NOME", "airnow:02-ATTU"}


def test_airnow_dedupes_six_hour_window_to_newest() -> None:
    now = datetime(2026, 8, 13, 18, tzinfo=timezone.utc)
    base = {"Parameter": "PM2.5", "Latitude": 47.6, "Longitude": -122.3, "Value": 20.0, "Unit": "UG/M3", "FullAQSCode": "53-033-001", "SiteName": "Seattle"}
    records = parse_airnow([
        {**base, "UTC": "2026-08-13T12:00:00Z", "AQI": 40},
        {**base, "UTC": "2026-08-13T15:00:00Z", "AQI": 55},
        {**base, "UTC": "2026-08-13T18:00:00Z", "AQI": 70, "Value": 22.0},
        {**base, "UTC": "2026-08-13T11:00:00Z", "AQI": 30, "FullAQSCode": "53-OTHER", "SiteName": "Other"},
    ], now)
    assert [item["id"] for item in records] == ["airnow:53-033-001", "airnow:53-OTHER"]
    assert records[0]["aqi"] == 70
    assert records[0]["observedAt"] == "2026-08-13T18:00:00Z"


def test_airnow_fetch_tiles_and_rejects_collapse(monkeypatch) -> None:
    tiles: list[str] = []
    params_seen: list[dict] = []
    retries_seen: list[int] = []

    def fake_fetch(url, **kwargs):
        params_seen.append(kwargs["params"])
        retries_seen.append(kwargs["retries"])
        tiles.append(kwargs["params"]["BBOX"])
        return b'[{"Parameter":"PM2.5","Latitude":47.6,"Longitude":-122.3,"AQI":40,"Value":9.8,"Unit":"UG/M3","UTC":"2026-08-13T17:00:00Z","FullAQSCode":"A","SiteName":"Seattle"}]'

    settings = type("S", (), {"airnow_api_key": "secret", "airnow_base_url": "https://www.airnowapi.org/aq/data/"})()
    now = datetime(2026, 8, 13, 18, tzinfo=timezone.utc)
    with patch("ingest.sources.airnow.fetch", fake_fetch):
        monitors = fetch_airnow(settings, now)
    assert set(tiles) == {
        "-180.0,50.0,-130.0,84.0",
        "-130.0,40.0,-110.0,84.0",
        "-110.0,40.0,-90.0,84.0",
        "-90.0,40.0,-45.0,84.0",
        "-170.0,10.0,-110.0,40.0",
        "-110.0,10.0,-90.0,40.0",
        "-90.0,10.0,-45.0,40.0",
        "170.0,50.0,180.0,84.0",
    }
    assert len(tiles) == 8
    assert all(params["monitorType"] == "2" for params in params_seen)
    assert all(params["startdate"] == "2026-08-13T12" and params["enddate"] == "2026-08-13T18" for params in params_seen)
    assert retries_seen == [3] * 8
    assert monitors[0]["id"] == "airnow:A"
    try:
        with patch("ingest.sources.airnow.fetch", fake_fetch):
            fetch_airnow(settings, now, previous_count=40)
    except ValueError as exc:
        assert "collapsed" in str(exc)
    else:
        raise AssertionError("collapse was accepted")


def test_airnow_tile_failure_is_atomic() -> None:
    def fake_fetch(url, **kwargs):
        if kwargs["params"]["BBOX"].startswith("170"):
            raise RuntimeError("timeout")
        return b"[]"

    settings = type("S", (), {"airnow_api_key": "secret", "airnow_base_url": "https://www.airnowapi.org/aq/data/"})()
    now = datetime(2026, 8, 13, 18, tzinfo=timezone.utc)
    with patch("ingest.sources.airnow.fetch", fake_fetch):
        try:
            fetch_airnow(settings, now)
        except RuntimeError as exc:
            assert "timeout" in str(exc)
        else:
            raise AssertionError("partial tiles were published")


def test_bc_parses_pst_and_nowcast() -> None:
    stations = parse_bc_stations("EMS_ID,STATION_NAME,STATION_OWNER,LATITUDE,LONGITUDE\nE1,Kamloops,ENV,50.67,-120.34\n")
    hourly = "\n".join([
        "DATE_PST,EMS_ID,STATION_NAME,PARAMETER,RAW_VALUE,UNITS,LATITUDE,LONGITUDE",
        *[f"2026-08-13 {hour:02d}:00,E1,Kamloops,PM25,12.0,ug/m3,50.67,-120.34" for hour in range(7, 19)],
    ])
    now = datetime(2026, 8, 14, 3, tzinfo=timezone.utc)
    monitors = parse_bc_hourly(hourly, stations, now)
    assert len(monitors) == 1
    assert monitors[0]["id"] == "bcair:E1"
    assert monitors[0]["aqiMethod"] == "epa-nowcast-2024"
    assert monitors[0]["agency"] == "ENV"
    assert monitors[0]["observedAt"] == "2026-08-14T02:00:00Z"


def test_bc_accepts_canonical_microgram_units() -> None:
    stations = parse_bc_stations("EMS_ID,STATION_NAME,STATION_OWNER,LATITUDE,LONGITUDE\nE1,Kamloops,ENV,50.67,-120.34\n")
    now = datetime(2026, 8, 14, 3, tzinfo=timezone.utc)
    for unit in ("µg/m³", "μg/m³", "UG/M3"):
        hourly = "\n".join([
            "DATE_PST,EMS_ID,STATION_NAME,PARAMETER,RAW_VALUE,UNITS,LATITUDE,LONGITUDE",
            *[f"2026-08-13 {hour:02d}:00,E1,Kamloops,PM25,12.0,{unit},50.67,-120.34" for hour in range(17, 19)],
        ])
        assert len(parse_bc_hourly(hourly, stations, now)) == 1


def test_bc_ignores_hours_older_than_nowcast_window() -> None:
    stations = parse_bc_stations("EMS_ID,STATION_NAME,STATION_OWNER,LATITUDE,LONGITUDE\nE1,Kamloops,ENV,50.67,-120.34\n")
    header = "DATE_PST,EMS_ID,STATION_NAME,PARAMETER,RAW_VALUE,UNITS,LATITUDE,LONGITUDE"
    recent = [f"2026-08-13 {hour:02d}:00,E1,Kamloops,PM25,12.0,ug/m3,50.67,-120.34" for hour in range(7, 19)]
    now = datetime(2026, 8, 14, 3, tzinfo=timezone.utc)
    baseline = parse_bc_hourly("\n".join([header, *recent]), stations, now)
    spiked = parse_bc_hourly("\n".join([header, "2026-08-12 12:00,E1,Kamloops,PM25,80.0,ug/m3,50.67,-120.34", *recent]), stations, now)
    assert len(spiked) == 1
    assert spiked[0]["concentration"] == 12.0
    assert spiked[0]["observedAt"] == "2026-08-14T02:00:00Z"
    assert spiked[0]["nowcastConcentration"] == baseline[0]["nowcastConcentration"]
    assert spiked[0]["aqi"] == baseline[0]["aqi"]


def test_bc_keeps_oldest_nowcast_clock_hour() -> None:
    stations = parse_bc_stations("EMS_ID,STATION_NAME,STATION_OWNER,LATITUDE,LONGITUDE\nE1,Kamloops,ENV,50.67,-120.34\n")
    header = "DATE_PST,EMS_ID,STATION_NAME,PARAMETER,RAW_VALUE,UNITS,LATITUDE,LONGITUDE"
    newer = [f"2026-08-13 {hour:02d}:00,E1,Kamloops,PM25,10.0,ug/m3,50.67,-120.34" for hour in range(9, 19)]
    now = datetime(2026, 8, 14, 3, tzinfo=timezone.utc)
    without_edge = parse_bc_hourly("\n".join([header, *newer]), stations, now)
    with_edge = parse_bc_hourly("\n".join([header, "2026-08-13 08:00,E1,Kamloops,PM25,500.0,ug/m3,50.67,-120.34", *newer]), stations, now)
    assert with_edge[0]["nowcastConcentration"] != without_edge[0]["nowcastConcentration"]
    assert with_edge[0]["concentration"] == 10.0


def test_bc_drops_hour_just_before_keep_window() -> None:
    stations = parse_bc_stations("EMS_ID,STATION_NAME,STATION_OWNER,LATITUDE,LONGITUDE\nE1,Kamloops,ENV,50.67,-120.34\n")
    header = "DATE_PST,EMS_ID,STATION_NAME,PARAMETER,RAW_VALUE,UNITS,LATITUDE,LONGITUDE"
    in_window = [f"2026-08-13 {hour:02d}:00,E1,Kamloops,PM25,10.0,ug/m3,50.67,-120.34" for hour in range(8, 19)]
    now = datetime(2026, 8, 14, 3, tzinfo=timezone.utc)
    baseline = parse_bc_hourly("\n".join([header, *in_window]), stations, now)
    before_window = parse_bc_hourly("\n".join([header, "2026-08-13 06:00,E1,Kamloops,PM25,80.0,ug/m3,50.67,-120.34", *in_window]), stations, now)
    at_oldest = parse_bc_hourly("\n".join([header, "2026-08-13 07:00,E1,Kamloops,PM25,80.0,ug/m3,50.67,-120.34", *in_window]), stations, now)
    assert before_window[0]["nowcastConcentration"] == baseline[0]["nowcastConcentration"]
    assert at_oldest[0]["nowcastConcentration"] == baseline[0]["nowcastConcentration"]
    assert before_window[0]["concentration"] == 10.0


def test_bc_window_uses_truncated_clock_hour() -> None:
    stations = parse_bc_stations("EMS_ID,STATION_NAME,STATION_OWNER,LATITUDE,LONGITUDE\nE1,Kamloops,ENV,50.67,-120.34\n")
    header = "DATE_PST,EMS_ID,STATION_NAME,PARAMETER,RAW_VALUE,UNITS,LATITUDE,LONGITUDE"
    newer = [f"2026-08-13 {hour:02d}:00,E1,Kamloops,PM25,10.0,ug/m3,50.67,-120.34" for hour in range(9, 19)]
    now = datetime(2026, 8, 14, 3, 45, tzinfo=timezone.utc)
    without_edge = parse_bc_hourly("\n".join([header, *newer]), stations, now)
    with_edge = parse_bc_hourly("\n".join([header, "2026-08-13 08:00,E1,Kamloops,PM25,500.0,ug/m3,50.67,-120.34", *newer]), stations, now)
    assert with_edge[0]["nowcastConcentration"] != without_edge[0]["nowcastConcentration"]


def test_bc_rejects_observations_beyond_future_grace() -> None:
    stations = parse_bc_stations("EMS_ID,STATION_NAME,STATION_OWNER,LATITUDE,LONGITUDE\nE1,Kamloops,ENV,50.67,-120.34\n")
    header = "DATE_PST,EMS_ID,STATION_NAME,PARAMETER,RAW_VALUE,UNITS,LATITUDE,LONGITUDE"
    recent = [
        "2026-08-13 17:00,E1,Kamloops,PM25,10.0,ug/m3,50.67,-120.34",
        "2026-08-13 18:00,E1,Kamloops,PM25,11.0,ug/m3,50.67,-120.34",
    ]
    now = datetime(2026, 8, 14, 3, 10, tzinfo=timezone.utc)
    kept = parse_bc_hourly("\n".join([header, *recent, "2026-08-13 19:25,E1,Kamloops,PM25,22.0,ug/m3,50.67,-120.34"]), stations, now)
    too_new = parse_bc_hourly("\n".join([header, *recent, "2026-08-13 19:26,E1,Kamloops,PM25,99.0,ug/m3,50.67,-120.34"]), stations, now)
    assert kept[0]["concentration"] == 22.0
    assert kept[0]["observedAt"] == "2026-08-14T03:00:00Z"
    assert too_new[0]["concentration"] == 11.0
    assert too_new[0]["observedAt"] == "2026-08-14T02:00:00Z"


def test_bc_omits_stations_with_only_pre_window_hours() -> None:
    stations = parse_bc_stations(
        "EMS_ID,STATION_NAME,STATION_OWNER,LATITUDE,LONGITUDE\n"
        "E1,Kamloops,ENV,50.67,-120.34\n"
        "E2,Prince George,ENV,53.91,-122.75\n"
    )
    hourly = "\n".join([
        "DATE_PST,EMS_ID,STATION_NAME,PARAMETER,RAW_VALUE,UNITS,LATITUDE,LONGITUDE",
        *[f"2026-08-13 {hour:02d}:00,E1,Kamloops,PM25,12.0,ug/m3,50.67,-120.34" for hour in range(7, 19)],
        *[f"2026-08-01 {hour:02d}:00,E2,Prince George,PM25,80.0,ug/m3,53.91,-122.75" for hour in range(0, 24)],
        "2026-08-13 06:59:59,E2,Prince George,PM25,80.0,ug/m3,53.91,-122.75",
    ])
    monitors = parse_bc_hourly(hourly, stations, datetime(2026, 8, 14, 3, tzinfo=timezone.utc))
    assert [item["id"] for item in monitors] == ["bcair:E1"]


def test_bc_stale_rolling_history_publishes_nothing() -> None:
    stations = parse_bc_stations("EMS_ID,STATION_NAME,STATION_OWNER,LATITUDE,LONGITUDE\nE1,Kamloops,ENV,50.67,-120.34\n")
    hourly = "\n".join([
        "DATE_PST,EMS_ID,STATION_NAME,PARAMETER,RAW_VALUE,UNITS,LATITUDE,LONGITUDE",
        *[f"2026-08-01 {hour:02d}:00,E1,Kamloops,PM25,80.0,ug/m3,50.67,-120.34" for hour in range(0, 24)],
    ])
    now = datetime(2026, 8, 14, 3, tzinfo=timezone.utc)
    assert parse_bc_hourly(hourly, stations, now) == []
    assert parse_bc_hourly("", stations, now) == []
    assert parse_bc_hourly("DATE_PST,EMS_ID,PARAMETER,RAW_VALUE,UNITS,LATITUDE,LONGITUDE\n", stations, now) == []

    def fake_fetch(url, **kwargs):
        if url.endswith("PM25.csv"):
            return hourly.encode()
        return b"EMS_ID,STATION_NAME,STATION_OWNER,LATITUDE,LONGITUDE\nE1,Kamloops,ENV,50.67,-120.34\n"

    settings = type("S", (), {
        "bc_hourly_url": "https://www.env.gov.bc.ca/epd/bcairquality/aqo/csv/Hourly_Raw_Air_Data/Air_Quality/PM25.csv",
        "bc_stations_url": "https://www.env.gov.bc.ca/epd/bcairquality/aqo/csv/bc_air_monitoring_stations.csv",
    })()
    try:
        with patch("ingest.sources.bc_air.fetch", fake_fetch):
            fetch_bc_air(settings, now)
    except ValueError as exc:
        assert "no current" in str(exc)
    else:
        raise AssertionError("stale rolling history was published")


def test_bc_hourly_budget_accepts_current_rolling_file() -> None:
    seen: dict[str, int] = {}

    def fake_fetch(url, **kwargs):
        seen[url] = kwargs["max_bytes"]
        if url.endswith("PM25.csv"):
            rows = [
                "DATE_PST,EMS_ID,STATION_NAME,PARAMETER,RAW_VALUE,UNITS,LATITUDE,LONGITUDE",
                *[f"2026-08-13 {hour:02d}:00,E1,Kamloops,PM25,12.0,ug/m3,50.67,-120.34" for hour in range(7, 19)],
            ]
            return "\n".join(rows).encode()
        return b"EMS_ID,STATION_NAME,STATION_OWNER,LATITUDE,LONGITUDE\nE1,Kamloops,ENV,50.67,-120.34\n"

    settings = type("S", (), {
        "bc_hourly_url": "https://www.env.gov.bc.ca/epd/bcairquality/aqo/csv/Hourly_Raw_Air_Data/Air_Quality/PM25.csv",
        "bc_stations_url": "https://www.env.gov.bc.ca/epd/bcairquality/aqo/csv/bc_air_monitoring_stations.csv",
    })()
    with patch("ingest.sources.bc_air.fetch", fake_fetch):
        monitors = fetch_bc_air(settings, datetime(2026, 8, 14, 3, tzinfo=timezone.utc))
    assert monitors[0]["id"] == "bcair:E1"
    assert seen[settings.bc_hourly_url] == HOURLY_MAX_BYTES
    assert seen[settings.bc_stations_url] == STATIONS_MAX_BYTES
    assert HOURLY_MAX_BYTES == 16 * 1024 * 1024
    assert STATIONS_MAX_BYTES == 1 * 1024 * 1024
    assert 4 * 1024 * 1024 < 4_961_896 <= HOURLY_MAX_BYTES


def test_bc_hourly_oversize_is_not_published() -> None:
    def fake_fetch(url, **kwargs):
        raise RuntimeError(f"response exceeds {kwargs['max_bytes']} bytes")

    settings = type("S", (), {
        "bc_hourly_url": "https://www.env.gov.bc.ca/epd/bcairquality/aqo/csv/Hourly_Raw_Air_Data/Air_Quality/PM25.csv",
        "bc_stations_url": "https://www.env.gov.bc.ca/epd/bcairquality/aqo/csv/bc_air_monitoring_stations.csv",
    })()
    try:
        with patch("ingest.sources.bc_air.fetch", fake_fetch):
            fetch_bc_air(settings, datetime(2026, 8, 14, 3, tzinfo=timezone.utc))
    except RuntimeError as exc:
        assert "exceeds" in str(exc)
        assert str(HOURLY_MAX_BYTES) in str(exc)
    else:
        raise AssertionError("oversize hourly CSV was published")


def test_bc_window_filter_does_not_collapse_current_stations() -> None:
    station_rows = ["EMS_ID,STATION_NAME,STATION_OWNER,LATITUDE,LONGITUDE"]
    hourly_rows = ["DATE_PST,EMS_ID,STATION_NAME,PARAMETER,RAW_VALUE,UNITS,LATITUDE,LONGITUDE"]
    for index in range(10):
        identifier = f"E{index}"
        lat = 50.67 + index * 0.01
        station_rows.append(f"{identifier},S{index},ENV,{lat},-120.34")
        hourly_rows.append(f"2026-08-01 12:00,{identifier},S{index},PM25,80.0,ug/m3,{lat},-120.34")
        hourly_rows.extend(
            f"2026-08-13 {hour:02d}:00,{identifier},S{index},PM25,12.0,ug/m3,{lat},-120.34" for hour in range(7, 19)
        )

    def fake_fetch(url, **kwargs):
        if url.endswith("PM25.csv"):
            return "\n".join(hourly_rows).encode()
        return "\n".join(station_rows).encode()

    settings = type("S", (), {
        "bc_hourly_url": "https://www.env.gov.bc.ca/epd/bcairquality/aqo/csv/Hourly_Raw_Air_Data/Air_Quality/PM25.csv",
        "bc_stations_url": "https://www.env.gov.bc.ca/epd/bcairquality/aqo/csv/bc_air_monitoring_stations.csv",
    })()
    with patch("ingest.sources.bc_air.fetch", fake_fetch):
        monitors = fetch_bc_air(settings, datetime(2026, 8, 14, 3, tzinfo=timezone.utc), previous_count=10)
    assert len(monitors) == 10


def test_bc_collapse_when_few_stations_remain_in_window() -> None:
    station_rows = ["EMS_ID,STATION_NAME,STATION_OWNER,LATITUDE,LONGITUDE"]
    hourly_rows = ["DATE_PST,EMS_ID,STATION_NAME,PARAMETER,RAW_VALUE,UNITS,LATITUDE,LONGITUDE"]
    for index in range(10):
        identifier = f"E{index}"
        lat = 50.67 + index * 0.01
        station_rows.append(f"{identifier},S{index},ENV,{lat},-120.34")
        hours = range(7, 19) if index < 3 else range(0, 12)
        day = "2026-08-13" if index < 3 else "2026-08-01"
        hourly_rows.extend(
            f"{day} {hour:02d}:00,{identifier},S{index},PM25,12.0,ug/m3,{lat},-120.34" for hour in hours
        )

    def fake_fetch(url, **kwargs):
        if url.endswith("PM25.csv"):
            return "\n".join(hourly_rows).encode()
        return "\n".join(station_rows).encode()

    settings = type("S", (), {
        "bc_hourly_url": "https://www.env.gov.bc.ca/epd/bcairquality/aqo/csv/Hourly_Raw_Air_Data/Air_Quality/PM25.csv",
        "bc_stations_url": "https://www.env.gov.bc.ca/epd/bcairquality/aqo/csv/bc_air_monitoring_stations.csv",
    })()
    try:
        with patch("ingest.sources.bc_air.fetch", fake_fetch):
            fetch_bc_air(settings, datetime(2026, 8, 14, 3, tzinfo=timezone.utc), previous_count=10)
    except ValueError as exc:
        assert "collapsed" in str(exc)
    else:
        raise AssertionError("windowed snapshot collapse was accepted")


def test_bc_rejects_malformed_and_non_pm_rows() -> None:
    hourly = "\n".join([
        "DATE_PST,EMS_ID,PARAMETER,RAW_VALUE,UNITS,LATITUDE,LONGITUDE",
        "not-a-date,E1,PM25,12,ug/m3,50.67,-120.34",
        "2026-08-13 12:00,E1,O3,12,ug/m3,50.67,-120.34",
        "2026-08-13 12:00,E1,PM25,12,ppm,50.67,-120.34",
        "2026-08-13 12:00,E1,PM25,12,ppb,50.67,-120.34",
    ])
    assert parse_bc_hourly(hourly, {}, datetime(2026, 8, 13, 21, tzinfo=timezone.utc)) == []


def test_regional_sources_ignore_negative_and_nonfinite_pm25() -> None:
    stations = parse_bc_stations("EMS_ID,STATION_NAME,STATION_OWNER,LATITUDE,LONGITUDE\nE1,Kamloops,ENV,50.67,-120.34\n")
    header = "DATE_PST,EMS_ID,STATION_NAME,PARAMETER,RAW_VALUE,UNITS,LATITUDE,LONGITUDE"
    valid = [f"2026-08-13 {hour:02d}:00,E1,Kamloops,PM25,10.0,ug/m3,50.67,-120.34" for hour in range(7, 19)]
    invalid = [f"2026-08-13 19:00,E1,Kamloops,PM25,{value},ug/m3,50.67,-120.34" for value in ("-1", "NaN", "Infinity")]
    now = datetime(2026, 8, 14, 3, tzinfo=timezone.utc)
    monitor = parse_bc_hourly("\n".join([header, *valid, *invalid]), stations, now)[0]
    assert monitor["concentration"] == 10.0
    assert monitor["observedAt"] == "2026-08-14T02:00:00Z"

    zone = sinaica_timezone("Ciudad de Mexico", "Benito Juarez", 19.43, -99.13)
    payload = [
        *[{"fecha": "2026-08-13", "hora": hour, "valor": 10} for hour in range(9, 21)],
        *[{"fecha": "2026-08-13", "hora": 21, "valor": value} for value in ("-1", "NaN", "Infinity")],
    ]
    hours = parse_sinaica_hours(payload, zone, now)
    assert len(hours) == 12
    assert max(hours) == datetime(2026, 8, 14, 2, tzinfo=timezone.utc)
    assert all(value.is_finite() and value >= 0 for value in hours.values())


def test_sinaica_discovers_stations_and_rejects_unknown_timezone() -> None:
    html = (
        "<script>window.stations=[{id:999,name:'Injected'}];eval('evil')</script>"
        '<select id="estacion"><option value="0">Select</option>'
        '<option value="abc">Bad</option><option value="102">Guadalajara</option></select>'
    )
    assert parse_sinaica_stations(html) == [("102", "Guadalajara")]
    zone = sinaica_timezone("Baja California", "Tijuana", 32.53, -117.02)
    hours = parse_sinaica_hours(
        [{"id": "1", "fecha": "2026-08-13", "hora": 12, "valor": 14}],
        zone,
        datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    assert hours
    assert sinaica_timezone("Unknown", "Unknown", 5.0, -70.0) is None
    assert sinaica_timezone("Unknown", "Unknown", 13.9, -99.1) is None


def test_sinaica_station_discovery_deduplicates_repeated_options() -> None:
    html = (
        '<select><option value="102">Guadalajara</option>'
        '<option value="103"></option><option value="102">Guadalajara duplicate</option>'
        '<option value="103">Monterrey</option></select>'
    )
    assert parse_sinaica_stations(html) == [("102", "Guadalajara"), ("103", "Monterrey")]


def test_sinaica_extracts_embedded_json_without_executing_javascript() -> None:
    executed = {"eval": False, "exec": False}

    def boom_eval(*_args, **_kwargs):
        executed["eval"] = True
        raise AssertionError("eval executed")

    def boom_exec(*_args, **_kwargs):
        executed["exec"] = True
        raise AssertionError("exec executed")

    text = (
        'var other = 1; eval("process.exit(1)"); '
        'var dat = [{"id":"1","fecha":"2026-08-13","hora":12,"valor":14}]; '
        '(function(){ throw new Error("executed"); })();'
    )
    with patch("builtins.eval", boom_eval), patch("builtins.exec", boom_exec):
        payload = parse_sinaica_json(text)
    assert payload[0]["valor"] == 14
    assert executed == {"eval": False, "exec": False}
    try:
        parse_sinaica_json("var dat = [1+1];")
    except (ValueError, json.JSONDecodeError):
        pass
    else:
        raise AssertionError("JavaScript expression was evaluated")


def test_http_blocks_hosts_and_redacts_keys() -> None:
    try:
        fetch("https://example.test/data", hosts=frozenset({"www.airnowapi.org"}))
    except ValueError as exc:
        assert "blocked host" in str(exc)
    else:
        raise AssertionError("host allowlist bypassed")


def test_sinaica_dst_and_border_timezones() -> None:
    tijuana = sinaica_timezone("Baja California", "Tijuana", 32.53, -117.02)
    mexico_city = sinaica_timezone("Ciudad de Mexico", "Benito Juarez", 19.43, -99.13)
    juarez = sinaica_timezone("Chihuahua", "Ciudad Juarez", 31.74, -106.49)
    now = datetime(2026, 8, 14, tzinfo=timezone.utc)
    summer = parse_sinaica_hours([{"id": "1", "fecha": "2026-08-13", "hora": 12, "valor": 14}], tijuana, now)
    winter = parse_sinaica_hours([{"id": "1", "fecha": "2026-01-13", "hora": 12, "valor": 14}], tijuana, datetime(2026, 1, 14, tzinfo=timezone.utc))
    capital = parse_sinaica_hours([{"id": "1", "fecha": "2026-08-13", "hora": 12, "valor": 14}], mexico_city, now)
    assert next(iter(summer)) == datetime(2026, 8, 13, 19, tzinfo=timezone.utc)
    assert next(iter(winter)) == datetime(2026, 1, 13, 20, tzinfo=timezone.utc)
    assert next(iter(capital)) == datetime(2026, 8, 13, 18, tzinfo=timezone.utc)
    assert juarez.key == "America/Ciudad_Juarez"


def test_sinaica_kill_switch_and_station_collapse() -> None:
    disabled = type("S", (), {"sinaica_enabled": False, "sinaica_base_url": "https://sinaica.inecc.gob.mx/"})()
    try:
        fetch_sinaica(disabled, datetime(2026, 8, 13, 18, tzinfo=timezone.utc))
    except PermissionError as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("kill switch was ignored")

    settings = type("S", (), {
        "sinaica_enabled": True,
        "sinaica_base_url": "https://sinaica.inecc.gob.mx/",
        "sinaica_stations_url": "https://sinaica.inecc.gob.mx/data.php",
        "sinaica_min_stations": 8,
        "sinaica_concurrency": 2,
        "sinaica_stale_rate": 0.85,
    })()
    html = b'<select id="estacion"><option value="102">Guadalajara</option></select>'
    with patch("ingest.sources.sinaica.fetch", return_value=html):
        try:
            fetch_sinaica(settings, datetime(2026, 8, 13, 18, tzinfo=timezone.utc))
        except ValueError as exc:
            assert "collapsed" in str(exc)
        else:
            raise AssertionError("station collapse was accepted")


def test_sinaica_schema_drift_is_rejected() -> None:
    try:
        parse_sinaica_json("<html><script>var stations = window.stations;</script></html>")
    except ValueError as exc:
        assert "JSON data" in str(exc)
    else:
        raise AssertionError("schema drift was parsed")


def test_sinaica_station_request_failure_is_skipped() -> None:
    settings = type("S", (), {
        "sinaica_rpc_url": "https://sinaica.inecc.gob.mx/lib/libd/cnxn.php",
        "sinaica_graph_url": "https://sinaica.inecc.gob.mx/pags/datGrafs.php",
    })()
    now = datetime(2026, 8, 13, 18, tzinfo=timezone.utc)
    station = {"id": "102", "name": "Guadalajara", "agency": "SIMAJ", "lat": 20.67, "lon": -103.35, "zone": sinaica_timezone("Jalisco", "Guadalajara", 20.67, -103.35)}
    with patch("ingest.sources.sinaica.fetch", side_effect=RuntimeError("timeout")):
        assert _station_metadata(settings, "102", "Guadalajara") is None
        monitor, stale = _station_hours(settings, station, now)
    assert monitor is None
    assert stale is True


def test_sinaica_parallel_metadata_skips_ineligible_station() -> None:
    settings = type("S", (), {
        "sinaica_enabled": True,
        "sinaica_base_url": "https://sinaica.inecc.gob.mx/",
        "sinaica_stations_url": "https://sinaica.inecc.gob.mx/data.php",
        "sinaica_rpc_url": "https://sinaica.inecc.gob.mx/lib/libd/cnxn.php",
        "sinaica_graph_url": "https://sinaica.inecc.gob.mx/pags/datGrafs.php",
        "sinaica_min_stations": 1,
        "sinaica_concurrency": 2,
        "sinaica_stale_rate": 0.85,
    })()
    now = datetime(2026, 8, 13, 18, tzinfo=timezone.utc)
    html = (
        b'<select id="estacion"><option value="102">Guadalajara</option>'
        b'<option value="103">Ineligible</option>'
        b'<option value="102">Guadalajara duplicate</option></select>'
    )
    station = {
        "id": "102",
        "name": "Guadalajara",
        "agency": "SIMAJ",
        "lat": 20.67,
        "lon": -103.35,
        "zone": sinaica_timezone("Jalisco", "Guadalajara", 20.67, -103.35),
    }
    monitor = {"id": "sinaica:102"}

    metadata_ids: list[str] = []
    hour_ids: list[str] = []

    def metadata(_settings, identifier, _fallback_name):
        metadata_ids.append(identifier)
        return station if identifier == "102" else None

    def hours(_settings, item, _now):
        hour_ids.append(item["id"])
        return monitor, False

    with (
        patch("ingest.sources.sinaica.fetch", return_value=html),
        patch("ingest.sources.sinaica._station_metadata", side_effect=metadata),
        patch("ingest.sources.sinaica._station_hours", side_effect=hours),
    ):
        assert fetch_sinaica(settings, now) == [monitor]
    assert sorted(metadata_ids) == ["102", "103"]
    assert hour_ids == ["102"]


def test_airnow_tiles_overlap_at_a_concurrency_barrier() -> None:
    barrier = threading.Barrier(4, timeout=2)
    now = datetime(2026, 8, 13, 18, tzinfo=timezone.utc)
    settings = type("S", (), {
        "airnow_api_key": "key",
        "airnow_base_url": "https://www.airnowapi.org/aq/data/",
    })()

    def fake_fetch(url, **kwargs):
        barrier.wait()
        bbox = kwargs["params"]["BBOX"]
        payload = [{
            "Parameter": "PM2.5",
            "Latitude": 47.6,
            "Longitude": -122.3,
            "AQI": 40,
            "Value": 10,
            "Unit": "UG/M3",
            "UTC": "2026-08-13T17:00:00Z",
            "FullAQSCode": bbox,
            "SiteName": "Tile",
            "AgencyName": "AirNow",
            "Category": {"Name": "Good"},
        }]
        return json.dumps(payload).encode()

    with patch("ingest.sources.airnow.fetch", side_effect=fake_fetch):
        monitors = fetch_airnow(settings, now)
    assert len(monitors) == 8


def test_sinaica_reuses_cached_station_metadata(tmp_path: Path) -> None:
    store = LocalFrameStore(tmp_path)
    now = datetime(2026, 8, 13, 18, tzinfo=timezone.utc)
    settings = type("S", (), {
        "sinaica_enabled": True,
        "sinaica_base_url": "https://sinaica.inecc.gob.mx/",
        "sinaica_stations_url": "https://sinaica.inecc.gob.mx/data.php",
        "sinaica_rpc_url": "https://sinaica.inecc.gob.mx/lib/libd/cnxn.php",
        "sinaica_graph_url": "https://sinaica.inecc.gob.mx/pags/datGrafs.php",
        "sinaica_min_stations": 1,
        "sinaica_concurrency": 1,
        "sinaica_stale_rate": 0.85,
    })()
    html = b'<select id="estacion"><option value="102">Guadalajara</option></select>'
    methods: list[str] = []

    def fake_fetch(url, **kwargs):
        data = kwargs.get("data") or {}
        method = data.get("metodo")
        if method:
            methods.append(method)
            if method == "getParamsPorEstAjax":
                return json.dumps([{"id": "PM2.5"}]).encode()
            if method == "infoEstacion":
                return json.dumps({
                    "lat": 20.67,
                    "long": -103.35,
                    "edo": "Jalisco",
                    "munc": "Guadalajara",
                    "nombre": "Guadalajara",
                    "redNom": "SIMAJ",
                }).encode()
        if "datGrafs" in url:
            return json.dumps([
                {"valor": 14, "fecha": "2026-08-13", "hora": 11},
                {"valor": 15, "fecha": "2026-08-13", "hora": 12},
            ]).encode()
        return html

    with patch("ingest.sources.sinaica.fetch", side_effect=fake_fetch):
        first = fetch_sinaica(settings, now, store=store)
        second = fetch_sinaica(settings, now, store=store)
    assert first and second
    assert methods.count("getParamsPorEstAjax") == 1
    assert methods.count("infoEstacion") == 1


def test_sinaica_cache_failure_falls_back_to_metadata_refresh() -> None:
    now = datetime(2026, 8, 13, 18, tzinfo=timezone.utc)
    settings = type("S", (), {
        "sinaica_enabled": True,
        "sinaica_base_url": "https://sinaica.inecc.gob.mx/",
        "sinaica_stations_url": "https://sinaica.inecc.gob.mx/data.php",
        "sinaica_rpc_url": "https://sinaica.inecc.gob.mx/lib/libd/cnxn.php",
        "sinaica_graph_url": "https://sinaica.inecc.gob.mx/pags/datGrafs.php",
        "sinaica_min_stations": 1,
        "sinaica_concurrency": 1,
        "sinaica_stale_rate": 0.85,
    })()
    html = b'<select id="estacion"><option value="102">Guadalajara</option></select>'

    class BrokenStore:
        def get_text(self, pathname: str) -> str | None:
            raise RuntimeError("corrupt")

        def put_json(self, *args, **kwargs):
            raise RuntimeError("write fail")

    def fake_fetch(url, **kwargs):
        data = kwargs.get("data") or {}
        method = data.get("metodo")
        if method == "getParamsPorEstAjax":
            return json.dumps([{"id": "PM2.5"}]).encode()
        if method == "infoEstacion":
            return json.dumps({
                "lat": 20.67,
                "long": -103.35,
                "edo": "Jalisco",
                "munc": "Guadalajara",
                "nombre": "Guadalajara",
                "redNom": "SIMAJ",
            }).encode()
        if "datGrafs" in url:
            return json.dumps([
                {"valor": 14, "fecha": "2026-08-13", "hora": 11},
                {"valor": 15, "fecha": "2026-08-13", "hora": 12},
            ]).encode()
        return html

    with patch("ingest.sources.sinaica.fetch", side_effect=fake_fetch):
        monitors = fetch_sinaica(settings, now, store=BrokenStore())
    assert monitors[0]["id"] == "sinaica:102"
