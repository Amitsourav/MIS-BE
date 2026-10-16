# MIS Frontend — Lead page (click a lead → its own page, with chat) — build prompt

> For the MIS **frontend** (`MIS FE`).
> - Don't rename fields. Don't compute amounts on the client.
> - Money is a **2-decimal string**; format it with `inr()`.
> - Dates are ISO strings: `"2026-06-12"` for a day, full timestamps for times.

**Why:** a partner clicks a student in **Leads** and gets that student's page.
- **Left:** where the file stands, with every lender's details and tranches.
- **Right:** a chat with the student's counsellor and the FundMyCampus team.

---

## 1 — API

| Call | Use |
|---|---|
| `GET /me/leads/{id}` | The page data → `LeadDetail` |
| `GET /me/leads/{id}/messages` | The chat thread → `ChatThread`. Opening it also marks replies as read. |
| `POST /me/leads/{id}/messages` | Send a message → `201 ChatMessage` |
| `GET /me/messages/latest` | Per lead: last message times and `unread`, for the Leads table dot |

`id` is `LeadOut.id` from the Leads table.

**Errors:**
- **404** on any of these → "Lead not found" (not this partner's lead, or deleted).
- **503** on a `messages` call → the chat panel alone shows "Chat unavailable, try again". The rest of the page still renders.
- **422** on POST → show the `detail` under the input.
- **429** → "Too many messages, wait a minute".

```ts
// lib/types.ts
export interface LeadTranche {
  tranche_no: number | null;
  amount: MoneyString | null;
  disbursed_on: string | null;        // "2026-06-12"
}

export interface LeadLender {           // existing, plus `tranches`
  bank: string | null;                  // real bank only; null → "—"
  status: "loan_login" | "sanctioned" | "pf_paid" | "disbursed" | "lost";
  status_label: string;                 // the bank stage: "Logged in" | "Sanctioned" | "PF Paid" | "Disbursed" | "Declined"
  amount: MoneyString | null;
  amount_kind: "sanctioned" | "applied" | null;
  application_no: string | null;
  tranches: LeadTranche[];              // NEW — [] until the bank releases money
}

export interface StageEvent { stage: string; label: string; at: string; }

export interface LeadDetail extends LeadOut {
  contacted_at: string | null;
  qualified_at: string | null;
  converted_at: string | null;
  stage_history: StageEvent[];          // oldest first
  payouts: PayoutItem[];                // [] for Admitverse / no payout
}

export type ChatTag = "counsellor" | "admin";
export interface ChatMessage {
  id: string;
  author_kind: "partner" | "staff";
  author_name: string;                  // partner org name, or staff's real name
  author_role: "partner" | "counsellor" | "team";
  body: string | null;                  // null = hidden by staff → "Message removed"
  is_hidden: boolean;
  tags: ChatTag[];
  created_at: string;
}
export interface ChatThread { counsellor_name: string | null; items: ChatMessage[]; }  // oldest first
export interface ChatPost { body: string; tags: ChatTag[]; client_message_id?: string; }
export interface ChatLatest {
  lead_id: string;
  last_message_at: string | null;
  last_staff_message_at: string | null;
  unread: boolean;
}
```

---

## 2 — Leads table → link + "new reply" dot

- Each row (desktop) and each card (mobile) opens `/leads/{id}`.
  - Put a Next `<Link>` on the name cell, so ⌘-click opens a new tab.
  - Add a row `onClick` that calls `router.push`.
- Fetch `GET /me/messages/latest` once alongside the leads. Where `unread` is true, show a small dot plus a chat icon next to the name.

## 3 — Page `app/(provider)/leads/[id]/page.tsx`

**Layout:**
- **Desktop:** two columns. Main is about 2/3 on the left; **chat** is about 1/3 on the right, sticky, full height.
- **Mobile:** main first. Chat sits behind a floating "Chat" button that opens a full-screen sheet, with an unread dot on the button.

**Main column, top: summary card.** A grid of label/value pairs:

| Label | Value |
|---|---|
| Lead | `#{lead_no}` · **full_name** |
| Phone | `phone` |
| Stage | badge with `crm_stage_label` |
| Counsellor | `counsellor_name` or "Not assigned yet" |
| Lender | `lenders[0].bank ?? "—"` (the API sends the furthest-along lender first) |
| Amount | `inr(lenders[0].amount)`, plus "(applied)" when `amount_kind === "applied"` |
| Bank stage | badge with `lenders[0].status_label` |

- If `lenders` is empty, Lender, Amount and Bank stage show "—".
- Show the Invalid or Duplicate chip when that flag is set.
- Show "Received {created_at}" under the name.

**Main column, middle: Journey.** A vertical timeline of `stage_history` (`label` + date/time), oldest at the top. The last item gets a "Now" tag when its `stage === crm_stage`.

**Main column, bottom: "Lender details" (one block per item in `lenders`).**
- Bank name (`bank ?? "—"`) and a bank-stage badge (`status_label`).
- Application no.: `application_no ?? "—"`.
- Amount: `inr(amount)`, plus "(applied)" when it's the applied amount.
- **Tranches:** a small table with columns *Tranche no.* · *Amount* · *Date*, one row per item in `tranches`. Show "No money released yet" when it's empty.
  - Don't add the tranche amounts up yourself.
- If this student has `payouts`, show a "Your payout" line under the matching bank. It shows earned · paid · balance, with balance in red when negative.

**Right column: Chat.**
- **Header:** "Chat with {counsellor_name ?? "your counsellor"} & FundMyCampus team".
- **Messages:**
  - Partner messages are on the **right**, in the accent colour.
  - Staff messages are on the **left**, showing `author_name` plus a small role label: "Counsellor" for `author_role === "counsellor"`, "FundMyCampus team" for `"team"`.
  - Show the time on each message.
  - On partner messages, show the tags as small chips ("@Counsellor", "@Admin").
  - A hidden message reads *"Message removed"* in muted italics.
  - Scroll to the newest message on open.
- **Composer:**
  - A textarea with a max of 2,000 characters and a counter near the limit.
  - Two **tag toggles** above the input, **@Counsellor** (on by default) and **@Admin**. They can both be on. Send the selected ones as `tags`; if none are on, send `[]`, which goes to the counsellor.
  - The Send button is disabled while the text is empty or a send is in flight.
- **Sending:**
  - Generate `client_message_id = crypto.randomUUID()` per message and **reuse it if you retry**. The server then posts it only once.
  - On 201, append the returned message; don't refetch the whole thread.
- **Polling:** refetch the thread every 20 s while the tab is visible, and on window focus.
- **Empty thread:** "Ask about this student. Your counsellor and our team reply here."

**Never show:** FMC serial numbers, brand, CRM source names, or any staff email or phone. The API never sends them.

---

## 4 — Acceptance checks
1. Clicking a Leads row opens `/leads/{id}`. ⌘-click opens a new tab. "Back to leads" keeps the same filters.
2. The summary shows phone, stage, counsellor, the real bank (never "UC PNB" or "UniCred"), amount and bank stage.
3. **Lender details** lists every lender. A disbursed file shows its tranches (no. · amount · date). A logged-in-only file shows "No money released yet".
4. Sending "Any update?" with @Admin on appears at once on the right, with an "@Admin" chip. A double click on Send posts only once.
5. A reply from the counsellor in the CRM shows up within about 20 s, on the left, labelled "Counsellor".
6. That lead shows an unread dot on the Leads table until the partner opens the page.
7. Pasting another partner's lead id in the URL shows "Lead not found". No chat data leaks.
8. If the chat service is down (503), the page still shows everything else, and the chat panel shows "Chat unavailable, try again".
9. Phone width: no sideways scroll, and chat opens as a full-screen sheet.

> **Note:** the tranches and the chat need the CRM side to be live: CRM migration `mislead002tranches`, plus the MIS's CRM API key set on Railway. Before that, `tranches` is `[]` and the chat calls return 503. Build and test against that; nothing breaks.
