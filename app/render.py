from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.async_api import async_playwright

from .config import settings
from .models import DailyPoint, ForecastPoint

TEMPLATE_DIR = Path(__file__).parent / "templates"
RU_WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
)
_browser_lock = asyncio.Lock()


def weather_emoji(summary: str | None) -> str:
    text = (summary or "").lower()
    if any(word in text for word in ("гроза", "гром")):
        return "⛈️"
    if any(word in text for word in ("снег", "метель")):
        return "🌨️"
    if any(word in text for word in ("дожд", "ливень")):
        return "🌧️"
    if "туман" in text:
        return "🌫️"
    if any(word in text for word in ("ясно", "солнечно")):
        return "☀️"
    if any(word in text for word in ("переменная", "прояснен")):
        return "🌤️"
    if any(word in text for word in ("облачно", "пасмур")):
        return "☁️"
    return "🌡️"


def fmt(value: float | None, suffix: str = "", empty: str = "—") -> str:
    if value is None:
        return empty
    if value.is_integer():
        return f"{int(value)}{suffix}"
    return f"{value:.1f}{suffix}".replace(".", ",")


def prepare_hourly(points: list[ForecastPoint]) -> list[dict]:
    rows = []
    for point in points:
        rows.append({
            "day_label": point.datetime.strftime("%d.%m"),
            "time": point.datetime.strftime("%H:%M"),
            "weekday": RU_WEEKDAYS_SHORT[point.datetime.weekday()],
            "temperature": fmt(point.temperature_c, "°"),
            "feels": fmt(point.feels_like_c, "°"),
            "summary": point.summary or "Нет описания",
            "emoji": weather_emoji(point.summary),
            "wind": " ".join(part for part in [point.wind_direction, fmt(point.wind_speed_ms, " м/с")] if part) or "—",
            "gust": fmt(point.wind_gust_ms, " м/с"),
            "precip": fmt(point.precipitation_mm, " мм"),
            "pressure": fmt(point.pressure_mmhg, " мм"),
            "humidity": fmt(point.humidity_percent, "%"),
        })
    return rows


def prepare_daily(points: list[DailyPoint]) -> list[dict]:
    rows = []
    for point in points:
        dt = datetime.fromisoformat(point.date)
        rows.append({
            "date": dt.strftime("%d.%m"),
            "weekday": point.weekday.capitalize(),
            "temperature": f"{fmt(point.temperature_min_c, '°')} / {fmt(point.temperature_max_c, '°')}",
            "avg": fmt(point.temperature_avg_c, "°"),
            "summary": point.summary or "Нет описания",
            "emoji": weather_emoji(point.summary),
            "wind": " ".join(part for part in [point.wind_direction, fmt(point.wind_speed_ms, " м/с")] if part) or "—",
            "gust": fmt(point.wind_gust_ms, " м/с"),
            "precip": fmt(point.precipitation_mm, " мм"),
            "pressure": fmt(point.pressure_mmhg, " мм"),
            "humidity": fmt(point.humidity_percent, "%"),
        })
    return rows


def build_html(kind: str, points: list[ForecastPoint] | list[DailyPoint], generated_at: datetime) -> str:
    if kind.startswith("hourly"):
        rows = prepare_hourly(points)  # type: ignore[arg-type]
        filename = "hourly.html" if kind == "hourly-today" else "hourly-2days.html"
    else:
        rows = prepare_daily(points)  # type: ignore[arg-type]
        filename = "daily.html" if kind == "daily-3days" else "daily-10days.html"

    template = env.get_template(filename)
    html = template.render(
        city=settings.city_name,
        region=settings.city_region,
        source_url=f"{settings.base_url}/weather-gorodets-{settings.city_id}/",
        generated_at=generated_at.astimezone(ZoneInfo(settings.timezone)).strftime("%d.%m.%Y в %H:%M"),
        timezone=settings.timezone,
        rows=rows,
        count=len(rows),
    )
    (settings.output_dir / f"weather-{kind}.html").write_text(html, encoding="utf-8")
    return html


async def render_png(kind: str, points: list[ForecastPoint] | list[DailyPoint], generated_at: datetime) -> Path:
    html = build_html(kind, points, generated_at)
    output = settings.output_dir / f"weather-{kind}.png"
    async with _browser_lock:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                viewport={"width": 1600, "height": 1200},
                device_scale_factor=2,
                locale="ru-RU",
                timezone_id=settings.timezone,
            )
            await page.set_content(html, wait_until="networkidle")
            await page.locator(".weather-sheet").screenshot(
                path=str(output),
                animations="disabled",
                timeout=settings.browser_timeout_ms,
            )
            await browser.close()
    return output
