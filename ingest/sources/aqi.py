from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Sequence

AQI_METHOD_PROVIDER = "provider"
AQI_METHOD_NOWCAST = "epa-nowcast-2024"
NOWCAST_HOURS = 12
NOWCAST_MIN_RECENT = 2
TENTH = Decimal("0.1")
PM25_BREAKPOINTS = (
    (Decimal("0.0"), Decimal("9.0"), 0, 50),
    (Decimal("9.1"), Decimal("35.4"), 51, 100),
    (Decimal("35.5"), Decimal("55.4"), 101, 150),
    (Decimal("55.5"), Decimal("125.4"), 151, 200),
    (Decimal("125.5"), Decimal("225.4"), 201, 300),
    (Decimal("225.5"), Decimal("325.4"), 301, 500),
)
LAST_SLOPE_LOW = Decimal("225.5")
LAST_SLOPE_HIGH = Decimal("325.4")
LAST_I_LOW = 301
LAST_I_HIGH = 500


def is_pm25_mass_unit(value: str) -> bool:
    return value.replace("µ", "u").replace("μ", "u").replace("³", "3").replace(" ", "").upper() == "UG/M3"


def aqi_category(aqi: int) -> str:
    if aqi <= 50:
        return "Good"
    if aqi <= 100:
        return "Moderate"
    if aqi <= 150:
        return "Unhealthy for sensitive groups"
    if aqi <= 200:
        return "Unhealthy"
    if aqi <= 300:
        return "Very unhealthy"
    if aqi <= 500:
        return "Hazardous"
    return "Beyond the AQI"


def truncate_tenth(value: Decimal) -> Decimal:
    return value.quantize(TENTH, rounding=ROUND_DOWN)


def nowcast_pm25(hours_newest_first: Sequence[Decimal | None]) -> Decimal | None:
    if len(hours_newest_first) != NOWCAST_HOURS:
        raise ValueError("NowCast requires 12 clock-hour slots")
    recent = hours_newest_first[:3]
    if sum(value is not None for value in recent) < NOWCAST_MIN_RECENT:
        return None
    valid = [value for value in hours_newest_first if value is not None]
    if not valid:
        return None
    maximum = max(valid)
    minimum = min(valid)
    if maximum == 0 and minimum == 0:
        weight = Decimal(1)
    elif maximum == 0:
        weight = Decimal("0.5")
    else:
        weight = max(Decimal("0.5"), Decimal(1) - (maximum - minimum) / maximum)
    numerator = Decimal(0)
    denominator = Decimal(0)
    power = Decimal(1)
    for value in hours_newest_first:
        if value is not None:
            numerator += value * power
            denominator += power
        power *= weight
    if denominator == 0:
        return None
    concentration = numerator / denominator
    if Decimal("-5") < concentration < 0:
        concentration = Decimal(0)
    return truncate_tenth(concentration)


def pm25_aqi(concentration: Decimal) -> int:
    if concentration < 0:
        concentration = Decimal(0)
    truncated = truncate_tenth(concentration)
    for low, high, i_low, i_high in PM25_BREAKPOINTS:
        if truncated <= high:
            scale = Decimal(i_high - i_low) / (high - low)
            raw = scale * (truncated - low) + Decimal(i_low)
            return int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    scale = Decimal(LAST_I_HIGH - LAST_I_LOW) / (LAST_SLOPE_HIGH - LAST_SLOPE_LOW)
    raw = scale * (truncated - LAST_SLOPE_LOW) + Decimal(LAST_I_LOW)
    return int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def clock_hours(series: dict[datetime, Decimal], end: datetime) -> list[Decimal | None]:
    aligned = end.replace(minute=0, second=0, microsecond=0)
    if aligned.tzinfo is None:
        aligned = aligned.replace(tzinfo=timezone.utc)
    hours: list[Decimal | None] = []
    for age in range(NOWCAST_HOURS):
        slot = aligned - timedelta(hours=age)
        hours.append(series.get(slot))
    return hours


def nowcast_aqi(series: dict[datetime, Decimal], end: datetime) -> tuple[int, Decimal] | None:
    concentration = nowcast_pm25(clock_hours(series, end))
    if concentration is None:
        return None
    return pm25_aqi(concentration), concentration
