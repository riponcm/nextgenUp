"""JWT encoding, decoding, and cryptographic validation."""

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from jose import jwt, JWTError, ExpiredSignatureError

from demerivo.config import get_settings


class JWTAuthError(Exception):
    """Base error for JWT operations."""
    pass


class TokenExpiredAuthError(JWTAuthError):
    """Token has expired."""
    pass


class InvalidTokenAuthError(JWTAuthError):
    """Token signature or claims are invalid."""
    pass


def create_jwt_token(
    user_id: str,
    email: str,
    secret: Optional[str] = None,
    expires_in_seconds: Optional[int] = None,
    algorithm: Optional[str] = None,
    audience: Optional[str] = None,
    extra_claims: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate a signed JWT token matching Supabase Auth claim conventions."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    
    sec = secret or settings.supabase_jwt_secret
    alg = algorithm or settings.jwt_algorithm
    aud = audience or settings.jwt_audience
    exp_seconds = expires_in_seconds if expires_in_seconds is not None else (settings.access_token_expire_minutes * 60)
    
    payload: Dict[str, Any] = {
        "sub": user_id,
        "email": email,
        "role": "authenticated",
        "aud": aud,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=exp_seconds)).timestamp()),
    }
    
    if extra_claims:
        payload.update(extra_claims)
        
    return jwt.encode(payload, sec, algorithm=alg)


def decode_jwt(
    token: str,
    secret: Optional[str] = None,
    algorithm: Optional[str] = None,
    audience: Optional[str] = None,
) -> Dict[str, Any]:
    """Cryptographically decode and validate a JWT token."""
    settings = get_settings()
    sec = secret or settings.supabase_jwt_secret
    alg = algorithm or settings.jwt_algorithm
    aud = audience or settings.jwt_audience

    try:
        claims = jwt.decode(
            token,
            sec,
            algorithms=[alg],
            audience=aud,
            options={"verify_aud": bool(aud)}
        )
        return claims
    except ExpiredSignatureError as e:
        raise TokenExpiredAuthError("Access token has expired") from e
    except JWTError as e:
        raise InvalidTokenAuthError(f"Invalid authentication token: {str(e)}") from e
