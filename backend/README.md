# MIS Lead-Provider Portal — Backend

Standalone backend for the MIS that lets external **lead providers** log in and
see how the leads they supplied perform across two CRMs (**FundMyCampus** + **Admitverse**).

It reads both CRMs **live and read-only** on every request — no sync, no copy — so any
CRM change shows on the next page load. Both pipelines are normalized into one
canonical funnel per request.

> This repo is **backend only**. The frontend (Next.js) lives in a separate repo.

---

## Stack

FastAPI · async SQLAlchemy 2.0 · asyncpg · Alembic · python-jose (JWT) ·
passlib[bcrypt] · Pydantic v2. **Python 3.11+** (the local machine's 3.9 won't run it —
PEP 604 `X | None` annotations are evaluated by Pydantic at runtime).

## Architecture

```
[FMC Supabase] ─┐  live, read-only, per request
                ├──────────────────────────────► [MIS API] ─► [Frontend]
[AV  Supabase] ─┘  (asyncpg pool, READ ONLY txn)     │
                                                     ▼
                                    [MIS Postgres]: providers, logins, admins,
                                    source mappings, scorecard targets only
```

- **CRM layer** (`app/crm/`): connection pools + the only SQL that touches the CRMs.
  Every query runs in a `READ ONLY` transaction (the CRMs sit behind Supabase's
  transaction pooler, where a session `SET` doesn't stick). Duplicate detection
  (same phone within `DUPLICATE_WINDOW_DAYS`) and milestone timestamps from the
  stage logs are computed in SQL; soft-deleted leads (`is_deleted`) are excluded.
- **Services** (`app/services/`): turn live rows into canonical lead facts and
  compute funnel / quality / rates / trends / scorecard — the same rules the old
  daily rollups used.
- **MIS API** (`app/api/`): provider routes are hard-scoped to the caller's mapped
  CRM sources, taken from the JWT — never from client input. A CRM outage
  returns `503`.

## Project layout

```
app/
  config.py        env settings              core/security.py   JWT + bcrypt
  database.py      MIS async engine          core/ratelimit.py  login throttle
  deps.py          auth + provider scoping   models/            SQLAlchemy tables
  main.py          FastAPI app + lifespan     schemas/           Pydantic IO
  api/             auth · provider · admin    services/          metrics · scorecard · rollup
  crm/             pool · reader (all CRM SQL) · stage_map · normalize
alembic/           migrations               scripts/           seed.py · crm_readonly_setup.sql
tests/             scoping · auth · normalize
```

## Setup (local)

```bash
cd backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # fill in MIS_DATABASE_URL, FMC_DB_URL, AV_DB_URL, JWT_SECRET

alembic upgrade head            # create the MIS schema
python -m scripts.seed admin you@example.com 'a-strong-password'
python -m scripts.seed targets  # seed default scorecard bands (override later via API)

uvicorn app.main:app --reload   # http://localhost:8000/docs
```

### One-time CRM setup
Run `scripts/crm_readonly_setup.sql` in **each** CRM's Supabase SQL editor to create
the `mis_readonly` role (SELECT-only), then put its DSN in `FMC_DB_URL` / `AV_DB_URL`.

## Live data (no sync)

- Every page reads the CRMs directly; there is no sync job, watermark or schedule.
  "Data as of" is the request time.
- Mapping a CRM source to a provider takes effect on the provider's next request.
- `POST /admin/sync/run` and `GET /admin/sync/status` remain for older clients and
  just report `live`.
- **Adding a 3rd brand** = add an enum value (`Brand`), a stage map in
  `app/crm/stage_map.py`, and a DSN. Nothing else changes.
- Typical cost: a partner page is one CRM query (~0.4–0.9 s); the admin leaderboard
  is one query per company.

### Stage mapping
All brand-specific stage strings live in `app/crm/stage_map.py`. Verify them against
each CRM's live `lead_stage` enum before trusting in production (FMC pipeline revamped
May 2026; AV has a ~17-stage pipeline). CRM table/column names are also overridable there.

## API surface

**Auth** — `POST /auth/login` · `POST /auth/logout` · `GET /auth/me`

**Provider (scoped to caller)** — `GET /me/overview` · `/me/trends` · `/me/brand-split`
· `/me/quality` · `/me/leads` · `/me/leads/export`

**Payouts (provider, FMC only)** — `GET /provider/payouts` · `/provider/payouts/export`
(`date_from`/`date_to` filter on the PF-paid date; omitted = all time; AV providers get
`brand_supported: false`). Money is returned as 2-dp strings.

**Admin** — `POST/GET/PUT /admin/providers` · `GET /admin/providers/{id}/payouts` · `POST /admin/providers/{id}/users`
· `POST/GET /admin/providers/{id}/sources` · `GET /admin/crm-sources` ·
`GET /admin/leaderboard` · `GET/PUT /admin/targets` · `POST /admin/sync/run` and
`GET /admin/sync/status` (both report `live`)

All list endpoints return `{ items, total, page, page_size }`. Interactive docs at `/docs`.

## Security

- Provider scoping enforced server-side on **every** query (`require_provider` in
  `app/deps.py`). Tested in `tests/test_scoping.py` (Provider A cannot read B).
- CRM access is a DB-level read-only role — the MIS can never mutate CRM data.
- bcrypt password hashing; `/auth/login` rate-limited; admin actions audit-logged.
- Provider leads expose only PII the provider originated (serial/name/phone/stage).
  Internal notes, transcripts, and `custom_fields` are never exposed.

## Tests

```bash
pytest            # runs on an in-memory SQLite DB; no Postgres needed
```

## Configurability

No industry benchmarks are hard-coded. Scorecard weights, rating bands, and grade
thresholds live in the `targets` table and are editable via `PUT /admin/targets`.
`scripts/seed.py targets` writes sensible starting defaults (override per your own data).

## Deploy (Railway)

`Dockerfile` / `Procfile` both run `alembic upgrade head` then start uvicorn.
Set env vars (`MIS_DATABASE_URL`, `FMC_DB_URL`, `AV_DB_URL`, `JWT_SECRET`,
`CORS_ORIGINS`; optional `CRM_POOL_MAX_SIZE`, `CRM_QUERY_TIMEOUT_SEC`) in the Railway dashboard.

### Partner payouts
`/provider/payouts` reads the FMC view `public.mis_partner_payouts` live (one row per
lender file that has reached PF paid; the partner earns rate × sanctioned loan amount,
or a hand-agreed amount, on the PF-paid date). `mis_readonly` can read only `leads`,
`lead_sources`, `lead_stage_logs` and this view — see `scripts/crm_readonly_setup.sql`.
The MIS never reads FMC's bank / lender-file / disbursement tables, and never FMC's own
commission.

### Legacy tables
`mis_leads`, `provider_daily_metrics`, `sync_state` and `mis_payouts` (migrations
0001–0003) were the old sync's copy. Nothing reads them any more; they can be dropped
in a later migration once live mode is settled.

## Phase 2 (reserved, not built)

Dispute/return workflow
(invalid categories, 30-day duplicate window, return-concentration red flag).
Schema shapes are noted in the build prompt; `providers.payout_config` is reserved.
