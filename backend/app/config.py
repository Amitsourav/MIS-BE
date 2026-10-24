"""Application settings, loaded from environment / .env.

All thresholds and operational knobs live here or in the DB (`targets`) — never
hard-coded into business logic, per the project's non-negotiables.
"""
from __future__ import annotations

from decimal import Decimal
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

    # --- CRM HTTP API (partner chat; a key minted with scope "mis") ---
    # The Demo Loans company lives in the FMC CRM but needs its own key.
    fmc_crm_api_url: str | None = None  # e.g. https://be-crm-production.up.railway.app/api/v1
    fmc_crm_api_key: str | None = None
    fmc_demo_crm_api_key: str | None = None
    av_crm_api_url: str | None = None
    av_crm_api_key: str | None = None
    crm_api_timeout_sec: float = 10.0

    # --- Partner payouts ---
    # GST the partner adds on top of what they earn (percent). The CRM's
    # amounts are before GST; the MIS shows earned / pending / balance with it.
    payout_gst_rate: Decimal = Decimal("18")

    # --- Partner invoices made by the MIS: the "Bill to" (FundMyCampus) ---
    # Printed on every partner invoice. Unset name = invoicing is off (503).
    fmc_bill_to_name: str | None = None
    fmc_bill_to_gstin: str | None = None
    fmc_bill_to_address: str | None = None  # lines separated by "|"
    fmc_bill_to_state_code: str | None = None  # 2-digit GST state code, e.g. "07"

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

    def crm_api(self, brand: str, demo: bool = False) -> tuple[str, str] | None:
        """(base url, key) of a brand's CRM API for partner chat, or None."""
        if brand == "fmc":
            url, key = self.fmc_crm_api_url, (self.fmc_demo_crm_api_key if demo else self.fmc_crm_api_key)
        elif brand == "av":
            url, key = self.av_crm_api_url, self.av_crm_api_key
        else:
            return None
        return (url.rstrip("/"), key) if url and key else None


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
