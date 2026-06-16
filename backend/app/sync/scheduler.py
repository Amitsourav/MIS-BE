"""APScheduler wiring for the periodic sync job."""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.sync.worker import run_sync

logger = logging.getLogger("mis.sync.scheduler")

_scheduler: AsyncIOScheduler | None = None


async def _job() -> None:
    try:
        await run_sync()
    except Exception:  # noqa: BLE001
        logger.exception("scheduled sync job raised")


def start_scheduler() -> None:
    global _scheduler
    if not settings.sync_enabled:
        logger.info("sync disabled (SYNC_ENABLED=false) — scheduler not started")
        return
    if _scheduler is not None:
        return
    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        _job,
        trigger="interval",
        minutes=settings.sync_interval_minutes,
        id="mis_sync",
        max_instances=1,
        coalesce=True,
        next_run_time=None,  # first run after one interval; trigger manually for now
    )
    _scheduler.start()
    logger.info(
        "scheduler started — sync every %s min", settings.sync_interval_minutes
    )


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("scheduler stopped")
