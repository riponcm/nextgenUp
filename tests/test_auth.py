"""Automated tests for authentication, JWT verification, and Supabase integration."""

import time
import pytest
from datetime import datetime, timezone, timedelta
from jose import jwt
from sqlalchemy.orm import Session

from demerivo.auth.jwt import (
    create_jwt_token,
    decode_jwt,
    InvalidTokenAuthError,
    TokenExpiredAuthError,
)
from demerivo.auth.provider import (
    InvalidCredentialsError,
    SupabaseAuthProvider,
    UserAlreadyExistsError,
)
from demerivo.auth.dependencies import get_current_user, get_current_tenant
from demerivo.config import Settings
from demerivo.db.models import TenantModel, TenantRole, UserModel, WalletModel
from demerivo.db.session import Base, create_db_engine


@pytest.fixture
def test_db():
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


class TestAuthenticationAndJWT:
    """Test cryptographic JWT validation and claims enforcement."""

    def test_valid_jwt_roundtrip(self):
        token = create_jwt_token(
            user_id="user-12345",
            email="alice@example.com",
            secret="test-secret-at-least-32-characters-long",
            expires_in_seconds=3600,
        )
        claims = decode_jwt(
            token=token,
            secret="test-secret-at-least-32-characters-long",
        )
        assert claims["sub"] == "user-12345"
        assert claims["email"] == "alice@example.com"
        assert claims["role"] == "authenticated"

    def test_expired_jwt_raises_error(self):
        token = create_jwt_token(
            user_id="user-12345",
            email="alice@example.com",
            secret="test-secret-at-least-32-characters-long",
            expires_in_seconds=-10,  # Expired 10 seconds ago
        )
        with pytest.raises(TokenExpiredAuthError):
            decode_jwt(
                token=token,
                secret="test-secret-at-least-32-characters-long",
            )

    def test_tampered_signature_raises_error(self):
        token = create_jwt_token(
            user_id="user-12345",
            email="alice@example.com",
            secret="test-secret-at-least-32-characters-long",
        )
        with pytest.raises(InvalidTokenAuthError):
            decode_jwt(
                token=token,
                secret="wrong-secret-key-that-does-not-match-at-all",
            )

    def test_wrong_audience_rejected(self):
        token = create_jwt_token(
            user_id="user-12345",
            email="alice@example.com",
            secret="test-secret-at-least-32-characters-long",
            audience="different_audience",
        )
        with pytest.raises(InvalidTokenAuthError):
            decode_jwt(
                token=token,
                secret="test-secret-at-least-32-characters-long",
                audience="authenticated",
            )

    def test_auth_provider_registration_and_login(self):
        provider = SupabaseAuthProvider()
        user_dict, token = provider.register_user(
            email="bob@example.com",
            password="SecurePassword123!",
            display_name="Bob Developer",
        )
        assert user_dict["email"] == "bob@example.com"
        assert token is not None

        # Verify duplicate registration is rejected
        with pytest.raises(UserAlreadyExistsError):
            provider.register_user(
                email="bob@example.com",
                password="AnotherPassword",
            )

        # Verify login with valid credentials
        login_user, login_token = provider.login_user(
            email="bob@example.com",
            password="SecurePassword123!",
        )
        assert login_user["id"] == user_dict["id"]
        assert login_token is not None

        # Verify login with invalid credentials fails
        with pytest.raises(InvalidCredentialsError):
            provider.login_user(
                email="bob@example.com",
                password="WrongPassword!",
            )

    def test_auth_provider_password_reset_flow(self):
        provider = SupabaseAuthProvider()
        user_dict, _ = provider.register_user(
            email="carol@example.com",
            password="InitialPassword123!",
        )

        reset_token = provider.request_password_reset("carol@example.com")
        assert reset_token is not None

        # Confirm reset
        success = provider.confirm_password_reset(reset_token, "NewPassword456!")
        assert success is True

        # Old password fails
        with pytest.raises(InvalidCredentialsError):
            provider.login_user("carol@example.com", "InitialPassword123!")

        # New password succeeds
        _, new_token = provider.login_user("carol@example.com", "NewPassword456!")
        assert new_token is not None

    def test_auto_synchronization_and_workspace_provisioning(self, test_db: Session):
        # Generate valid JWT for an external user
        token = create_jwt_token(
            user_id="user-auto-sync-999",
            email="sync_user@example.com",
            secret="demerivo-pixelfix-default-jwt-secret-for-dev-32chars",
            extra_claims={"user_metadata": {"display_name": "Synced User"}},
        )

        from fastapi.security import HTTPAuthorizationCredentials
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

        # Call get_current_user
        user = get_current_user(credentials=creds, db=test_db)
        assert user.id == "user-auto-sync-999"
        assert user.email == "sync_user@example.com"
        assert user.display_name == "Synced User"

        # Verify personal workspace was auto-provisioned
        tenant, role = get_current_tenant(x_tenant_id=None, current_user=user, db=test_db)
        assert tenant.owner_id == user.id
        assert tenant.is_personal is True
        assert role == TenantRole.OWNER

        # Verify wallet was auto-provisioned with free tier credits (5 credits matching Free plan)
        wallet = test_db.query(WalletModel).filter_by(tenant_id=tenant.id).first()
        assert wallet is not None
        assert wallet.balance == 5
