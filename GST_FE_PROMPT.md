# MIS Frontend — Payout page: show amounts with 18% GST — build prompt

> For the MIS **frontend** (`MIS FE`). There's **no new endpoint**: the existing
> payout calls now return extra fields. Don't rename anything. Don't compute GST
> on the client. Money is a **2-decimal string**; format it with `inr()`.
>
> Calls: `GET /provider/payouts` (partner) and `GET /admin/providers/{id}/payouts`
> (admin Payouts tab).

**Why:** partners bill FundMyCampus their earnings **plus 18% GST**, so the
Payout page must show Earned, Pending and Balance with GST added and say so.

---

## 1 — Types (`lib/types.ts`)

The new fields are on **both** `PayoutSummary` and each `PayoutItem`:

```ts
  gst_rate: MoneyString;          // "18.00" (percent; it's a setting, don't hard-code 18)
  earned_gst: MoneyString;        // the GST amount on earned
  earned_with_gst: MoneyString;   // earned + GST
  pending_with_gst: MoneyString;  // pending + GST
  balance_with_gst: MoneyString;  // balance + GST — SIGNED like balance (negative when overpaid)
```

Existing fields (`earned`, `pending`, `balance`, `paid`, …) are unchanged: they're the amounts **before** GST.

## 2 — KPI cards

| Card | Big number | Small line under it |
|---|---|---|
| **Earned + {gst_rate}% GST** | `inr(earned_with_gst)` | `inr(earned)` + `inr(earned_gst)` GST |
| **Pending + {gst_rate}% GST** | `inr(pending_with_gst)` | Before GST: `inr(pending)` |
| **Balance + {gst_rate}% GST** | `inr(balance_with_gst)`, red when negative | Before GST: `inr(balance)` |

- **Paid** stays as it is, with no GST.
- Show the rate as `Number(gst_rate)` without trailing zeros: "18%".

## 3 — Table

- Rename the columns to **"Earned + 18% GST"**, **"Pending + 18% GST"** and **"Balance + 18% GST"**, and show the `*_with_gst` values.
- Hovering an amount shows a tooltip with the before-GST amount ("₹10,800 + ₹1,944 GST").

## 4 — Unchanged
- **Pie chart:** keeps showing before-GST amounts. Add a muted note under it: "Amounts before GST".
- **Revenue vs Disbursement graph:** before GST, same note.
- **CSV:** the backend adds `earned_with_gst, pending_with_gst, balance_with_gst` columns at the end. There's nothing to do in the frontend.

## 5 — Acceptance checks
1. As Altera: Earned ₹81,195.59 shows **Earned + 18% GST ₹95,810.80**, with "₹81,195.59 + ₹14,615.21 GST" under it.
2. A row with earned ₹10,800 shows **₹12,744.00** in "Earned + 18% GST".
3. An overpaid partner's Balance + GST is negative and red.
4. Paid is unchanged, with no GST.
5. Admin → provider → Payouts tab shows the same.
6. Admitverse partner: the page still shows "Payouts are tracked for FundMyCampus loans", with no GST cards.
