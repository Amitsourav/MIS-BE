"""Admin-facing endpoints (role=admin): provider/user/source management,
leaderboard, targets, and live-data status. Lead and payout numbers are read
live from the CRMs on every request."""
from __future__ import annotations

import logging
import secrets
import uuid
from datetime import date, datetime, timezone

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
from app.schemas.payout import PayoutsResponse
from app.schemas.target import TargetOut, TargetsBulkUpdate
from app.crm import reader
from app.services import live, payouts
from app.services import metrics as M
from app.services.scorecard import compute_scorecard, load_targets

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

    # No backfill needed: data is read live, so the provider sees this
    # source's leads and payouts on their very next request.
    await db.commit()
    await db.refresh(mapping)
    _audit(principal, "map_source", provider_id=str(provider_id),
           brand=body.brand.value, crm_source_id=str(body.crm_source_id))
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


@router.get("/providers/{provider_id}/payouts", response_model=PayoutsResponse)
async def provider_payouts(
    provider_id: uuid.UUID,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> PayoutsResponse:
    """The provider's Payout page as an admin sees it. Out-of-scope providers
    404; an AV provider returns brand_supported=false."""
    provider = await _get_scoped_provider(db, principal, provider_id)
    rows = await db.execute(
        select(ProviderSource.crm_source_id).where(
            ProviderSource.provider_id == provider.id,
            ProviderSource.brand == provider.brand,
        )
    )
    return await payouts.build_payouts_page(
        provider_brand=provider.brand,
        source_ids=[r[0] for r in rows.all()],
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )


@router.get("/crm-sources", response_model=list[CrmSourceOut])
async def crm_sources(
    brand: Brand,
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[CrmSourceOut]:
    """Read-only proxy of a CRM's lead_sources, annotated with current mapping."""
    _assert_brand(principal, brand)
    sources = await reader.lead_sources(brand)  # CrmUnavailable -> 503 (main.py)

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
    from app.api.provider import _range  # reuse the default-range rule

    frm, to = _range(date_from, date_to)
    rows: list[LeaderboardRow] = []
    for brand in principal.admin_brands():
        # Score each vendor by its own company's targets.
        targets = await load_targets(db, brand)
        mapped = await db.execute(
            select(Provider, ProviderSource.crm_source_id)
            .join(ProviderSource, ProviderSource.provider_id == Provider.id)
            .where(Provider.brand == brand, ProviderSource.brand == brand)
        )
        provider_by_source: dict[uuid.UUID, Provider] = {}
        for p, sid in mapped.all():
            provider_by_source[sid] = p
        if not provider_by_source:
            continue

        # One live query per company for every mapped source, then group.
        facts = await live.lead_facts(brand, list(provider_by_source), frm, to)
        by_provider: dict[uuid.UUID, list[live.LeadFact]] = {}
        for f in facts:
            p = provider_by_source.get(f.crm_source_id)
            if p is not None:
                by_provider.setdefault(p.id, []).append(f)
        names = {p.id: p.name for p in provider_by_source.values()}

        for pid, pfacts in by_provider.items():
            agg = M.aggregate(pfacts)
            if agg["delivered"] == 0:
                continue
            validity = M.safe_div(agg["valid"], agg["delivered"])
            qual_rate = M.safe_div(agg["qualified"], agg["valid"])
            conv_rate = M.safe_div(agg["converted"], agg["valid"])
            reliability = M.volume_reliability(pfacts, frm, to)
            rows.append(
                _leaderboard_row(
                    pid, names[pid], agg, targets,
                    validity=validity, qual_rate=qual_rate,
                    conv_rate=conv_rate, reliability=reliability,
                )
            )
    rows.sort(key=lambda r: r.score_pct, reverse=True)
    return rows


def _leaderboard_row(
    provider_id: uuid.UUID,
    provider_name: str,
    agg: dict[str, int],
    targets: dict[str, float],
    *,
    validity: float,
    qual_rate: float,
    conv_rate: float,
    reliability: float,
) -> LeaderboardRow:
    sc = compute_scorecard(
        {
            "validity": validity,
            "qualification_rate": qual_rate,
            "conversion_rate": conv_rate,
            "volume_reliability": reliability,
            "dispute_rate": 0.0,
        },
        targets,
    )
    return LeaderboardRow(
        provider_id=provider_id,
        provider_name=provider_name,
        delivered=agg["delivered"],
        valid=agg["valid"],
        qualified=agg["qualified"],
        converted=agg["converted"],
        qualification_rate=qual_rate,
        conversion_rate=conv_rate,
        score_pct=sc.score_pct,
        grade=sc.grade,
    )


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


# ---------------- Live-data status (former sync control) ----------------
# There is no sync any more: every page reads the CRMs live. These two routes
# stay so existing clients keep working; they report "live" and never start
# anything.
_LIVE_DETAIL = "Data is live: every page reads the CRM directly, so no sync is needed."


@router.post("/sync/run", response_model=SyncRunResponse)
async def run_sync_now(
    brand: Brand | None = None,
    principal: Principal = Depends(require_admin),
) -> SyncRunResponse:
    if brand is not None:
        _assert_brand(principal, brand)
    return SyncRunResponse(triggered=False, detail=_LIVE_DETAIL)


@router.get("/sync/status", response_model=SyncStatusResponse)
async def sync_status(principal: Principal = Depends(require_admin)) -> SyncStatusResponse:
    now = datetime.now(timezone.utc)
    states = [
        SyncStateOut(brand=b, last_watermark=None, last_run_at=now, last_status="live")
        for b in principal.admin_brands()
    ]
    return SyncStatusResponse(states=states, running=False)
