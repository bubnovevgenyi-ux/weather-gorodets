from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ForecastPoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    datetime: datetime
    temperature_c: float | None = None
    feels_like_c: float | None = None
    summary: str | None = None
    wind_direction: str | None = None
    wind_speed_ms: float | None = None
    wind_gust_ms: float | None = None
    precipitation_mm: float | None = None
    pressure_mmhg: float | None = None
    humidity_percent: float | None = None


class DailyPoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    date: str
    weekday: str
    temperature_min_c: float | None = None
    temperature_max_c: float | None = None
    temperature_avg_c: float | None = None
    summary: str | None = None
    wind_direction: str | None = None
    wind_speed_ms: float | None = None
    wind_gust_ms: float | None = None
    precipitation_mm: float | None = None
    pressure_mmhg: float | None = None
    humidity_percent: float | None = None


class ForecastResponse(BaseModel):
    city: str
    region: str
    source: str = "Gismeteo"
    source_url: str
    generated_at: datetime
    timezone: str
    kind: Literal["hourly-today", "hourly-2days", "daily-3days", "daily-10days"]
    interval_note: str | None = None
    hourly: list[ForecastPoint] = Field(default_factory=list)
    daily: list[DailyPoint] = Field(default_factory=list)
