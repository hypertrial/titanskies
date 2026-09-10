from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from ingest.sources.aqi import aqi_category, clock_hours, nowcast_aqi, nowcast_pm25, pm25_aqi


def _dec(values: list[float | None]) -> list[Decimal | None]:
    return [None if value is None else Decimal(str(value)) for value in values]


def test_official_nowcast_example() -> None:
    hours = _dec([21, None, 35, 49.2, 48.6, 53.7, 66.2, 69.2, 64.9, 50, 43, 34.9])
    concentration = nowcast_pm25(hours)
    assert concentration == Decimal("28.4")
    assert pm25_aqi(concentration) == 87


def test_nowcast_preserves_missing_hour_exponents() -> None:
    hours = _dec([10, None, 30] + [None] * 9)
    assert nowcast_pm25(hours) == Decimal("14.0")
    assert pm25_aqi(Decimal("14.0")) == 60


def test_nowcast_requires_two_of_latest_three_clock_hours() -> None:
    assert nowcast_pm25(_dec([10, None, None] + [None] * 9)) is None
    assert nowcast_pm25(_dec([10, 12, None] + [None] * 9)) == Decimal("10.9")


def test_nowcast_all_zero_and_spike() -> None:
    assert nowcast_pm25(_dec([0] * 12)) == Decimal("0.0")
    concentration = nowcast_pm25(_dec([100] + [0] * 11))
    assert concentration == Decimal("50.0")
    assert pm25_aqi(concentration) == 137


def test_pm25_breakpoints_and_beyond_aqi() -> None:
    assert pm25_aqi(Decimal("9.0")) == 50
    assert pm25_aqi(Decimal("9.1")) == 51
    assert pm25_aqi(Decimal("35.4")) == 100
    assert pm25_aqi(Decimal("35.5")) == 101
    assert pm25_aqi(Decimal("55.4")) == 150
    assert pm25_aqi(Decimal("55.5")) == 151
    assert pm25_aqi(Decimal("125.4")) == 200
    assert pm25_aqi(Decimal("125.5")) == 201
    assert pm25_aqi(Decimal("225.4")) == 300
    assert pm25_aqi(Decimal("225.5")) == 301
    assert pm25_aqi(Decimal("325.4")) == 500
    assert pm25_aqi(Decimal("500.0")) == 848
    assert aqi_category(500) == "Hazardous"
    assert aqi_category(848) == "Beyond the AQI"


def test_clock_hours_keep_empty_slots() -> None:
    end = datetime(2026, 8, 13, 18, tzinfo=timezone.utc)
    series = {
        end: Decimal("10"),
        end - timedelta(hours=2): Decimal("30"),
    }
    hours = clock_hours(series, end)
    assert hours[0] == Decimal("10")
    assert hours[1] is None
    assert hours[2] == Decimal("30")
    assert nowcast_aqi(series, end) == (60, Decimal("14.0"))
