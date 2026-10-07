"""Test fixtures: in-memory SQLite, app with overridden DB, data factories."""
from __future__ import annotations

import os

# Configure env BEFORE importing the app (settings are cached at import).
os.environ.setdefault("MIS_DATABASE_URL", "sqlite+aiosqlite://")
os.environ.setdefault("SYNC_ENABLED", "false")
os.environ.setdefault("JWT_SECRET", "test-secret")

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402

_engine = create_async_engine(
    "sqlite+aiosqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSession = async_sessionmaker(_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _TestSession() as session:
        yield session
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(db):
    async def _override_get_db():
        async with _TestSession() as s:
            yield s

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


# ---------------- Fake CRM (no test ever reaches a real CRM) ----------------
class FakeCrm:
    """In-memory stand-in for `app.crm.reader`. Rows use the reader's output
    shape. Filtering mirrors the SQL: by brand, source ids, and date window."""

    def __init__(self) -> None:
        self.leads: dict = {}    # brand -> list[row]
        self.payouts: list = []  # FMC view rows (+ serial_no/full_name)
        self.sources: dict = {}  # brand -> list[{crm_source_id, name}]
        self.down = False
        self.calls: list = []    # (fn, brand, source_ids) for scoping asserts

    def _check(self):
        if self.down:
            from app.crm.pool import CrmUnavailable
            raise CrmUnavailable("The CRM is unavailable right now")

    async def lead_rows(self, brand, source_ids, start, end):
        self._check()
        self.calls.append(("lead_rows", brand, list(source_ids)))
        ids = set(source_ids)
        return [
            dict(r) for r in self.leads.get(brand, [])
            if r["crm_source_id"] in ids
            and (start is None or r["created_at"] >= start)
            and (end is None or r["created_at"] < end)
        ]

    async def payout_rows(self, brand, source_ids, date_from, date_to):
        self._check()
        self.calls.append(("payout_rows", brand, list(source_ids)))
        from app.models.enums import Brand
        if brand != Brand.FMC:
            return []
        ids = set(source_ids)
        return [
            dict(r) for r in self.payouts
            if r["lead_source_id"] in ids
            and (date_from is None or (r["pf_paid_on"] and r["pf_paid_on"] >= date_from))
            and (date_to is None or (r["pf_paid_on"] and r["pf_paid_on"] <= date_to))
        ]

    async def lead_sources(self, brand):
        self._check()
        return list(self.sources.get(brand, []))


@pytest_asyncio.fixture(autouse=True)
async def crm(monkeypatch):
    from app.crm import reader

    fake = FakeCrm()
    monkeypatch.setattr(reader, "lead_rows", fake.lead_rows)
    monkeypatch.setattr(reader, "payout_rows", fake.payout_rows)
    monkeypatch.setattr(reader, "lead_sources", fake.lead_sources)
    return fake
