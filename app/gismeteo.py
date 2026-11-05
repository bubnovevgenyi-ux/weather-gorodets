from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

from .config import settings
from .models import DailyPoint, ForecastPoint

MONTHS = {
    "янв": 1, "января": 1,
    "фев": 2, "февраля": 2,
    "мар": 3, "марта": 3,
    "апр": 4, "апреля": 4,
    "мая": 5, "май": 5,
    "июн": 6, "июня": 6,
    "июл": 7, "июля": 7,
    "авг": 8, "августа": 8,
    "сен": 9, "сентября": 9,
    "окт": 10, "октября": 10,
    "ноя": 11, "ноября": 11,
    "дек": 12, "декабря": 12,
}
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def _text(node: Any) -> str:
    return " ".join(node.stripped_strings) if node else ""


def _number(value: str | None) -> float | None:
    if not value:
        return None
    value = value.replace("−", "-").replace(",", ".")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", value)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _first_attr(nodes: list[Any], attr: str) -> list[str]:
    return [str(node.get(attr, "")).strip() for node in nodes]


def _parse_datetime_title(value: str) -> datetime | None:
    """Parse Gismeteo's title timestamps. Explicit UTC timestamps are converted to Moscow time."""
    value = value.strip()
    iso_match = re.search(r"(20\d{2}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::(\d{2}))?", value)
    if iso_match:
        stamp = f"{iso_match.group(1)} {iso_match.group(2)}:{iso_match.group(3) or '00'}"
        parsed = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
        if "UTC" in value.upper() or "GMT" in value.upper():
            parsed = parsed.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(settings.timezone))
        else:
            parsed = parsed.replace(tzinfo=ZoneInfo(settings.timezone))
        return parsed

    # Numeric Russian date forms: "05.11.2026, 03:00" or "05/11, 03:00".
    numeric = re.search(r"(\d{1,2})[./](\d{1,2})(?:[./](20\d{2}))?[, T]+.*?(\d{1,2}):(\d{2})", value)
    if numeric:
        day, month = int(numeric.group(1)), int(numeric.group(2))
        year = int(numeric.group(3)) if numeric.group(3) else datetime.now(ZoneInfo(settings.timezone)).year
        now_date = datetime.now(ZoneInfo(settings.timezone)).date()
        if not numeric.group(3) and month == 1 and now_date.month == 12 and day < now_date.day:
            year += 1
        try:
            return datetime(year, month, day, int(numeric.group(4)), int(numeric.group(5)), tzinfo=ZoneInfo(settings.timezone))
        except ValueError:
            pass

    # Alternative date forms seen in title attributes: "5 нояб, 03:00".
    m = re.search(r"(\d{1,2})\s+([А-Яа-яЁё]+)[, ]+.*?(\d{1,2}):(\d{2})", value)
    if m:
        month_token = m.group(2).lower()[:3]
        month = MONTHS.get(month_token) or MONTHS.get(m.group(2).lower())
        if month:
            year = datetime.now(ZoneInfo(settings.timezone)).year
            return datetime(year, month, int(m.group(1)), int(m.group(3)), int(m.group(4)), tzinfo=ZoneInfo(settings.timezone))
    return None


def _parse_russian_date(value: str, year_hint: int | None = None) -> date | None:
    value = value.replace("ё", "е").lower().strip()
    m = re.search(r"(\d{1,2})\s+([а-я]+)", value)
    if not m:
        # Some layouts render a full text date in a title attribute.
        iso = re.search(r"(20\d{2})-(\d{2})-(\d{2})", value)
        if iso:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        return None
    day = int(m.group(1))
    month_word = m.group(2)
    month = MONTHS.get(month_word) or MONTHS.get(month_word[:3])
    if not month:
        return None
    year = year_hint or datetime.now(ZoneInfo(settings.timezone)).year
    now_date = datetime.now(ZoneInfo(settings.timezone)).date()
    # Correct year boundary when the forecast starts in December and extends to January.
    if month == 1 and now_date.month == 12 and day < now_date.day:
        year += 1
    elif month == 12 and now_date.month == 1 and day > now_date.day:
        year -= 1
    try:
        return date(year, month, day)
    except ValueError:
        return None


class GismeteoClient:
    """Reads the public page markup. CSS selectors are isolated here for easy maintenance."""

    BASE = f"{settings.base_url}/weather-gorodets-{settings.city_id}/"
    HOURLY_SELECTORS = {
        "times": 'div[data-stat-value*="weather"] div.widget-row-time div.row-item',
        "temperature": 'div[data-stat-value*="weather"] div.widget-row-chart-temperature div.value span.unit_temperature_c',
        "pressure": 'div[data-stat-value*="pressure"] div.widget-row-chart-pressure div.value span.unit_pressure_mm_hg_atm',
        "wind_speed": 'div[data-stat-value*="wind"] div.widget-row-wind-speed div.row-item span.unit_wind_m_s',
        "wind_gust": 'div[data-stat-value*="wind"] div.widget-row-wind-gust div.row-item span.unit_wind_m_s',
        "wind_direction": 'div[data-stat-value*="wind"] div.widget-row-wind-direction div.row-item div.direction',
        "precipitation": 'div[data-stat-value*="weather"] div.widget-row-precipitation-bars div.row-item div.item-unit',
        "humidity": 'div[data-stat-value*="humidity"] div.widget-row-humidity div.row-item',
        "summary": 'div[data-stat-value*="weather"] div.widget-row-icon div.row-item div.tooltip',
    }
    DAILY_SELECTORS = {
        "dates": 'div[data-stat-value*="weather"] div.widget-row-days-date div.date',
        "tmax": 'div[data-stat-value*="weather"] div.widget-row-chart-temperature div.value div.maxt span.unit_temperature_c',
        "tmin": 'div[data-stat-value*="weather"] div.widget-row-chart-temperature div.value div.mint span.unit_temperature_c',
        "tavg": 'div[data-stat-value*="temperature-avg"] div.value span.unit_temperature_c',
        "pressure": 'div[data-stat-value*="pressure"] div.widget-row-chart-pressure div.value div.maxt span.unit_pressure_mm_hg_atm',
        "wind_speed": 'div[data-stat-value*="wind"] div.widget-row-wind-speed div.row-item span.unit_wind_m_s',
        "wind_gust": 'div[data-stat-value*="wind"] div.widget-row-wind-gust div.row-item span.unit_wind_m_s',
        "wind_direction": 'div[data-stat-value*="wind"] div.widget-row-wind-direction div.row-item div.direction',
        "precipitation": 'div[data-stat-value*="weather"] div.widget-row-precipitation-bars div.row-item div.item-unit',
        "humidity": 'div[data-stat-value*="humidity"] div.widget-row-humidity div.row-item',
        "summary": 'div[data-stat-value*="weather"] div.widget-row-icon div.row-item div.tooltip',
    }

    def __init__(self) -> None:
        self.tz = ZoneInfo(settings.timezone)

    async def _get_html(self, path: str, debug_name: str) -> str:
        """Load the forecast in Chromium so JavaScript-rendered rows are present in the DOM."""
        url = f"{self.BASE}{path}"
        debug_path = settings.debug_dir / f"{debug_name}.html"
        browser_error: Exception | None = None

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                try:
                    context = await browser.new_context(
                        locale="ru-RU",
                        timezone_id=settings.timezone,
                        user_agent=settings.user_agent,
                        viewport={"width": 1440, "height": 1200},
                    )
                    page = await context.new_page()
                    response = await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=settings.browser_timeout_ms,
                    )
                    # The chart is sometimes hydrated after the first HTML response.
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10_000)
                    except PlaywrightTimeoutError:
                        pass
                    try:
                        await page.wait_for_selector(
                            '.widget-row-time, [class*="row-time"], [data-stat-value]',
                            timeout=8_000,
                        )
                    except PlaywrightTimeoutError:
                        # Save the rendered DOM anyway; diagnostics below will explain what is missing.
                        pass
                    await page.wait_for_timeout(1_000)
                    html = await page.content()
                    status = response.status if response else None
                    if len(html) < 3_000:
                        raise RuntimeError(
                            f"Chromium получил слишком короткую страницу ({len(html)} байт), HTTP {status}"
                        )
                    debug_path.write_text(html, encoding="utf-8")
                    return html
                finally:
                    await browser.close()
        except Exception as exc:
            browser_error = exc

        # Keep an HTTP fallback for hosts where Chromium navigation is unavailable.
        headers = {
            "User-Agent": settings.user_agent,
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": settings.base_url + "/",
        }
        last_error: Exception | None = None
        try:
            async with httpx.AsyncClient(
                headers=headers,
                timeout=settings.request_timeout_seconds,
                follow_redirects=True,
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                html = response.text
                debug_path.write_text(html, encoding="utf-8")
                if len(html) < 3_000:
                    raise RuntimeError(f"Получена слишком короткая HTML-страница ({len(html)} байт)")
                return html
        except Exception as exc:
            last_error = exc

        raise RuntimeError(
            f"Не удалось получить страницу Gismeteo {url}. "
            f"Chromium: {browser_error}; HTTP: {last_error}. "
            f"Диагностика сохранена в {debug_path}"
        ) from last_error

    @staticmethod
    def _values(soup: BeautifulSoup, selector: str, attr: str | None = None) -> list[str]:
        result = []
        for node in soup.select(selector):
            value = node.get(attr, "") if attr else _text(node)
            result.append(str(value).strip())
        return result

    def _parse_hourly_page(self, html: str, label: str) -> list[ForecastPoint]:
        soup = BeautifulSoup(html, "lxml")
        raw_times = [
            value for value in self._values(soup, self.HOURLY_SELECTORS["times"], "title")
            if _parse_datetime_title(value) is not None
        ]
        if not raw_times:
            # Fallback: inspect title/time attributes when Gismeteo changes row wrappers or class names.
            discovered: list[str] = []
            seen: set[str] = set()
            candidates = soup.select("[title], [datetime], [data-time], [data-date]")
            for node in candidates:
                title = str(
                    node.get("title")
                    or node.get("datetime")
                    or node.get("data-time")
                    or node.get("data-date")
                    or ""
                ).strip()
                if not title or _parse_datetime_title(title) is None:
                    continue
                classes = " ".join(node.get("class", []))
                parent_classes = " ".join(node.parent.get("class", [])) if node.parent else ""
                context = f"{classes} {parent_classes}".lower()
                # Prefer nodes from a time/date row; if classes have been renamed,
                # retain all parseable timestamps as the final fallback.
                if any(token in context for token in ("time", "row-item", "date")) and title not in seen:
                    seen.add(title)
                    discovered.append(title)

            if not discovered:
                for node in candidates:
                    title = str(
                        node.get("title")
                        or node.get("datetime")
                        or node.get("data-time")
                        or node.get("data-date")
                        or ""
                    ).strip()
                    if title and _parse_datetime_title(title) is not None and title not in seen:
                        seen.add(title)
                        discovered.append(title)

            raw_times = discovered

        if not raw_times:
            data_stat_values = sorted({
                str(node.get("data-stat-value", "")).strip()
                for node in soup.select("[data-stat-value]")
                if node.get("data-stat-value")
            })
            class_hints = sorted({
                cls
                for node in soup.select('[class*="time"], [class*="date"], [class*="widget-row"]')
                for cls in node.get("class", [])
            })
            page_title = _text(soup.title)
            body_sample = _text(soup.body)[:700] if soup.body else _text(soup)[:700]
            raise RuntimeError(
                f"На странице {label} не найден временной ряд. "
                f"Заголовок: {page_title!r}; data-stat-value: {data_stat_values[:30]}; "
                f"похожие классы: {class_hints[:40]}; текст страницы: {body_sample!r}. "
                "Сохранён HTML в /app/debug."
            )
        times = [_parse_datetime_title(value) for value in raw_times]
        if any(item is None for item in times):
            unresolved = [raw_times[i] for i, item in enumerate(times) if item is None]
            raise RuntimeError(f"Не удалось разобрать время в title: {unresolved[:3]}")

        values = {
            "temperature": self._values(soup, self.HOURLY_SELECTORS["temperature"]),
            "pressure": self._values(soup, self.HOURLY_SELECTORS["pressure"]),
            "wind_speed": self._values(soup, self.HOURLY_SELECTORS["wind_speed"]),
            "wind_gust": self._values(soup, self.HOURLY_SELECTORS["wind_gust"]),
            "wind_direction": self._values(soup, self.HOURLY_SELECTORS["wind_direction"]),
            "precipitation": self._values(soup, self.HOURLY_SELECTORS["precipitation"]),
            "humidity": self._values(soup, self.HOURLY_SELECTORS["humidity"]),
            "summary": self._values(soup, self.HOURLY_SELECTORS["summary"], "data-text"),
        }
        if not any(_number(value) is not None for value in values["temperature"]):
            data_stat_values = sorted({
                str(node.get("data-stat-value", "")).strip()
                for node in soup.select("[data-stat-value]")
                if node.get("data-stat-value")
            })
            match_counts = {key: len(items) for key, items in values.items()}
            raise RuntimeError(
                f"Временные метки на странице {label} найдены ({len(times)}), "
                f"но значения температуры не извлечены. Количество элементов по селекторам: "
                f"{match_counts}; data-stat-value: {data_stat_values[:30]}. "
                "HTML сохранён в /app/debug для диагностики."
            )

        points: list[ForecastPoint] = []
        for i, dt in enumerate(times):
            assert dt is not None
            points.append(ForecastPoint(
                datetime=dt,
                temperature_c=_number(_at(values, "temperature", i)),
                pressure_mmhg=_number(_at(values, "pressure", i)),
                wind_speed_ms=_number(_at(values, "wind_speed", i)),
                wind_gust_ms=_number(_at(values, "wind_gust", i)),
                wind_direction=_at(values, "wind_direction", i) or None,
                precipitation_mm=_number(_at(values, "precipitation", i)),
                humidity_percent=_number(_at(values, "humidity", i)),
                summary=_at(values, "summary", i) or None,
            ))
        return points

    async def hourly(self, days: int) -> list[ForecastPoint]:
        today_html, tomorrow_html = await asyncio.gather(
            self._get_html("", "gismeteo_today"),
            self._get_html("tomorrow/", "gismeteo_tomorrow"),
        )
        now = datetime.now(self.tz)
        points = self._parse_hourly_page(today_html, "сегодня") + self._parse_hourly_page(tomorrow_html, "завтра")
        # The page may overlap the day boundary. Keep unique timestamps and only forecast from now.
        unique: dict[str, ForecastPoint] = {}
        for point in points:
            if point.datetime >= now.replace(minute=0, second=0, microsecond=0):
                unique[point.datetime.isoformat()] = point
        points = sorted(unique.values(), key=lambda item: item.datetime)
        end = (now.date() + timedelta(days=days - 1))
        points = [item for item in points if item.datetime.date() <= end]
        if days == 2:
            # Keep source-provided timestamps only. No synthetic 1-hour interpolation.
            return points
        return [item for item in points if item.datetime.date() == now.date()]

    def _parse_daily(self, html: str) -> list[DailyPoint]:
        soup = BeautifulSoup(html, "lxml")
        raw_dates = self._values(soup, self.DAILY_SELECTORS["dates"])
        if not raw_dates:
            raise RuntimeError(
                "На странице долгосрочного прогноза не найдены даты. "
                "Возможно, Gismeteo изменил HTML-разметку. Сохранён HTML в /app/debug."
            )
        dates = [_parse_russian_date(value) for value in raw_dates]
        values = {
            "tmax": self._values(soup, self.DAILY_SELECTORS["tmax"]),
            "tmin": self._values(soup, self.DAILY_SELECTORS["tmin"]),
            "tavg": self._values(soup, self.DAILY_SELECTORS["tavg"]),
            "pressure": self._values(soup, self.DAILY_SELECTORS["pressure"]),
            "wind_speed": self._values(soup, self.DAILY_SELECTORS["wind_speed"]),
            "wind_gust": self._values(soup, self.DAILY_SELECTORS["wind_gust"]),
            "wind_direction": self._values(soup, self.DAILY_SELECTORS["wind_direction"]),
            "precipitation": self._values(soup, self.DAILY_SELECTORS["precipitation"]),
            "humidity": self._values(soup, self.DAILY_SELECTORS["humidity"]),
            "summary": self._values(soup, self.DAILY_SELECTORS["summary"], "data-text"),
        }
        result: list[DailyPoint] = []
        for i, day in enumerate(dates):
            if day is None:
                continue
            result.append(DailyPoint(
                date=day.isoformat(),
                weekday=WEEKDAYS[day.weekday()],
                temperature_min_c=_number(_at(values, "tmin", i)),
                temperature_max_c=_number(_at(values, "tmax", i)),
                temperature_avg_c=_number(_at(values, "tavg", i)),
                summary=_at(values, "summary", i) or None,
                wind_direction=_at(values, "wind_direction", i) or None,
                wind_speed_ms=_number(_at(values, "wind_speed", i)),
                wind_gust_ms=_number(_at(values, "wind_gust", i)),
                precipitation_mm=_number(_at(values, "precipitation", i)),
                pressure_mmhg=_number(_at(values, "pressure", i)),
                humidity_percent=_number(_at(values, "humidity", i)),
            ))
        return result

    async def daily(self, days: int) -> list[DailyPoint]:
        # Gismeteo's 2-weeks page contains one daily summary per date.
        html = await self._get_html("2-weeks/", "gismeteo_2weeks")
        result = self._parse_daily(html)
        result = sorted(result, key=lambda item: item.date)
        if len(result) < days:
            raise RuntimeError(f"Получено только {len(result)} дневных записей, ожидалось не менее {days}")
        return result[:days]


def _at(data: dict[str, list[str]], key: str, index: int) -> str:
    values = data.get(key, [])
    return values[index] if index < len(values) else ""
