# MIS Frontend — Partner Earnings ("Payout" page) — build prompt

> Hand this whole file to whoever works on the MIS **frontend** (`MIS FE`, repo
> `Amitsourav/MIS-FE`). It is self-contained and supersedes Part 2 of
> `MIS_PARTNER_EARNINGS_PROMPT.md`. The **backend half is already built** (repo
> `Amitsourav/MIS-BE`) and verified against the live FMC data. The API contract
> below is what it actually returns, not a proposal. Do not change field names or
> paths on the frontend side.

---

## 0 — What this is

Lead partners ("providers") send students to FundMyCampus (FMC). When a student
**pays the processing fee (PF)** to a lender (FMC's confirmation the loan is
real), the partner earns a payout: **their rate × the loan amount that lender
sanctioned**, e.g. 0.6% × ₹20 L = ₹12,000, earned on the PF-paid date. A few
older deals have a fixed, hand-agreed amount instead (`payout_basis = "agreed"`).
FMC records what it has already paid the partner.

The Payout page lets a partner see, per student × lender: lender, loan amount,
PF-paid date, what they earned, what has been paid, what is still pending, and
(information only) how much the lender has disbursed so far. Admins see the same
per provider.

**Hard rules**
1. **FundMyCampus only.** Admitverse (AV) has no payouts. An AV provider gets an
   explanatory empty state, never an error.
2. **Only from PF paid.** Students who are only sanctioned (no PF yet) never
   appear. The backend guarantees this; don't filter, add, or infer on the client.
3. **Never show, compute, or label anything as FMC's commission, margin, or
   revenue.** The API doesn't contain it; don't derive it.
4. **No client-side money maths.** Money arrives as strings. Parse with
   `Number()` only to format for display. Never sum rows. Totals come from
   `summary` (it covers the whole filtered set, not just the current page).
   Never compute earned = rate × loan on the client either; show `earned` as given.
5. **Never send `provider_id`.** The provider route is scoped by the JWT. The
   only client inputs are dates and paging.

---

## 1 — Backend API contract

All calls go through the existing same-origin proxy, e.g.
`axios.get('/api/provider/payouts', { params })` → backend `GET /provider/payouts`.

> ⚠️ Two differences from the other provider routes — get these right:
> - The path is **`/provider/payouts`**, NOT `/me/payouts`.
> - The date params are **`date_from` / `date_to`**, NOT `from` / `to`.
>   If `lib/use-filters.ts` stores `from`/`to` in the URL, map them when calling.

| Method | Path | Who | Query | Returns |
|---|---|---|---|---|
| GET | `/provider/payouts` | provider | `date_from?`, `date_to?`, `page=1`, `page_size=50` (1–200) | `PayoutsResponse` |
| GET | `/provider/payouts/export` | provider | `date_from?`, `date_to?` | `text/csv`, filename `payouts.csv`, all filtered rows |
| GET | `/admin/providers/{id}/payouts` | admin | `date_from?`, `date_to?`, `page`, `page_size` | `PayoutsResponse` |

- Dates are `YYYY-MM-DD` and filter on the **PF-paid date** (inclusive).
  **Omit both = all time.** This page defaults to all time, unlike the dashboard
  (which defaults to 30 days). Don't send a default range.
- Rows come back **already sorted** newest PF-paid first. Don't re-sort on the
  client (you'd only be sorting one page).
- There is **no admin export endpoint**. Hide the Export button in the admin tab.

**Status codes**
| Code | When | UI |
|---|---|---|
| 200 + `brand_supported: false` | AV provider (or an AV provider viewed by an admin) | "FMC only" empty state |
| 401 | session expired | existing handler → `/login` |
| 403 | wrong role (admin token on `/provider/*`), inactive provider | existing error pattern |
| 404 | admin opens a provider outside their company scope | existing not-found pattern |
| 422 | malformed date / page | shouldn't happen if inputs are validated; show existing error |

### Response — example (FMC provider)
```json
{
  "brand_supported": true,
  "summary": {
    "students": 11,
    "loan_total": "21900000.00",
    "earned": "81180.00",
    "paid": "50845.00",
    "pending": "30335.00"
  },
  "items": [
    {
      "serial_no": 8862,
      "full_name": "Riya Sharma",
      "bank_name": "UC PNB",
      "loan_amount": "1800000.00",
      "pf_paid_on": "2026-08-20",
      "disbursed_total": "400000.00",
      "payout_basis": "rate",
      "payout_rate": "0.60",
      "earned": "10800.00",
      "paid": "0.00",
      "pending": "10800.00"
    },
    {
      "serial_no": null,
      "full_name": null,
      "bank_name": "SBI",
      "loan_amount": "2500000.00",
      "pf_paid_on": "2026-07-14",
      "disbursed_total": "0.00",
      "payout_basis": "agreed",
      "payout_rate": null,
      "earned": "25000.00",
      "paid": "25000.00",
      "pending": "0.00"
    }
  ],
  "page": 1,
  "page_size": 50,
  "total": 11,
  "data_as_of": "2026-10-07T09:15:02.118000+00:00"
}
```

### Response — AV provider
```json
{
  "brand_supported": false,
  "summary": { "students": 0, "loan_total": "0.00", "earned": "0.00", "paid": "0.00", "pending": "0.00" },
  "items": [], "page": 1, "page_size": 50, "total": 0, "data_as_of": null
}
```

### Field notes
- `total` = number of **rows** (student × lender). `summary.students` = number of
  **distinct students**. One student with two lenders gives `total` 2 and
  `students` 1. Use `total` for pagination and `students` for the KPI card.
- `payout_rate` is a **percent** string (`"0.60"` means 0.6%). It is `null` when
  `payout_basis === "agreed"`. Expect plenty of agreed rows: today **all** live
  rows are agreed.
- `loan_amount` = what that lender sanctioned. It can be `null` (render `—`).
  `summary.loan_total` sums the non-null ones.
- `disbursed_total` = what the lender has released so far, `"0.00"` if nothing
  yet. **Information only.** It is not what the payout is based on. Style it
  muted and never label it as earnings.
- `serial_no`, `full_name`, `bank_name`, `pf_paid_on` and `data_as_of` can be
  `null` (e.g. the student's lead hasn't synced yet). Render `—`; never drop the row.
- Every money field except `loan_amount` is a 2-decimal string, `"0.00"` when
  zero, never null.

### CSV export columns (already produced by the backend)
`serial_no, full_name, bank_name, loan_amount, pf_paid_on, disbursed_total,
payout_basis, payout_rate, earned, paid, pending`

---

## 2 — Types (`lib/types.ts`)

```ts
/** Decimal money as a string, e.g. "10800.00". Never do arithmetic on it. */
export type MoneyString = string;

export type PayoutBasis = "rate" | "agreed";

export interface PayoutSummary {
  students: number;
  loan_total: MoneyString;
  earned: MoneyString;
  paid: MoneyString;
  pending: MoneyString;
}

export interface PayoutItem {
  serial_no: number | null;
  full_name: string | null;
  bank_name: string | null;
  loan_amount: MoneyString | null;  // sanctioned by this lender
  pf_paid_on: string | null;        // YYYY-MM-DD — the date the payout is earned
  disbursed_total: MoneyString;     // information only
  payout_basis: PayoutBasis;
  payout_rate: string | null;       // percent, e.g. "0.60"; null when basis = "agreed"
  earned: MoneyString;
  paid: MoneyString;
  pending: MoneyString;
}

export interface PayoutsResponse {
  brand_supported: boolean;
  summary: PayoutSummary;
  items: PayoutItem[];
  page: number;
  page_size: number;
  total: number;
  data_as_of: string | null;
}

export interface PayoutFilters {
  date_from?: string; // omit for all time
  date_to?: string;
}
```

---

## 3 — Queries (`lib/queries.ts`)

Follow the existing React Query patterns (key by every input, so the URL drives
the cache):

```ts
usePayouts(filters: PayoutFilters, page: number, pageSize: number)
  // GET /api/provider/payouts  — key ["payouts", filters, page, pageSize]
useAdminProviderPayouts(providerId: string, filters: PayoutFilters, page: number, pageSize: number)
  // GET /api/admin/providers/{id}/payouts — key ["admin", "provider", id, "payouts", filters, page, pageSize]
payoutsExportUrl(filters: PayoutFilters): string
  // "/api/provider/payouts/export?" + only the dates that are set
```

- Use `placeholderData: keepPreviousData` (TanStack v5; `keepPreviousData: true`
  in v4) so paging and filter changes don't flash empty, **and** a failed refetch
  keeps the last good data on screen.
- Strip `undefined` / empty params before sending. Never send `date_from=`.

---

## 4 — Formatting (`lib/format.ts`)

Add these helpers if they don't exist:

```ts
/** "1234567.5" -> "₹12,34,567.50"; "81180.00" -> "₹81,180". Indian grouping. */
export function inr(value: string | null | undefined): string {
  if (value == null) return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const hasPaise = Math.round(n * 100) % 100 !== 0;
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    minimumFractionDigits: hasPaise ? 2 : 0,
    maximumFractionDigits: 2,
  }).format(n);
}

/** "0.60" -> "0.6%", "1.00" -> "1%"; null -> null (caller shows "Agreed"). */
export function ratePct(rate: string | null): string | null {
  if (rate == null) return null;
  return `${Number(rate)}%`;
}
```

Use the existing `date()` helper for `YYYY-MM-DD`. If it builds a `Date` from the
string, beware the UTC off-by-one: parse `YYYY-MM-DD` as a local date
(e.g. `date-fns/parseISO`) or format the parts directly.

---

## 5 — Provider page: `app/(portal)/payout/page.tsx`

Replace the "Coming soon" placeholder. Also remove the "Soon" badge on the Payout
nav item.

### 5.1 Layout (top to bottom)
1. **Header:** title "Payout". Subtitle: *"What you have earned on students who
   have paid the processing fee to a lender."* Show the existing "Data as of" chip
   from `data_as_of` (hide it when null).
2. **Date filter:** reuse the existing date-range picker / `lib/use-filters.ts`.
   Label it so it's clear it filters by PF-paid date (e.g. helper text "Filtered
   by PF-paid date"). **Default = All time**: add an "All time" preset if the
   picker lacks one, selected when no dates are in the URL. Changing the filter
   resets `page` to 1. Keep the filter in the URL like the other pages. If the
   shared hook forces a 30-day default, give this page its own default rather than
   changing the dashboard's behaviour.
3. **4 KPI cards** (reuse `components/kpi-card.tsx`), in this order:
   | Card | Value | Sub-stat |
   |---|---|---|
   | Earned | `inr(summary.earned)` | — |
   | Paid | `inr(summary.paid)` | — |
   | Pending | `inr(summary.pending)`, **amber** when `Number(summary.pending) > 0` | — |
   | Students (PF paid) | `summary.students` | `inr(summary.loan_total)` in loans |
4. **Table** (one row per student × lender) + pagination + Export button.
5. **Explainer** under the table, small muted text:
   *"You earn your agreed rate on the loan amount once the student pays the
   processing fee to the lender. Sanctioned loans appear here once PF is paid.
   'Paid' is what FundMyCampus has paid you."*

### 5.2 Table columns
| Column | Content |
|---|---|
| `#` | `serial_no` or `—` |
| Student | `full_name` or `—` |
| Lender | `bank_name` or `—` |
| Loan amount | `inr(loan_amount)` (`—` when null) |
| PF paid on | `pf_paid_on` formatted, or `—` |
| Rate | `ratePct(payout_rate)`, or a neutral **"Agreed"** badge when `payout_basis === "agreed"` |
| Earned | `inr(earned)` |
| Paid | `inr(paid)` |
| Pending | `inr(pending)`; amber text when `Number(pending) > 0` |
| Disbursed so far | `inr(disbursed_total)`, **muted** (information only) |

- Money columns are right-aligned with tabular numerals (`tabular-nums`).
- Server order only (PF paid, newest first), no client sorting. Don't make headers
  look clickable.
- **Pagination:** same control as the Leads table, bound to `total / page /
  page_size`. Default 50, options 25 / 50 / 100.
- On mobile, the table scrolls horizontally (like Leads). KPI cards stack.
- Skeletons for the KPI cards and table rows while loading (reuse `skeletons.tsx`).

### 5.3 Export CSV
Button **"Export CSV"** → download `payoutsExportUrl(currentFilters)` (same
mechanism as the Leads export). Disabled when `total === 0` or
`brand_supported === false`.

### 5.4 States (reuse `components/empty-state.tsx`)
Check in this order:
| Condition | Show |
|---|---|
| Loading, no data yet | skeletons |
| Error and no previous data | existing error pattern (retry button) |
| `brand_supported === false` | Empty state: **"Payouts are tracked for FundMyCampus loans."** Hide the KPI cards, table, filter and export. |
| `total === 0` | Empty state: **"No earnings yet. They appear here once a student you sent pays the processing fee to a lender."** Keep the date filter visible; if a date filter is set, add a "Show all time" action. KPI cards may show ₹0 or be hidden; pick one and be consistent. |
| Error on refetch with previous data | keep showing the previous data + a toast ("Couldn't refresh payouts") |

---

## 6 — Admin: Payouts tab on `app/(admin)/admin/providers/[id]/page.tsx`

- Add a **Payouts** tab next to the existing provider-detail sections. If the
  page doesn't use tabs yet, introduce shadcn `Tabs`, with the current content as
  the first tab(s).
- Fetch with `useAdminProviderPayouts(id, filters, page, pageSize)`. Only fetch
  when the tab is active (`enabled`).
- Render the **same** KPI cards + table + pagination + date filter as the provider
  page. **Extract shared components** (e.g. `components/payouts/payout-kpis.tsx`,
  `payout-table.tsx`, `payout-empty.tsx`) and use them in both places. Don't
  copy-paste.
- **No Export button** in the admin tab (no admin export endpoint).
- `brand_supported === false` → same "Payouts are tracked for FundMyCampus loans."
  empty state.
- Keep the admin tab's filter/page state local (or under a distinct URL prefix)
  so it doesn't collide with other tabs' query params.

---

## 7 — Files you'll touch

```
app/(portal)/payout/page.tsx                 # replace placeholder
app/(admin)/admin/providers/[id]/page.tsx    # add Payouts tab
components/payouts/payout-kpis.tsx           # new, shared
components/payouts/payout-table.tsx          # new, shared
components/payouts/payout-empty.tsx          # new, shared (optional)
components/shell/...                         # remove "Soon" badge on Payout nav
lib/types.ts                                 # §2
lib/queries.ts                               # §3
lib/format.ts                                # inr(), ratePct()
lib/use-filters.ts                           # only if needed for "All time" / date_from mapping
```

---

## 8 — Data you'll see while developing

- The live FMC view currently has **7 rows, all `payout_basis = "agreed"`**
  (no rate rows yet). Use the example JSON in §1 as a local mock (MSW handler or a
  dev-only fixture) to build and check the **rate** rendering ("0.6%"), then
  remove the mock before merging.
- A partner only sees rows from FMC sources that an admin has **mapped** to them.
  If a test partner sees "No earnings yet", check their source mapping before
  suspecting the frontend.
- Payouts refresh on each FMC sync. An admin can force one from the Sync page.

---

## 9 — Do NOT
- call any path other than the three in §1;
- show, compute, or label anything as FMC commission, margin, or revenue;
- add, sum, or multiply money on the client (totals only from `summary`, earned
  only from `earned`);
- present `disbursed_total` as earnings or as the payout basis;
- send `provider_id` from the provider page;
- sort rows on the client;
- show an error for AV providers;
- send a default 30-day range on this page.

---

## 10 — Acceptance checks
- [ ] FMC provider with payouts: 4 KPI cards match `summary` exactly; table shows
      rows newest PF-paid first; Pending is amber when > 0.
- [ ] One student with two lenders shows as 2 rows, and "Students (PF paid)" counts 1.
- [ ] Agreed-basis row shows "Agreed" in Rate; rate rows show e.g. "0.6%".
- [ ] "Disbursed so far" is muted and shows ₹0 when nothing is released yet.
- [ ] Money shows Indian grouping: `₹1,23,456` (paise only when non-zero).
- [ ] Null name/serial/lender/loan/date renders `—` and the row is still shown.
- [ ] Default view is All time (no `date_from`/`date_to` in the request).
      Picking a range sends `date_from`/`date_to` (not `from`/`to`), filters by
      PF-paid date, and resets to page 1.
- [ ] Changing page keeps the KPI totals unchanged.
- [ ] Export downloads `payouts.csv` with the same date filter; disabled when empty.
- [ ] AV provider sees "Payouts are tracked for FundMyCampus loans." with no error
      and no export.
- [ ] No rows → "No earnings yet…" state.
- [ ] Refetch failure keeps the last data on screen and shows a toast.
- [ ] Admin provider page has a Payouts tab with the same cards/table, no export;
      an out-of-scope provider shows the not-found state.
- [ ] "Soon" badge removed from the Payout nav item.
- [ ] Nothing on the page says commission, margin, or revenue.
