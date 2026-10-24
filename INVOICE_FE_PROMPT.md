# MIS Frontend — "Raise invoice" on the Payout page: build prompt

> For the MIS **frontend** (`MIS FE`).
> - Don't rename fields. Money is a **2-decimal string**; format it with `inr()`.
> - Amounts are **before GST** everywhere except `gst_amount`, `amount` and `net_amount`.
> - The existing `/api` proxy already forwards file uploads and PDF downloads. Nothing to change there.

**Why:** partners bill FundMyCampus for their payouts.
1. They press **Raise invoice** and pick the students.
2. They type their own invoice number and date, and attach their PDF.
3. The invoice goes straight to FMC's finance team as "raised", for payouts + 18% GST.

---

## 1 — API

| Call | Use |
|---|---|
| `GET /provider/invoices/eligible` | Students still to invoice → `InvoiceEligible` |
| `POST /provider/invoices` (multipart) | Raise one → `201 Invoice` |
| `GET /provider/invoices` | The partner's invoices, newest first → `Invoice[]` |
| `GET /provider/invoices/{id}/pdf` | The PDF (download) |

```ts
// lib/types.ts
export interface EligibleStudent {
  lead_bank_id: string;
  lead_id: string;
  lead_no: number | null;           // the partner's own #N, as on Leads
  full_name: string | null;
  bank_name: string | null;         // null → "—"
  released: MoneyString;            // what the bank released
  payout_due: MoneyString;
  payout_paid: MoneyString;
  on_open_invoices: MoneyString;
  remaining: MoneyString;           // what can still be invoiced (before GST)
}
export interface InvoiceEligible { gst_rate: MoneyString; items: EligibleStudent[]; }  // gst_rate "18.00"

export interface InvoiceLine {
  lead_id: string | null; lead_no: number | null;
  full_name: string | null; bank_name: string | null; amount: MoneyString;
}
export interface Invoice {
  id: string;
  invoice_number: string;
  invoice_date: string;             // "2026-10-24"
  due_date: string | null;
  lines_total: MoneyString;         // before GST
  gst_rate: MoneyString;
  gst_amount: MoneyString;
  amount: MoneyString;              // total billed = lines_total + gst_amount
  tds_amount: MoneyString;
  net_amount: MoneyString;          // amount − TDS
  status: "raised" | "paid" | "cancelled";
  paid_on: string | null;
  raised_via: "mis" | "crm";        // crm = entered by FMC
  has_pdf: boolean;
  created_at: string | null;
  lines: InvoiceLine[];
}
```

**Errors:** every error is `{detail}` with a sentence you can show as it is.
- **422:** shown inside the dialog. Examples: "Attach your invoice as a PDF.", "The PDF is larger than 4 MB.", "Riya Sharma: Rs 20000.00 is more than the Rs 14547.02 still to invoice."
- **409:** "Invoice number … is already used". Show it under the invoice-number field.
- **404:** "Payouts are tracked for FundMyCampus loans" (Admitverse), or something not theirs.
- **429:** "Too many invoices, wait a minute".
- **503:** "Couldn't send the invoice right now, try again". Retry with the **same** `client_invoice_id`. Nothing was saved.

---

## 2 — Payout page: the button

- Put **Raise invoice** in the page header, next to the CSV export.
- Hide it when `brand_supported` is false (Admitverse).
- Fetch `/provider/invoices/eligible` with the page.
  - If `items` is empty, disable the button with the tooltip "Nothing left to invoice".
  - Otherwise show the count: **Raise invoice (5)**.

## 3 — The dialog (full-screen sheet on mobile)

**Students:** a list of `items` with a checkbox each, **all ticked** by default, plus a "Select all" checkbox.
- Each row shows `#lead_no` · name · bank (`bank_name ?? "—"`) · **`inr(remaining)`**.
- Each student is billed for all of `remaining`. There's no amount editing in this version.

**Totals:** live, from the ticked rows. These are preview only; the server's figures are final.

```
Payouts           inr(sum of ticked remaining)
GST {Number(gst_rate)}%    inr(round_half_up(sum × gst_rate / 100, 2))
Invoice total     inr(sum + gst)
```

Do this arithmetic in paise (integers) so it can't drift: `paise = Math.round(Number(x) * 100)`.

**Fields:**
- **Invoice number:** required, max 60 characters, trim it. Hint: "As printed on your invoice".
- **Invoice date:** a date picker. Default today; dates after today are disabled.
- **Invoice PDF:** required. `accept="application/pdf"`, at most **4 MB**. Check the size in the browser too and show "The PDF is larger than 4 MB." Show the file name, with a "Change" link.

**Send button:** "Raise invoice for {inr(total)}". It is disabled until at least one student is ticked, the number is filled in and a PDF is attached, and while a send is in flight.

**Sending:**
- Create `client_invoice_id = crypto.randomUUID()` when the dialog opens. **Reuse it on every retry** of that dialog, so the CRM stores the invoice only once.
- Send a `FormData`; don't set the Content-Type yourself:

```ts
const fd = new FormData();
fd.append("invoice_number", number.trim());
fd.append("invoice_date", dateISO);                    // "2026-10-24"
fd.append("lines", JSON.stringify(ticked.map(s => ({ lead_bank_id: s.lead_bank_id }))));
fd.append("client_invoice_id", clientInvoiceId);
fd.append("pdf", file);
await api.post("/provider/invoices", fd);
```

**On 201:**
- Close the dialog with the toast "Invoice {invoice_number} sent to FundMyCampus for {inr(amount)}".
- Refetch `/provider/payouts`, `/provider/invoices/eligible` and `/provider/invoices`. Those students now show under "Invoice raised" in the pie and table.

## 4 — "My invoices" (below the payout table)

**Table columns:**
- Invoice no.
- Date
- Students (`lines.length`)
- Payouts (`lines_total`)
- GST (`gst_amount`)
- **Total** (`amount`)
- Status badge: Raised (amber), Paid (green, with "on {paid_on}"), Cancelled (grey, strike-through total)
- **PDF**

Details:
- The **PDF** column is a download link to `/api/provider/invoices/{id}/pdf` when `has_pdf` is true, and "—" otherwise.
- When `raised_via === "crm"`, add a small "Entered by FundMyCampus" label.
- Clicking a row expands its lines: `#lead_no` · name · bank · `inr(amount)`.
- If TDS was deducted (`tds_amount` > 0), add a line under the total: "TDS {inr(tds_amount)} · Net {inr(net_amount)}".
- **Empty state:** "No invoices yet. Raise one when you have payouts to bill."
- On a phone, show one card per invoice.

## 5 — Acceptance checks

1. **Demo partner:** the button shows the count. The dialog lists the students with `remaining`, all ticked. Totals show payouts + 18% GST.
2. **Raise:** untick one student, enter a number and attach a PDF, then send. The toast appears, the invoice appears in "My invoices" as **Raised** with a working PDF link, and the button count drops.
3. **Duplicate number:** the same number again (any capitalisation) shows "already used" under the field, and the dialog stays open with its data.
4. **Retries:** a double click on Send, or a retry after a 503, raises only one invoice.
5. **File checks:** a 6 MB file or a .jpg is refused in the browser, before upload.
6. **Admitverse partner:** no button and no "My invoices".
7. **Nothing left:** with nothing to invoice, the button is disabled with its tooltip.
8. **Phone width:** no sideways scroll; the dialog is full-screen and the totals stay visible above the Send button.
