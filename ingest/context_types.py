from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, TypeAlias, TypedDict

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

SourceName: TypeAlias = Literal[
    "airnow",
    "bcair",
    "sinaica",
    "aqhi",
    "wfigs",
    "cwfis",
    "firework",
    "hrrr",
]
SectionName: TypeAlias = Literal["air", "fires", "forecast", "hrrr"]
SourceStatus: TypeAlias = Literal["ok", "stale", "error", "unavailable"]


class SourceState(TypedDict):
    status: SourceStatus
    checkedAt: str
    observedAt: str | None
    provenance: str
    error: str | None


@dataclass(frozen=True, slots=True)
class SourceSuccess:
    payload: dict[str, Any]
    observed_at: datetime
    partial_error: str | None = None
    perimeter_observed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SourceFailure:
    error: Exception


SourceOutcome: TypeAlias = SourceSuccess | SourceFailure

ForecastProviderName: TypeAlias = Literal["firework", "hrrr"]


@dataclass(frozen=True, slots=True)
class ProviderStageSuccess:
    provider: ForecastProviderName


@dataclass(frozen=True, slots=True)
class ProviderStageFailure:
    provider: ForecastProviderName
    error: BaseException


ProviderStageOutcome: TypeAlias = ProviderStageSuccess | ProviderStageFailure
