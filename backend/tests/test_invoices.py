"""Partner invoices: relayed to the CRM with the caller's own sources and the
right company key, the PDF and fields checked first, the CRM's refusals
passed on, and the partner's own lead numbers on every student."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from app.config import settings
from app.crm import invoices as inv_mod
from app.models.enums import Brand
from tests.factories import lead_row, make_provider, map_source, provider_headers

pytestmark = pytest.mark.asyncio

# The real client functions, taken before the autouse `invoices` fixture swaps them.
REAL_ELIGIBLE, REAL_RAISE, REAL_PDF = inv_mod.eligible, inv_mod.raise_invoice, inv_mod.pdf

PDF = b"%PDF-1.4\n%test invoice\n"


def _eligible(src, lead, remaining="10000.00", name="Riya Sharma", bank="SBI"):
    return {"lead_bank_id": str(uuid.uuid4()), "lead_id": str(lead["crm_lead_id"]),
            "lead_source_id": str(src), "full_name": name, "bank_name": bank,
            "released": "1000000.00", "payout_due": remaining, "payout_paid": "0.00",
            "on_open_invoices": "0.00", "remaining": remaining}


async def _setup(db, crm, invoices, email="a@x.com", name="Altera"):
    p = await make_provider(db, name, email)
    src = await map_source(db, p)
    first, second = lead_row(src), lead_row(src, created_at=datetime(2026, 6, 2, tzinfo=timezone.utc))
    crm.leads.setdefault(Brand.FMC, []).extend([first, second])
    rows = [_eligible(src, second, "10000.00", "Riya Sharma"), _eligible(src, first, "2500.50", "Aman Gupta")]
    invoices.eligible_rows.extend(rows)
    return p, src, rows


def _form(rows, number="EDU/24-25/017", day=None, client_id=None, amounts=None):
    lines = [{"lead_bank_id": r["lead_bank_id"], **({"amount": amounts[i]} if amounts and amounts[i] else {})}
             for i, r in enumerate(rows)]
    data = {"invoice_number": number, "invoice_date": (day or datetime.now(timezone.utc).date()).isoformat(),
            "lines": json.dumps(lines)}
    if client_id:
        data["client_invoice_id"] = str(client_id)
    return data


async def test_eligible_lists_students_with_their_own_lead_numbers(client, db, crm, invoices):
    p, src, rows = await _setup(db, crm, invoices)
    r = await client.get("/provider/invoices/eligible", headers=provider_headers(p))
    assert r.status_code == 200
    body = r.json()
    assert body["gst_rate"] == "18.00"
    assert [(i["lead_no"], i["full_name"], i["remaining"]) for i in body["items"]] == [
        (2, "Riya Sharma", "10000.00"), (1, "Aman Gupta", "2500.50")]
    assert "lead_source_id" not in body["items"][0]  # never shown to the partner
    assert invoices.calls[-1] == ("eligible", Brand.FMC, False, [src])


async def test_raise_invoice_sends_scope_gst_and_pdf(client, db, crm, invoices):
    p, src, rows = await _setup(db, crm, invoices)
    cid = uuid.uuid4()
    r = await client.post("/provider/invoices", headers=provider_headers(p),
                          data=_form(rows, client_id=cid, amounts=["4000.00", None]),
                          files={"pdf": ("mine.pdf", PDF, "application/pdf")})
    assert r.status_code == 201, r.text
    inv = r.json()
    assert (inv["lines_total"], inv["gst_amount"], inv["amount"]) == ("6500.50", "1170.09", "7670.59")
    assert inv["status"] == "raised" and inv["raised_via"] == "mis" and inv["has_pdf"] is True
    assert [(l["lead_no"], l["amount"]) for l in inv["lines"]] == [(2, "4000.00"), (1, "2500.50")]
    sent = invoices.raised[-1]
    assert sent["lead_source_id"] == src and sent["invoice_id"] == cid
    assert sent["gst_rate"] == Decimal("18") and sent["pdf"] == PDF
    assert sent["partner_name"] == "Altera" and sent["partner_user_email"] == "a@x.com"
    assert sent["lines"][1]["amount"] is None  # "all that's left" is the CRM's call

    # A retry with the same id gives back the same invoice, not a second one.
    again = await client.post("/provider/invoices", headers=provider_headers(p),
                              data=_form(rows, client_id=cid, amounts=["4000.00", None]),
                              files={"pdf": ("mine.pdf", PDF, "application/pdf")})
    assert again.json()["id"] == inv["id"] and len(invoices.raised) == 1

    listed = (await client.get("/provider/invoices", headers=provider_headers(p))).json()
    assert [i["id"] for i in listed] == [inv["id"]] and listed[0]["lines"][0]["lead_no"] == 2

    pdf = await client.get(f"/provider/invoices/{inv['id']}/pdf", headers=provider_headers(p))
    assert pdf.status_code == 200 and pdf.content == PDF
    assert pdf.headers["content-type"] == "application/pdf" and pdf.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("case,expect", [
    ("no_pdf", "Attach your invoice as a PDF."),
    ("not_pdf", "The attachment must be a PDF file."),
    ("big_pdf", "The PDF is larger than 4 MB."),
    ("future", "The invoice date can't be in the future."),
    ("no_number", "Enter your invoice number."),
    ("no_lines", "Pick at least one student."),
    ("twice", "The same student is on this invoice twice."),
    ("garbage", "The chosen students couldn't be read. Reload and try again."),
])
async def test_raise_invoice_checks_fields_before_calling_the_crm(client, db, crm, invoices, case, expect):
    p, src, rows = await _setup(db, crm, invoices)
    data, files = _form(rows), {"pdf": ("mine.pdf", PDF, "application/pdf")}
    if case == "no_pdf":
        files = None
    elif case == "not_pdf":
        files = {"pdf": ("mine.pdf", b"PK\x03\x04 a zip", "application/pdf")}
    elif case == "big_pdf":
        files = {"pdf": ("mine.pdf", b"%PDF" + b"0" * (4 * 1024 * 1024), "application/pdf")}
    elif case == "future":
        data = _form(rows, day=datetime.now(timezone.utc).date() + timedelta(days=2))
    elif case == "no_number":
        data = _form(rows, number="   ")
    elif case == "no_lines":
        data["lines"] = "[]"
    elif case == "twice":
        data = _form([rows[0], rows[0]])
    elif case == "garbage":
        data["lines"] = "not json"
    r = await client.post("/provider/invoices", headers=provider_headers(p), data=data, files=files)
    assert r.status_code == 422 and r.json()["detail"] == expect
    assert not invoices.raised


async def test_crm_refusals_pass_through(client, db, crm, invoices):
    p, src, rows = await _setup(db, crm, invoices)
    files = {"pdf": ("mine.pdf", PDF, "application/pdf")}
    ok = await client.post("/provider/invoices", headers=provider_headers(p), data=_form(rows[:1]), files=files)
    assert ok.status_code == 201
    dup = await client.post("/provider/invoices", headers=provider_headers(p),
                            data=_form(rows[1:], number="edu/24-25/017"), files=files)
    assert dup.status_code == 409 and dup.json()["detail"] == "Invoice number edu/24-25/017 is already used"
    invoices.down = True
    down = await client.post("/provider/invoices", headers=provider_headers(p),
                             data=_form(rows[1:], number="X-2"), files=files)
    assert down.status_code == 503


async def test_partners_never_reach_each_others_invoices(client, db, crm, invoices):
    a, a_src, a_rows = await _setup(db, crm, invoices)
    b, b_src, b_rows = await _setup(db, crm, invoices, email="b@x.com", name="Beta")
    files = {"pdf": ("mine.pdf", PDF, "application/pdf")}
    inv = (await client.post("/provider/invoices", headers=provider_headers(a),
                             data=_form(a_rows), files=files)).json()
    # B sees none of A's students or invoices, and can't fetch A's PDF.
    elig = (await client.get("/provider/invoices/eligible", headers=provider_headers(b))).json()
    assert {i["lead_bank_id"] for i in elig["items"]} == {r["lead_bank_id"] for r in b_rows}
    assert (await client.get("/provider/invoices", headers=provider_headers(b))).json() == []
    assert (await client.get(f"/provider/invoices/{inv['id']}/pdf", headers=provider_headers(b))).status_code == 404
    # B billing A's students is refused by the CRM (out of scope).
    r = await client.post("/provider/invoices", headers=provider_headers(b),
                          data=_form(a_rows, number="B-1"), files=files)
    assert r.status_code == 404
    assert invoices.calls[0][3] == [a_src] and all(c[3] == [b_src] for c in invoices.calls[1:])


async def test_several_sources_pick_the_students_one(client, db, crm, invoices):
    p, src, rows = await _setup(db, crm, invoices)
    other = await map_source(db, p, name="second")
    extra = lead_row(other)
    crm.leads[Brand.FMC].append(extra)
    invoices.eligible_rows.append(_eligible(other, extra, "700.00", "Neha"))
    files = {"pdf": ("mine.pdf", PDF, "application/pdf")}
    r = await client.post("/provider/invoices", headers=provider_headers(p),
                          data=_form(invoices.eligible_rows[2:]), files=files)
    assert r.status_code == 201 and invoices.raised[-1]["lead_source_id"] == other
    mixed = await client.post("/provider/invoices", headers=provider_headers(p),
                              data=_form([rows[0], invoices.eligible_rows[2]], number="M-1"), files=files)
    assert mixed.status_code == 422 and "different FundMyCampus accounts" in mixed.json()["detail"]


async def test_demo_partner_uses_the_demo_key(client, db, crm, invoices):
    p, src, rows = await _setup(db, crm, invoices)
    p.is_demo = True
    await db.commit()
    await client.get("/provider/invoices/eligible", headers=provider_headers(p))
    assert invoices.calls[-1][2] is True


async def test_admitverse_partner_has_no_invoices(client, db, crm, invoices):
    p = await make_provider(db, "AV partner", "av@x.com", brand=Brand.AV)
    await map_source(db, p)
    h = provider_headers(p)
    assert (await client.get("/provider/invoices/eligible", headers=h)).json()["items"] == []
    assert (await client.get("/provider/invoices", headers=h)).json() == []
    r = await client.post("/provider/invoices", headers=h, data={"invoice_number": "A", "invoice_date": "2026-10-01", "lines": "[]"})
    assert r.status_code == 404
    assert not invoices.calls


async def test_invoice_routes_need_a_partner_login(client, db, invoices):
    for method, path in [("GET", "/provider/invoices/eligible"), ("GET", "/provider/invoices"),
                         ("POST", "/provider/invoices"), ("GET", f"/provider/invoices/{uuid.uuid4()}/pdf")]:
        assert (await client.request(method, path)).status_code == 401


# --- the real HTTP client, against a mock CRM ---
async def test_client_sends_multipart_and_maps_errors(monkeypatch):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path.endswith("/pdf"):
            return httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})
        if req.method == "POST":
            return httpx.Response(409, json={"detail": "Invoice number X is already used"})
        return httpx.Response(503, json={"detail": "storage down"})

    real = httpx.AsyncClient
    monkeypatch.setattr(inv_mod.chat.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(settings, "fmc_crm_api_url", "https://crm.test/api/v1")
    monkeypatch.setattr(settings, "fmc_crm_api_key", "crmk_live_fmc")
    monkeypatch.setattr(settings, "fmc_demo_crm_api_key", "crmk_live_demo")
    src, inv_id, bank = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    with pytest.raises(inv_mod.InvoiceError) as e:
        await REAL_RAISE(Brand.FMC, True, [src], lead_source_id=src, invoice_id=inv_id,
                         invoice_number="X", invoice_date=datetime(2026, 10, 24).date(),
                         gst_rate=Decimal("18"), partner_name="Demo", partner_user_email=None,
                         lines=[{"lead_bank_id": bank, "amount": Decimal("100")}, {"lead_bank_id": src, "amount": None}],
                         pdf=PDF)
    assert e.value.status == 409 and e.value.detail == "Invoice number X is already used"
    req = seen[-1]
    assert req.headers["X-API-Key"] == "crmk_live_demo" and str(req.url) == "https://crm.test/api/v1/mis/invoices"
    body = req.read()
    assert b'name="pdf"; filename="invoice.pdf"' in body and PDF in body
    payload = json.loads(body.split(b'name="payload"\r\n\r\n')[1].split(b"\r\n--")[0])
    assert payload["mis_invoice_id"] == str(inv_id) and payload["gst_rate"] == "18.00"
    assert payload["lead_source_ids"] == [str(src)] and payload["invoice_date"] == "2026-10-24"
    assert payload["lines"] == [{"lead_bank_id": str(bank), "amount": "100.00"}, {"lead_bank_id": str(src)}]

    assert await REAL_PDF(Brand.FMC, False, [src], inv_id) == PDF
    assert seen[-1].headers["X-API-Key"] == "crmk_live_fmc"

    from app.crm.chat import ChatUnavailable
    with pytest.raises(ChatUnavailable, match="Invoicing is unavailable"):  # CRM 503: retry later
        await REAL_ELIGIBLE(Brand.FMC, False, [src])
