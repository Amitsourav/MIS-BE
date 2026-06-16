"""Auth endpoints: login (provider or admin), logout, me."""
# NOTE: no `from __future__ import annotations` here on purpose — slowapi's
# @limiter.limit wrapper changes the function's __globals__, which breaks
# FastAPI's resolution of string (forward-ref) annotations. Concrete
# annotations sidestep that.
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, verify_password
from app.database import get_db
from app.deps import Principal, get_principal
from app.models import AdminUser, Provider, ProviderUser
from app.schemas.auth import LoginRequest, MeResponse, TokenResponse
from app.core.ratelimit import limiter

router = APIRouter(prefix="/auth", tags=["auth"])

_BAD_CREDS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
)


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(
    request: Request, body: LoginRequest, db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    email = body.email.lower().strip()

    # Try provider login first.
    pu = (
        await db.execute(select(ProviderUser).where(ProviderUser.email == email))
    ).scalar_one_or_none()
    if pu is not None:
        if not pu.is_active or not verify_password(body.password, pu.password_hash):
            raise _BAD_CREDS
        provider = await db.get(Provider, pu.provider_id)
        if provider is None or not provider.is_active:
            raise _BAD_CREDS
        token = create_access_token(
            subject=str(pu.id), role="provider", provider_id=str(pu.provider_id)
        )
        return TokenResponse(
            access_token=token, role="provider", provider_id=str(pu.provider_id)
        )

    # Else try admin login.
    au = (
        await db.execute(select(AdminUser).where(AdminUser.email == email))
    ).scalar_one_or_none()
    if au is not None and verify_password(body.password, au.password_hash):
        brand = au.brand.value if au.brand is not None else None
        token = create_access_token(subject=str(au.id), role="admin", brand=brand)
        return TokenResponse(access_token=token, role="admin", brand=brand)

    raise _BAD_CREDS


@router.post("/logout")
async def logout(_: Principal = Depends(get_principal)) -> dict:
    # Stateless JWT: client discards the token. Endpoint exists for symmetry and
    # future token-revocation support.
    return {"detail": "logged out"}


@router.get("/me", response_model=MeResponse)
async def me(
    principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)
) -> MeResponse:
    try:
        user_uuid = uuid.UUID(principal.user_id)
    except (ValueError, TypeError):
        raise _BAD_CREDS

    if principal.role == "admin":
        au = await db.get(AdminUser, user_uuid)
        if au is None:
            raise _BAD_CREDS
        return MeResponse(
            user_id=str(au.id),
            email=au.email,
            role="admin",
            brand=au.brand.value if au.brand is not None else None,
        )

    pu = await db.get(ProviderUser, user_uuid)
    if pu is None:
        raise _BAD_CREDS
    provider = await db.get(Provider, pu.provider_id)
    return MeResponse(
        user_id=str(pu.id),
        email=pu.email,
        role="provider",
        provider_id=str(pu.provider_id),
        provider_name=provider.name if provider else None,
        brand=provider.brand.value if provider else None,
    )
