"""User profile management endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from demerivo.auth.dependencies import get_current_user
from demerivo.db.models import UserModel
from demerivo.db.session import get_db
from demerivo.services.admin_audit_service import record_audit_log
from demerivo.users.schemas import ProfileResponse, ProfileUpdateRequest
from demerivo.users.service import get_user_service

router = APIRouter(prefix="/api/v1/profile", tags=["User Profile"])


@router.get(
    "",
    response_model=ProfileResponse,
    summary="Retrieve current user profile",
)
def get_profile(
    current_user: UserModel = Depends(get_current_user),
):
    """Return profile details of the authenticated user."""
    return ProfileResponse(
        id=current_user.id,
        email=current_user.email,
        display_name=current_user.display_name,
        avatar_url=current_user.avatar_url,
        is_active=current_user.is_active,
        email_verified=current_user.email_verified,
        created_at=current_user.created_at.isoformat() if current_user.created_at else None,
        updated_at=current_user.updated_at.isoformat() if current_user.updated_at else None,
    )


@router.patch(
    "",
    response_model=ProfileResponse,
    summary="Update current user display name or avatar URL",
)
def update_profile(
    payload: ProfileUpdateRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update profile attributes."""
    user_service = get_user_service()
    updated = user_service.update_profile(
        user_id=current_user.id,
        display_name=payload.display_name,
        avatar_url=payload.avatar_url,
        db=db,
    )
    return ProfileResponse(
        id=updated.id,
        email=updated.email,
        display_name=updated.display_name,
        avatar_url=updated.avatar_url,
        is_active=updated.is_active,
        email_verified=updated.email_verified,
        created_at=updated.created_at.isoformat() if updated.created_at else None,
        updated_at=updated.updated_at.isoformat() if updated.updated_at else None,
    )


@router.delete(
    "",
    summary="Deactivate and delete user account",
)
def delete_account(
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Execute customer account deactivation and anonymization workflow."""
    # M-4: Prevent final active platform administrator from deleting their own account
    if current_user.is_platform_admin:
        active_admins_count = (
            db.query(UserModel)
            .filter(UserModel.is_platform_admin == True, UserModel.is_active == True)
            .count()
        )
        if active_admins_count <= 1:
            record_audit_log(
                db=db,
                actor_email=current_user.email,
                actor_id=current_user.id,
                action="admin.self_delete.rejected",
                target_type="user",
                target_id=current_user.id,
                reason="Attempted deletion of the final active platform administrator account",
                status="failure",
                metadata={
                    "user_email": current_user.email,
                    "active_admins_count": active_admins_count,
                },
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Operation prohibited: You are the final active platform administrator. To delete your account or resign, promote and designate another active platform administrator first.",
            )

    user_service = get_user_service()
    user_service.delete_account(current_user.id, db)
    return {"message": "Account successfully deactivated and scheduled for deletion"}
