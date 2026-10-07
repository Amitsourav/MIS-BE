"""Auth dependencies and the server-side provider-scoping guard.

The cardinal rule: a provider's `provider_id` is read ONLY from their verified
JWT — never from query/body/header input. Every provider-facing query is scoped
by this value. `require_provider` is the single chokepoint that enforces it.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import JWTError, decode_access_token
from app.database import get_db
from app.models import AdminUser, Provider, ProviderSource
from app.models.enums import Brand

_bearer = HTTPBearer(auto_error=False)

_UNAUTH = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)
_FORBIDDEN = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
)


@dataclass
class Principal:
    user_id: str
    role: str  # 'provider' | 'admin'
    provider_id: uuid.UUID | None = None
    # Company scope:
    #   - admin: None = super-admin (both companies); Brand = company admin
    #   - provider: the single company the vendor belongs to
    brand: Brand | None = None
    # The provider's mapped CRM source ids (in its own company) and their
    # display names — the ONLY scope any live CRM query may use.
    source_ids: list[uuid.UUID] = field(default_factory=list)
    source_names: dict[uuid.UUID, str | None] = field(default_factory=dict)

    @property
    def is_super_admin(self) -> bool:
        return self.role == "admin" and self.brand is None

    def admin_brands(self) -> list[Brand]:
        """Brands this admin may act on: their one company, or all if super."""
        return [self.brand] if self.brand is not None else list(Brand)

    def can_admin_brand(self, brand: Brand) -> bool:
        """True if this admin is allowed to act on the given company."""
        return self.brand is None or self.brand == brand


async def get_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    if creds is None or not creds.credentials:
        raise _UNAUTH
    try:
        payload = decode_access_token(creds.credentials)
    except JWTError:
        raise _UNAUTH

    sub = payload.get("sub")
    role = payload.get("role")
    if not sub or role not in ("provider", "admin"):
        raise _UNAUTH

    provider_id = payload.get("provider_id")
    brand_raw = payload.get("brand")
    return Principal(
        user_id=sub,
        role=role,
        provider_id=uuid.UUID(provider_id) if provider_id else None,
        brand=Brand(brand_raw) if brand_raw else None,
    )


async def require_admin(
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    """Verify the caller is an admin and (re)load their company scope from the DB,
    so a changed/revoked scope takes effect without waiting for token expiry."""
    if principal.role != "admin":
        raise _FORBIDDEN
    try:
        admin = await db.get(AdminUser, uuid.UUID(principal.user_id))
    except (ValueError, TypeError):
        raise _UNAUTH
    if admin is None:
        raise _FORBIDDEN
    principal.brand = admin.brand  # authoritative scope (None = super-admin)
    return principal


async def require_provider(
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
) -> Principal:
    """Resolve and verify the caller is an active provider, and attach the
    provider's source ids. The returned `provider_id` is the ONLY identity any
    provider-facing query may use for scoping."""
    if principal.role != "provider" or principal.provider_id is None:
        raise _FORBIDDEN

    provider = await db.get(Provider, principal.provider_id)
    if provider is None or not provider.is_active:
        raise _FORBIDDEN

    principal.brand = provider.brand  # the vendor's single company
    rows = await db.execute(
        select(ProviderSource.crm_source_id, ProviderSource.source_name).where(
            ProviderSource.provider_id == principal.provider_id,
            ProviderSource.brand == provider.brand,
        )
    )
    principal.source_names = {sid: name for sid, name in rows.all()}
    principal.source_ids = list(principal.source_names)
    return principal
