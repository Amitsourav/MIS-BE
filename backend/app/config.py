"""Application settings, loaded from environment / .env.

All thresholds and operational knobs live here or in the DB (`targets`) — never
hard-coded into business logic, per the project's non-negotiables.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- MIS DB ---
    mis_database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/mis"
    )

    # --- CRM read-only DSNs (plain asyncpg, no +asyncpg suffix) ---
    fmc_db_url: str | None = None
    av_db_url: str | None = None

    # --- Auth ---
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 720

    # --- Live CRM reads ---
    crm_pool_max_size: int = 5
    crm_query_timeout_sec: float = 30.0
    duplicate_window_days: int = 30

    # --- CORS ---
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # --- App ---
    env: str = "development"
    log_level: str = "INFO"

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"

    def crm_dsn(self, brand: str) -> str | None:
        """Return the read-only DSN for a brand ('fmc' | 'av')."""
        return {"fmc": self.fmc_db_url, "av": self.av_db_url}.get(brand)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
