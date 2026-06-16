# MIS Lead-Provider Portal — Backend

Standalone backend for the MIS that lets external **lead providers** log in and
see how the leads they supplied perform across two CRMs (**FundMyCampus** + **Admitverse**).

It reads both CRMs **read-only**, normalizes both pipelines into one canonical
funnel, pre-aggregates daily rollups, and serves a fast provider portal + admin API.

> This repo is **backend only**. The frontend (Next.js) lives in a separate repo.

---

## Stack

FastAPI · async SQLAlchemy 2.0 · asyncpg · Alembic · APScheduler · python-jose (JWT) ·
passlib[bcrypt] · Pydantic v2. **Python 3.11+** (the local machine's 3.9 won't run it —
PEP 604 `X | None` annotations are evaluated by Pydantic at runtime).

## Architecture

```
[FMC Supabase] ─┐ read-only, incremental
                ├─► [SYNC worker] ─► [MIS Postgres] ─► [MIS API] ─► [Frontend]
[AV  Supabase] ─┘   (every 45 min)   (normalized +      (fast local
                                      pre-aggregated)    reads only)
```

- **Sync worker** (`app/sync/`): incremental pull (watermark on `updated_at`),
  stage normalization, duplicate/invalid flagging, rollup recompute.
- **MIS API** (`app/api/`): reads only the MIS DB. Provider routes are hard-scoped
  to the caller's `provider_id` (from the JWT — never from client input).

## Project layout

```
app/
  config.py        env settings              core/security.py   JWT + bcrypt
  database.py      MIS async engine          core/ratelimit.py  login throttle
  deps.py          auth + provider scoping   models/            SQLAlchemy tables
  main.py          FastAPI app + lifespan     schemas/           Pydantic IO
  api/             auth · provider · admin    services/          metrics · scorecard · rollup
  sync/            connectors · stage_map · normalize · worker · scheduler
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

## Sync

- Runs automatically every `SYNC_INTERVAL_MINUTES` (default 45) once the app boots
  (APScheduler, started in the FastAPI lifespan). Set `SYNC_ENABLED=false` to disable.
- Trigger manually: `POST /admin/sync/run` (admin). Watch `GET /admin/sync/status`.
- Incremental: cost ∝ changed rows, not table size. Watermark per brand in `sync_state`.
- **Adding a 3rd brand** = add an enum value (`Brand`), a stage map in
  `app/sync/stage_map.py`, a DSN, and the brand to the worker loop. Nothing else changes.

### Stage mapping
All brand-specific stage strings live in `app/sync/stage_map.py`. Verify them against
each CRM's live `lead_stage` enum before trusting in production (FMC pipeline revamped
May 2026; AV has a ~17-stage pipeline). CRM table/column names are also overridable there.

## API surface

**Auth** — `POST /auth/login` · `POST /auth/logout` · `GET /auth/me`

**Provider (scoped to caller)** — `GET /me/overview` · `/me/trends` · `/me/brand-split`
· `/me/quality` · `/me/leads` · `/me/leads/export`

**Admin** — `POST/GET/PUT /admin/providers` · `POST /admin/providers/{id}/users`
· `POST/GET /admin/providers/{id}/sources` · `GET /admin/crm-sources` ·
`GET /admin/leaderboard` · `GET/PUT /admin/targets` · `POST /admin/sync/run` ·
`GET /admin/sync/status`

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
`CORS_ORIGINS`, `SYNC_INTERVAL_MINUTES`) in the Railway dashboard.

## Phase 2 (reserved, not built)

Payout module (billing model per provider) and dispute/return workflow
(invalid categories, 30-day duplicate window, return-concentration red flag).
Schema shapes are noted in the build prompt; `providers.payout_config` is reserved.
