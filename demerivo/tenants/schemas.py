"""Pydantic schemas for tenant and workspace operations."""

from typing import List, Optional
from pydantic import BaseModel, EmailStr, Field

from demerivo.db.models import TenantRole


class TenantCreateRequest(BaseModel):
    """Payload to create a new team or enterprise workspace."""
    name: str = Field(..., min_length=2, max_length=100, json_schema_extra={"example": "Acme Studios"})


class TenantResponse(BaseModel):
    """Workspace representation."""
    id: str
    name: str
    slug: str
    is_personal: bool
    owner_id: str
    role: Optional[str] = None
    created_at: Optional[str] = None


class MemberAddRequest(BaseModel):
    """Payload to invite or add a member to a workspace."""
    email: EmailStr = Field(..., json_schema_extra={"example": "colleague@example.com"})
    role: TenantRole = Field(TenantRole.MEMBER, description="Role: admin or member")


class MemberResponse(BaseModel):
    """Workspace member details."""
    id: str
    tenant_id: str
    user_id: str
    email: str
    display_name: Optional[str] = None
    role: str
    created_at: Optional[str] = None
