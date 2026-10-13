"""Payout page pie: where the partner's money stands (cash transferred, TDS,
and pending split by the student's latest partner invoice)."""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta

import pytest

from app.services import payouts as P
from tests.factories import make_provider, map_source, payout_row, provider_headers

pytestmark = pytest.mark.asyncio

TODAY = date(2026, 10, 12)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    monkeypatch.setattr(P, "_today", lambda: TODAY)


async def test_pie_splits_paid_and_pending(client, db, crm):
    p = await make_provider(db, "A", "pie@x.com")
    src = await map_source(db, p)
    crm.payouts = [
        payout_row(src, earned="1000.00", paid="1000.00", tds="100.00", invoice_status="paid"),
        payout_row(src, earned="500.00"),  # no invoice
        payout_row(src, earned="300.00", invoice_status="raised", invoice_due=TODAY),  # due today: not late
        payout_row(src, earned="200.00", invoice_status="raised",
                   invoice_due=TODAY - timedelta(days=1)),
        payout_row(src, earned="80.00", invoice_status="cancelled"),
        payout_row(src, earned="900.00", paid="600.00", invoice_status="paid"),  # later release
        payout_row(src, earned="50.00", invoice_status="RAISED"),  # no due date
    ]
    s = (await client.get("/provider/payouts", headers=provider_headers(p))).json()["summary"]
    assert s["paid"] == "1600.00" and s["pending"] == "1430.00"
    assert s["cash_transferred"] == "1500.00" and s["tds"] == "100.00"
    assert s["invoice_not_raised"] == "880.00"  # 500 + 80 + 300 left after a paid invoice
    assert s["invoice_raised"] == "350.00"
    assert s["invoice_overdue"] == "200.00"
    parts = ("cash_transferred", "tds", "invoice_not_raised", "invoice_raised", "invoice_overdue")
    from decimal import Decimal
    assert sum(Decimal(s[k]) for k in parts) == Decimal(s["paid"]) + Decimal(s["pending"])


async def test_items_carry_invoice_and_tds(client, db, crm):
    p = await make_provider(db, "A", "pie2@x.com")
    src = await map_source(db, p)
    crm.payouts = [payout_row(src, earned="1000.00", paid="400.00", tds="40.00",
                              invoice_status="raised", invoice_due=TODAY - timedelta(days=3))]
    it = (await client.get("/provider/payouts", headers=provider_headers(p))).json()["items"][0]
    assert it["tds"] == "40.00" and it["net_paid"] == "360.00"
    assert it["invoice_number"] == "INV-1" and it["invoice_status"] == "raised"
    assert it["invoice_due_date"] == "2026-10-09" and it["invoice_state"] == "overdue"

    r = await client.get("/provider/payouts/export", headers=provider_headers(p))
    row = next(csv.DictReader(io.StringIO(r.text.lstrip("﻿"))))
    assert row["tds"] == "40.00" and row["net_paid"] == "360.00"
    assert row["invoice_status"] == "raised" and row["invoice_due_date"] == "2026-10-09"


async def test_no_invoices_yet_is_all_not_raised(client, db, crm):
    p = await make_provider(db, "A", "pie3@x.com")
    src = await map_source(db, p)
    crm.payouts = [payout_row(src, earned="700.00", paid="200.00")]
    s = (await client.get("/provider/payouts", headers=provider_headers(p))).json()["summary"]
    assert s["cash_transferred"] == "200.00" and s["tds"] == "0.00"
    assert s["invoice_not_raised"] == "500.00"
    assert s["invoice_raised"] == "0.00" and s["invoice_overdue"] == "0.00"


async def test_pie_adds_up_to_earned_when_overpaid(client, db, crm):
    # Altera after the switch to 0.60%: paid at the old agreed amount.
    p = await make_provider(db, "A", "pie4@x.com")
    src = await map_source(db, p)
    crm.payouts = [
        payout_row(src, earned="9830.59", paid="11040.00"),
        payout_row(src, earned="6558.00"),
    ]
    s = (await client.get("/provider/payouts", headers=provider_headers(p))).json()["summary"]
    assert s["earned"] == "16388.59" and s["overpaid"] == "1209.41"
    assert s["cash_transferred"] == "9830.59" and s["invoice_not_raised"] == "6558.00"
    from decimal import Decimal
    parts = ("cash_transferred", "tds", "invoice_not_raised", "invoice_raised", "invoice_overdue")
    assert sum(Decimal(s[k]) for k in parts) == Decimal(s["earned"])


async def test_invoice_covers_only_its_amount(client, db, crm):
    p = await make_provider(db, "A", "pie5@x.com")
    src = await map_source(db, p)
    crm.payouts = [
        payout_row(src, earned="1000.00", invoice_status="raised", invoice_due=TODAY,
                   invoice_amount="600.00"),
        payout_row(src, earned="500.00", invoice_status="raised",
                   invoice_due=TODAY - timedelta(days=1), invoice_amount="900.00"),
    ]
    body = (await client.get("/provider/payouts", headers=provider_headers(p))).json()
    s = body["summary"]
    assert s["invoice_raised"] == "600.00"
    assert s["invoice_overdue"] == "500.00"  # capped at what's pending
    assert s["invoice_not_raised"] == "400.00"
    assert {i["invoice_amount"] for i in body["items"]} == {"600.00", "900.00"}


async def test_balance_is_earned_minus_paid(client, db, crm):
    p = await make_provider(db, "A", "bal@x.com")
    src = await map_source(db, p)
    crm.payouts = [
        payout_row(src, earned="9830.59", paid="11040.00"),  # paid extra
        payout_row(src, earned="6558.00", paid="1000.00"),  # still owed
    ]
    body = (await client.get("/provider/payouts", headers=provider_headers(p))).json()
    assert body["summary"]["balance"] == "4348.59"  # 16388.59 - 12040
    assert sorted(i["balance"] for i in body["items"]) == ["-1209.41", "5558.00"]
    r = await client.get("/provider/payouts/export", headers=provider_headers(p))
    rows = list(csv.DictReader(io.StringIO(r.text.lstrip("﻿"))))
    assert sorted(x["balance"] for x in rows) == ["-1209.41", "5558.00"]
