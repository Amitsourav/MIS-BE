"""Partner Leads page: their own lead number, the FMC counsellor, and the
lenders a file was logged in with — real bank only, never the route or the
aggregator, and never FMC's serial / brand / source."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.crm.lenders import lender_display
from app.models.enums import Brand
from tests.factories import lead_row, make_provider, map_source, payout_row, provider_headers

pytestmark = pytest.mark.asyncio

D1 = datetime(2026, 6, 1, 10, tzinfo=timezone.utc)
D2 = datetime(2026, 6, 2, 10, tzinfo=timezone.utc)
D3 = datetime(2026, 6, 3, 10, tzinfo=timezone.utc)


def _lender(bank, status, amount=None, app=None, tranches=None):
    return {"bank_name": bank, "bank_status": status, "loan_amount": amount,
            "application_id": app, "logged_in_at": None, "lead_bank_id": "lb-1",
            "tranches": tranches or []}


@pytest.mark.parametrize("crm_name, shown", [
    ("UC PNB", "PNB"),
    ("UC Axis", "Axis"),
    ("Axis Direct (UC Code)", "Axis"),
    ("Axis Maharashtra", "Axis"),
    ("Credila Hemant", "Credila"),
    ("UniCred Credila Domestic", "Credila"),
    ("UniCred", None),
    ("UniCred Normal", None),
    ("Nomad Normal", None),
    ("Nomad US", None),
    ("UC  SBI", "SBI"),  # a future UniCred route
    ("Nomad Canada", None),  # a future Nomad route
    ("SBI", "SBI"),
    ("Axis", "Axis"),
    (None, None),
    ("  ", None),
])
async def test_lender_display_shows_the_bank_not_the_route(crm_name, shown):
    assert lender_display(crm_name) == shown


async def test_leads_carry_counsellor_and_post_login_lenders(client, db, crm):
    p = await make_provider(db, "Acme", "acme@x.com")
    src = await map_source(db, p, name="Acme WhatsApp")
    other = await map_source(db, await make_provider(db, "B", "b@x.com"))
    first, second, third = (
        lead_row(src, phone="9000000001", full_name="Riya", created_at=D1, serial_no=8801),
        lead_row(src, phone="9000000002", full_name="Arjun", created_at=D2,
                 raw_stage="sanctioned", serial_no=8802),
        lead_row(src, phone="9000000003", full_name="Meera", created_at=D3,
                 raw_stage="logged_in", serial_no=8803),
    )
    theirs = lead_row(other, phone="9000000009", full_name="Not Yours", created_at=D2)
    crm.leads[Brand.FMC] = [first, second, third, theirs]
    crm.lead_info[Brand.FMC] = {
        first["crm_lead_id"]: {"lead_source_id": src, "counsellor_name": "Priya K",
                               "loan_amount_lakh": None, "lenders": []},
        second["crm_lead_id"]: {
            "lead_source_id": src, "counsellor_name": "Rahul M",
            "loan_amount_lakh": Decimal("25.00"),
            "lenders": [_lender("UC PNB", "sanctioned", Decimal("2000000"), "PNB-77", [
                            # as the view's jsonb gives them: rupees, ISO dates
                            {"tranche_no": 1, "amount": Decimal("950000.0"), "disbursed_on": "2026-02-01"},
                            {"tranche_no": 2, "amount": Decimal("868546.005"), "disbursed_on": "2026-06-12"},
                        ]),
                        _lender("Nomad Normal", "loan_login", None, "NM-1"),
                        _lender("Credila Hemant", "lost", None, None)],
        },
        third["crm_lead_id"]: {
            "lead_source_id": src, "counsellor_name": "Rahul M",
            "loan_amount_lakh": Decimal("18.5"),
            "lenders": [_lender("Axis Maharashtra", "loan_login", None, "AX-9")],
        },
        theirs["crm_lead_id"]: {"lead_source_id": other, "counsellor_name": "Secret",
                                "loan_amount_lakh": None,
                                "lenders": [_lender("SBI", "disbursed", Decimal("1"), "X")]},
    }
    h = provider_headers(p)

    body = (await client.get("/me/leads?all_time=true", headers=h)).json()
    by_name = {i["full_name"]: i for i in body["items"]}
    assert set(by_name) == {"Riya", "Arjun", "Meera"}
    assert [by_name[n]["lead_no"] for n in ("Riya", "Arjun", "Meera")] == [1, 2, 3]
    assert "8802" not in str(body) and "Acme WhatsApp" not in str(body)
    assert "Secret" not in str(body)

    assert by_name["Riya"]["counsellor_name"] == "Priya K"
    assert by_name["Riya"]["lenders"] == []  # not logged in anywhere yet

    arjun = by_name["Arjun"]
    assert arjun["counsellor_name"] == "Rahul M"
    assert arjun["lenders"] == [
        {"bank": "PNB", "status": "sanctioned", "status_label": "Sanctioned",
         "amount": "2000000.00", "amount_kind": "sanctioned", "application_no": "PNB-77",
         "tranches": [
             {"tranche_no": 1, "amount": "950000.00", "disbursed_on": "2026-02-01"},
             {"tranche_no": 2, "amount": "868546.01", "disbursed_on": "2026-06-12"},
         ]},
        {"bank": None, "status": "loan_login", "status_label": "Logged in",  # aggregator hidden
         "amount": "2500000.00", "amount_kind": "applied", "application_no": "NM-1", "tranches": []},
        {"bank": "Credila", "status": "lost", "status_label": "Declined",
         "amount": "2500000.00", "amount_kind": "applied", "application_no": None, "tranches": []},
    ]
    assert "UC PNB" not in str(body) and "Nomad" not in str(body) and "lb-1" not in str(body)

    meera = by_name["Meera"]["lenders"][0]
    assert (meera["bank"], meera["amount"], meera["amount_kind"]) == ("Axis", "1850000.00", "applied")

    # Numbers don't shift with the date filter.
    june2 = (await client.get("/me/leads?from=2026-06-02&to=2026-06-02", headers=h)).json()
    assert [i["lead_no"] for i in june2["items"]] == [2]
    assert crm.calls[-1] == ("lead_info_rows", Brand.FMC, [src])  # scoped to A's sources

    text = (await client.get("/me/leads/export?all_time=true", headers=h)).text.lstrip("﻿")
    rows = {r["full_name"]: r for r in csv.DictReader(io.StringIO(text))}
    assert rows["Arjun"]["lead_no"] == "2"
    assert rows["Arjun"]["counsellor"] == "Rahul M"
    assert rows["Arjun"]["lender"] == "PNB; —; Credila"
    assert rows["Arjun"]["lender_status"] == "Sanctioned; Logged in; Declined"
    assert rows["Arjun"]["loan_amount"] == "2000000.00; 2500000.00; 2500000.00"
    assert rows["Arjun"]["application_no"] == "PNB-77; NM-1; "
    assert "8802" not in text and "UC PNB" not in text and "Not Yours" not in text


async def test_leads_work_when_the_crm_has_no_details_view(client, db, crm):
    """Before the CRM view is deployed the reader returns []: the page still
    loads, just without counsellor / lenders."""
    p = await make_provider(db, "Acme", "acme2@x.com")
    src = await map_source(db, p)
    crm.leads[Brand.FMC] = [lead_row(src, created_at=D1)]
    item = (await client.get("/me/leads?all_time=true",
                             headers=provider_headers(p))).json()["items"][0]
    assert item["lead_no"] == 1 and item["counsellor_name"] is None and item["lenders"] == []


async def test_payout_rows_show_the_bank_and_partner_lead_no(client, db, crm):
    p = await make_provider(db, "Acme", "acme3@x.com")
    src = await map_source(db, p)
    a, b = lead_row(src, created_at=D1), lead_row(src, created_at=D2)
    crm.leads[Brand.FMC] = [a, b]
    crm.payouts = [payout_row(src, lead_id=b["crm_lead_id"], bank="UC PNB", serial_no=8802),
                   payout_row(src, lead_id=a["crm_lead_id"], bank="UniCred Normal")]
    body = (await client.get("/provider/payouts", headers=provider_headers(p))).json()
    assert {(i["lead_no"], i["bank_name"]) for i in body["items"]} == {(2, "PNB"), (1, None)}
    assert "UC PNB" not in str(body) and "UniCred" not in str(body) and "8802" not in str(body)

    csv_text = (await client.get("/provider/payouts/export",
                                 headers=provider_headers(p))).text
    assert "UC PNB" not in csv_text and "UniCred" not in csv_text
