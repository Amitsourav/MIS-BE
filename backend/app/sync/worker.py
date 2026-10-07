"""The sync worker: incremental pull → normalize → dedupe → upsert → rollup.

Runs per brand on a schedule (APScheduler) and on-demand (admin endpoint).
A module-level lock guarantees only one sync runs at a time across both triggers.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import SessionLocal
from app.models import MisLead, ProviderSource, SyncState
from app.models.enums import Brand
from app.sync.connectors import (
    CrmUnavailable,
    fetch_changed_leads,
    fetch_partner_payouts,
    fetch_stage_logs,
)
from app.sync.normalize import build_lead_fact
from app.services import payouts
from app.services.rollup import cohort_day, recompute_cohorts

logger = logging.getLogger("mis.sync.worker")

BATCH_SIZE = 1000
MAX_BATCHES_PER_RUN = 100  # safety cap (≤100k changed rows per brand per run)
_MIN_UUID = uuid.UUID(int=0)  # keyset start id for a given watermark second

_sync_lock = asyncio.Lock()
_running = False


def is_running() -> bool:
    return _running


async def _load_source_map(db: AsyncSession, brand: Brand) -> dict[uuid.UUID, uuid.UUID]:
    """crm_source_id -> provider_id for this brand."""
    rows = await db.execute(
        select(ProviderSource.crm_source_id, ProviderSource.provider_id).where(
            ProviderSource.brand == brand
        )
    )
    return {src: pid for src, pid in rows.all()}


async def _get_or_create_state(db: AsyncSession, brand: Brand) -> SyncState:
    state = await db.get(SyncState, brand)
    if state is None:
        state = SyncState(brand=brand)
        db.add(state)
        await db.flush()
    return state


async def _is_duplicate(
    db: AsyncSession, brand: Brand, phone: str | None, created_at, crm_lead_id
) -> bool:
    """True if an earlier lead with the same brand+phone exists inside the
    duplicate window (default 30 days)."""
    if not phone or created_at is None:
        return False
    window_start = created_at - timedelta(days=settings.duplicate_window_days)
    exists = await db.execute(
        select(MisLead.id)
        .where(
            and_(
                MisLead.brand == brand,
                MisLead.phone == phone,
                MisLead.crm_lead_id != crm_lead_id,
                MisLead.created_at >= window_start,
                MisLead.created_at < created_at,
            )
        )
        .limit(1)
    )
    return exists.first() is not None


async def _upsert_lead(db: AsyncSession, fact: dict) -> None:
    stmt = pg_insert(MisLead).values(**fact)
    update_cols = {k: stmt.excluded[k] for k in fact if k not in ("brand", "crm_lead_id")}
    update_cols["synced_at"] = datetime.now(timezone.utc)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_mis_leads_brand_lead", set_=update_cols
    )
    await db.execute(stmt)


async def _sync_payouts(
    db: AsyncSession, brand: Brand, source_map: dict[uuid.UUID, uuid.UUID]
) -> str:
    """Full-refresh `mis_payouts` from the CRM view in one transaction.

    Never raises: a failure is rolled back and reported in the returned status
    note, so it can't fail the (already committed) lead sync. If the fetch fails
    nothing is deleted — an error is never mistaken for an empty view.
    """
    if brand not in payouts.PAYOUT_BRANDS:
        return ""
    try:
        rows = await fetch_partner_payouts(brand)
        counts = await payouts.apply_snapshot(db, brand, rows, source_map)
        await db.commit()
        logger.info("[sync %s] payouts %s", brand.value, counts)
        return (
            f"; payouts: {len(rows)} files ({counts['inserted']} new, "
            f"{counts['deleted']} removed, {counts['unmapped']} unmapped)"
        )
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        logger.exception("[sync %s] payout sync failed", brand.value)
        return f"; payouts error: {exc}"


async def sync_brand(brand: Brand) -> dict:
    """Run a full incremental sync for one brand. Returns a small summary dict."""
    processed = 0
    skipped_unmapped = 0
    affected: set = set()

    async with SessionLocal() as db:
        state = await _get_or_create_state(db, brand)
        # Keyset cursor. Starting at (last_watermark, MIN_UUID) re-includes every
        # row sharing the watermark second (idempotent upsert) so none are lost.
        cursor_ts = state.last_watermark
        cursor_id = _MIN_UUID
        source_map = await _load_source_map(db, brand)

        try:
            for _ in range(MAX_BATCHES_PER_RUN):
                rows = await fetch_changed_leads(brand, cursor_ts, cursor_id, BATCH_SIZE)
                if not rows:
                    break

                lead_ids = [r["crm_lead_id"] for r in rows]
                logs_by_lead = await fetch_stage_logs(brand, lead_ids)

                for r in rows:
                    # Unmapped sources get provider_id=null and are STILL stored,
                    # so that mapping the source later can backfill them (see
                    # claim_unmapped_leads). Null-provider rows never roll up.
                    provider_id = source_map.get(r.get("crm_source_id"))
                    if provider_id is None:
                        skipped_unmapped += 1

                    fact = build_lead_fact(
                        brand, r, logs_by_lead.get(r["crm_lead_id"], [])
                    )
                    fact["provider_id"] = provider_id
                    fact["is_duplicate"] = await _is_duplicate(
                        db, brand, fact["phone"], fact["created_at"], fact["crm_lead_id"]
                    )
                    await _upsert_lead(db, fact)
                    await db.flush()  # so later dup checks in this batch see it

                    # Only mapped leads contribute to rollups (cohorts are keyed
                    # by a non-null provider_id).
                    if provider_id is not None:
                        day = cohort_day(
                            MisLead(
                                created_at=fact["created_at"],
                                crm_updated_at=fact["crm_updated_at"],
                            )
                        )
                        if day is not None:
                            affected.add((provider_id, brand, day))
                    processed += 1

                # Advance the keyset cursor to the last row (rows are ordered by
                # (updated_at, id)), and persist the watermark = max updated_at seen.
                last = rows[-1]
                cursor_ts = last.get("crm_updated_at")
                cursor_id = last.get("crm_lead_id")
                if cursor_ts is not None:
                    state.last_watermark = cursor_ts
                await db.commit()

                if len(rows) < BATCH_SIZE:
                    break
                # Can't keyset past a null timestamp; stop rather than restart.
                if cursor_ts is None:
                    break

            # recompute affected rollups
            await recompute_cohorts(db, affected)
            await db.commit()

            # Payouts run after leads are committed, in their own transaction.
            payout_note = await _sync_payouts(db, brand, source_map)

            state = await _get_or_create_state(db, brand)  # reload after a possible rollback
            state.last_run_at = datetime.now(timezone.utc)
            state.last_status = (
                f"ok: {processed} processed, {skipped_unmapped} unmapped (stored, "
                f"awaiting source mapping), {len(affected)} cohorts recomputed"
                f"{payout_note}"
            )
            await db.commit()
            logger.info("[sync %s] %s", brand.value, state.last_status)

        except CrmUnavailable as exc:
            state.last_run_at = datetime.now(timezone.utc)
            state.last_status = f"skipped: {exc}"
            await db.commit()
            logger.warning("[sync %s] %s", brand.value, exc)
        except Exception as exc:  # noqa: BLE001 — record failure, don't crash scheduler
            await db.rollback()
            state2 = await _get_or_create_state(db, brand)
            state2.last_run_at = datetime.now(timezone.utc)
            state2.last_status = f"error: {exc}"
            await db.commit()
            logger.exception("[sync %s] failed", brand.value)

    return {
        "brand": brand.value,
        "processed": processed,
        "skipped_unmapped": skipped_unmapped,
        "cohorts": len(affected),
    }


async def run_sync(brands: list[Brand] | None = None) -> list[dict]:
    """Run sync for the given brands (default: all), guarded by the global lock."""
    global _running
    brands = brands or list(Brand)
    async with _sync_lock:
        _running = True
        try:
            results = []
            for brand in brands:
                results.append(await sync_brand(brand))
            return results
        finally:
            _running = False
