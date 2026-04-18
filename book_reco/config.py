"""Configuration for book_reco (stdlib-only)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return value if value is not None else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class Settings:
    """Application settings loaded from environment variables."""

    naver_client_id: str = field(default_factory=lambda: _env("NAVER_CLIENT_ID"))
    naver_client_secret: str = field(default_factory=lambda: _env("NAVER_CLIENT_SECRET"))
    data4library_api_key: str = field(default_factory=lambda: _env("DATA4LIBRARY_API_KEY"))
    nlk_api_key: str = field(default_factory=lambda: _env("NLK_API_KEY"))
    saseo_api_key: str = field(
        default_factory=lambda: _env("SASEO_API_KEY") or _env("NLK_API_KEY")
    )
    kbook_timeout_seconds: float = field(default_factory=lambda: _env_float("KBOOK_TIMEOUT_SECONDS", 10.0))
    kbook_max_results: int = field(default_factory=lambda: _env_int("KBOOK_MAX_RESULTS", 10))
    kbook_user_agent: str = field(default_factory=lambda: _env("KBOOK_USER_AGENT", "kbook-reco/0.1.0"))
    kbook_log_level: str = field(default_factory=lambda: _env("KBOOK_LOG_LEVEL", "INFO"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""

    return Settings()


def reset_settings_cache() -> None:
    """Clear the cached settings (used in tests)."""

    get_settings.cache_clear()
