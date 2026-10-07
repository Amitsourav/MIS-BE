"""Partner payouts (Payout page, earned on PF paid): scoping, unmapped storage +
back-stamping, full-refresh deletes, AV unsupported, page-independent summary,
string money."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.security import create_access_token, hash_password
from app.models import AdminUser, MisLead, MisPayout, ProviderSource
from app.models.enums import Brand, CanonicalStage
from app.services.payouts import apply_snapshot
from tests.factories import make_provider

pytestmark = pytest.mark.asyncio


def _provider_headers(provider) -> dict:
    token = create_access_token(
        subject="u-" + str(provider.id), role="provider", provider_id=str(provider.id)
    )
    return {"Authorization": f"Bearer {token}"}


async def _admin_headers(db, email: str, brand: Brand | None) -> dict:
    admin = AdminUser(email=email, password_hash=hash_password("pw"), brand=brand)
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    token = create_access_token(
        subject=str(admin.id), role="admin", brand=brand.value if brand else None
    )
    return {"Authorization": f"Bearer {token}"}


async def _map(db, provider, source_id: uuid.UUID) -> None:
    db.add(ProviderSource(provider_id=provider.id, brand=provider.brand,
                          crm_source_id=source_id, source_name="src"))
    await db.commit()


def _view_row(
    source_id: uuid.UUID,
    *,
    lead_id: uuid.UUID | None = None,
    bank: str = "UC PNB",
    loan: str | None = "1800000.00",
    disbursed: str = "400000.00",
    earned: str = "10800.00",
    paid: str = "0.00",
    pf: date = date(2026, 8, 20),
    basis: str = "rate",
    rate: str | None = "0.60",
) -> dict:
    """A row shaped like public.mis_partner_payouts."""
    pending = max(Decimal(earned) - Decimal(paid), Decimal("0"))
    return {
        "lead_bank_id": uuid.uuid4(),
        "lead_id": lead_id or uuid.uuid4(),
        "lead_source_id": source_id,
        "bank_name": bank,
        "loan_amount": None if loan is None else Decimal(loan),
        "pf_paid_on": pf,
        "disbursed_total": Decimal(disbursed),
        "payout_basis": basis,
        "payout_rate": None if rate is None else Decimal(rate),
        "payout_earned": Decimal(earned),
        "payout_paid": Decimal(paid),
        "payout_pending": pending,
        "updated_at": datetime(2026, 9, 30, 10, tzinfo=timezone.utc),
    }


async def _sync(db, rows, source_map) -> dict:
    counts = await apply_snapshot(db, Brand.FMC, rows, source_map)
    await db.commit()
    return counts


async def test_provider_sees_only_own_sources_payouts(client, db):
    a = await make_provider(db, "A", "pa@x.com")
    b = await make_provider(db, "B", "pb@x.com")
    sa, sb = uuid.uuid4(), uuid.uuid4()
    await _sync(db, [_view_row(sa, bank="SBI"), _view_row(sb, bank="HDFC")],
                {sa: a.id, sb: b.id})

    res = await client.get("/provider/payouts", headers=_provider_headers(a))
    assert res.status_code == 200
    body = res.json()
    assert body["brand_supported"] is True
    assert [i["bank_name"] for i in body["items"]] == ["SBI"]
    assert body["total"] == 1
    assert "HDFC" not in str(body)


async def test_unmapped_source_stored_null_and_hidden(client, db):
    a = await make_provider(db, "A", "pa2@x.com")
    orphan_src = uuid.uuid4()
    counts = await _sync(db, [_view_row(orphan_src)], {})
    assert counts["unmapped"] == 1

    stored = (await db.execute(select(MisPayout))).scalars().all()
    assert len(stored) == 1 and stored[0].provider_id is None

    res = await client.get("/provider/payouts", headers=_provider_headers(a))
    assert res.json()["total"] == 0


async def test_mapping_a_source_back_stamps_payouts(client, db):
    provider = await make_provider(db, "Altera", "alt@x.com")
    src = uuid.uuid4()
    await _sync(db, [_view_row(src), _view_row(src, bank="SBI")], {})

    headers = await _admin_headers(db, "fmcadmin@x.com", Brand.FMC)
    res = await client.post(
        f"/admin/providers/{provider.id}/sources",
        headers=headers,
        json={"brand": "fmc", "crm_source_id": str(src)},
    )
    assert res.status_code == 201

    res = await client.get("/provider/payouts", headers=_provider_headers(provider))
    assert res.json()["total"] == 2


async def test_file_removed_from_view_is_deleted(client, db):
    a = await make_provider(db, "A", "pa3@x.com")
    src = uuid.uuid4()
    keep, drop = _view_row(src), _view_row(src, bank="SBI")
    await _sync(db, [keep, drop], {src: a.id})

    keep["payout_paid"] = Decimal("5000.00")  # a later change is upserted too
    counts = await _sync(db, [keep], {src: a.id})
    assert counts == {"inserted": 0, "updated": 1, "deleted": 1, "unmapped": 0}

    rows = (await db.execute(select(MisPayout))).scalars().all()
    assert [r.crm_lead_bank_id for r in rows] == [keep["lead_bank_id"]]
    assert rows[0].payout_paid == Decimal("5000.00")


async def test_av_provider_is_unsupported_not_an_error(client, db):
    av = await make_provider(db, "AV Vendor", "av@x.com", brand=Brand.AV)
    res = await client.get("/provider/payouts", headers=_provider_headers(av))
    assert res.status_code == 200
    body = res.json()
    assert body["brand_supported"] is False
    assert body["items"] == [] and body["total"] == 0
    assert body["summary"]["earned"] == "0.00"

    export = await client.get("/provider/payouts/export", headers=_provider_headers(av))
    assert export.status_code == 200
    assert export.text.strip().count("\n") == 0  # header only


async def test_summary_covers_whole_filtered_set_not_the_page(client, db):
    a = await make_provider(db, "A", "pa4@x.com")
    src = uuid.uuid4()
    shared_student = uuid.uuid4()  # one student with two lenders
    rows = [
        _view_row(src, lead_id=shared_student, loan="1000000.00",
                  earned="6000.00", paid="6000.00", pf=date(2026, 9, 1)),
        _view_row(src, lead_id=shared_student, loan="500000.00",
                  earned="3000.00", pf=date(2026, 9, 20)),
        _view_row(src, loan="250000.00", earned="1500.50",
                  paid="500.25", pf=date(2026, 9, 10)),
        # agreed deal with no sanctioned amount: counts toward earned, not loan_total
        _view_row(src, loan=None, basis="agreed", rate=None, earned="0.00",
                  pf=date(2026, 8, 1)),
    ]
    await _sync(db, rows, {src: a.id})

    res = await client.get("/provider/payouts?page_size=1", headers=_provider_headers(a))
    body = res.json()
    assert len(body["items"]) == 1
    assert body["total"] == 4
    assert body["summary"] == {
        "students": 3,
        "loan_total": "1750000.00",
        "earned": "10500.50",
        "paid": "6500.25",
        "pending": "4000.25",
    }
    # newest PF-paid first
    assert body["items"][0]["pf_paid_on"] == "2026-09-20"

    page3 = (await client.get("/provider/payouts?page=3&page_size=1",
                              headers=_provider_headers(a))).json()
    assert page3["items"][0]["pf_paid_on"] == "2026-09-01"
    assert page3["summary"] == body["summary"]


async def test_date_filter_uses_pf_paid_on(client, db):
    a = await make_provider(db, "A", "pa5@x.com")
    src = uuid.uuid4()
    await _sync(db, [_view_row(src, pf=date(2026, 8, 5)),
                     _view_row(src, pf=date(2026, 9, 25))], {src: a.id})

    res = await client.get(
        "/provider/payouts?date_from=2026-09-01&date_to=2026-09-30",
        headers=_provider_headers(a),
    )
    body = res.json()
    assert body["total"] == 1
    assert body["items"][0]["pf_paid_on"] == "2026-09-25"


async def test_money_serialises_as_strings_and_joins_lead(client, db):
    a = await make_provider(db, "A", "pa6@x.com")
    src = uuid.uuid4()
    lead_id = uuid.uuid4()
    db.add(MisLead(brand=Brand.FMC, crm_lead_id=lead_id, provider_id=a.id,
                   crm_source_id=src, serial_no=8862, full_name="Riya S",
                   phone="9000000009", canonical_stage=CanonicalStage.CONVERTED))
    await db.commit()
    await _sync(db, [
        _view_row(src, lead_id=lead_id),
        _view_row(src, basis="agreed", rate=None, loan=None,
                  earned="25000.00"),  # no lead synced
    ], {src: a.id})

    body = (await client.get("/provider/payouts", headers=_provider_headers(a))).json()
    by_basis = {i["payout_basis"]: i for i in body["items"]}

    rate_row = by_basis["rate"]
    assert rate_row["serial_no"] == 8862 and rate_row["full_name"] == "Riya S"
    assert rate_row["payout_rate"] == "0.60"
    for key in ("loan_amount", "disbursed_total", "earned", "paid", "pending"):
        assert isinstance(rate_row[key], str)
    assert rate_row["loan_amount"] == "1800000.00"
    assert rate_row["disbursed_total"] == "400000.00"
    assert rate_row["pf_paid_on"] == "2026-08-20"
    assert rate_row["earned"] == "10800.00"
    assert set(rate_row) == {
        "serial_no", "full_name", "bank_name", "loan_amount", "pf_paid_on",
        "disbursed_total", "payout_basis", "payout_rate", "earned", "paid", "pending",
    }
    assert "phone" not in rate_row

    agreed = by_basis["agreed"]
    assert agreed["payout_rate"] is None
    assert agreed["loan_amount"] is None
    assert agreed["full_name"] is None  # row still returned without a lead
    assert isinstance(body["summary"]["earned"], str)


async def test_export_returns_all_filtered_rows(client, db):
    a = await make_provider(db, "A", "pa7@x.com")
    src = uuid.uuid4()
    await _sync(db, [_view_row(src) for _ in range(3)], {src: a.id})

    res = await client.get("/provider/payouts/export", headers=_provider_headers(a))
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    lines = res.text.strip().splitlines()
    assert lines[0] == ("serial_no,full_name,bank_name,loan_amount,pf_paid_on,"
                        "disbursed_total,payout_basis,payout_rate,earned,paid,pending")
    assert len(lines) == 4
    assert "10800.00" in lines[1]


async def test_admin_payouts_respect_company_scope(client, db):
    fmc_vendor = await make_provider(db, "FMC V", "fv@x.com", brand=Brand.FMC)
    av_vendor = await make_provider(db, "AV V", "avv@x.com", brand=Brand.AV)
    src = uuid.uuid4()
    await _sync(db, [_view_row(src)], {src: fmc_vendor.id})

    fmc_admin = await _admin_headers(db, "fa@x.com", Brand.FMC)
    av_admin = await _admin_headers(db, "aa@x.com", Brand.AV)

    ok = await client.get(f"/admin/providers/{fmc_vendor.id}/payouts", headers=fmc_admin)
    assert ok.status_code == 200 and ok.json()["total"] == 1

    # An AV admin can't reach FMC vendors at all, and their own vendors are unsupported.
    hidden = await client.get(f"/admin/providers/{fmc_vendor.id}/payouts", headers=av_admin)
    assert hidden.status_code == 404
    own = await client.get(f"/admin/providers/{av_vendor.id}/payouts", headers=av_admin)
    assert own.status_code == 200 and own.json()["brand_supported"] is False


async def test_admin_token_cannot_use_provider_payouts(client, db):
    headers = await _admin_headers(db, "x@x.com", None)
    res = await client.get("/provider/payouts", headers=headers)
    assert res.status_code == 403


async def test_worker_payout_failure_keeps_rows_and_reports(db, monkeypatch):
    from app.sync import worker

    a = await make_provider(db, "A", "pa8@x.com")
    src = uuid.uuid4()
    await _sync(db, [_view_row(src)], {src: a.id})

    async def boom(brand):
        raise RuntimeError('relation "public.mis_partner_payouts" does not exist')

    monkeypatch.setattr(worker, "fetch_partner_payouts", boom)
    note = await worker._sync_payouts(db, Brand.FMC, {src: a.id})
    assert note.startswith("; payouts error:")
    # A failed fetch is never treated as an empty view.
    assert len((await db.execute(select(MisPayout))).scalars().all()) == 1


async def test_worker_payout_refresh_applies_and_skips_av(db, monkeypatch):
    from app.sync import worker

    a = await make_provider(db, "A", "pa9@x.com")
    src = uuid.uuid4()

    async def fetch(brand):
        return [_view_row(src), _view_row(src)]

    monkeypatch.setattr(worker, "fetch_partner_payouts", fetch)
    note = await worker._sync_payouts(db, Brand.FMC, {src: a.id})
    assert "2 files (2 new, 0 removed, 0 unmapped)" in note
    assert await worker._sync_payouts(db, Brand.AV, {}) == ""
