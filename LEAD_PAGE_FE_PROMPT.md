# MIS Frontend — Lead page (click a lead → its own page) — build prompt

> For the MIS **frontend** (`MIS FE`). The backend adds **one endpoint**,
> `GET /me/leads/{id}`. Don't rename fields. Don't compute amounts on the client.
> Money is a **2-decimal string**; format it with `inr()`. Dates are ISO strings.

**Why:** today a partner only sees a lead as one row in the Leads table. Clicking
a row must open a page for that student: where they are now, how they got
there, which bank the file is with, and what the partner earns on it.

---

## 1 — API

`GET /me/leads/{id}`, where `id` is `LeadOut.id` from the Leads table.

- **200** → `LeadDetailOut` (below)
- **404** → not this partner's lead, or it no longer exists (e.g. deleted in the CRM). Show "Lead not found" and a "Back to leads" link.
- **401** → the normal "send to login" handling. **503** → the normal "CRM unavailable" message.

```ts
// lib/types.ts
export interface StageEvent {
  stage: string;   // as stored, e.g. "pf_paid"
  label: string;   // the CRM's name, e.g. "PF Paid"
  at: string;      // when the lead moved to it
}

export interface LeadDetail extends LeadOut {   // every LeadOut field, plus:
  contacted_at: string | null;
  qualified_at: string | null;
  converted_at: string | null;
  stage_history: StageEvent[];  // oldest first; can be []
  payouts: PayoutItem[];        // one per lender file; [] for Admitverse or no payout yet
}
```

`PayoutItem` is the same type the Payout page uses.

---

## 2 — Leads table → link

- In `components/leads-table.tsx`, make each row (desktop) and each card (mobile) open `/leads/{id}`.
  - Use a Next `<Link>` on the name cell, plus a row `onClick` → `router.push`.
  - Keep middle-click / ⌘-click opening a new tab: the `<Link>` handles that.
  - Show `cursor-pointer` and the row hover style.
- Keep the table's filters in the URL (they already are). The page's "Back to leads" uses `router.back()` when there's history, else `/leads`, so the partner returns to the same filtered list.

## 3 — The page: `app/(provider)/leads/[id]/page.tsx`

Fetch with the same hook/pattern as the other partner pages. Show a skeleton while loading.

**Header**
- `#{lead_no}` · **full_name** · phone
- Current stage badge: `crm_stage_label`
- Red "Invalid" or amber "Duplicate" chip when the flag is set
- "Lead received {created_at}" (date + time, local)

**Cards** (2 columns on desktop, 1 on mobile)
1. **Counsellor**: `counsellor_name` or "Not assigned yet".
2. **Lenders**: same rendering as the Leads table's Lender cell, but every lender
   is listed (no "+N more"). Show `bank ?? "—"`, the status badge,
   `inr(amount)` with "(applied)" when `amount_kind === "applied"`, and the
   application no. If `lenders` is empty, show "Not logged in with a lender yet".
3. **Journey** (timeline, vertical): one row per `stage_history` item, showing the
   `label` and the formatted `at`, oldest at the top. Mark the last one "Now"
   when its `stage === crm_stage`. If the list is empty, show only "Received"
   (`created_at`) and the current stage.
4. **Payout**: only when `payouts.length > 0`. Show one block per item:
   - bank
   - earned · paid · balance (Balance in red when negative)
   - last released on
   - invoice status chip (same as the Payout page)

   Hide the card entirely for Admitverse or when there are no payouts. Don't show an empty state.

**Never show:** FMC serial numbers, brand, CRM source name. The API doesn't send them.

---

## 4 — Acceptance checks
1. Clicking any row in Leads opens `/leads/{id}`; ⌘-click opens it in a new tab.
2. "Back to leads" returns to the same filters and page.
3. A disbursed Altera student shows Journey ending in **Disbursed**, a lender with the real bank, and a Payout block whose Earned matches that row on the Payout page.
4. Pasting another partner's lead id into the URL shows "Lead not found" (API 404). Nothing leaks.
5. An Admitverse partner's lead shows AV stage names (e.g. **Hot**) and no Payout card.
6. Reloading `/leads/{id}` works (no dependence on table state).
7. Phone width: no sideways scroll; the cards stack.
