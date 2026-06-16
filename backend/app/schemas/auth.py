"""Auth request/response schemas."""
from __future__ import annotations

from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    provider_id: str | None = None
    # Admin company scope: null = super-admin (both companies); 'fmc'/'av' = company admin.
    brand: str | None = None


class MeResponse(BaseModel):
    user_id: str
    email: str
    role: str
    provider_id: str | None = None
    provider_name: str | None = None
    # For admins: their company scope (null = super-admin). For providers: their company.
    brand: str | None = None
