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
        self.releases: list = []  # FMC mis_partner_disbursements rows
        self.sources: dict = {}  # brand -> list[{crm_source_id, name}]
        # brand -> {lead_id: {lead_source_id, counsellor_name, loan_amount_lakh, lenders}}
        self.lead_info: dict = {}
        self.history: dict = {}  # lead id -> [{to_stage, at}], oldest first
        self.down = False
        self.calls: list = []    # (fn, brand, source_ids) for scoping asserts

    def _check(self):
        if self.down:
            from app.crm.pool import CrmUnavailable
            raise CrmUnavailable("The CRM is unavailable right now")

    def _lead_numbers(self, brand, source_ids) -> dict:
        """lead id -> the partner's 1, 2, 3... over all their leads (SQL twin)."""
        ids = set(source_ids)
        mine = sorted(
            (r for r in self.leads.get(brand, []) if r["crm_source_id"] in ids),
            key=lambda r: (r["created_at"], str(r["crm_lead_id"])),
        )
        return {r["crm_lead_id"]: n for n, r in enumerate(mine, start=1)}

    async def lead_rows(self, brand, source_ids, start, end, lead_id=None):
        self._check()
        self.calls.append(("lead_rows", brand, list(source_ids)))
        ids = set(source_ids)
        numbers = self._lead_numbers(brand, source_ids)
        return [
            {**r, "lead_no": numbers[r["crm_lead_id"]]} for r in self.leads.get(brand, [])
            if r["crm_source_id"] in ids
            and (lead_id is None or r["crm_lead_id"] == lead_id)
            and (start is None or r["created_at"] >= start)
            and (end is None or r["created_at"] < end)
        ]

    async def stage_history(self, brand, source_ids, lead_id):
        self._check()
        self.calls.append(("stage_history", brand, list(source_ids)))
        ids = set(source_ids)
        if not any(r["crm_lead_id"] == lead_id and r["crm_source_id"] in ids
                   for r in self.leads.get(brand, [])):
            return []
        return [dict(h) for h in self.history.get(lead_id, [])]

    async def lead_info_rows(self, brand, source_ids, lead_ids=None):
        self._check()
        self.calls.append(("lead_info_rows", brand, list(source_ids)))
        ids = set(source_ids)
        return [
            {"lead_id": lid, **{k: v for k, v in info.items() if k != "lead_source_id"}}
            for lid, info in self.lead_info.get(brand, {}).items()
            if info["lead_source_id"] in ids and (lead_ids is None or lid in lead_ids)
        ]

    async def payout_rows(self, brand, source_ids, date_from, date_to):
        self._check()
        self.calls.append(("payout_rows", brand, list(source_ids)))
        from app.models.enums import Brand
        if brand != Brand.FMC:
            return []
        ids = set(source_ids)
        numbers = self._lead_numbers(brand, source_ids)
        return [
            {**r, "lead_no": numbers.get(r["lead_id"])} for r in self.payouts
            if r["lead_source_id"] in ids
            and (date_from is None
                 or (r["last_disbursed_on"] and r["last_disbursed_on"] >= date_from))
            and (date_to is None
                 or (r["last_disbursed_on"] and r["last_disbursed_on"] <= date_to))
        ]

    async def payout_rows_unmapped(self, brand, mapped_source_ids):
        self._check()
        self.calls.append(("payout_rows_unmapped", brand, list(mapped_source_ids)))
        from app.models.enums import Brand
        if brand != Brand.FMC:
            return []
        mapped = set(mapped_source_ids)
        names = {s["crm_source_id"]: s["name"] for s in self.sources.get(brand, [])}
        return [
            {**r, "source_name": names.get(r["lead_source_id"])}
            for r in self.payouts if r["lead_source_id"] not in mapped
        ]

    async def disbursement_rows(self, brand, source_ids):
        self._check()
        self.calls.append(("disbursement_rows", brand, list(source_ids)))
        from app.models.enums import Brand
        if brand != Brand.FMC:
            return []
        ids = set(source_ids)
        return [dict(r) for r in self.releases if r["lead_source_id"] in ids]

    async def lead_reach_rows(self, brand, source_ids, start, end):
        self._check()
        self.calls.append(("lead_reach_rows", brand, list(source_ids)))
        ids = set(source_ids)
        return [
            {"crm_lead_id": r["crm_lead_id"], "raw_stage": r["raw_stage"],
             "logged_stages": r.get("logged_stages", [])}
            for r in self.leads.get(brand, [])
            if r["crm_source_id"] in ids
            and (start is None or r["created_at"] >= start)
            and (end is None or r["created_at"] < end)
        ]

    async def lead_sources(self, brand):
        self._check()
        return list(self.sources.get(brand, []))


@pytest_asyncio.fixture(autouse=True)
async def _fresh_login_guard():
    from app.core.ratelimit import login_guard
    login_guard.reset()
    yield


@pytest_asyncio.fixture(autouse=True)
async def crm(monkeypatch):
    from app.crm import reader

    fake = FakeCrm()
    monkeypatch.setattr(reader, "lead_rows", fake.lead_rows)
    monkeypatch.setattr(reader, "payout_rows", fake.payout_rows)
    monkeypatch.setattr(reader, "lead_sources", fake.lead_sources)
    monkeypatch.setattr(reader, "payout_rows_unmapped", fake.payout_rows_unmapped)
    monkeypatch.setattr(reader, "disbursement_rows", fake.disbursement_rows)
    monkeypatch.setattr(reader, "lead_reach_rows", fake.lead_reach_rows)
    monkeypatch.setattr(reader, "lead_info_rows", fake.lead_info_rows)
    monkeypatch.setattr(reader, "stage_history", fake.stage_history)
    return fake
