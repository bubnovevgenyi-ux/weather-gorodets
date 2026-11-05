from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    city_name: str = os.getenv("CITY_NAME", "Городец")
    city_region: str = os.getenv("CITY_REGION", "Нижегородская область")
    city_id: str = os.getenv("GISMETEO_CITY_ID", "4334")
    timezone: str = os.getenv("WEATHER_TIMEZONE", "Europe/Moscow")
    base_url: str = os.getenv("GISMETEO_BASE_URL", "https://www.gismeteo.ru")
    cache_ttl_seconds: int = int(os.getenv("CACHE_TTL_SECONDS", "900"))
    request_timeout_seconds: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "35"))
    browser_timeout_ms: int = int(os.getenv("BROWSER_TIMEOUT_MS", "45000"))
    output_dir: Path = Path(os.getenv("OUTPUT_DIR", "/app/output"))
    debug_dir: Path = Path(os.getenv("DEBUG_DIR", "/app/debug"))
    debug_enabled: bool = os.getenv("DEBUG_ENABLED", "true").lower() in {"1", "true", "yes"}
    user_agent: str = os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    )


settings = Settings()
settings.output_dir.mkdir(parents=True, exist_ok=True)
settings.debug_dir.mkdir(parents=True, exist_ok=True)
