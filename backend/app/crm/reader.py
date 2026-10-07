"""Live, read-only queries against the CRMs. The only place SQL for the CRMs
lives. Callers get plain dicts; all business rules live in `app.services`.

Scoping: every lead / payout query takes the caller's CRM source ids and
filters on them in SQL. An empty source list returns [] without querying.

Tests replace these functions with in-memory fakes (see tests/conftest.py), so
always call them as `reader.lead_rows(...)`, never `from ... import lead_rows`.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from app.config import settings
from app.crm import pool
from app.crm.stage_map import CRM_SCHEMAS, raw_stages_for
from app.models.enums import Brand, CanonicalStage


def _nphone(col: str) -> str:
    """SQL twin of `app.crm.normalize.normalize_phone`."""
    d = f"regexp_replace(coalesce({col}::text, ''), '[^0-9]', '', 'g')"
    return (
        f"CASE WHEN {d} = '' THEN NULL "
        f"WHEN length({d}) = 12 AND left({d}, 2) = '91' THEN substr({d}, 3) "
        f"WHEN length({d}) = 11 AND left({d}, 1) = '0' THEN substr({d}, 2) "
        f"ELSE {d} END"
    )


def _lead_sql(brand: Brand) -> str:
    s = CRM_SCHEMAS[brand]
    days = int(settings.duplicate_window_days)
    return f"""
        WITH base AS (
            SELECT
                l.{s.lead_id}         AS crm_lead_id,
                l.{s.lead_serial}     AS serial_no,
                l.{s.lead_name}       AS full_name,
                {_nphone('l.' + s.lead_phone)} AS phone,
                l.{s.lead_source_fk}  AS crm_source_id,
                l.{s.lead_stage}::text AS raw_stage,
                l.{s.lead_created}    AS created_at,
                l.{s.lead_updated}    AS crm_updated_at
            FROM {s.leads_table} l
            WHERE NOT l.{s.lead_deleted}
        ),
        flagged AS (
            -- Duplicate = an earlier lead (any source) with the same phone in
            -- the last N days. Computed over the whole brand, then filtered.
            SELECT b.*,
                CASE WHEN b.phone IS NULL THEN false ELSE
                    count(*) OVER (
                        PARTITION BY b.phone ORDER BY b.created_at
                        RANGE BETWEEN interval '{days} days' PRECEDING
                              AND interval '1 microsecond' PRECEDING
                    ) > 0
                END AS is_duplicate
            FROM base b
        ),
        scoped AS (
            SELECT * FROM flagged f
            WHERE f.crm_source_id = ANY($1::uuid[])
              AND ($6::timestamptz IS NULL OR f.created_at >= $6)
              AND ($7::timestamptz IS NULL OR f.created_at <  $7)
        ),
        milestones AS (
            -- One pass over the logs, grouped by lead. (A per-lead lookup is
            -- catastrophically slow on a CRM with no index on log lead_id.)
            SELECT
                g.{s.log_lead_fk} AS lead_id,
                min(g.{s.log_created}) FILTER (WHERE lower(trim(g.{s.log_stage}::text)) = ANY($2::text[])) AS contacted_at,
                min(g.{s.log_created}) FILTER (WHERE lower(trim(g.{s.log_stage}::text)) = ANY($3::text[])) AS qualified_at,
                min(g.{s.log_created}) FILTER (WHERE lower(trim(g.{s.log_stage}::text)) = ANY($4::text[])) AS converted_at,
                max(g.{s.log_created}) FILTER (WHERE lower(trim(g.{s.log_stage}::text)) = ANY($5::text[])) AS lost_at
            FROM {s.stage_logs_table} g
            WHERE g.{s.log_lead_fk} IN (SELECT crm_lead_id FROM scoped)
            GROUP BY g.{s.log_lead_fk}
        )
        SELECT sc.*, m.contacted_at, m.qualified_at, m.converted_at, m.lost_at
        FROM scoped sc
        LEFT JOIN milestones m ON m.lead_id = sc.crm_lead_id
    """


async def lead_rows(
    brand: Brand,
    source_ids: list[uuid.UUID],
    start: datetime | None,
    end: datetime | None,
) -> list[dict[str, Any]]:
    """Every non-deleted lead from `source_ids`, created in [start, end)
    (None = unbounded), with its duplicate flag and first-reached milestone
    timestamps from the stage logs."""
    if not source_ids:
        return []
    rows = await pool.fetch(
        brand,
        _lead_sql(brand),
        list(source_ids),
        raw_stages_for(brand, CanonicalStage.CONTACTED),
        raw_stages_for(brand, CanonicalStage.QUALIFIED),
        raw_stages_for(brand, CanonicalStage.CONVERTED),
        raw_stages_for(brand, CanonicalStage.LOST),
        start,
        end,
    )
    return [dict(r) for r in rows]


_PAYOUT_SQL = """
    SELECT
        p.lead_bank_id, p.lead_id, p.lead_source_id, p.bank_name, p.loan_amount,
        p.pf_paid_on, p.disbursed_total, p.payout_basis, p.payout_rate,
        p.payout_earned, p.payout_paid, p.payout_pending, p.updated_at,
        l.serial_no, l.full_name
    FROM public.mis_partner_payouts p
    LEFT JOIN public.leads l ON l.id = p.lead_id
    WHERE p.lead_source_id = ANY($1::uuid[])
      AND ($2::date IS NULL OR p.pf_paid_on >= $2)
      AND ($3::date IS NULL OR p.pf_paid_on <= $3)
"""


async def payout_rows(
    brand: Brand,
    source_ids: list[uuid.UUID],
    date_from: date | None,
    date_to: date | None,
) -> list[dict[str, Any]]:
    """Partner payouts (FMC view only) for `source_ids`, PF paid in
    [date_from, date_to]. Admitverse has no payouts → []."""
    if brand != Brand.FMC or not source_ids:
        return []
    rows = await pool.fetch(brand, _PAYOUT_SQL, list(source_ids), date_from, date_to)
    return [dict(r) for r in rows]


async def lead_sources(brand: Brand) -> list[dict[str, Any]]:
    """All lead_sources, for the admin mapping picker."""
    s = CRM_SCHEMAS[brand]
    rows = await pool.fetch(
        brand,
        f"""SELECT {s.source_id} AS crm_source_id, {s.source_name} AS name
            FROM {s.lead_sources_table}
            ORDER BY {s.source_name} ASC NULLS LAST""",
    )
    return [dict(r) for r in rows]
