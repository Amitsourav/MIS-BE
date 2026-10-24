# CRM (BE-CRM): let partners raise their invoice from the MIS: brief

> For the **BE-CRM** developer. The MIS backend will call these endpoints; the
> MIS never writes to the CRM database. Reply with the final contract
> (paths, fields, error codes) and the MIS will build its side to it, as with
> partner chat.

## Why

Today a partner emails FMC an invoice for their payouts, and an FMC admin
records it in **Partner invoices** (`/api/v1/partner-invoices`, admin JWT
only). We want a **"Raise invoice"** button on the partner's Payout page in
the MIS:

1. The partner picks students.
2. They enter their own invoice number and date.
3. They attach the PDF.
4. The invoice lands in the CRM as `raised`, just as if an admin had entered it.

The existing admin endpoints can't be reused: they need an admin login, and
they return `serial_no` and `source_name`, which partners must never see.

## Decisions already made (by the business)

| Topic | Decision |
|---|---|
| Invoice number | The partner types their own. It must be unique per partner; a duplicate is refused. |
| GST | The invoice is **payout + 18% GST**. The payout amounts per student stay before GST, as today. |
| Students | The partner ticks them from the list of students still to invoice. All are ticked by default. |
| PDF | **Required.** It is the partner's actual tax invoice. |
| TDS | Not entered by the partner. FMC sets it later in the CRM, as today. |
| After raising | The partner can't edit or cancel. Changes go through FMC, who can still edit or cancel in the CRM. |

## Endpoints (under the existing `mis_router`, `/api/v1/mis/*`)

Auth and scoping are the same as partner chat:
- A `mis`-scoped `X-API-Key`. The Demo Loans company uses its own key.
- Every call carries the caller's `lead_source_ids`. Anything outside them is a **404**.
- Rate limit per key, as for chat.
- Never return `serial_no`, `source_name`, any FMC commission, or an aggregator name. Bank = `real_bank_name`, null when still on an aggregator.

### 1. `GET /mis/invoices/eligible?lead_source_ids=a,b`
These are the students whose payout can still be invoiced, meaning `remaining > 0`. It is the same rule as the admin `/partner-invoices/eligible`, across all the given sources.

```json
[{ "lead_bank_id": "uuid", "lead_id": "uuid", "lead_source_id": "uuid",
   "full_name": "Riya Sharma", "bank_name": "SBI",
   "released": "2424504.00", "payout_due": "14547.02", "payout_paid": "0.00",
   "on_open_invoices": "0.00", "remaining": "14547.02" }]
```
All amounts are **before GST**. The MIS adds the partner's own lead number itself.

### 2. `POST /mis/invoices` (multipart/form-data)
| Part | Type | Rule |
|---|---|---|
| `payload` | JSON string | see below |
| `pdf` | file | required; `application/pdf`; starts with `%PDF`; ≤ 5 MB |

```json
{ "lead_source_ids": ["uuid"],          // the caller's sources (scope)
  "lead_source_id": "uuid",             // the source this invoice is for; must be in lead_source_ids
  "mis_invoice_id": "uuid",             // idempotency: same id again → the stored invoice, 200, no duplicate
  "invoice_number": "EDU/24-25/017",    // 1–60 chars, unique per lead_source_id (case-insensitive)
  "invoice_date": "2026-10-24",         // not in the future
  "gst_rate": "18.00",                  // percent; the MIS sends its setting
  "partner_name": "Demo Partner – EduLink",
  "partner_user_email": "x@y.com",      // who pressed the button (audit)
  "lines": [{ "lead_bank_id": "uuid", "amount": "14547.02" }] }  // amount optional = all remaining; before GST
```

The CRM then:
- Checks that every `lead_bank_id` belongs to `lead_source_id` (otherwise 404), and that each amount is ≤ its `remaining` (otherwise 422, naming the student).
- Sets `lines_total` = sum of lines (before GST), `gst_amount` = round-half-up(`lines_total` × rate / 100), and `amount` = `lines_total` + `gst_amount`.
- Leaves `tds_amount` = 0, `status` = `raised`, `created_by` = null, and `due_date` per your usual rule (or null).
- Stores the PDF in the existing private **"Invoices"** Supabase bucket, e.g. `partner-invoices/{company_id}/{invoice_id}.pdf`.
- Notifies the finance/admin users that a partner raised an invoice.
- Returns **201** with the invoice (shape as in §3).

**Errors:**
- **409** duplicate invoice number: `{"detail": "Invoice number EDU/24-25/017 is already used"}`.
- **422** validation: send `detail` as a sentence the partner can read.
- **404** for anything out of scope.

### 3. `GET /mis/invoices?lead_source_ids=a,b`
The partner's own invoices, newest first. It includes the ones an admin entered.

```json
[{ "id": "uuid", "invoice_number": "EDU/24-25/017", "invoice_date": "2026-10-24",
   "lines_total": "14547.02", "gst_rate": "18.00", "gst_amount": "2618.46", "amount": "17165.48",
   "tds_amount": "0.00", "net_amount": "17165.48", "status": "raised", "paid_on": null,
   "raised_via": "mis", "has_pdf": true, "created_at": "…",
   "lines": [{ "lead_id": "uuid", "full_name": "Riya Sharma", "bank_name": "SBI", "amount": "14547.02" }] }]
```

### 4. `GET /mis/invoices/{id}/pdf?lead_source_ids=a,b`
Returns the stored PDF bytes (or a short-lived signed URL). 404 if it's not theirs or there's no PDF.

## Data changes (migration)

Add to `partner_invoices`:
- `gst_rate numeric(5,2) default 0`
- `gst_amount numeric(14,2) default 0`
- `pdf_path text null`
- `raised_via text default 'crm'` (`'crm'` | `'mis'`)
- `mis_invoice_id uuid null unique`
- `raised_by_email text null`

Admin screens and payout rules:
- **Admin screen:** show GST and the PDF download. "Raised from partner portal" when `raised_via = 'mis'`.
- **Mismatch check:** the existing "lines total ≠ amount" check must compare `lines_total + gst_amount`. Otherwise every MIS invoice looks wrong.
- **Payout rules must not change:** payout paid, pending, `remaining` and the `mis_partner_payouts` view keep working on the **before-GST** line amounts. When FMC pays an invoice, the GST part must not inflate `payout_paid`. Please confirm how `/pay` allocates, or tell us if a paid invoice should record the GST separately.
- **Optional:** add `partner_invoice_gst_amount` to `mis_partner_payouts`, so the MIS can show it per row.

## Acceptance checks

1. **Demo Loans:** the eligible list shows only EduLink students with something left. Posting two of them with a PDF creates a `raised` invoice in the CRM admin screen, with the GST and the PDF.
2. **Retry:** the same `mis_invoice_id` again returns the same invoice and sends no second notification.
3. **Duplicate number:** the same invoice number again with a new `mis_invoice_id` returns **409**.
4. **Too much:** an amount over `remaining` returns **422**. Another partner's `lead_bank_id` returns **404**.
5. **No PDF:** a missing PDF, a non-PDF file, or a file over 5 MB returns **422**.
6. **Pending:** after raising, that student's `remaining` drops. The MIS Payout page shows it under "Invoice raised", and pending (before GST) is unchanged.
7. **Payment:** when the admin pays it, `payout_paid` rises by the before-GST amount only.
8. **Admitverse:** returns 404/400, since there are no payouts there.
