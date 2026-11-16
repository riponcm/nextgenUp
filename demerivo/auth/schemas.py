"""Pydantic schemas for authentication and identity operations."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    """User registration payload."""
    email: EmailStr = Field(..., json_schema_extra={"example": "user@example.com"})
    password: str = Field(..., min_length=8, description="Minimum 8 characters password")
    display_name: Optional[str] = Field(None, max_length=100, json_schema_extra={"example": "Jane Doe"})


class LoginRequest(BaseModel):
    """User login payload."""
    email: EmailStr = Field(..., json_schema_extra={"example": "user@example.com"})
    password: str = Field(..., min_length=1)


class PasswordResetRequest(BaseModel):
    """Request password reset link/token."""
    email: EmailStr = Field(..., json_schema_extra={"example": "user@example.com"})


class PasswordResetConfirmRequest(BaseModel):
    """Confirm password reset with received token."""
    token: str = Field(..., min_length=10)
    new_password: str = Field(..., min_length=8)


class EmailVerificationRequest(BaseModel):
    """Payload to verify user email address with cryptographic token."""
    token: str = Field(..., min_length=16, description="Cryptographic single-use verification token")
    email: Optional[EmailStr] = Field(None, description="Optional bound email address for verification check")


class ResendVerificationRequest(BaseModel):
    """Payload to request a new email verification token."""
    email: EmailStr = Field(..., description="Registered account email address")


class UserProfileResponse(BaseModel):
    """Public customer profile data."""
    id: str
    email: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    is_active: bool
    email_verified: bool
    created_at: Optional[str] = None


class TenantRoleInfo(BaseModel):
    """Tenant information with user's specific membership role."""
    id: str
    name: str
    slug: str
    is_personal: bool
    role: str


class TokenResponse(BaseModel):
    """Standard Bearer token response."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserProfileResponse


class AuthMeResponse(BaseModel):
    """Contextual authenticated session summary including active tenant and balance."""
    user: UserProfileResponse
    active_tenant: TenantRoleInfo
    tenants: List[TenantRoleInfo]
    wallet_balance: int
