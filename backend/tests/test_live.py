"""Dashboard numbers computed live from CRM rows follow the same rules the old
daily rollups did: invalid / duplicate buckets, valid-only funnel, milestones
from stage logs with current-stage fallback, trends, lead filters, leaderboard."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.security import create_access_token, hash_password
from app.models import AdminUser
from app.models.enums import Brand
from tests.factories import lead_row, make_provider, map_source, provider_headers

pytestmark = pytest.mark.asyncio

D1 = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
D2 = datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc)
D9 = datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc)  # a later ISO week
RANGE = "from=2026-06-01&to=2026-06-10"


def _h(hours: float) -> datetime:
    from datetime import timedelta
    return D1 + timedelta(hours=hours)


async def _setup(db, crm, rows_for):
    p = await make_provider(db, "Acme", "acme@x.com")
    src = await map_source(db, p, name="Acme Web")
    crm.leads[Brand.FMC] = rows_for(src)
    return p, src


async def test_quality_buckets_and_valid_only_funnel(client, db, crm):
    p, _ = await _setup(db, crm, lambda s: [
        lead_row(s, phone=None),                                   # invalid: empty phone
        lead_row(s, phone="9999999999"),                           # invalid: repeated digit
        lead_row(s, phone="9000000002", is_duplicate=True),        # duplicate
        lead_row(s, phone="9000000003", raw_stage="contacted"),    # valid, contacted
        lead_row(s, phone="9000000004", raw_stage="processing"),   # valid, in process → qualified
        lead_row(s, phone="9000000005", raw_stage="disbursed"),    # valid, converted
        lead_row(s, phone="9000000006", raw_stage="dnp"),          # valid, side state
        lead_row(s, phone="9000000007", raw_stage="lost",
                 qualified_at=_h(3)),                              # valid, lost after qualifying
    ])
    body = (await client.get(f"/me/overview?{RANGE}", headers=provider_headers(p))).json()

    assert body["quality"] == {
        "delivered": 8, "invalid": 2, "duplicates": 1, "valid": 5,
        "invalid_rate": 0.25, "duplicate_rate": 0.125,
    }
    f = body["funnel"]
    assert f["delivered"] == 8
    # contacted comes from contacted_at or a linear stage ≥ contacted, so the
    # lost lead (no contacted_at, side state) doesn't count: contacted +
    # processing + disbursed = 3 (unchanged rule from the old rollups).
    assert f["contacted"] == 3
    assert f["qualified"] == 3  # processing (backfilled), disbursed, lost-with-qualified_at
    assert f["converted"] == 1
    assert f["dnp"] == 1 and f["lost"] == 1
    assert body["rates"]["conversion_rate"] == 0.2  # 1 of 5 valid
    assert body["data_as_of"]  # live: always "now"


async def test_milestones_from_logs_drive_time_to_metrics(client, db, crm):
    p, _ = await _setup(db, crm, lambda s: [
        lead_row(s, phone="9000000001", created_at=D1, raw_stage="qualified",
                 contacted_at=_h(2), qualified_at=_h(4)),
        lead_row(s, phone="9000000002", created_at=D1, raw_stage="contacted",
                 contacted_at=_h(4)),
    ])
    rates = (await client.get(f"/me/overview?{RANGE}", headers=provider_headers(p))).json()["rates"]
    assert rates["time_to_first_contact_sec"] == 3 * 3600  # avg of 2h and 4h
    assert rates["time_to_qualify_sec"] == 4 * 3600


async def test_trends_by_day_and_week(client, db, crm):
    p, _ = await _setup(db, crm, lambda s: [
        lead_row(s, phone="9000000001", created_at=D1),
        lead_row(s, phone="9000000002", created_at=D1, raw_stage="disbursed"),
        lead_row(s, phone="9000000003", created_at=D2, raw_stage=None),
        lead_row(s, phone="9000000004", created_at=D9),
    ])
    h = provider_headers(p)
    day = (await client.get(f"/me/trends?{RANGE}", headers=h)).json()["points"]
    assert [(pt["period"], pt["delivered"], pt["converted"]) for pt in day] == [
        ("2026-06-01", 2, 1), ("2026-06-02", 1, 0), ("2026-06-09", 1, 0),
    ]
    week = (await client.get(f"/me/trends?granularity=week&{RANGE}", headers=h)).json()["points"]
    assert [(pt["period"], pt["delivered"]) for pt in week] == [("2026-06-01", 3), ("2026-06-08", 1)]


async def test_leads_table_filters_paging_and_shape(client, db, crm):
    p, src = await _setup(db, crm, lambda s: [
        lead_row(s, phone="9000000001", full_name="Riya Sharma", created_at=D1, serial_no=11),
        lead_row(s, phone="9000000002", full_name="Arjun Rao", created_at=D2,
                 raw_stage="disbursed", serial_no=12),
        lead_row(s, phone="9000000003", full_name="Old Lead",
                 created_at=datetime(2025, 1, 1, tzinfo=timezone.utc)),
    ])
    h = provider_headers(p)

    page = (await client.get(f"/me/leads?{RANGE}&page_size=1", headers=h)).json()
    assert page["total"] == 2 and len(page["items"]) == 1
    first = page["items"][0]
    assert first["full_name"] == "Arjun Rao"  # newest first
    assert first["source_name"] == "Acme Web" and first["brand"] == "fmc"
    assert first["canonical_stage"] == "converted"

    converted = (await client.get(f"/me/leads?{RANGE}&stage=converted", headers=h)).json()
    assert [i["full_name"] for i in converted["items"]] == ["Arjun Rao"]
    by_name = (await client.get(f"/me/leads?{RANGE}&q=riya", headers=h)).json()
    assert [i["serial_no"] for i in by_name["items"]] == [11]
    by_phone = (await client.get(f"/me/leads?{RANGE}&q=0000002", headers=h)).json()
    assert [i["serial_no"] for i in by_phone["items"]] == [12]

    everything = (await client.get("/me/leads?all_time=true", headers=h)).json()
    assert everything["total"] == 3

    bad = await client.get("/me/leads?stage=nope", headers=h)
    assert bad.status_code == 422

    csv_text = (await client.get(f"/me/leads/export?{RANGE}", headers=h)).text
    assert csv_text.splitlines()[0].startswith("serial_no,full_name,phone,brand,source,stage")
    assert len(csv_text.strip().splitlines()) == 3


async def _admin(db, email, brand):
    admin = AdminUser(email=email, password_hash=hash_password("pw"), brand=brand)
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    token = create_access_token(subject=str(admin.id), role="admin",
                                brand=brand.value if brand else None)
    return {"Authorization": f"Bearer {token}"}


async def test_leaderboard_groups_live_leads_by_provider_and_scope(client, db, crm):
    a = await make_provider(db, "A", "a@x.com")
    b = await make_provider(db, "B", "b@x.com")
    av = await make_provider(db, "AV", "av@x.com", brand=Brand.AV)
    sa1, sa2 = await map_source(db, a), await map_source(db, a)
    sb, sav = await map_source(db, b), await map_source(db, av)
    crm.leads[Brand.FMC] = [
        lead_row(sa1, phone="9000000001", raw_stage="disbursed"),
        lead_row(sa2, phone="9000000002"),
        lead_row(sb, phone="9000000003", raw_stage=None),
    ]
    crm.leads[Brand.AV] = [lead_row(sav, phone="9000000004")]

    fmc_board = (await client.get(f"/admin/leaderboard?{RANGE}",
                                  headers=await _admin(db, "f@x.com", Brand.FMC))).json()
    rows = {r["provider_name"]: r for r in fmc_board}
    assert set(rows) == {"A", "B"}  # never the AV vendor
    assert rows["A"]["delivered"] == 2 and rows["A"]["converted"] == 1  # both sources
    assert rows["B"]["delivered"] == 1

    super_board = (await client.get(f"/admin/leaderboard?{RANGE}",
                                    headers=await _admin(db, "s@x.com", None))).json()
    assert {r["provider_name"] for r in super_board} == {"A", "B", "AV"}


async def test_sync_routes_report_live(client, db):
    h = await _admin(db, "s2@x.com", None)
    run = (await client.post("/admin/sync/run", headers=h)).json()
    assert run["triggered"] is False and "live" in run["detail"].lower()
    status = (await client.get("/admin/sync/status", headers=h)).json()
    assert status["running"] is False
    assert {s["brand"] for s in status["states"]} == {"fmc", "av"}
    assert all(s["last_status"] == "live" for s in status["states"])


async def test_crm_outage_is_a_503(client, db, crm):
    p, _ = await _setup(db, crm, lambda s: [lead_row(s)])
    crm.down = True
    res = await client.get(f"/me/overview?{RANGE}", headers=provider_headers(p))
    assert res.status_code == 503
    assert "unavailable" in res.json()["detail"]


async def test_leads_show_the_crm_stage_name(client, db, crm):
    p, _ = await _setup(db, crm, lambda s: [
        lead_row(s, phone="9000000001", raw_stage="pf_paid", created_at=D1),
        lead_row(s, phone="9000000002", raw_stage="docs_pending", created_at=D1),
        lead_row(s, phone="9000000003", raw_stage="sanctioned", created_at=D2),
        lead_row(s, phone="9000000004", raw_stage="created", created_at=D2),
        lead_row(s, phone="9000000005", raw_stage="pf_paid", created_at=D2),
        lead_row(s, phone="9000000006", raw_stage="brand_new_stage", created_at=D2),
        lead_row(s, phone="9000000007", raw_stage=None, created_at=D2),
    ])
    h = provider_headers(p)
    items = (await client.get(f"/me/leads?{RANGE}", headers=h)).json()["items"]
    by_stage = {i["crm_stage"]: i for i in items}
    assert by_stage["pf_paid"]["crm_stage_label"] == "PF Paid"
    assert by_stage["pf_paid"]["canonical_stage"] == "in_process"  # funnel bucket unchanged
    assert by_stage["docs_pending"]["crm_stage_label"] == "Docs Pending"
    assert by_stage["docs_pending"]["canonical_stage"] == "in_process"  # was mis-keyed
    assert by_stage["created"]["crm_stage_label"] == "Created"
    assert by_stage["brand_new_stage"]["crm_stage_label"] == "Brand New Stage"
    assert by_stage[None]["crm_stage_label"] is None

    only = (await client.get(f"/me/leads?{RANGE}&crm_stage=pf_paid", headers=h)).json()
    assert only["total"] == 2 and {i["crm_stage"] for i in only["items"]} == {"pf_paid"}

    stages = (await client.get(f"/me/stages?{RANGE}", headers=h)).json()
    assert stages == [  # CRM pipeline order, unknown stages last, no-stage leads skipped
        {"value": "created", "label": "Created", "count": 1},
        {"value": "docs_pending", "label": "Docs Pending", "count": 1},
        {"value": "sanctioned", "label": "Sanctioned", "count": 1},
        {"value": "pf_paid", "label": "PF Paid", "count": 2},
        {"value": "brand_new_stage", "label": "Brand New Stage", "count": 1},
    ]

    csv_lines = (await client.get(f"/me/leads/export?{RANGE}&crm_stage=sanctioned",
                                  headers=h)).text.strip().splitlines()
    assert csv_lines[0].split(",")[5:7] == ["stage", "funnel_stage"]
    assert csv_lines[1].split(",")[5:7] == ["Sanctioned", "in_process"]


async def test_av_leads_use_admitverse_stage_names(client, db, crm):
    p = await make_provider(db, "AVP", "avp@x.com", brand=Brand.AV)
    src = await map_source(db, p)
    crm.leads[Brand.AV] = [
        lead_row(src, phone="9000000001", raw_stage="qualified"),
        lead_row(src, phone="9000000002", raw_stage="dnp_pre_qualified"),
        lead_row(src, phone="9000000003", raw_stage="warm"),
    ]
    items = (await client.get(f"/me/leads?{RANGE}", headers=provider_headers(p))).json()["items"]
    assert {i["crm_stage"]: i["crm_stage_label"] for i in items} == {
        "qualified": "Hot", "dnp_pre_qualified": "DNP", "warm": "Warm",
    }


async def test_all_time_covers_every_dashboard_endpoint(client, db, crm):
    old = datetime(2025, 7, 10, 10, 0, tzinfo=timezone.utc)  # far outside the 30-day default
    p, _ = await _setup(db, crm, lambda s: [
        lead_row(s, phone="9000000001", created_at=old, raw_stage="disbursed"),
        lead_row(s, phone="9000000002", created_at=old, raw_stage="opportunity"),
    ])
    h = provider_headers(p)

    default = (await client.get("/me/overview", headers=h)).json()
    assert default["funnel"]["delivered"] == 0  # last 30 days only

    ov = (await client.get("/me/overview?all_time=true", headers=h)).json()
    assert ov["funnel"]["delivered"] == 2 and ov["funnel"]["converted"] == 1
    assert ov["date_from"] == "2025-07-10"  # span actually covered
    # all_time wins over any from/to the client also sends
    both = (await client.get(f"/me/overview?all_time=true&{RANGE}", headers=h)).json()
    assert both["funnel"] == ov["funnel"]

    q = (await client.get("/me/quality?all_time=true", headers=h)).json()
    assert q == ov["quality"]
    tr = (await client.get("/me/trends?all_time=true", headers=h)).json()
    assert [(pt["period"], pt["delivered"]) for pt in tr["points"]] == [("2025-07-10", 2)]
    split = (await client.get("/me/brand-split?all_time=true", headers=h)).json()
    assert split["rows"][0]["funnel"] == ov["funnel"]
    leads = (await client.get("/me/leads?all_time=true", headers=h)).json()
    assert leads["total"] == ov["funnel"]["delivered"]


async def test_all_time_with_no_leads_is_empty_not_an_error(client, db, crm):
    p, _ = await _setup(db, crm, lambda s: [])
    res = await client.get("/me/overview?all_time=true", headers=provider_headers(p))
    assert res.status_code == 200
    body = res.json()
    assert body["funnel"]["delivered"] == 0 and body["date_from"] == body["date_to"]
