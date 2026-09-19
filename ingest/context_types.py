from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, TypedDict

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]

type SourceName = Literal[
    "airnow",
    "bcair",
    "sinaica",
    "aqhi",
    "wfigs",
    "cwfis",
    "firework",
    "hrrr",
]
type SectionName = Literal["air", "fires", "forecast", "hrrr"]
type SourceStatus = Literal["ok", "stale", "error", "unavailable"]


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


type SourceOutcome = SourceSuccess | SourceFailure

type ForecastProviderName = Literal["firework", "hrrr"]


@dataclass(frozen=True, slots=True)
class ProviderStageSuccess:
    provider: ForecastProviderName


@dataclass(frozen=True, slots=True)
class ProviderStageFailure:
    provider: ForecastProviderName
    error: BaseException


type ProviderStageOutcome = ProviderStageSuccess | ProviderStageFailure
