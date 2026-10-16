"""Auth dependencies and the server-side provider-scoping guard.

The cardinal rule: a provider's `provider_id` is read ONLY from their verified
JWT — never from query/body/header input. Every provider-facing query is scoped
by this value. `require_provider` is the single chokepoint that enforces it.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import timing
from app.core.security import JWTError, decode_access_token
from app.database import get_db
from app.models import AdminUser, Provider, ProviderSource, ProviderUser
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
    issued_at: int | None = None  # token "iat" (unix seconds)
    # provider only: the vendor's name and whether it is a sales demo
    provider_name: str | None = None
    is_demo: bool = False

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
        issued_at=payload.get("iat"),
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
    with timing.span("auth"):
        return await _require_provider(principal, db)


@dataclass(frozen=True)
class _LoginState:
    """What `require_provider` needs about one provider login, read in one query."""

    user_active: bool
    provider_id: uuid.UUID
    password_changed_at: datetime | None
    provider_active: bool
    brand: Brand
    provider_name: str
    is_demo: bool
    source_names: dict[uuid.UUID, str | None]


# Every partner request checks its login. The MIS database is a network hop
# away, so a login's state is remembered briefly: at most _LOGIN_TTL_SEC old,
# and dropped at once by any admin change (see `forget_logins`, called after
# every admin write) — so a reset or deactivation still takes effect on the
# partner's next click. Per process; with several replicas the TTL bounds it.
_LOGIN_TTL_SEC = 30.0
_logins: dict[uuid.UUID, tuple[float, _LoginState | None]] = {}


def forget_logins() -> None:
    _logins.clear()


async def _login_state(db: AsyncSession, user_id: uuid.UUID) -> _LoginState | None:
    hit = _logins.get(user_id)
    if hit is not None and time.monotonic() - hit[0] < _LOGIN_TTL_SEC:
        return hit[1]
    rows = (await db.execute(
        select(
            ProviderUser.is_active, ProviderUser.provider_id, ProviderUser.password_changed_at,
            Provider.is_active, Provider.brand, Provider.name, Provider.is_demo,
            ProviderSource.crm_source_id, ProviderSource.source_name,
        )
        .join(Provider, Provider.id == ProviderUser.provider_id)
        .outerjoin(ProviderSource, and_(
            ProviderSource.provider_id == Provider.id, ProviderSource.brand == Provider.brand,
        ))
        .where(ProviderUser.id == user_id)
    )).all()
    state = None
    if rows:
        r = rows[0]
        state = _LoginState(
            user_active=r[0], provider_id=r[1], password_changed_at=r[2],
            provider_active=r[3], brand=r[4], provider_name=r[5], is_demo=bool(r[6]),
            source_names={row[7]: row[8] for row in rows if row[7] is not None},
        )
    _logins[user_id] = (time.monotonic(), state)
    return state


async def _require_provider(principal: Principal, db: AsyncSession) -> Principal:
    if principal.role != "provider" or principal.provider_id is None:
        raise _FORBIDDEN

    # The login itself must still be valid: not deactivated, still this
    # provider's, and the token not issued before a password reset.
    try:
        user_id = uuid.UUID(principal.user_id)
    except (ValueError, TypeError):
        raise _UNAUTH
    state = await _login_state(db, user_id)
    if state is None or not state.user_active or state.provider_id != principal.provider_id:
        raise _UNAUTH
    if state.password_changed_at is not None:
        changed = state.password_changed_at
        if changed.tzinfo is None:  # SQLite drops tz; stored values are UTC
            changed = changed.replace(tzinfo=timezone.utc)
        if principal.issued_at is None or principal.issued_at < int(changed.timestamp()):
            raise _UNAUTH
    if not state.provider_active:
        raise _FORBIDDEN

    principal.brand = state.brand  # the vendor's single company
    principal.provider_name = state.provider_name
    principal.is_demo = state.is_demo
    principal.source_names = dict(state.source_names)
    principal.source_ids = list(principal.source_names)
    return principal
