"""Provider, source-mapping, and admin-management schemas."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr

from app.models.enums import Brand


# --- Providers ---
class ProviderCreate(BaseModel):
    name: str
    # The company this vendor belongs to. Required for a super-admin; ignored
    # (forced to the admin's own company) for a company admin.
    brand: Brand | None = None
    contact_email: EmailStr | None = None
    is_active: bool = True


class ProviderUpdate(BaseModel):
    name: str | None = None
    contact_email: EmailStr | None = None
    is_active: bool | None = None


class ProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    brand: Brand
    contact_email: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


# --- Provider login users ---
class ProviderUserCreate(BaseModel):
    email: EmailStr
    # If omitted, the API generates a temp password and returns it once.
    password: str | None = None


class ProviderUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider_id: uuid.UUID
    email: str
    is_active: bool
    created_at: datetime


class ProviderUserCreated(ProviderUserOut):
    # Returned once on creation so the admin can hand the temp password over.
    temp_password: str | None = None


# --- Source mapping ---
class ProviderSourceCreate(BaseModel):
    brand: Brand
    crm_source_id: uuid.UUID
    source_name: str | None = None


class ProviderSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider_id: uuid.UUID
    brand: Brand
    crm_source_id: uuid.UUID
    source_name: str | None


class CrmSourceOut(BaseModel):
    """A lead_source row proxied read-only from a CRM, for the mapping picker."""

    crm_source_id: uuid.UUID
    name: str | None
    already_mapped_to: str | None = None  # provider name if taken


# --- Admin accounts (managed by the super-admin from the dashboard) ---
class AdminCreate(BaseModel):
    email: EmailStr
    # If omitted, the API generates a temp password and returns it once.
    password: str | None = None
    # null = super-admin (both companies); 'fmc'/'av' = company admin.
    brand: Brand | None = None


class AdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    brand: Brand | None
    created_at: datetime


class AdminCreated(AdminOut):
    # Returned once on creation so the super-admin can hand the temp password over.
    temp_password: str | None = None
