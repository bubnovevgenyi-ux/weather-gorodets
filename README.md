# Weather Gorodets — Python + Docker

Сервис получает прогноз для Городца с Gismeteo, нормализует данные и формирует PNG-карточки через Playwright/Chromium.

## Форматы

| Тип | Endpoint | Представление |
|---|---|---|
| Сегодня | `/html/hourly-today` и `/render/hourly-today.png` | Временные точки на текущий день |
| Сегодня + завтра | `/html/hourly-2days` и `/render/hourly-2days.png` | Временные точки на два календарных дня |
| 3 дня | `/html/daily-3days` и `/render/daily-3days.png` | Дневные сводки |
| 10 дней | `/html/daily-10days` и `/render/daily-10days.png` | Дневные сводки |

JSON доступен по аналогичным адресам `/api/forecast/{kind}`, например `/api/forecast/daily-10days`.

`GET /render/{kind}.png?refresh=true` принудительно обновляет данные, не используя кэш. `POST /render/all` формирует все четыре изображения. Готовые файлы HTML и PNG сохраняются в `./output`.

## Запуск

```bash
cp .env.example .env
docker compose up -d --build
```

Откройте:

- http://localhost:8080 — стартовая страница;
- http://localhost:8080/docs — Swagger UI;
- http://localhost:8080/health — проверка сервиса.

Получить все изображения:

```bash
curl -X POST http://localhost:8080/render/all
```

Или получить одно изображение:

```bash
curl -o gorodets-10days.png \
  http://localhost:8080/render/daily-10days.png?refresh=true
```

## Важные особенности

1. Почасовые страницы Gismeteo на практике могут предоставлять точки не каждый час, а с интервалом, определённым самим источником. Сервис выводит существующие временные точки и **не интерполирует** отсутствующие часы.
2. Дневной прогноз берётся со страницы `/2-weeks/`, содержащей ежедневные значения; для карточки 3 дней берутся первые 3 дня, для десятидневной — первые 10.
3. Кэш по умолчанию — 15 минут (`CACHE_TTL_SECONDS`).
4. Парсер использует CSS-селекторы текущей разметки Gismeteo. Если сайт изменит HTML, запрос вернёт диагностическую ошибку; полученная страница сохраняется в `./debug/`.
5. Структура и классы HTML-селекторов вынесены в `GismeteoClient` в `app/gismeteo.py`. Они ориентированы на разметку страниц прогноза Gismeteo и должны проверяться после изменений сайта.
6. Для просмотра сохранённого исходного HTML:
   - `/debug/source/gismeteo_today.html`
   - `/debug/source/gismeteo_tomorrow.html`
   - `/debug/source/gismeteo_2weeks.html`

   Диагностические адреса доступны при `DEBUG_ENABLED=true`. Не публикуйте их наружу без необходимости.

## Состав

```text
app/
  main.py          FastAPI endpoints, caching
  gismeteo.py      загрузка HTML и извлечение рядов прогноза
  models.py        типизированная модель прогноза
  render.py        HTML rendering и PNG через Playwright
  templates/       четыре HTML-макета и общие стили
output/            готовые PNG
 debug/             исходные HTML для диагностики
```

## Проверка парсера после первого запуска

Сначала запросите JSON:

```bash
curl http://localhost:8080/api/forecast/hourly-today
curl http://localhost:8080/api/forecast/daily-10days
```

Проверьте:
- `hourly` содержит реальные даты и время из title-атрибутов Gismeteo;
- `daily` содержит ожидаемое количество дат;
- температура, ветер, давление и осадки не перепутаны между временными слотами.

Затем откройте PNG endpoints. При ошибке парсинга возьмите соответствующий файл из `./debug/` — CSS-селекторы изолированы в `app/gismeteo.py` и корректируются в одном месте.

## Часовой пояс

По умолчанию используется `Europe/Moscow`, поскольку Городец находится в Нижегородской области. Его можно изменить через `WEATHER_TIMEZONE`.
