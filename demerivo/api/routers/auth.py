"""Authentication and session management endpoints."""

from typing import List, Tuple
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from demerivo.auth.dependencies import get_current_tenant, get_current_user
from demerivo.auth.provider import (
    AuthProvider,
    InvalidCredentialsError,
    UserAlreadyExistsError,
    get_auth_provider,
)
from demerivo.auth.schemas import (
    AuthMeResponse,
    LoginRequest,
    EmailVerificationRequest,
    ResendVerificationRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    RegisterRequest,
    TenantRoleInfo,
    TokenResponse,
    UserProfileResponse,
)
from demerivo.credits.wallet_service import get_wallet_service
from demerivo.db.models import TenantModel, TenantRole, UserModel
from demerivo.db.session import get_db
from demerivo.security.rate_limiter import rate_limit_auth
from demerivo.services.email_service import (
    InvalidVerificationTokenError,
    create_verification_token,
    get_email_provider,
    verify_email_with_token,
)
from demerivo.tenants.service import get_tenant_service

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication & Identity"])


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_auth)],
    summary="Register a new customer account",
)
def register(
    request: RegisterRequest,
    auth_provider: AuthProvider = Depends(get_auth_provider),
    db: Session = Depends(get_db),
):
    """Register customer, automatically provision their personal workspace, and seed free credits."""
    try:
        user_dict, token = auth_provider.register_user(
            email=request.email,
            password=request.password,
            display_name=request.display_name,
        )
    except UserAlreadyExistsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Synchronize user entity in local database and provision workspace + wallet
    user = db.query(UserModel).filter_by(id=user_dict["id"]).first()
    if not user:
        user = UserModel(
            id=user_dict["id"],
            email=user_dict["email"],
            display_name=user_dict.get("display_name"),
            is_active=True,
            email_verified=bool(user_dict.get("email_verified", False)),
        )
        db.add(user)
        db.flush()

        # Provision personal workspace and wallet with welcome credits
        tenant_service = get_tenant_service()
        tenant = tenant_service.create_workspace(
            user_id=user.id,
            name=f"{user.display_name or 'Personal'} Workspace",
            db=db,
            is_personal=True,
        )

        wallet_service = get_wallet_service()
        wallet_service.allocate_credits(
            tenant_id=tenant.id,
            amount=20,
            description="Welcome bonus credits",
            db=db,
            user_id=user.id,
        )

        # Generate initial single-use cryptographic verification token and send verification email
        try:
            v_token = create_verification_token(user_id=user.id, email=user.email, db=db)
            get_email_provider().send_verification_email(
                to_email=user.email,
                token=v_token,
                user_name=user.display_name,
            )
        except Exception:
            pass

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=86400,
        user=UserProfileResponse(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            avatar_url=user.avatar_url,
            is_active=user.is_active,
            email_verified=user.email_verified,
            created_at=user.created_at.isoformat() if user.created_at else None,
        ),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(rate_limit_auth)],
    summary="Authenticate user credentials and obtain access token",
)
def login(
    request: LoginRequest,
    auth_provider: AuthProvider = Depends(get_auth_provider),
    db: Session = Depends(get_db),
):
    """Verify credentials and issue signed JWT access token."""
    try:
        user_dict, token = auth_provider.login_user(
            email=request.email,
            password=request.password,
        )
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    user = db.query(UserModel).filter_by(id=user_dict["id"]).first()
    if not user:
        user = db.query(UserModel).filter_by(email=request.email.strip().lower()).first()

    # H-3: Check is_active before issuing access tokens to prevent inactive users from obtaining authenticated sessions
    if user and not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated or suspended. Please contact customer support.",
        )

    if not user:
        # User signed up via external provider; auto-sync
        user = UserModel(
            id=user_dict["id"],
            email=user_dict["email"],
            display_name=user_dict.get("display_name"),
            is_active=True,
            email_verified=bool(user_dict.get("email_verified", False)),
        )
        db.add(user)
        db.flush()
        tenant_service = get_tenant_service()
        tenant = tenant_service.create_workspace(
            user_id=user.id,
            name=f"{user.display_name or 'Personal'} Workspace",
            db=db,
            is_personal=True,
        )
        wallet_service = get_wallet_service()
        wallet_service.allocate_credits(
            tenant_id=tenant.id,
            amount=20,
            description="Welcome bonus credits",
            db=db,
            user_id=user.id,
        )

        # Generate initial single-use cryptographic verification token and send verification email
        try:
            v_token = create_verification_token(user_id=user.id, email=user.email, db=db)
            get_email_provider().send_verification_email(
                to_email=user.email,
                token=v_token,
                user_name=user.display_name,
            )
        except Exception:
            pass

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=86400,
        user=UserProfileResponse(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            avatar_url=user.avatar_url,
            is_active=user.is_active,
            email_verified=user.email_verified,
            created_at=user.created_at.isoformat() if user.created_at else None,
        ),
    )


@router.get(
    "/me",
    response_model=AuthMeResponse,
    summary="Retrieve current session identity, active workspace, and credit balance",
)
def get_me(
    current_user: UserModel = Depends(get_current_user),
    tenant_context: Tuple[TenantModel, TenantRole] = Depends(get_current_tenant),
    db: Session = Depends(get_db),
):
    """Return user profile, active tenant, all tenant memberships, and active wallet balance."""
    active_tenant, active_role = tenant_context
    tenant_service = get_tenant_service()
    workspaces = tenant_service.get_user_workspaces(current_user.id, db)

    wallet_service = get_wallet_service()
    wallet = wallet_service.get_wallet(active_tenant.id, db)

    tenant_list = [
        TenantRoleInfo(
            id=t.id,
            name=t.name,
            slug=t.slug,
            is_personal=t.is_personal,
            role=role.value,
        )
        for t, role in workspaces
    ]

    return AuthMeResponse(
        user=UserProfileResponse(
            id=current_user.id,
            email=current_user.email,
            display_name=current_user.display_name,
            avatar_url=current_user.avatar_url,
            is_active=current_user.is_active,
            email_verified=current_user.email_verified,
            created_at=current_user.created_at.isoformat() if current_user.created_at else None,
        ),
        active_tenant=TenantRoleInfo(
            id=active_tenant.id,
            name=active_tenant.name,
            slug=active_tenant.slug,
            is_personal=active_tenant.is_personal,
            role=active_role.value,
        ),
        tenants=tenant_list,
        wallet_balance=wallet.balance,
    )


@router.post(
    "/password-reset",
    dependencies=[Depends(rate_limit_auth)],
    summary="Request a password reset token",
)
def request_password_reset(
    request: PasswordResetRequest,
    auth_provider: AuthProvider = Depends(get_auth_provider),
):
    """Trigger password reset email flow."""
    token = auth_provider.request_password_reset(request.email)
    return {"message": "Password reset instructions sent", "reset_token": token}


@router.post(
    "/password-reset/confirm",
    dependencies=[Depends(rate_limit_auth)],
    summary="Confirm password reset with received token",
)
def confirm_password_reset(
    request: PasswordResetConfirmRequest,
    auth_provider: AuthProvider = Depends(get_auth_provider),
):
    """Set new password using reset token."""
    success = auth_provider.confirm_password_reset(request.token, request.new_password)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )
    return {"message": "Password updated successfully"}


@router.post(
    "/verify-email",
    dependencies=[Depends(rate_limit_auth)],
    summary="Verify user email address using cryptographic token",
)
def verify_email(
    request: EmailVerificationRequest,
    auth_provider: AuthProvider = Depends(get_auth_provider),
    db: Session = Depends(get_db),
):
    """Confirm user email address and activate verified account status using single-use cryptographic token."""
    try:
        user = verify_email_with_token(raw_token=request.token, email=request.email, db=db)
    except InvalidVerificationTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    auth_provider.verify_email(user.email)
    return {
        "message": "Email address verified successfully",
        "email": user.email,
        "email_verified": True,
    }


@router.post(
    "/resend-verification",
    dependencies=[Depends(rate_limit_auth)],
    summary="Resend account verification email",
)
def resend_verification(
    request: ResendVerificationRequest,
    db: Session = Depends(get_db),
):
    """Dispatch a fresh verification link if account exists and is not yet verified."""
    norm_email = request.email.strip().lower()
    user = db.query(UserModel).filter_by(email=norm_email).first()
    if user and not user.email_verified:
        token_str = create_verification_token(user.id, user.email, db)
        email_provider = get_email_provider()
        email_provider.send_verification_email(user.email, token_str, user.display_name)

    return {
        "message": "If an unverified account exists for this email, a verification link has been dispatched.",
    }
