from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np

from ingest.forecast_palette import KG_PER_UG
from ingest.sources.hrrr import HRRR_NATIVE_PROCESSING_VERSION, decode_hrrr_massden, fetch_hrrr, fetch_hrrr_native_hour, nomads_file, nomads_filter_url, nomads_idx_url, parse_hrrr_index, planned_hours, HrrrCycle


class _FakeEccodes:
    def __init__(self, keys: dict, values: np.ndarray | None = None):
        self._keys = keys
        self._values = np.zeros(4, dtype=np.float32) if values is None else values
        self.released: list[int] = []

    def codes_new_from_message(self, _data: bytes) -> int:
        return 1

    def codes_is_defined(self, _gid: int, key: str) -> bool:
        return key in self._keys

    def codes_get(self, _gid: int, key: str):
        return self._keys[key]

    def codes_get_values(self, _gid: int) -> np.ndarray:
        return self._values

    def codes_release(self, gid: int) -> None:
        self.released.append(gid)


def _grib_keys(*, short_name: str = "UNKNOWN", level: int = 8, type_of_level: str = "heightAboveGround", parameter: tuple[int, int, int] | None = (0, 20, 0)) -> dict:
    keys = {
        "shortName": short_name,
        "level": level,
        "typeOfLevel": type_of_level,
        "Nx": 2,
        "Ny": 2,
        "missingValue": 9999.0,
        "dataDate": "20260814",
        "dataTime": 1200,
        "forecastTime": 6,
    }
    if parameter is not None:
        keys["discipline"], keys["parameterCategory"], keys["parameterNumber"] = parameter
    return keys


def _decode(keys: dict, values: np.ndarray | None = None):
    fake = _FakeEccodes(keys, values)
    with patch.dict(sys.modules, {"eccodes": fake, "eccodeslib": fake}), patch("ingest.sources.hrrr.validate_hrrr_grid"):
        field = decode_hrrr_massden(b"GRIB")
    assert fake.released == [1]
    return field


def test_nomads_urls_request_only_massden_at_8m() -> None:
    run = datetime(2026, 8, 14, 12, tzinfo=timezone.utc)
    url = nomads_filter_url("https://nomads.ncep.noaa.gov", run, 6)
    assert "var_MASSDEN=on" in url
    assert "lev_8_m_above_ground=on" in url
    assert "hrrr.t12z.wrfsfcf06.grib2" in url
    assert "leftlon=-145" in url
    assert nomads_file(run, 18) == "hrrr.t12z.wrfsfcf18.grib2"
    assert nomads_idx_url("https://nomads.ncep.noaa.gov", run, 0).endswith(".idx")


def test_index_requires_massden_and_8m_level() -> None:
    assert parse_hrrr_index("76:45444206:d=2026081400:MASSDEN:8 m above ground:anl:")
    assert not parse_hrrr_index("TMP:2 m above ground")
    assert not parse_hrrr_index("MASSDEN:2 m above ground\nTMP:8 m above ground")


def test_native_hrrr_hour_preserves_grid_values_validity_and_identity() -> None:
    field = _decode(_grib_keys(), np.array([5e-8, 1e-8, 2e-8, 3e-8], dtype=np.float32))
    with patch("ingest.sources.hrrr.fetch_hrrr_grib", return_value=b"GRIB"), patch("ingest.sources.hrrr.decode_hrrr_massden", return_value=field):
        native = fetch_hrrr_native_hour(
            object(),
            "2026-08-14T12:00:00Z",
            "2026-08-14T18:00:00Z",
        )
    assert native.grid == field.grid
    assert np.array_equal(native.values, field.values)
    assert np.array_equal(native.valid, field.valid)
    assert native.model_id == "hrrr"
    assert native.units == "µg/m³"
    assert native.processing_version == HRRR_NATIVE_PROCESSING_VERSION


def test_planned_hours_use_hourly_then_extended_cycle() -> None:
    day = datetime(2026, 8, 14, tzinfo=timezone.utc)
    cases = (
        (day.replace(hour=13), day.replace(hour=12), 20),
        (day.replace(hour=9), day.replace(hour=6), 22),
        (day.replace(hour=12), day.replace(hour=12), 19),
        (day.replace(hour=17), day.replace(hour=12), 24),
        (day.replace(hour=0) + timedelta(days=1), day.replace(hour=18), 25),
        (day.replace(hour=11), day.replace(hour=12), 18),
    )
    for standard_run, extended_run, first_extended_hour in cases:
        standard = HrrrCycle(standard_run, tuple(range(19)), "standard")
        extended = HrrrCycle(extended_run, tuple(range(49)), "extended")
        jobs = planned_hours(standard, extended)
        valid_times = [cycle.run + timedelta(hours=hour) for cycle, hour in jobs]
        assert jobs[:19] == [(standard, hour) for hour in range(19)]
        assert jobs[19] == (extended, first_extended_hour)
        assert jobs[-1] == (extended, 48)
        assert len(valid_times) == len(set(valid_times))
        assert all(right - left == timedelta(hours=1) for left, right in zip(valid_times, valid_times[1:]))

    standard = HrrrCycle(day.replace(hour=13), tuple(range(19)), "standard")
    assert planned_hours(standard, None) == [(standard, hour) for hour in standard.hours]


def test_required_hrrr_window_keeps_full_discovery_metadata() -> None:
    run = datetime(2026, 8, 14, 12, tzinfo=timezone.utc)
    cycle = HrrrCycle(run, (0, 1, 2, 3), "standard")
    required = {
        (run + timedelta(hours=hour)).isoformat().replace("+00:00", "Z")
        for hour in (0, 1)
    }
    reuse = {
        (run.isoformat().replace("+00:00", "Z"), hour): {
            "validTime": (run + timedelta(hours=hour)).isoformat().replace("+00:00", "Z"),
            "modelRun": run.isoformat().replace("+00:00", "Z"),
            "values": np.ones((2, 2), dtype=np.float32),
            "valid": np.ones((2, 2), dtype=bool),
        }
        for hour in (0, 1)
    }
    settings = type("Settings", (), {"hrrr_concurrency": 2})()
    frames, _legend, stats = fetch_hrrr(
        settings,
        run,
        (cycle, None),
        reuse=reuse,
        required_times=required,
        encode_png=False,
    )
    assert len(frames) == 4
    assert [frame.get("values") is not None for frame in frames] == [True, True, False, False]
    assert stats["reused"] == 2


def test_decode_accepts_unknown_or_named_massden_at_8m_and_converts_kg_to_ug() -> None:
    kg = 5 * KG_PER_UG
    values = np.array([kg, 0.0, 9999.0, -1.0], dtype=np.float32)
    for short_name, parameter in (("UNKNOWN", (0, 20, 0)), ("MASSDEN", None), ("mass den", None)):
        field = _decode(_grib_keys(short_name=short_name, parameter=parameter), values)
        assert np.isclose(field.values[0, 0], 5.0)
        assert field.values[0, 1] == 0.0
        assert np.isnan(field.values[1]).all()
        assert tuple(field.valid.ravel()) == (True, True, False, False)
        assert field.model_run == datetime(2026, 8, 14, 12, tzinfo=timezone.utc)
        assert field.valid_time == datetime(2026, 8, 14, 18, tzinfo=timezone.utc)


def test_decode_rejects_unknown_identity_and_non_8m_height() -> None:
    cases = (
        (_grib_keys(short_name="TMP", parameter=(0, 20, 0)), "unexpected HRRR variable TMP"),
        (_grib_keys(short_name="UNKNOWN", parameter=(0, 20, 1)), "unexpected HRRR variable UNKNOWN"),
        (_grib_keys(short_name="UNKNOWN", parameter=(0, 19, 0)), "unexpected HRRR variable UNKNOWN"),
        (_grib_keys(short_name="UNKNOWN", parameter=(1, 20, 0)), "unexpected HRRR variable UNKNOWN"),
        (_grib_keys(short_name="UNKNOWN", parameter=None), "unexpected HRRR variable UNKNOWN"),
        (_grib_keys(short_name="UNKNOWN", level=7), "unexpected HRRR level"),
        (_grib_keys(short_name="UNKNOWN", type_of_level="surface"), "unexpected HRRR level"),
        (_grib_keys(short_name="UNKNOWN", type_of_level="heightAboveSea"), "unexpected HRRR level"),
        (_grib_keys(short_name="MASSDEN", parameter=None, type_of_level="surface"), "unexpected HRRR level"),
        (_grib_keys(short_name="MASSDEN", parameter=None, type_of_level="heightAboveSea"), "unexpected HRRR level"),
    )
    for keys, message in cases:
        try:
            _decode(keys)
        except ValueError as exc:
            assert message in str(exc)
            assert str(exc).startswith("invalid HRRR GRIB2:")
        else:
            raise AssertionError(f"accepted GRIB identity {keys.get('shortName')} {keys.get('level')} {keys.get('typeOfLevel')}")
