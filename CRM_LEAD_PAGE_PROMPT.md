# BE-CRM / CRM-UI — Partner lead page: tranches + partner chat — discussion brief

> For the **CRM developer** (BE-CRM + CRM-UI). This comes from the lead-provider
> **MIS** (the partner portal, repo `MIS-BE`). Read this, then agree the open
> questions in §4 with Amit before building.
>
> The MIS reads the CRM **read-only** (role `mis_readonly`, a few views only) and
> must never write to the CRM database. Everything below respects that.

---

## 0 — What the partner will see

A partner (a lead source such as Altera) opens one of their students in the MIS
and sees a **lead page**:

| Left / main | Right side |
|---|---|
| Phone · CRM stage · counsellor · lender (real bank only) · amount · bank stage | **Chat** with the student's **counsellor** and the **FMC admin team** |
| At the bottom, **all lender files**: bank, bank stage, application no., amount, and every **tranche** (tranche no. + amount + date) | |

**The MIS already has** phone, stage, counsellor, banks, amounts, bank stage and
application no., from the view `mis_partner_lead_info`.

**Two things are missing; this brief asks for both:**
- **A. Tranches for every lender file.** One small view change.
- **B. Partner chat.** A new table, a small service API for the MIS, and a chat panel in the CRM lead page.

---

## A — Tranches in `mis_partner_lead_info` (view change only)

**Today:**
- Each item in `lenders` has: `bank_name, bank_status, loan_amount, application_id, logged_in_at`.
- The MIS can see tranches only for files that already have a partner payout (view `mis_partner_disbursements`, commission-earning tranches only).
- So a sanctioned or disbursed file with no payout set shows no tranches.

**Ask:** a new migration (after `mislead001info`) that re-creates the view with
two more keys on every lender object:

```sql
'lead_bank_id', lb.id,
'tranches', COALESCE((
    SELECT jsonb_agg(jsonb_build_object(
               'tranche_no',   d.tranche_no,
               'amount',       d.disbursed_amount,
               'disbursed_on', d.disbursed_on)
           ORDER BY d.tranche_no, d.disbursed_on NULLS LAST)
    FROM public.bank_disbursements d
    WHERE d.lead_bank_id = lb.id
), '[]'::jsonb)
```

**Rules:**
- **Expose only** tranche no., amount and date.
- **Never expose** `commission_rate`, `commission_amount`, `gst_amount`, `utr_reference`, `invoice_id`, `amount_received` or `earns_commission`.
- Keep the existing keys and order unchanged; the MIS already reads them.
- Keep the `GRANT SELECT … TO mis_readonly` block as in `mislead001info`.
- Downgrade = re-create the `mislead001info` version.

**Open question (§4):** list every tranche the bank released, or only
`earns_commission = true` ones like `mis_partner_disbursements`?
**MIS suggestion:** list every real release. This is "how much the bank has
released", not the payout, and the Payout page already uses the commission view.

---

## B — Partner chat on a lead

### B1. Behaviour

- **One thread per lead.** It's between the partner and FMC staff.
- **Partner side.** The partner writes in the MIS and can **tag** `@counsellor`, `@admin`, or both. If they tag no one, the message goes to the counsellor.
- **Who is notified:**
  - **Tagged counsellor:** the lead's assigned counsellor (`leads.assigned_agent_id`) gets a CRM notification.
  - **Tagged admin:** the company's admins get one (or a named "partner desk" admin, see §4).
- **Staff side.** The counsellor and admins read and reply **inside the CRM**, on the lead's page, in a new **"Partner chat"** panel. Any CRM user who can open the lead can read the thread and reply.
- **What the partner sees on each staff reply:** the sender's name plus a role label: "Counsellor" for the assigned agent, "FundMyCampus team" for anyone else. Never the staff's email or phone.
- **Separate from internal notes.** This thread is kept apart from `lead_remarks` and the call transcripts. Nothing internal is ever shown to the partner, and partner messages never mix into the remarks timeline.
- **Text only in v1.** Max 2,000 characters. No attachments, no edits. Staff can **hide** a message (soft delete, e.g. for abuse); the partner then sees "Message removed".

### B2. Data (BE-CRM, new table)

Model it like `lead_bank_messages`:

```
lead_partner_messages
  id               uuid pk default gen_random_uuid()
  company_id       uuid not null → companies.id  (cascade)
  lead_id          uuid not null → leads.id      (cascade)
  author_kind      text not null  check in ('partner', 'staff')
  author_profile_id uuid null → profiles.id      -- staff only
  author_name      text not null  -- partner org name (from the MIS) or staff full_name
  partner_user_email text null    -- which partner login wrote it (audit only)
  body             text not null  check (length(body) between 1 and 2000)
  tags             text[] not null default '{}'  -- subset of {'counsellor','admin'}
  is_hidden        boolean not null default false
  hidden_by        uuid null → profiles.id
  mis_message_id   uuid null unique  -- idempotency key from the MIS (retries are no-ops)
  created_at       timestamptz not null default now()
  index (lead_id, created_at)
```

**Notifications:**
- Add a `notification_type` enum value `partner_message`.
- Write one row per recipient, with `lead_id` set so the bell opens the lead.
- Title: `"{partner name} on {student name}"`.

### B3. Service API for the MIS (BE-CRM)

**Auth:**
- Use the existing machine-client `X-API-Key` (`app/core/api_key.py`), with one key named "MIS".
- The key's profile is a system user. It must not be a real counsellor, so it never shows up as a sender.
- These routes accept **only** that key, not normal user JWTs.

**Who checks what:**
- **The MIS** has already checked that the lead belongs to the calling partner before it calls.
- **The CRM checks:**
  - the lead exists, isn't deleted, and is in the key's company;
  - its `lead_source_id` is in the `lead_source_ids` the MIS sends (defence in depth). If not, return 404.

```
GET  /api/v1/mis/leads/{lead_id}/messages?lead_source_ids=<uuid>,<uuid>
  → 200 { "counsellor_name": "Rahul M" | null,
          "items": [ { "id", "author_kind": "partner"|"staff",
                       "author_name", "author_role": "partner"|"counsellor"|"team",
                       "body" (or null when hidden), "is_hidden",
                       "tags": ["counsellor","admin"], "created_at" } ] }   -- oldest first

POST /api/v1/mis/leads/{lead_id}/messages
  body { "lead_source_ids": [...], "mis_message_id": "<uuid>",
         "partner_name": "Altera", "partner_user_email": "amit15@gmail.com",
         "body": "…", "tags": ["counsellor"] }
  → 201 the created item (same shape). Same mis_message_id again → 200 with the
    existing item, no second notification.

GET  /api/v1/mis/messages/latest?lead_source_ids=<uuid>,…
  → 200 [ { "lead_id", "last_message_at", "last_staff_message_at" } ]
  (lets the MIS show "new reply" dots on its Leads table; one row per lead
  that has any message)
```

**Errors:**
- 404: lead not found or not in those sources.
- 422: body empty or too long, or an unknown tag.
- 401 / 403: bad key.

Rate-limit `POST` (e.g. 30/min per key).

### B4. CRM-UI

On the lead detail page, add a **"Partner chat"** panel (or tab):
- **When it shows:** only when the lead's source is a partner source. Simplest rule: show it whenever the thread has messages, and always show it on leads from the partner sources listed in the MIS (see §4).
- **Messages:** partner messages sit on the left with the partner's name; staff messages sit on the right.
- **Reply box:** posts as the logged-in profile with `author_kind = 'staff'`. Use a normal CRM user endpoint for this, not the MIS one, e.g. `POST /api/v1/leads/{id}/partner-messages`.
- **Hide:** a "Hide" action on any message (admin/manager only).
- **Notifications:** the bell shows `partner_message` notifications and links to this panel.
- **Optional:** a "Partner chats" inbox listing threads with unanswered partner messages.

---

## C — What the MIS will do once A and B are live (FYI, MIS side)

- `GET /me/leads/{id}` will include `tranches` on each lender.
- New MIS routes `GET/POST /me/leads/{id}/messages` will call B3. The MIS adds:
  - the partner's source ids
  - the partner name and login email
  - a fresh `mis_message_id`
- Partners never call the CRM directly.
- The MIS will keep the CRM API URL and key in its env vars (`CRM_API_URL`, `CRM_API_KEY`).
- If the CRM is down, the chat panel shows "Chat unavailable, try again". The rest of the page still loads.

---

## 4 — Decide together before building

1. **Tranches:** every release, or only commission-earning ones? (MIS suggests every release.)
2. **"Admin" when tagged:** notify all company admins, all admins + managers, or one named "partner desk" profile?
3. **Who may reply in the CRM:** anyone who can open the lead, or only the assigned counsellor + admins/managers?
4. **Staff names shown to partners:** the counsellor's real name, or only "FundMyCampus team" for everyone?
5. **Admitverse:** does the AV CRM run this same BE-CRM code? If yes, build once and both companies get it. If no, FundMyCampus goes first.
6. **Message history when a lead is reassigned:** the new counsellor sees the whole thread. Fine?
7. **Partner sources in the CRM:** should CRM-UI mark a lead as "from a partner" (and show the panel only then)? If so, add a flag on `lead_sources` such as `is_partner_source`. The MIS knows the mapping, but the CRM doesn't today.

## 5 — Acceptance checks

1. **Tranches:** for a disbursed file with 2 tranches, the view has `tranches` with both rows: amounts, dates and numbers 1 and 2. A logged-in-only file has `[]`. No commission, GST or UTR fields anywhere in the view.
2. **Posting:** a `POST` from the MIS with tag `counsellor` creates the message, and the assigned counsellor gets one notification that opens the lead's Partner chat.
3. **Idempotency:** the same `mis_message_id` sent twice gives one message and one notification.
4. **Scoping:** a `POST`/`GET` with a lead outside the given `lead_source_ids` returns 404.
5. **Staff reply:** a reply from CRM-UI appears in the MIS `GET` with `author_role = "counsellor"` (or `"team"` for an admin). It never includes an email or phone.
6. **Hiding:** a hidden message comes back as `body: null, is_hidden: true`.
7. **Separation:** partner messages never appear in `lead_remarks` or the activity timeline, and remarks never appear in the partner chat.
8. **Auth:** a normal CRM user JWT can't call `/api/v1/mis/*`, and the MIS key can't call normal CRM routes.
