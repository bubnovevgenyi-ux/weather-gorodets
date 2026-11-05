from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .config import settings
from .gismeteo import GismeteoClient
from .models import ForecastResponse
from .render import build_html, render_png

app = FastAPI(
    title="Gorodets Weather Images",
    version="0.1.0",
    description="Получение прогноза Gismeteo и формирование PNG-карточек.",
)
client = GismeteoClient()
_cache: dict[str, tuple[float, ForecastResponse]] = {}
_cache_lock = asyncio.Lock()

KINDS = {
    "hourly-today": ("hourly", 1),
    "hourly-2days": ("hourly", 2),
    "daily-3days": ("daily", 3),
    "daily-10days": ("daily", 10),
}


def now_local() -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


async def get_forecast(kind: str, force: bool = False) -> ForecastResponse:
    if kind not in KINDS:
        raise HTTPException(status_code=404, detail=f"Неизвестный формат: {kind}")
    async with _cache_lock:
        cached = _cache.get(kind)
        if not force and cached and (time.monotonic() - cached[0] < settings.cache_ttl_seconds):
            return cached[1]

    family, count = KINDS[kind]
    generated_at = now_local()
    try:
        if family == "hourly":
            hourly = await client.hourly(count)
            if not hourly:
                raise RuntimeError("Источник не вернул ни одной будущей временной точки")
            response = ForecastResponse(
                city=settings.city_name,
                region=settings.city_region,
                source_url=f"{settings.base_url}/weather-gorodets-{settings.city_id}/",
                generated_at=generated_at,
                timezone=settings.timezone,
                kind=kind,
                interval_note="Показаны временные точки с исходным шагом Gismeteo; отсутствующие часы не интерполируются.",
                hourly=hourly,
            )
        else:
            daily = await client.daily(count)
            response = ForecastResponse(
                city=settings.city_name,
                region=settings.city_region,
                source_url=f"{settings.base_url}/weather-gorodets-{settings.city_id}/2-weeks/",
                generated_at=generated_at,
                timezone=settings.timezone,
                kind=kind,
                daily=daily,
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка получения прогноза: {exc}") from exc

    async with _cache_lock:
        _cache[kind] = (time.monotonic(), response)
    return response


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    cards = "".join(
        f'<a class="item" href="/render/{kind}.png"><b>{label}</b><span>PNG</span></a>'
        for kind, label in [
            ("hourly-today", "Сегодня · по временным точкам"),
            ("hourly-2days", "Сегодня и завтра · по временным точкам"),
            ("daily-3days", "3 дня · дневная сводка"),
            ("daily-10days", "10 дней · дневная сводка"),
        ]
    )
    return f"""<!doctype html><html lang='ru'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>Погода · Городец</title><style>
    *{{box-sizing:border-box}}body{{margin:0;background:#eef3f9;color:#15253c;font:16px Arial,sans-serif;padding:36px}}
    main{{max-width:900px;margin:auto}}h1{{font-size:32px;margin:0 0 8px}}p{{color:#617087;line-height:1.5}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;margin-top:24px}}
    .item{{background:#fff;border:1px solid #dce5ef;border-radius:16px;padding:22px;text-decoration:none;color:#15253c;display:flex;justify-content:space-between;gap:12px;box-shadow:0 6px 20px #1c35550b}}
    .item span{{color:#5177a7;font-weight:700}}.links{{margin-top:28px;font-size:14px}}a{{color:#235f9f}}
    </style><main><h1>Погода в Городце</h1><p>Генератор погодных изображений. Выберите нужный формат — PNG будет сформирован при запросе.</p>
    <div class='grid'>{cards}</div><div class='links'><a href='/docs'>API-документация</a> · <a href='/health'>Состояние сервиса</a></div></main></html>"""


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "city": settings.city_name, "timezone": settings.timezone}


@app.get("/api/forecast/{kind}")
async def api_forecast(kind: str, refresh: bool = False) -> JSONResponse:
    response = await get_forecast(kind, force=refresh)
    return JSONResponse(response.model_dump(mode="json"))


@app.get("/html/{kind}", response_class=HTMLResponse)
async def html_layout(kind: str, refresh: bool = False) -> HTMLResponse:
    forecast = await get_forecast(kind, force=refresh)
    points = forecast.hourly if kind.startswith("hourly") else forecast.daily
    html = build_html(kind, points, forecast.generated_at)
    return HTMLResponse(content=html)


@app.get("/render/{kind}.png")
async def render(kind: str, refresh: bool = False) -> FileResponse:
    forecast = await get_forecast(kind, force=refresh)
    points = forecast.hourly if kind.startswith("hourly") else forecast.daily
    try:
        path = await render_png(kind, points, forecast.generated_at)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка формирования PNG: {exc}") from exc
    return FileResponse(path, media_type="image/png", filename=path.name)


@app.post("/render/all")
async def render_all(refresh: bool = False) -> dict[str, str]:
    result: dict[str, str] = {}
    for kind in KINDS:
        forecast = await get_forecast(kind, force=refresh)
        points = forecast.hourly if kind.startswith("hourly") else forecast.daily
        path = await render_png(kind, points, forecast.generated_at)
        result[kind] = f"/files/{path.name}"
    return result


@app.get("/files/{filename}")
async def files(filename: str) -> FileResponse:
    if Path(filename).name != filename or not filename.endswith((".png", ".html")):
        raise HTTPException(status_code=400, detail="Недопустимое имя файла")
    path = settings.output_dir / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Файл ещё не сформирован")
    media_type = "image/png" if filename.endswith(".png") else "text/html; charset=utf-8"
    return FileResponse(path, media_type=media_type, filename=filename)


@app.get("/debug/source/{name}.html")
async def debug_source(name: str) -> FileResponse:
    if not settings.debug_enabled:
        raise HTTPException(status_code=404, detail="Debug endpoints disabled")
    allowed = {"gismeteo_today", "gismeteo_tomorrow", "gismeteo_2weeks"}
    if name not in allowed:
        raise HTTPException(status_code=404, detail="Unknown debug page")
    path = settings.debug_dir / f"{name}.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Сначала выполните запрос прогноза")
    return FileResponse(path, media_type="text/html; charset=utf-8")
