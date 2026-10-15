# MIS Frontend — Partner Leads: their lead no., counsellor, lender — build prompt

> For the MIS **frontend** (`MIS FE`). The backend change is in the MIS backend
> and one CRM view (`mis_partner_lead_info`). There's **no new endpoint**: the
> existing calls return different fields. Don't rename anything. Don't compute
> amounts on the client. Money is a **2-decimal string**; format it with `inr()`.

**Why:** a partner must never see FundMyCampus's own lead number, the brand or
our CRM source name. They see **their own** number (1, 2, 3… in the order their
leads came in), the FMC counsellor on the student and, once the file is logged
in with a lender, which **bank**, the amount and the application number. The
bank is always the real bank ("PNB"), never our route or aggregator ("UC PNB",
"UniCred", "Nomad"). The backend already does this mapping, so just show `bank`.

---

## 1 — Types (`lib/types.ts`)

```ts
export interface LeadLender {
  bank: string | null;          // real bank, e.g. "PNB". null = still on an aggregator → show "—"
  status: "loan_login" | "sanctioned" | "pf_paid" | "disbursed" | "lost";
  status_label: string;         // "Logged in" | "Sanctioned" | "PF Paid" | "Disbursed" | "Declined"
  amount: MoneyString | null;
  amount_kind: "sanctioned" | "applied" | null; // applied = asked for, before a sanction
  application_no: string | null;
}

export interface LeadOut {
  id: string;
  lead_no: number | null;       // NEW — the partner's own number
  full_name: string | null;
  phone: string | null;
  canonical_stage: CanonicalStage;
  crm_stage: string | null;
  crm_stage_label: string | null;
  is_invalid: boolean;
  is_duplicate: boolean;
  created_at: string | null;
  counsellor_name: string | null; // NEW
  lenders: LeadLender[];          // NEW — [] until the file is logged in somewhere
  // REMOVED: serial_no, brand, source_name
}

export interface PayoutItem {
  lead_no: number | null;       // REPLACES serial_no
  bank_name: string | null;     // now the real bank; null → "—"
  // ...everything else unchanged
}
```

`UnmappedPayoutItem` (admin "Unassigned payouts" page only) still has `serial_no`,
plus `crm_bank_name` (the CRM route, e.g. "UC PNB"). Admin pages may show those.

---

## 2 — Partner Leads table (`components/leads-table.tsx`)

**Columns:** **Lead no.** · Name · Phone · Stage · **Counsellor** · **Lender** · Created · flags

- **Lead no.**: `lead_no` (e.g. `#12`). Remove the old serial column.
- **Remove** the Brand and Source columns.
- **Counsellor**: `counsellor_name` or "—".
- **Lender**: if `lenders` is empty show "—". Otherwise, for each lender (the
  API sends the furthest-along lender first):
  - line 1: `bank ?? "—"` + a small status badge (`status_label`; Disbursed
    green, PF Paid / Sanctioned blue, Logged in grey, Declined red/muted)
  - line 2, muted: `inr(amount)` with "(applied)" when `amount_kind === "applied"`,
    then ` · App no. {application_no}` when set
  - On mobile, show only the first lender and add a "+N more" toggle.
- Search placeholder: "Name, phone or lead no." (the backend matches an exact lead no.).
- **Payout page table and the admin Payouts tab:** the `#` column shows `lead_no`.
  `bank_name` null → "—". `components/payouts/payout-table.tsx` is shared with the
  admin Unassigned page, so render `row.lead_no ?? row.serial_no ?? "—"` and use
  `lead_no` in the row `key` instead of `serial_no`.

## 3 — CSV
No FE change. The backend now exports `lead_no, full_name, phone, stage,
funnel_stage, is_invalid, is_duplicate, created_at, counsellor, lender,
lender_status, loan_amount, application_no`. Several lenders are separated by `; `.
The payout CSV's first column is `lead_no`.

---

## 4 — Acceptance checks
1. No page a partner can open shows FMC's serial numbers, "fmc"/"av", or a CRM
   source name. Check the Network tab too: the fields aren't in the response.
2. A partner's oldest lead is #1. The numbers don't change when the date filter changes.
3. A file shared with "UC PNB" and logged in shows **PNB**, never "UC PNB".
   A file on "UniCred" or "Nomad …" shows "—" as the lender.
4. A sanctioned file shows the sanctioned amount. A file only logged in shows the
   applied amount, marked "(applied)".
5. A lead that hasn't reached login shows "—" in Lender.
6. Searching "12" finds lead #12 (and phones containing 12).
7. Until the CRM view is deployed, Counsellor and Lender show "—" and nothing errors.
8. Mobile width: no sideways page scroll.
