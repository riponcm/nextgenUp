"""Authentication provider interface and Supabase Auth implementation."""

import hashlib
import hmac
import os
import secrets
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple

import httpx

from demerivo.auth.jwt import create_jwt_token, decode_jwt, InvalidTokenAuthError, TokenExpiredAuthError
from demerivo.config import Settings, get_settings


class AuthProviderError(Exception):
    """Base error for authentication provider operations."""
    pass


class InvalidCredentialsError(AuthProviderError):
    """Email or password invalid."""
    pass


class UserAlreadyExistsError(AuthProviderError):
    """Email is already registered."""
    pass


class AuthProvider(ABC):
    """Abstract authentication provider contract."""

    @abstractmethod
    def verify_token(self, token: str) -> Dict[str, Any]:
        """Validate an access token and return verified claims."""
        pass

    @abstractmethod
    def register_user(
        self, email: str, password: str, display_name: Optional[str] = None
    ) -> Tuple[Dict[str, Any], str]:
        """Register a user and return (user_dict, access_token)."""
        pass

    @abstractmethod
    def login_user(self, email: str, password: str) -> Tuple[Dict[str, Any], str]:
        """Authenticate user credentials and return (user_dict, access_token)."""
        pass

    @abstractmethod
    def request_password_reset(self, email: str) -> str:
        """Trigger password reset flow and return reset token/confirmation."""
        pass

    @abstractmethod
    def confirm_password_reset(self, token: str, new_password: str) -> bool:
        """Apply new password using reset token."""
        pass

    @abstractmethod
    def verify_email(self, email: str) -> bool:
        """Mark email as verified."""
        pass


class SupabaseAuthProvider(AuthProvider):
    """Supabase Auth implementation supporting remote GoTrue API and local offline test engine."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        # In-memory secure credential store for offline testing / development simulation
        # Maps email -> {"password_hash": "salt$hex", "user_id": "uuid", "display_name": "...", "metadata": {}}
        self._local_auth_store: Dict[str, Dict[str, Any]] = {}
        # Maps reset_token -> {"email": email, "expires_at": timestamp}
        self._reset_tokens: Dict[str, Dict[str, Any]] = {}

    def _hash_password(self, password: str) -> str:
        """Hash password using PBKDF2-HMAC-SHA256 with 100,000 iterations and salt."""
        salt = os.urandom(16)
        key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
        return f"{salt.hex()}${key.hex()}"

    def _verify_password(self, password: str, stored_hash: str) -> bool:
        """Verify password against salt$key hash in constant time."""
        try:
            salt_hex, key_hex = stored_hash.split("$", 1)
            salt = bytes.fromhex(salt_hex)
            expected_key = bytes.fromhex(key_hex)
            actual_key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
            return hmac.compare_digest(actual_key, expected_key)
        except Exception:
            return False

    def verify_token(self, token: str) -> Dict[str, Any]:
        """Verify JWT signature against Supabase JWT secret or JWKS."""
        return decode_jwt(
            token=token,
            secret=self.settings.supabase_jwt_secret,
            algorithm=self.settings.jwt_algorithm,
            audience=self.settings.jwt_audience,
        )

    def register_user(
        self, email: str, password: str, display_name: Optional[str] = None
    ) -> Tuple[Dict[str, Any], str]:
        """Register a new user account."""
        normalized_email = email.strip().lower()

        # If configured for real remote Supabase, call GoTrue API
        if self.settings.supabase_url and self.settings.supabase_anon_key and not self.settings.is_testing:
            try:
                url = f"{self.settings.supabase_url.rstrip('/')}/auth/v1/signup"
                headers = {
                    "apikey": self.settings.supabase_anon_key,
                    "Content-Type": "application/json",
                }
                payload = {
                    "email": normalized_email,
                    "password": password,
                    "data": {"display_name": display_name or ""}
                }
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(url, json=payload, headers=headers)
                    if resp.status_code >= 400:
                        data = resp.json()
                        raise AuthProviderError(data.get("msg") or data.get("error_description") or "Registration failed")
                    data = resp.json()
                    user = data.get("user", {})
                    token = data.get("access_token") or create_jwt_token(user.get("id"), normalized_email)
                    return user, token
            except Exception as e:
                if isinstance(e, AuthProviderError):
                    raise
                raise AuthProviderError(f"Supabase connection error: {str(e)}") from e

        # Offline / Development simulation engine
        if normalized_email in self._local_auth_store:
            raise UserAlreadyExistsError(f"User with email '{normalized_email}' already exists")

        user_id = secrets.token_hex(16)
        # Format UUID: 8-4-4-4-12
        uuid_str = f"{user_id[:8]}-{user_id[8:12]}-{user_id[12:16]}-{user_id[16:20]}-{user_id[20:32]}"
        password_hash = self._hash_password(password)

        user_data = {
            "id": uuid_str,
            "email": normalized_email,
            "display_name": display_name or normalized_email.split("@")[0],
            "avatar_url": None,
            "is_active": True,
            "email_verified": False,
        }
        self._local_auth_store[normalized_email] = {
            "password_hash": password_hash,
            "user_data": user_data,
        }

        token = create_jwt_token(
            user_id=uuid_str,
            email=normalized_email,
            secret=self.settings.supabase_jwt_secret,
        )
        return user_data, token

    def login_user(self, email: str, password: str) -> Tuple[Dict[str, Any], str]:
        """Authenticate existing user credentials."""
        normalized_email = email.strip().lower()

        # If configured for real remote Supabase
        if self.settings.supabase_url and self.settings.supabase_anon_key and not self.settings.is_testing:
            try:
                url = f"{self.settings.supabase_url.rstrip('/')}/auth/v1/token?grant_type=password"
                headers = {
                    "apikey": self.settings.supabase_anon_key,
                    "Content-Type": "application/json",
                }
                payload = {"email": normalized_email, "password": password}
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(url, json=payload, headers=headers)
                    if resp.status_code >= 400:
                        raise InvalidCredentialsError("Invalid email or password")
                    data = resp.json()
                    user = data.get("user", {})
                    token = data.get("access_token")
                    return user, token
            except Exception as e:
                if isinstance(e, InvalidCredentialsError):
                    raise
                raise AuthProviderError(f"Supabase connection error: {str(e)}") from e

        # Offline / Development simulation engine
        record = self._local_auth_store.get(normalized_email)
        if not record or not self._verify_password(password, record["password_hash"]):
            raise InvalidCredentialsError("Invalid email or password")

        user_data = record["user_data"]
        token = create_jwt_token(
            user_id=user_data["id"],
            email=normalized_email,
            secret=self.settings.supabase_jwt_secret,
        )
        return user_data, token

    def request_password_reset(self, email: str) -> str:
        """Generate a secure password reset token."""
        normalized_email = email.strip().lower()
        reset_token = secrets.token_urlsafe(32)
        self._reset_tokens[reset_token] = {
            "email": normalized_email,
        }
        return reset_token

    def confirm_password_reset(self, token: str, new_password: str) -> bool:
        """Confirm password reset and update credentials."""
        record = self._reset_tokens.pop(token, None)
        if not record:
            raise InvalidCredentialsError("Invalid or expired password reset token")
        
        email = record["email"]
        user_record = self._local_auth_store.get(email)
        if user_record:
            user_record["password_hash"] = self._hash_password(new_password)
            return True
        return False

    def verify_email(self, email: str) -> bool:
        """Mark email as verified in local auth store."""
        normalized_email = email.strip().lower()
        record = self._local_auth_store.get(normalized_email)
        if record:
            record["user_data"]["email_verified"] = True
            return True
        return False


_auth_provider_instance: Optional[AuthProvider] = None


def get_auth_provider() -> AuthProvider:
    """Return the global AuthProvider singleton."""
    global _auth_provider_instance
    if _auth_provider_instance is None:
        _auth_provider_instance = SupabaseAuthProvider()
    return _auth_provider_instance
