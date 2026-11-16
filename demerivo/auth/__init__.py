"""Authentication and identity synchronization package for Demerivo PixelFix."""

from demerivo.auth.jwt import decode_jwt, create_jwt_token
from demerivo.auth.provider import get_auth_provider
from demerivo.auth.dependencies import get_current_user, get_current_tenant, require_role

__all__ = [
    "decode_jwt",
    "create_jwt_token",
    "get_auth_provider",
    "get_current_user",
    "get_current_tenant",
    "require_role",
]
