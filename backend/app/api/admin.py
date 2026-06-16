"""Admin-facing endpoints (role=admin): provider/user/source management,
leaderboard, targets, and sync control."""
from __future__ import annotations

import asyncio
import logging
import secrets
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.database import get_db
from app.deps import Principal, require_admin
from app.models import (
    AdminUser,
    Provider,
    ProviderSource,
    ProviderUser,
    Target,
)
from app.models.enums import Brand
from app.schemas.provider import (
    AdminCreate,
    AdminCreated,
    AdminOut,
    CrmSourceOut,
    ProviderCreate,
    ProviderOut,
    ProviderSourceCreate,
    ProviderSourceOut,
    ProviderUpdate,
    ProviderUserCreate,
    ProviderUserCreated,
)
from app.schemas.sync import (
    LeaderboardRow,
    SyncRunResponse,
    SyncStateOut,
    SyncStatusResponse,
)
from app.schemas.target import TargetOut, TargetsBulkUpdate
from app.models import SyncState
from app.services import metrics as M
from app.services import rollup
from app.services.scorecard import compute_scorecard, load_targets
from app.sync import connectors, worker

logger = logging.getLogger("mis.admin")
router = APIRouter(prefix="/admin", tags=["admin"])


def _audit(principal: Principal, action: str, **ctx) -> None:
    logger.info("AUDIT admin=%s action=%s %s", principal.user_id, action, ctx)


def _assert_brand(principal: Principal, brand: Brand) -> None:
    """Reject a company admin trying to act outside their own company."""
    if not principal.can_admin_brand(brand):
        raise HTTPException(403, "Outside your company scope")


async def _get_scoped_provider(
    db: AsyncSession, principal: Principal, provider_id: uuid.UUID
) -> Provider:
    """Load a provider, treating out-of-scope providers as not found (so a
    company admin can't even probe for the other company's vendors)."""
    provider = await db.get(Provider, provider_id)
    if provider is None or not principal.can_admin_brand(provider.brand):
        raise HTTPException(404, "Provider not found")
    return provider


# ---------------- Admin accounts (super-admin only) ----------------
@router.post("/admins", response_model=AdminCreated, status_code=201)
async def create_admin(
    body: AdminCreate,
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminCreated:
    """Create another admin from the dashboard. Only a super-admin may do this;
    company admins cannot mint admins."""
    if not principal.is_super_admin:
        raise HTTPException(403, "Only a super-admin can create admins")

    temp_password = body.password or secrets.token_urlsafe(9)
    admin = AdminUser(
        email=body.email.lower().strip(),
        password_hash=hash_password(temp_password),
        brand=body.brand,  # None = super-admin; fmc/av = company admin
    )
    db.add(admin)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Email already in use")
    await db.refresh(admin)
    _audit(
        principal,
        "create_admin",
        admin_id=str(admin.id),
        scope=admin.brand.value if admin.brand else "super",
    )
    return AdminCreated(
        id=admin.id,
        email=admin.email,
        brand=admin.brand,
        created_at=admin.created_at,
        # Returned once so the super-admin can hand it over; never stored in plaintext.
        temp_password=None if body.password else temp_password,
    )


@router.get("/admins", response_model=list[AdminOut])
async def list_admins(
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[AdminOut]:
    if not principal.is_super_admin:
        raise HTTPException(403, "Only a super-admin can view admins")
    rows = await db.execute(select(AdminUser).order_by(AdminUser.created_at.desc()))
    return [AdminOut.model_validate(a) for a in rows.scalars().all()]


# ---------------- Providers ----------------
@router.post("/providers", response_model=ProviderOut, status_code=201)
async def create_provider(
    body: ProviderCreate,
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ProviderOut:
    # A company admin can only create vendors in their own company; a super-admin
    # must say which company the vendor belongs to.
    if principal.brand is not None:
        brand = principal.brand
    elif body.brand is not None:
        brand = body.brand
    else:
        raise HTTPException(422, "brand is required (which company this vendor belongs to)")

    provider = Provider(
        name=body.name,
        brand=brand,
        contact_email=body.contact_email,
        is_active=body.is_active,
    )
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    _audit(principal, "create_provider", provider_id=str(provider.id), brand=brand.value)
    return ProviderOut.model_validate(provider)


@router.get("/providers", response_model=list[ProviderOut])
async def list_providers(
    principal: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> list[ProviderOut]:
    rows = await db.execute(
        select(Provider)
        .where(Provider.brand.in_(principal.admin_brands()))
        .order_by(Provider.created_at.desc())
    )
    return [ProviderOut.model_validate(p) for p in rows.scalars().all()]


@router.put("/providers/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: uuid.UUID,
    body: ProviderUpdate,
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ProviderOut:
    provider = await _get_scoped_provider(db, principal, provider_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(provider, field, value)
    await db.commit()
    await db.refresh(provider)
    _audit(principal, "update_provider", provider_id=str(provider_id))
    return ProviderOut.model_validate(provider)


# ---------------- Provider login users ----------------
@router.post(
    "/providers/{provider_id}/users",
    response_model=ProviderUserCreated,
    status_code=201,
)
async def create_provider_user(
    provider_id: uuid.UUID,
    body: ProviderUserCreate,
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ProviderUserCreated:
    await _get_scoped_provider(db, principal, provider_id)

    temp_password = body.password or secrets.token_urlsafe(9)
    user = ProviderUser(
        provider_id=provider_id,
        email=body.email.lower().strip(),
        password_hash=hash_password(temp_password),
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Email already in use")
    await db.refresh(user)
    _audit(principal, "create_provider_user", provider_id=str(provider_id),
           email=user.email)
    return ProviderUserCreated(
        id=user.id,
        provider_id=user.provider_id,
        email=user.email,
        is_active=user.is_active,
        created_at=user.created_at,
        # Returned once so the admin can hand it over; never stored in plaintext.
        temp_password=None if body.password else temp_password,
    )


# ---------------- Source mapping ----------------
@router.post(
    "/providers/{provider_id}/sources",
    response_model=ProviderSourceOut,
    status_code=201,
)
async def map_source(
    provider_id: uuid.UUID,
    body: ProviderSourceCreate,
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> ProviderSourceOut:
    provider = await _get_scoped_provider(db, principal, provider_id)
    if body.brand != provider.brand:
        raise HTTPException(
            422, "Source company must match the provider's company"
        )

    mapping = ProviderSource(
        provider_id=provider_id,
        brand=body.brand,
        crm_source_id=body.crm_source_id,
        source_name=body.source_name,
    )
    db.add(mapping)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            409, "This CRM source is already mapped to a provider"
        )

    # Backfill: claim any leads already pulled from this source before it was
    # mapped, attributing them to the provider and rebuilding their rollups.
    claimed = await rollup.claim_unmapped_leads(
        db,
        provider_id=provider_id,
        brand=body.brand,
        crm_source_id=body.crm_source_id,
    )
    await db.commit()
    await db.refresh(mapping)
    _audit(principal, "map_source", provider_id=str(provider_id),
           brand=body.brand.value, crm_source_id=str(body.crm_source_id),
           backfilled=claimed)
    return ProviderSourceOut.model_validate(mapping)


@router.get("/providers/{provider_id}/sources", response_model=list[ProviderSourceOut])
async def list_provider_sources(
    provider_id: uuid.UUID,
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[ProviderSourceOut]:
    await _get_scoped_provider(db, principal, provider_id)
    rows = await db.execute(
        select(ProviderSource).where(ProviderSource.provider_id == provider_id)
    )
    return [ProviderSourceOut.model_validate(s) for s in rows.scalars().all()]


@router.get("/crm-sources", response_model=list[CrmSourceOut])
async def crm_sources(
    brand: Brand,
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[CrmSourceOut]:
    """Read-only proxy of a CRM's lead_sources, annotated with current mapping."""
    _assert_brand(principal, brand)
    try:
        sources = await connectors.list_lead_sources(brand)
    except connectors.CrmUnavailable as exc:
        raise HTTPException(503, str(exc))

    mapped = await db.execute(
        select(ProviderSource.crm_source_id, Provider.name)
        .join(Provider, Provider.id == ProviderSource.provider_id)
        .where(ProviderSource.brand == brand)
    )
    mapped_by = {sid: name for sid, name in mapped.all()}
    return [
        CrmSourceOut(
            crm_source_id=s["crm_source_id"],
            name=s.get("name"),
            already_mapped_to=mapped_by.get(s["crm_source_id"]),
        )
        for s in sources
    ]


# ---------------- Leaderboard ----------------
@router.get("/leaderboard", response_model=list[LeaderboardRow])
async def leaderboard(
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[LeaderboardRow]:
    from app.api.provider import _range, _volume_reliability  # reuse helpers

    frm, to = _range(date_from, date_to)
    # Cache each company's flattened targets so a company admin (and the
    # super-admin's cross-company board) score each vendor by its own company.
    targets_by_brand = {b: await load_targets(db, b) for b in principal.admin_brands()}

    providers = (
        await db.execute(
            select(Provider).where(Provider.brand.in_(principal.admin_brands()))
        )
    ).scalars().all()
    rows: list[LeaderboardRow] = []
    for p in providers:
        brands = [p.brand]  # each vendor belongs to exactly one company
        agg = await M.sum_rollups(db, p.id, brands, frm, to)
        if agg["delivered"] == 0:
            continue
        validity = M.safe_div(agg["valid"], agg["delivered"])
        qual_rate = M.safe_div(agg["qualified"], agg["valid"])
        conv_rate = M.safe_div(agg["converted"], agg["valid"])
        reliability = await _volume_reliability(db, p.id, brands, frm, to)
        sc = compute_scorecard(
            {
                "validity": validity,
                "qualification_rate": qual_rate,
                "conversion_rate": conv_rate,
                "volume_reliability": reliability,
                "dispute_rate": 0.0,
            },
            targets_by_brand[p.brand],
        )
        rows.append(
            LeaderboardRow(
                provider_id=p.id,
                provider_name=p.name,
                delivered=agg["delivered"],
                valid=agg["valid"],
                qualified=agg["qualified"],
                converted=agg["converted"],
                qualification_rate=qual_rate,
                conversion_rate=conv_rate,
                score_pct=sc.score_pct,
                grade=sc.grade,
            )
        )
    rows.sort(key=lambda r: r.score_pct, reverse=True)
    return rows


# ---------------- Targets ----------------
@router.get("/targets", response_model=list[TargetOut])
async def get_targets(
    principal: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> list[TargetOut]:
    stmt = select(Target).order_by(Target.metric_key)
    if principal.brand is not None:
        # Company admin sees the global defaults plus their own company's overrides.
        stmt = stmt.where(or_(Target.brand.is_(None), Target.brand == principal.brand))
    rows = await db.execute(stmt)
    return [TargetOut.model_validate(t) for t in rows.scalars().all()]


@router.put("/targets", response_model=list[TargetOut])
async def put_targets(
    body: TargetsBulkUpdate,
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[TargetOut]:
    """Replace targets by (brand, metric_key). Upsert semantics, brand-aware.

    A company admin may only write overrides scoped to their own company (never
    the global defaults, which would affect the other company). The super-admin
    may write any target, including the global defaults (brand = null).
    """
    if principal.brand is not None:
        for t in body.targets:
            if t.brand != principal.brand:
                raise HTTPException(
                    403,
                    "Company admins may only edit targets for their own company",
                )

    for t in body.targets:
        await db.execute(
            delete(Target).where(
                Target.metric_key == t.metric_key,
                Target.brand.is_(None) if t.brand is None else Target.brand == t.brand,
            )
        )
        db.add(Target(brand=t.brand, metric_key=t.metric_key, target_value=t.target_value))
    await db.commit()
    _audit(principal, "update_targets", count=len(body.targets))
    return await get_targets(principal, db)


# ---------------- Sync control ----------------
@router.post("/sync/run", response_model=SyncRunResponse)
async def run_sync_now(
    brand: Brand | None = None,
    principal: Principal = Depends(require_admin),
) -> SyncRunResponse:
    if worker.is_running():
        return SyncRunResponse(triggered=False, detail="A sync is already running")
    if brand is not None:
        _assert_brand(principal, brand)
        brands = [brand]
    else:
        # A company admin can only run their own company; super-admin runs all.
        brands = principal.admin_brands()
    # Fire-and-forget; status is observable via /admin/sync/status.
    asyncio.create_task(worker.run_sync(brands))
    _audit(principal, "run_sync", brand=brand.value if brand else "all-in-scope")
    return SyncRunResponse(triggered=True, detail="Sync started")


@router.get("/sync/status", response_model=SyncStatusResponse)
async def sync_status(
    principal: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db)
) -> SyncStatusResponse:
    rows = await db.execute(
        select(SyncState).where(SyncState.brand.in_(principal.admin_brands()))
    )
    states = [
        SyncStateOut(
            brand=s.brand,
            last_watermark=s.last_watermark,
            last_run_at=s.last_run_at,
            last_status=s.last_status,
        )
        for s in rows.scalars().all()
    ]
    return SyncStatusResponse(states=states, running=worker.is_running())
