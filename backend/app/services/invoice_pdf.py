"""The PDF of a partner's invoice to FundMyCampus, made by the MIS from the
partner's billing details. A tax invoice when they are GST-registered, a plain
invoice (no GST) otherwise."""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from xml.sax.saxutils import escape

from app.models.billing import BillingProfile
from app.services.billing import STATES, Tax, amount_in_words


@dataclass
class BillTo:
    name: str
    gstin: str | None
    address_lines: list[str]
    state_code: str | None


@dataclass
class PdfLine:
    lead_no: int | None
    full_name: str | None
    bank_name: str | None
    amount: Decimal  # before GST


def _rs(d: Decimal) -> str:
    """Indian grouping: 1234567.5 -> "12,34,567.50"."""
    d = d.quantize(Decimal("0.01"))
    sign = "-" if d < 0 else ""
    whole, frac = f"{abs(d):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}{whole}.{frac}"


def _state(code: str | None) -> str:
    return f"{STATES.get(code or '', '—')} ({code})" if code else "—"


def build_invoice_pdf(
    *,
    profile: BillingProfile,
    bill_to: BillTo,
    invoice_number: str,
    invoice_date: date,
    lines: list[PdfLine],
    tax: Tax,
) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
        title=f"Invoice {invoice_number}", author=profile.legal_name,
    )
    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["Normal"], fontSize=9, leading=12)
    small = ParagraphStyle("s", parent=body, fontSize=8, leading=10, textColor=colors.HexColor("#475569"))
    bold = ParagraphStyle("bd", parent=body, fontName="Helvetica-Bold")
    right = ParagraphStyle("r", parent=body, alignment=TA_RIGHT)
    title = ParagraphStyle("t", parent=ss["Title"], fontSize=16, leading=20, spaceAfter=2)
    e = lambda s: escape(s or "")  # noqa: E731

    reg = profile.gst_registered
    story: list = [Paragraph("TAX INVOICE" if reg else "INVOICE", title)]
    if not reg:
        story.append(Paragraph("Supplier not registered under GST: no GST charged.", ParagraphStyle(
            "c", parent=small, alignment=1)))
    story.append(Spacer(1, 4 * mm))

    supplier = [
        f"<b>{e(profile.legal_name)}</b>",
        e(profile.address_line1),
        *([e(profile.address_line2)] if profile.address_line2 else []),
        f"{e(profile.city)} - {e(profile.pincode)}",
        f"State: {e(_state(profile.state_code))}",
        *([f"GSTIN: <b>{e(profile.gstin)}</b>"] if reg and profile.gstin else []),
        f"PAN: {e(profile.pan)}",
        f"{e(profile.email)} · {e(profile.phone)}",
    ]
    meta = [
        f"Invoice no.: <b>{e(invoice_number)}</b>",
        f"Invoice date: {invoice_date.strftime('%d %b %Y')}",
        f"Place of supply: {e(_state(bill_to.state_code))}",
    ]
    head = Table(
        [[Paragraph("<br/>".join(supplier), body), Paragraph("<br/>".join(meta), right)]],
        colWidths=[110 * mm, 70 * mm],
    )
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [head, Spacer(1, 5 * mm)]

    billed = [
        "<b>Bill to</b>",
        f"<b>{e(bill_to.name)}</b>",
        *[e(x) for x in bill_to.address_lines],
        *([f"GSTIN: {e(bill_to.gstin)}"] if bill_to.gstin else []),
        f"State: {e(_state(bill_to.state_code))}",
    ]
    box = Table([[Paragraph("<br/>".join(billed), body)]], colWidths=[180 * mm])
    box.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#cbd5e1")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story += [box, Spacer(1, 5 * mm)]

    rows = [[Paragraph("<b>#</b>", body), Paragraph("<b>Description</b>", body),
             Paragraph("<b>Amount (Rs.)</b>", right)]]
    taxable = sum((l.amount for l in lines), Decimal("0"))
    for i, l in enumerate(lines, 1):
        who = " ".join(x for x in [f"#{l.lead_no}" if l.lead_no else "", l.full_name or "Student"] if x)
        desc = f"Referral payout for {e(who)}" + (f" ({e(l.bank_name)})" if l.bank_name else "")
        rows.append([str(i), Paragraph(desc, body), Paragraph(_rs(l.amount), right)])

    def total_row(label: str, value: Decimal, strong: bool = False) -> list:
        st = bold if strong else body
        return ["", Paragraph(label, ParagraphStyle("x", parent=st, alignment=TA_RIGHT)),
                Paragraph(_rs(value), ParagraphStyle("y", parent=st, alignment=TA_RIGHT))]

    n_items = len(rows)
    if reg:
        rows.append(total_row("Taxable value", taxable))
        if tax.igst:
            rows.append(total_row(f"IGST @ {tax.rate.normalize():f}%", tax.igst))
        else:
            half = (tax.rate / 2).normalize()
            rows.append(total_row(f"CGST @ {half:f}%", tax.cgst))
            rows.append(total_row(f"SGST @ {half:f}%", tax.sgst))
    grand = taxable + tax.total
    rows.append(total_row("Invoice total", grand, strong=True))

    table = Table(rows, colWidths=[10 * mm, 135 * mm, 35 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
        ("LINEBELOW", (0, 0), (-1, n_items - 1), 0.4, colors.HexColor("#e2e8f0")),
        ("LINEABOVE", (1, n_items), (-1, n_items), 0.8, colors.HexColor("#94a3b8")),
        ("LINEABOVE", (1, -1), (-1, -1), 0.8, colors.HexColor("#0f172a")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    story += [table, Spacer(1, 3 * mm),
              Paragraph(f"<b>Amount in words:</b> {amount_in_words(grand)}", body),
              Spacer(1, 6 * mm)]

    bank = [
        "<b>Pay to</b>",
        f"Account name: {e(profile.bank_account_name)}",
        f"Account no.: {e(profile.bank_account_number)}",
        f"IFSC: {e(profile.bank_ifsc)}",
        f"Bank: {e(profile.bank_name)}" + (f", {e(profile.bank_branch)}" if profile.bank_branch else ""),
    ]
    sign = [
        f"For <b>{e(profile.legal_name)}</b>",
        "<br/><br/>",
        e(profile.signatory_name) or "Authorised signatory",
    ]
    foot = Table([[Paragraph("<br/>".join(bank), body), Paragraph("<br/>".join(sign), right)]],
                 colWidths=[110 * mm, 70 * mm])
    foot.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [foot, Spacer(1, 6 * mm),
              Paragraph("This is a computer-generated invoice and needs no physical signature. "
                        "Made on the FundMyCampus partner portal.", small)]
    doc.build(story)
    return buf.getvalue()
