"""Read-only connection pools to the CRM Supabase Postgres databases.

The MIS reads the CRMs live on every request (no sync, no copy). Guarantees:
- The `mis_readonly` role can SELECT only leads, lead_sources, lead_stage_logs
  and (FMC) mis_partner_payouts — enforced by the CRM.
- Every query here runs inside an explicit READ ONLY transaction. The CRMs sit
  behind Supabase's transaction-mode pooler (port 6543), where a session-level
  `SET default_transaction_read_only` does not reliably persist, so the
  per-transaction guarantee is the one that matters.
- `statement_cache_size=0`: the transaction pooler can't hold prepared statements.

This module must NEVER issue a write — keep it that way.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import asyncpg

from app.config import settings
from app.models.enums import Brand

logger = logging.getLogger("mis.crm")


class CrmUnavailable(RuntimeError):
    """The CRM for a brand isn't configured or can't be reached."""


_pools: dict[Brand, asyncpg.Pool] = {}
_lock = asyncio.Lock()


async def _init_conn(conn: asyncpg.Connection) -> None:
    # Defense in depth only (may not persist through the transaction pooler);
    # the READ ONLY transaction in `fetch` is the real guarantee.
    await conn.execute("SET default_transaction_read_only = on;")


async def _get_pool(brand: Brand) -> asyncpg.Pool:
    pool = _pools.get(brand)
    if pool is not None:
        return pool
    async with _lock:
        pool = _pools.get(brand)
        if pool is not None:
            return pool
        dsn = settings.crm_dsn(brand.value)
        if not dsn:
            raise CrmUnavailable(f"No DSN configured for brand '{brand.value}'")
        try:
            pool = await asyncpg.create_pool(
                dsn,
                min_size=1,
                max_size=settings.crm_pool_max_size,
                statement_cache_size=0,
                command_timeout=settings.crm_query_timeout_sec,
                init=_init_conn,
            )
        except (OSError, asyncpg.PostgresError) as exc:
            raise CrmUnavailable(f"Can't reach the {brand.value} CRM: {exc}") from exc
        _pools[brand] = pool
        logger.info("CRM pool opened for %s", brand.value)
        return pool


async def fetch(brand: Brand, sql: str, *args: Any) -> list[asyncpg.Record]:
    """Run one SELECT on a brand's CRM inside a READ ONLY transaction."""
    pool = await _get_pool(brand)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                return await conn.fetch(sql, *args)
    except (OSError, asyncio.TimeoutError, asyncpg.exceptions.ConnectionDoesNotExistError,
            asyncpg.exceptions.InterfaceError, asyncpg.exceptions.InternalServerError,
            asyncpg.exceptions.CannotConnectNowError) as exc:
        logger.warning("CRM %s query failed: %s", brand.value, exc)
        raise CrmUnavailable(f"The {brand.value} CRM is unavailable right now") from exc


async def close_pools() -> None:
    for brand, pool in list(_pools.items()):
        await pool.close()
        _pools.pop(brand, None)
