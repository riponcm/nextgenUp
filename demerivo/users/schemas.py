"""Pydantic schemas for user profile operations."""

from typing import Optional
from pydantic import BaseModel, Field


class ProfileUpdateRequest(BaseModel):
    """Payload to update personal profile information."""
    display_name: Optional[str] = Field(None, max_length=100, json_schema_extra={"example": "Jane Doe"})
    avatar_url: Optional[str] = Field(None, max_length=512, json_schema_extra={"example": "https://example.com/avatar.jpg"})


class ProfileResponse(BaseModel):
    """User profile response."""
    id: str
    email: str
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    is_active: bool
    email_verified: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
