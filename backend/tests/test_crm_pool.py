"""Identical CRM queries in flight at the same moment share one round trip;
nothing is cached after it finishes."""
from __future__ import annotations

import asyncio

import pytest

from app.crm import pool
from app.models.enums import Brand

pytestmark = pytest.mark.asyncio


async def test_same_query_at_once_runs_once_and_never_after(monkeypatch):
    runs = []

    async def fake_fetch(brand, sql, *args):
        runs.append((sql, args))
        n = len(runs)
        await asyncio.sleep(0.05)
        return [{"n": n}]

    monkeypatch.setattr(pool, "_fetch", fake_fetch)
    a, b, c = await asyncio.gather(
        pool.fetch(Brand.FMC, "SELECT 1", [1]),
        pool.fetch(Brand.FMC, "SELECT 1", [1]),
        pool.fetch(Brand.FMC, "SELECT 1", [2]),  # different args: its own query
    )
    assert len(runs) == 2 and a == b == [{"n": 1}] and c != a
    # Once done, the next call reads live again.
    assert await pool.fetch(Brand.FMC, "SELECT 1", [1]) == [{"n": 3}]
    assert pool._inflight == {}


async def test_a_failure_reaches_every_waiter_and_is_not_kept(monkeypatch):
    calls = 0

    async def boom(brand, sql, *args):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        raise pool.CrmUnavailable("down")

    monkeypatch.setattr(pool, "_fetch", boom)
    res = await asyncio.gather(*(pool.fetch(Brand.FMC, "SELECT 1") for _ in range(3)),
                               return_exceptions=True)
    assert calls == 1 and all(isinstance(r, pool.CrmUnavailable) for r in res)
    assert pool._inflight == {}
