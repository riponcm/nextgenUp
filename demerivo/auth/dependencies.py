"""FastAPI authentication and multi-tenancy dependency injections."""

import uuid
from typing import List, Optional, Tuple

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from demerivo.auth.jwt import decode_jwt, InvalidTokenAuthError, TokenExpiredAuthError
from demerivo.config import get_settings
from demerivo.db.models import (
    CreditTransactionModel,
    CreditTransactionType,
    TenantMemberModel,
    TenantModel,
    TenantRole,
    UserModel,
    WalletModel,
)
from demerivo.db.session import get_db

security_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    db: Session = Depends(get_db),
) -> UserModel:
    """Validate Bearer JWT and return or auto-synchronize the authenticated UserModel."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    try:
        claims = decode_jwt(token)
    except TokenExpiredAuthError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except InvalidTokenAuthError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = claims.get("sub")
    email = claims.get("email")
    if not user_id or not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing required subject or email claims",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check if user already exists in local DB
    user = db.query(UserModel).filter_by(id=user_id).first()
    if not user:
        # Check by email to prevent duplicate email conflicts
        user = db.query(UserModel).filter_by(email=email).first()
        if user:
            # Re-key id if necessary or use existing user
            user_id = user.id
        else:
            # Synchronize new user and provision default personal workspace + wallet
            settings = get_settings()
            user = UserModel(
                id=user_id,
                email=email,
                display_name=claims.get("user_metadata", {}).get("display_name") or email.split("@")[0],
                is_active=True,
                email_verified=bool(claims.get("email_verified", False)),
            )
            db.add(user)
            db.flush()

            # Provision default personal tenant
            tenant = TenantModel(
                name=f"{user.display_name or 'Personal'} Workspace",
                slug=f"personal-{user_id[:8]}-{uuid.uuid4().hex[:4]}",
                is_personal=True,
                owner_id=user.id,
            )
            db.add(tenant)
            db.flush()

            # Assign Owner role
            member = TenantMemberModel(
                tenant_id=tenant.id,
                user_id=user.id,
                role=TenantRole.OWNER,
            )
            db.add(member)

            # Provision credit wallet
            wallet = WalletModel(
                tenant_id=tenant.id,
                balance=settings.free_tier_initial_credits,
            )
            db.add(wallet)
            db.flush()

            # Initial credit allocation audit record
            tx = CreditTransactionModel(
                tenant_id=tenant.id,
                wallet_id=wallet.id,
                user_id=user.id,
                transaction_type=CreditTransactionType.ALLOCATION,
                amount=settings.free_tier_initial_credits,
                balance_after=settings.free_tier_initial_credits,
                description="Free tier welcome bonus credits",
            )
            db.add(tx)
            db.commit()
            db.refresh(user)

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account has been deactivated",
        )

    return user


def get_current_tenant(
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Tuple[TenantModel, TenantRole]:
    """Resolve active tenant from X-Tenant-ID header or default to user's personal workspace.
    
    Guarantees strict tenant isolation by verifying user's membership.
    """
    if x_tenant_id:
        # User explicitly requested a tenant: verify membership
        membership = (
            db.query(TenantMemberModel)
            .filter_by(tenant_id=x_tenant_id, user_id=current_user.id)
            .first()
        )
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: You are not a member of the requested workspace",
            )
        tenant = db.query(TenantModel).filter_by(id=x_tenant_id).first()
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Requested workspace does not exist",
            )
        return tenant, membership.role

    # Default to user's personal workspace
    membership = (
        db.query(TenantMemberModel)
        .join(TenantModel, TenantMemberModel.tenant_id == TenantModel.id)
        .filter(TenantMemberModel.user_id == current_user.id, TenantModel.is_personal == True)
        .first()
    )
    if membership:
        tenant = db.query(TenantModel).filter_by(id=membership.tenant_id).first()
        return tenant, membership.role

    # Fallback to any workspace the user belongs to
    membership = (
        db.query(TenantMemberModel)
        .filter_by(user_id=current_user.id)
        .first()
    )
    if membership:
        tenant = db.query(TenantModel).filter_by(id=membership.tenant_id).first()
        return tenant, membership.role

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="No workspace found for this user account",
    )


def require_role(allowed_roles: List[TenantRole]):
    """Factory creating a dependency that enforces tenant role permissions."""
    def _role_checker(
        tenant_context: Tuple[TenantModel, TenantRole] = Depends(get_current_tenant),
    ) -> Tuple[TenantModel, TenantRole]:
        tenant, user_role = tenant_context
        if user_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation requires one of {[r.value for r in allowed_roles]} role, but user has '{user_role.value}'",
            )
        return tenant, user_role
    return _role_checker


class AdminPrincipal:
    """Represents an authenticated administrative actor (user or service)."""
    def __init__(self, actor_id: Optional[str], email: str, is_service: bool = False):
        self.actor_id = actor_id
        self.email = email
        self.is_service = is_service


def get_current_platform_admin(
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserModel:
    """Validate that the authenticated user possesses platform administrator privileges.
    
    Verifies the user's database `is_platform_admin` flag or configured admin emails.
    Requires account to be active and email verified for bootstrap elevation.
    Strictly denies ordinary tenant owners, tenant admins, and workspace members.
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform administrator access denied. User account is deactivated or suspended.",
        )

    settings = get_settings()
    admin_emails = {e.strip().lower() for e in settings.admin_emails.split(",") if e.strip()}
    is_bootstrap_email = current_user.email.lower() in admin_emails

    # H-1: Unverified email must never grant platform admin bootstrap elevation
    if is_bootstrap_email and not current_user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform administrator access denied. Configured administrative email must be verified before administrative privileges can be activated.",
        )

    is_admin = current_user.is_platform_admin or (is_bootstrap_email and current_user.email_verified)
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform administrator access denied. User does not possess platform admin privileges.",
        )

    if not current_user.is_platform_admin and is_bootstrap_email and current_user.email_verified:
        current_user.is_platform_admin = True
        db.commit()
        db.refresh(current_user)

    return current_user


def get_admin_principal(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Secret-Token"),
    db: Session = Depends(get_db),
) -> AdminPrincipal:
    """Authenticate platform administrator via Bearer JWT or internal service token.
    
    Permits machine-to-machine scripts via X-Admin-Secret-Token while allowing
    browser-based dashboard sessions via standard platform-admin Bearer JWTs.
    Strictly checks database account active status (C-1) and email verification (H-1).
    """
    settings = get_settings()

    # 1. Bearer JWT
    if credentials:
        token = credentials.credentials
        try:
            claims = decode_jwt(token)
            email = claims.get("email")
            user_id = claims.get("sub")
            if not user_id and not email:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token missing required subject or email claims",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            # Query fresh user record directly from database on every request
            user = None
            if user_id:
                user = db.query(UserModel).filter_by(id=user_id).first()
            if not user and email:
                user = db.query(UserModel).filter_by(email=email).first()

            if not user:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Administrative access denied. User record not found.",
                )

            # C-1: Strictly reject inactive/suspended accounts
            if not user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Administrative access denied. Administrator account is deactivated or suspended.",
                )

            admin_emails = {e.strip().lower() for e in settings.admin_emails.split(",") if e.strip()}
            is_bootstrap_email = user.email.lower() in admin_emails

            # H-1: Bootstrap administrator elevation strictly requires verified email
            if is_bootstrap_email and not user.email_verified:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Administrative access denied. Configured administrative email must be verified before administrative privileges can be activated.",
                )

            is_admin = user.is_platform_admin or (is_bootstrap_email and user.email_verified)
            if not is_admin:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Administrative access denied. Platform administrator credentials required.",
                )

            if not user.is_platform_admin and is_bootstrap_email and user.email_verified:
                user.is_platform_admin = True
                db.commit()
                db.refresh(user)

            return AdminPrincipal(actor_id=user.id, email=user.email, is_service=False)
        except (TokenExpiredAuthError, InvalidTokenAuthError):
            pass  # Fall through to check x_admin_token

    # 2. X-Admin-Secret-Token
    if x_admin_token and settings.admin_secret_token:
        import hmac
        if hmac.compare_digest(settings.admin_secret_token.encode("utf-8"), x_admin_token.encode("utf-8")):
            return AdminPrincipal(actor_id=None, email="service-token", is_service=True)

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Administrative access denied. Platform administrator credentials required.",
    )

