"""Read-only connectors to the CRM Supabase Postgres databases.

These use raw asyncpg and issue SELECT-only statements. The DB-level guarantee
is the `mis_readonly` role (SELECT grants on leads/lead_sources/lead_stage_logs
only). This module must NEVER issue a write — keep it that way.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import asyncpg

from app.config import settings
from app.models.enums import Brand
from app.sync.stage_map import CRM_SCHEMAS

logger = logging.getLogger("mis.sync.connectors")


class CrmUnavailable(RuntimeError):
    """Raised when a brand has no configured DSN."""


async def _connect(brand: Brand) -> asyncpg.Connection:
    dsn = settings.crm_dsn(brand.value)
    if not dsn:
        raise CrmUnavailable(f"No DSN configured for brand '{brand.value}'")
    # statement_cache_size=0 lets us run through Supabase's transaction-mode
    # pooler (port 6543 / pgbouncer), which does not support prepared statements.
    # Harmless on direct/session connections too.
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    # Defense in depth: make the whole session read-only at the protocol level.
    await conn.execute("SET default_transaction_read_only = on;")
    return conn


async def fetch_changed_leads(
    brand: Brand,
    since_ts: datetime | None,
    since_id: Any | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Pull leads changed since the keyset cursor (since_ts, since_id), incremental.

    Pagination is by the composite key (updated_at, id) — NOT updated_at alone —
    so that the many leads sharing one bulk-import timestamp are never skipped.
    Ordered by (updated_at ASC, id ASC); the caller advances the cursor to the
    last row of each batch. Returns plain dicts with canonical keys regardless of
    CRM column names.
    """
    s = CRM_SCHEMAS[brand]
    if since_ts is None:
        where = ""
        args: list[Any] = []
    else:
        where = (
            f"WHERE (l.{s.lead_updated}, l.{s.lead_id}) > ($1::timestamptz, $2::uuid)"
        )
        args = [since_ts, since_id]
    limit_pos = f"${len(args) + 1}"
    args.append(limit)

    sql = f"""
        SELECT
            l.{s.lead_id}          AS crm_lead_id,
            l.{s.lead_serial}      AS serial_no,
            l.{s.lead_name}        AS full_name,
            l.{s.lead_phone}       AS phone,
            l.{s.lead_source_fk}   AS crm_source_id,
            l.{s.lead_stage}       AS raw_stage,
            l.{s.lead_created}     AS created_at,
            l.{s.lead_updated}     AS crm_updated_at,
            src.{s.source_name}    AS source_name
        FROM {s.leads_table} l
        LEFT JOIN {s.lead_sources_table} src
               ON src.{s.source_id} = l.{s.lead_source_fk}
        {where}
        ORDER BY l.{s.lead_updated} ASC, l.{s.lead_id} ASC
        LIMIT {limit_pos}
    """
    conn = await _connect(brand)
    try:
        rows = await conn.fetch(sql, *args)
    finally:
        await conn.close()
    return [dict(r) for r in rows]


async def fetch_stage_logs(
    brand: Brand, lead_ids: list[Any]
) -> dict[Any, list[dict[str, Any]]]:
    """Fetch stage-change logs for the given lead ids, grouped by lead id."""
    if not lead_ids:
        return {}
    s = CRM_SCHEMAS[brand]
    sql = f"""
        SELECT
            {s.log_lead_fk} AS lead_id,
            {s.log_stage}   AS stage,
            {s.log_created} AS created_at
        FROM {s.stage_logs_table}
        WHERE {s.log_lead_fk} = ANY($1::uuid[])
        ORDER BY {s.log_created} ASC
    """
    conn = await _connect(brand)
    try:
        rows = await conn.fetch(sql, lead_ids)
    finally:
        await conn.close()

    out: dict[Any, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["lead_id"], []).append(
            {"stage": r["stage"], "created_at": r["created_at"]}
        )
    return out


async def fetch_partner_payouts(brand: Brand) -> list[dict[str, Any]]:
    """Full snapshot of the CRM's partner-payout view (one row per lender file
    that has reached PF paid). FMC only — Admitverse has no lender commission,
    so AV returns []. Reads ONLY the view; mis_readonly is blocked from every
    other CRM table besides leads / lead_sources / lead_stage_logs."""
    if brand != Brand.FMC:
        return []
    sql = """
        SELECT
            lead_bank_id, lead_id, lead_source_id, bank_name, loan_amount,
            pf_paid_on, disbursed_total, payout_basis, payout_rate,
            payout_earned, payout_paid, payout_pending, updated_at
        FROM public.mis_partner_payouts
    """
    conn = await _connect(brand)
    try:
        rows = await conn.fetch(sql)
    finally:
        await conn.close()
    return [dict(r) for r in rows]


async def list_lead_sources(brand: Brand) -> list[dict[str, Any]]:
    """List all lead_sources for the admin mapping picker (read-only proxy)."""
    s = CRM_SCHEMAS[brand]
    sql = f"""
        SELECT {s.source_id} AS crm_source_id, {s.source_name} AS name
        FROM {s.lead_sources_table}
        ORDER BY {s.source_name} ASC NULLS LAST
    """
    conn = await _connect(brand)
    try:
        rows = await conn.fetch(sql)
    finally:
        await conn.close()
    return [dict(r) for r in rows]
