# MIS Lead-Provider Portal — Frontend Build Prompt

> Hand this entire file to the new **frontend** repo. It is self-contained: goal, stack,
> auth, the exact backend API contract (TypeScript types included), routing, every page,
> components, and UX. It pairs with the already-built **backend** (FastAPI). The two must
> stay in lockstep — the types in §4 mirror the backend responses 1:1.

---

## 0 — Goal & non-negotiables

Build the **frontend** for the MIS: a portal where external **lead providers** log in and
see how the leads they supplied perform across two CRMs (**FundMyCampus** `fmc` + **Admitverse** `av`),
plus an **internal admin** area to manage providers, map CRM sources, set targets, and run syncs.

It talks ONLY to the MIS backend API (never to the CRMs directly).

**Hard rules**
1. The frontend **never** sends `provider_id` or source ids to scope data — the backend derives
   scope from the JWT. The UI only sends filters (brand, date range, stage, search, paging).
2. **Role-based routing**: a `provider` token may only reach `/dashboard`, `/leads`, `/payout`;
   an `admin` token only `/admin/*`. Enforce in middleware (cookie/role) AND let the backend 401/403
   be the final authority.
3. The JWT is stored in an **httpOnly, Secure, SameSite cookie** — never in `localStorage`
   (XSS-safe). All backend calls go through a same-origin Next.js proxy that injects the Bearer.
4. No hard-coded thresholds/benchmarks in the UI. Targets/grades come from the backend; the UI
   only formats and color-codes what it receives.

---

## 1 — Tech stack

- **Next.js 14** (App Router) · **TypeScript** (strict) · **Tailwind CSS** · **shadcn/ui**
- **Recharts** for charts · **Axios** for HTTP (pointed at the same-origin `/api` proxy)
- **TanStack Query** (React Query) for data fetching/caching/loading-states (recommended; sits on top of Axios)
- **lucide-react** icons · **date-fns** for date math · **sonner** (or shadcn `toast`) for toasts
- Deploy on **Vercel**. Single env var: `NEXT_PUBLIC_API_URL` is **not** exposed to the client —
  the backend URL is server-only (`API_URL`), used by the proxy. (Keeps the backend origin private
  and the token httpOnly.)

---

## 2 — Architecture (BFF proxy + httpOnly cookie)

```
Browser (Axios → /api/*)  ─┐
                           ├─►  Next.js route handlers  ─►  FastAPI backend
httpOnly cookie `mis_token`┘     (read cookie, add        (Bearer JWT auth,
  (set on login,                  Authorization: Bearer)    provider-scoped)
   cleared on logout)
```

- **`POST /api/auth/login`** (Next route handler): receives `{email,password}`, calls backend
  `POST /auth/login`, on success sets httpOnly cookie `mis_token` (+ a readable, non-sensitive
  `mis_role` cookie for middleware/UI) and returns `{role}`. Never returns the token to JS.
- **`POST /api/auth/logout`**: clears cookies, calls backend `/auth/logout`.
- **`/api/[...path]`** (catch-all proxy): forwards method/body/query to `${API_URL}/<path>`,
  injects `Authorization: Bearer <mis_token>` from the cookie, streams the response back
  (including CSV downloads). On backend 401, clear cookies so the client redirects to `/login`.
- **`middleware.ts`**: reads `mis_token` + `mis_role` cookies. Unauthenticated → redirect to
  `/login`. Wrong role for the path → redirect to that role's home. Pure gatekeeping; the proxy +
  backend still enforce real auth.

Result: client code is simple (`axios.get('/api/me/overview')`), the token is XSS-safe, and the
backend origin stays private.

---

## 3 — Backend API contract (what the proxy targets)

Base = backend root. All non-auth routes require the Bearer the proxy injects.
All list endpoints return `{ items, total, page, page_size }` unless noted.

**Auth**
| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/auth/login` | `{email,password}` | `{access_token, token_type:"bearer", role, provider_id?}` |
| POST | `/auth/logout` | — | `{detail}` |
| GET  | `/auth/me` | — | `{user_id, email, role, provider_id?, provider_name?}` |

**Provider (role=provider; auto-scoped, send filters only)**
| Method | Path | Query | Returns |
|---|---|---|---|
| GET | `/me/overview` | `brand?`,`from?`,`to?` | `OverviewResponse` |
| GET | `/me/trends` | `brand?`,`granularity=day\|week`,`from?`,`to?` | `TrendsResponse` |
| GET | `/me/brand-split` | `from?`,`to?` | `BrandSplitResponse` |
| GET | `/me/quality` | `brand?`,`from?`,`to?` | `QualityMetrics` |
| GET | `/me/leads` | `brand?`,`stage?`,`from?`,`to?`,`q?`,`page=1`,`page_size=50` | `Page<LeadOut>` |
| GET | `/me/leads/export` | same filters as `/me/leads` | `text/csv` (download) |

**Admin (role=admin)**
| Method | Path | Body / Query | Returns |
|---|---|---|---|
| POST | `/admin/providers` | `{name,contact_email?,is_active?}` | `ProviderOut` |
| GET  | `/admin/providers` | — | `ProviderOut[]` |
| PUT  | `/admin/providers/{id}` | `{name?,contact_email?,is_active?}` | `ProviderOut` |
| POST | `/admin/providers/{id}/users` | `{email,password?}` | `ProviderUserCreated` (has one-time `temp_password`) |
| POST | `/admin/providers/{id}/sources` | `{brand,crm_source_id,source_name?}` | `ProviderSourceOut` |
| GET  | `/admin/providers/{id}/sources` | — | `ProviderSourceOut[]` |
| GET  | `/admin/crm-sources` | `brand` | `CrmSourceOut[]` |
| GET  | `/admin/leaderboard` | `from?`,`to?` | `LeaderboardRow[]` |
| GET  | `/admin/targets` | — | `TargetOut[]` |
| PUT  | `/admin/targets` | `{targets:[{brand?,metric_key,target_value}]}` | `TargetOut[]` |
| POST | `/admin/sync/run` | `brand?` | `{triggered, detail}` |
| GET  | `/admin/sync/status` | — | `{states:[SyncStateOut], running}` |

`brand` values: `"fmc" | "av"` (and `"both"` / omitted for provider metric routes).
Dates are `YYYY-MM-DD`. Default range when omitted: last 30 days (backend decides).

---

## 4 — TypeScript types (`lib/types.ts`) — mirror the backend exactly

```ts
export type Brand = "fmc" | "av";
export type BrandFilter = Brand | "both";
export type CanonicalStage =
  | "delivered" | "contacted" | "connected" | "qualified" | "in_process"
  | "converted" | "opportunity" | "dnp" | "lost";
export type Grade = "A" | "B" | "C" | "D" | "F";

export interface Page<T> { items: T[]; total: number; page: number; page_size: number; }

// --- auth ---
export interface LoginResponse { access_token: string; token_type: string; role: "provider" | "admin"; provider_id?: string | null; }
export interface Me { user_id: string; email: string; role: "provider" | "admin"; provider_id?: string | null; provider_name?: string | null; }

// --- metrics ---
export interface FunnelCounts { delivered: number; contacted: number; connected: number; qualified: number; converted: number; dnp: number; lost: number; }
export interface QualityMetrics { delivered: number; invalid: number; duplicates: number; valid: number; invalid_rate: number; duplicate_rate: number; }
export interface RateMetrics { contact_rate: number; qualification_rate: number; conversion_rate: number; time_to_first_contact_sec: number | null; time_to_qualify_sec: number | null; }
export interface Scorecard { score_pct: number; grade: Grade; criteria: Record<string, { value: number; rating: number; weight: number }>; }
export interface OverviewResponse { brand: string; date_from: string; date_to: string; funnel: FunnelCounts; quality: QualityMetrics; rates: RateMetrics; scorecard: Scorecard | null; data_as_of: string | null; }
export interface TrendPoint { period: string; delivered: number; valid: number; qualified: number; converted: number; }
export interface TrendsResponse { granularity: "day" | "week"; points: TrendPoint[]; }
export interface BrandSplitRow { brand: Brand; funnel: FunnelCounts; quality: QualityMetrics; rates: RateMetrics; }
export interface BrandSplitResponse { rows: BrandSplitRow[]; }

// --- leads ---
export interface LeadOut { id: string; serial_no: number | null; full_name: string | null; phone: string | null; brand: Brand; source_name: string | null; canonical_stage: CanonicalStage; is_invalid: boolean; is_duplicate: boolean; created_at: string | null; }

// --- admin ---
export interface ProviderOut { id: string; name: string; contact_email: string | null; is_active: boolean; created_at: string; updated_at: string; }
export interface ProviderUserCreated { id: string; provider_id: string; email: string; is_active: boolean; created_at: string; temp_password?: string | null; }
export interface ProviderSourceOut { id: string; provider_id: string; brand: Brand; crm_source_id: string; source_name: string | null; }
export interface CrmSourceOut { crm_source_id: string; name: string | null; already_mapped_to: string | null; }
export interface LeaderboardRow { provider_id: string; provider_name: string; delivered: number; valid: number; qualified: number; converted: number; qualification_rate: number; conversion_rate: number; score_pct: number; grade: Grade; }
export interface TargetOut { id: string; brand: Brand | null; metric_key: string; target_value: number; }
export interface SyncStateOut { brand: Brand; last_watermark: string | null; last_run_at: string | null; last_status: string | null; }
export interface SyncStatusResponse { states: SyncStateOut[]; running: boolean; }
```

---

## 5 — Project structure

```
frontend/
├── app/
│   ├── layout.tsx                  # root: providers (React Query, Toaster), fonts
│   ├── globals.css                 # Tailwind + design tokens (stage/grade colors)
│   ├── login/page.tsx              # shared login; routes by role on success
│   ├── (portal)/                   # provider area
│   │   ├── layout.tsx              # portal shell: topbar (brand/date filters, "Data as of")
│   │   ├── dashboard/page.tsx
│   │   ├── leads/page.tsx
│   │   └── payout/page.tsx         # Phase 2 placeholder ("Coming soon")
│   ├── (admin)/                    # admin area
│   │   ├── layout.tsx              # admin shell: sidebar nav
│   │   └── admin/
│   │       ├── page.tsx            # redirect → providers
│   │       ├── providers/page.tsx
│   │       ├── providers/[id]/page.tsx
│   │       ├── leaderboard/page.tsx
│   │       ├── targets/page.tsx
│   │       └── sync/page.tsx
│   └── api/                        # BFF proxy (server only)
│       ├── auth/login/route.ts
│       ├── auth/logout/route.ts
│       └── [...path]/route.ts
├── middleware.ts                   # cookie/role route guard
├── components/
│   ├── ui/                         # shadcn primitives (button, card, table, select, dialog, …)
│   ├── shell/                      # Topbar, Sidebar, BrandFilter, DateRangePicker, DataAsOf
│   ├── charts/                     # FunnelChart, TrendChart, BrandSplitChart, QualityPanel
│   ├── kpi-card.tsx
│   ├── grade-badge.tsx
│   ├── stage-badge.tsx
│   ├── scorecard-panel.tsx
│   ├── leads-table.tsx
│   ├── empty-state.tsx
│   └── skeletons.tsx
├── lib/
│   ├── api.ts                      # axios instance → "/api", 401 handler
│   ├── types.ts                    # §4
│   ├── queries.ts                  # React Query hooks (useOverview, useLeads, …)
│   ├── filters.ts                  # URL <-> filter state (brand/date/stage/q/page)
│   ├── format.ts                   # pct(), int(), duration(), date()
│   └── tokens.ts                   # STAGE_COLORS, GRADE_COLORS, FUNNEL_ORDER
├── .env.example                    # API_URL=...
├── tailwind.config.ts · postcss · tsconfig · package.json · next.config.js
└── README.md
```

---

## 6 — Auth flow & guards (concrete)

- **Login page** (`/login`): email + password form (shadcn `Form` + `zod`). POST `/api/auth/login`.
  On success read `{role}` and `router.replace(role === "admin" ? "/admin" : "/dashboard")`.
  Show inline error on 401; disable submit while pending; the backend rate-limits login (handle 429
  with a "too many attempts, wait a minute" toast).
- **Cookies**: `mis_token` (httpOnly, Secure, SameSite=Lax, path=/, ~12h maxAge to match backend
  `ACCESS_TOKEN_EXPIRE_MINUTES=720`) + `mis_role` (readable, non-sensitive, same maxAge) for
  middleware/UI. Logout clears both.
- **middleware.ts** matchers: protect `/(portal)` and `/(admin)` groups. No `mis_token` → `/login`.
  `mis_role !== "admin"` on `/admin/*` → `/dashboard`. `mis_role === "admin"` on portal → `/admin`.
- **Axios 401 handling** (`lib/api.ts`): on any `401`, hard-redirect to `/login` (session expired).
  React Query: `retry: (n,e) => e.status >= 500 && n < 2`, sensible `staleTime` (30–60s).

---

## 7 — Pages (detailed specs)

### Provider portal

**Shell** (`(portal)/layout.tsx`)
- Topbar: brand filter (`FMC | AV | Both`), date-range picker (presets: 7d, 30d, 90d, custom),
  and a **"Data as of HH:MM"** chip fed by `overview.data_as_of` (relative + exact on hover).
  Filter state lives in the URL (`?brand=&from=&to=`) so views are shareable and refresh-safe.
- Left nav: Dashboard · Leads · Payout (Phase 2, badge "Soon"). Provider name + logout in corner.

**`/dashboard`** — calls `/me/overview`, `/me/trends`, `/me/brand-split`, `/me/quality`.
- **KPI cards** (`KpiCard`): Delivered, Valid % (`valid/delivered`), Qualified, Converted,
  Conversion Rate (`rates.conversion_rate`), Scorecard grade (`GradeBadge` + score%).
  Each card: big number, label, small sub-stat (e.g. invalid/dup count). Skeletons while loading.
- **Funnel chart** (`FunnelChart`, Recharts): delivered → contacted → connected → qualified →
  converted, with **drop-off %** between stages. Use `FUNNEL_ORDER` + `STAGE_COLORS`.
- **Trend chart** (`TrendChart`): line/area of `delivered` + `valid` + `qualified` + `converted`
  over `points`; **day/week toggle** (re-fetches with `granularity`).
- **Brand split** (`BrandSplitChart`): render ONLY if `brandSplit.rows.length > 1` (provider has
  both brands). Side-by-side FMC vs AV funnel/rates.
- **Quality panel** (`QualityPanel`): invalid % and duplicate % as small donut/stat tiles with
  raw counts; subtle warning color if above the target band (target from scorecard criteria, not hard-coded).
- **Scorecard panel** (`ScorecardPanel`): grade + score%, and the per-criterion breakdown from
  `scorecard.criteria` (value, rating 1/3/5, weight) as a small bar/row list.
- Empty state when `delivered === 0` for the range ("No leads in this period").

**`/leads`** — calls `/me/leads` (paged) and triggers `/me/leads/export`.
- Filters row: brand, stage (`CanonicalStage` dropdown), date range, **debounced search** `q`
  (name/phone). All filters in the URL; reset page to 1 on change.
- Table (`LeadsTable`): columns = serial · name · phone · brand (`StageBadge`-style brand chip) ·
  source (`source_name`) · stage (`StageBadge`) · created date · status flags (Invalid/Duplicate badges).
- Pagination control bound to `total/page/page_size` (50 default, allow 25/50/100).
- **Export CSV** button: `window.location = '/api/me/leads/export?<same query>'` (cookie auth via proxy)
  OR fetch blob and download; filename `leads.csv`. Disable while empty.
- Skeleton rows on load; empty state with the active filters echoed.

**`/payout`** — Phase 2 placeholder: a clean "Coming soon" card explaining payouts/disputes will
appear here once the billing model is set. No data calls.

### Admin

**Shell** (`(admin)/layout.tsx`): sidebar — Providers · Leaderboard · Targets · Sync. Admin email + logout.

**`/admin/providers`**
- List (`/admin/providers`): table of name · contact · active · created. "New provider" opens a
  dialog (`POST /admin/providers`). Toggle active via `PUT`.
- **Detail** `/admin/providers/[id]`:
  - Edit provider (name/email/active) → `PUT /admin/providers/{id}`.
  - **Logins**: "Create login" dialog (`POST .../users`); on success show the returned one-time
    `temp_password` in a copyable callout ("shown once") + toast. List existing users.
  - **Map sources**: picker driven by `GET /admin/crm-sources?brand=` (each row shows
    `name` and `already_mapped_to`; disable rows already mapped to another provider). Selecting +
    confirm → `POST .../sources`. Show this provider's current mappings (`GET .../sources`) with brand chips.
    Handle `409` (source already mapped) with a clear toast.

**`/admin/leaderboard`** — `GET /admin/leaderboard?from=&to=`.
- Sortable table: provider · delivered · valid · qualified · converted · qualification_rate ·
  conversion_rate · score% · grade (`GradeBadge`). Default sort by score% desc. Date-range filter in topbar.

**`/admin/targets`** — `GET/PUT /admin/targets`.
- Editable form of target rows grouped by `metric_key` (weights `weight.*`, bands `band.*.ok` /
  `band.*.good`, grades `grade.A..D`), with optional per-`brand` override (null = all brands).
  "Save" sends the full `{targets:[...]}` to `PUT`. Validate numbers; explain each key inline
  (these are company settings, not benchmarks). Provide a "Reset to defaults" hint (re-run backend seed).

**`/admin/sync`** — `GET /admin/sync/status`, `POST /admin/sync/run`.
- Cards per brand: last run, last watermark, last status (color by ok/skipped/error). A global
  "running" indicator. **"Run now"** button (optionally per-brand) → POST, then poll status every
  ~5s while `running` is true (React Query `refetchInterval`). Toast on trigger; disable button while running.

---

## 8 — Components & design tokens

- **`lib/tokens.ts`**:
  ```ts
  export const FUNNEL_ORDER: CanonicalStage[] = ["delivered","contacted","connected","qualified","in_process","converted"];
  export const STAGE_COLORS: Record<CanonicalStage,string> = { /* delivered→converted gradient; dnp/lost muted/red; opportunity amber */ };
  export const GRADE_COLORS: Record<Grade,string> = { A:"emerald", B:"green", C:"amber", D:"orange", F:"red" };
  ```
  Use the SAME stage/grade colors everywhere (badges, charts, funnel).
- **`format.ts`**: `pct(n)` → `"12.3%"` (input is a 0–1 ratio from backend), `int(n)` with
  thousands separators, `duration(sec)` → `"2h 14m"` / `"3.5 days"`, `date(iso)` localized.
- **`KpiCard`, `GradeBadge`, `StageBadge`, `ScorecardPanel`, `LeadsTable`, `EmptyState`, skeletons** —
  all shadcn-based, responsive (cards stack on mobile, tables scroll-x).
- Charts in `components/charts/` wrap Recharts with consistent axes/tooltips/legend and the tokens.

**UX requirements**: skeleton loaders on every async panel; explicit empty states; toasts on every
mutation (create provider/user/source, save targets, run sync); debounced search; keyboard-accessible
dialogs; fully responsive; dark-mode optional but tokens should support it.

---

## 9 — Data fetching (`lib/queries.ts`)

Provide typed React Query hooks, each keyed by its filters so the URL drives the cache:
`useOverview(filters)`, `useTrends(filters, granularity)`, `useBrandSplit(filters)`,
`useQuality(filters)`, `useLeads(filters, page, pageSize)`, and admin hooks
`useProviders()`, `useProvider(id)`, `useCrmSources(brand)`, `useLeaderboard(range)`,
`useTargets()`, `useSyncStatus({refetchInterval})`, plus mutations (`useCreateProvider`,
`useCreateProviderUser`, `useMapSource`, `useSaveTargets`, `useRunSync`) that invalidate the right keys.
All call the Axios instance at `/api/...`; the proxy handles auth.

---

## 10 — Environment

`.env.example`
```
# Server-only — the backend origin. NOT NEXT_PUBLIC (keeps it private; token stays httpOnly).
API_URL=http://localhost:8000
# Cookie security in prod (Vercel sets NODE_ENV=production automatically).
```
On Vercel set `API_URL` to the Railway backend URL. Ensure the backend `CORS_ORIGINS` includes the
Vercel domain (only strictly needed if you ever call the backend directly; the proxy is same-origin
so CORS mostly won't apply).

---

## 11 — Build order

**Phase 1 — MVP**
1. Scaffold Next 14 + TS + Tailwind + shadcn; tokens, `format.ts`, `types.ts`.
2. BFF: `/api/auth/login`, `/api/auth/logout`, `/api/[...path]` proxy; `middleware.ts`; Axios client.
3. Login page + role routing + logout.
4. Provider shell (brand/date filters in URL, "Data as of") + React Query setup.
5. Dashboard: KPI cards → funnel → trend (day/week) → quality → brand-split → scorecard.
6. Leads table: filters + search + pagination + CSV export.
7. Admin: providers list/detail (logins, **map sources** via crm-sources), leaderboard, targets, sync.
8. Polish: skeletons, empty states, toasts, responsive, error boundaries.

**Phase 2** — `/payout` real view + disputes UI, once the backend payout/dispute endpoints land.

---

## 12 — Acceptance checks
- A provider login lands on `/dashboard` and can never reach `/admin/*` (middleware + backend 403).
- All dashboard numbers come from `/me/*` with no `provider_id` ever sent by the client.
- Brand split only renders when the provider has both brands.
- CSV export downloads the currently-filtered leads.
- Admin can create a provider, create a login (sees the one-time temp password), map an unmapped
  CRM source (mapped ones disabled), edit targets, and trigger a sync that flips "running" then shows
  an updated "last run / status".
- Session expiry (backend 401) bounces the user to `/login` cleanly.
- httpOnly cookie: the JWT is not readable from `document.cookie` / `localStorage`.
