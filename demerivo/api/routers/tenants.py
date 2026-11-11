"""Multi-tenant workspace and membership management endpoints."""

from typing import List, Tuple
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from demerivo.auth.dependencies import get_current_user
from demerivo.db.models import TenantModel, TenantRole, UserModel
from demerivo.db.session import get_db
from demerivo.tenants.schemas import (
    MemberAddRequest,
    MemberResponse,
    TenantCreateRequest,
    TenantResponse,
)
from demerivo.tenants.service import (
    MemberAlreadyExistsError,
    TenantNotFoundError,
    TenantPermissionDeniedError,
    get_tenant_service,
)

router = APIRouter(prefix="/api/v1/tenants", tags=["Workspaces & Multi-Tenancy"])


@router.get(
    "",
    response_model=List[TenantResponse],
    summary="List all workspaces accessible by the current user",
)
def list_tenants(
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retrieve list of workspaces where the user is an owner, admin, or member."""
    tenant_service = get_tenant_service()
    workspaces = tenant_service.get_user_workspaces(current_user.id, db)
    return [
        TenantResponse(
            id=t.id,
            name=t.name,
            slug=t.slug,
            is_personal=t.is_personal,
            owner_id=t.owner_id,
            role=role.value,
            created_at=t.created_at.isoformat() if t.created_at else None,
        )
        for t, role in workspaces
    ]


@router.post(
    "",
    response_model=TenantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new team or enterprise workspace",
)
def create_tenant(
    payload: TenantCreateRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new workspace with current user as OWNER."""
    tenant_service = get_tenant_service()
    tenant = tenant_service.create_workspace(
        user_id=current_user.id,
        name=payload.name,
        db=db,
        is_personal=False,
    )
    return TenantResponse(
        id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        is_personal=tenant.is_personal,
        owner_id=tenant.owner_id,
        role=TenantRole.OWNER.value,
        created_at=tenant.created_at.isoformat() if tenant.created_at else None,
    )


@router.get(
    "/{tenant_id}",
    response_model=TenantResponse,
    summary="Retrieve workspace metadata",
)
def get_tenant(
    tenant_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get single workspace metadata, enforcing membership authorization."""
    tenant_service = get_tenant_service()
    workspaces = tenant_service.get_user_workspaces(current_user.id, db)
    matched = next((w for w in workspaces if w[0].id == tenant_id), None)
    if not matched:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to requested workspace",
        )
    tenant, role = matched
    return TenantResponse(
        id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        is_personal=tenant.is_personal,
        owner_id=tenant.owner_id,
        role=role.value,
        created_at=tenant.created_at.isoformat() if tenant.created_at else None,
    )


@router.get(
    "/{tenant_id}/members",
    response_model=List[MemberResponse],
    summary="List members in a workspace",
)
def list_members(
    tenant_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List members of a workspace if current user has access."""
    tenant_service = get_tenant_service()
    try:
        members = tenant_service.get_workspace_members(tenant_id, current_user.id, db)
    except TenantPermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

    return [
        MemberResponse(
            id=m.id,
            tenant_id=m.tenant_id,
            user_id=u.id,
            email=u.email,
            display_name=u.display_name,
            role=m.role.value,
            created_at=m.created_at.isoformat() if m.created_at else None,
        )
        for m, u in members
    ]


@router.post(
    "/{tenant_id}/members",
    response_model=MemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invite or add a member to a workspace",
)
def add_member(
    tenant_id: str,
    payload: MemberAddRequest,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add new member (requires Owner or Admin role in workspace)."""
    tenant_service = get_tenant_service()
    try:
        new_member = tenant_service.add_member(
            tenant_id=tenant_id,
            requester_user_id=current_user.id,
            email=payload.email,
            role=payload.role,
            db=db,
        )
    except TenantPermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except MemberAlreadyExistsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    target_user = db.query(UserModel).filter_by(id=new_member.user_id).first()
    return MemberResponse(
        id=new_member.id,
        tenant_id=new_member.tenant_id,
        user_id=target_user.id,
        email=target_user.email,
        display_name=target_user.display_name,
        role=new_member.role.value,
        created_at=new_member.created_at.isoformat() if new_member.created_at else None,
    )


@router.delete(
    "/{tenant_id}/members/{target_user_id}",
    summary="Remove a member from a workspace",
)
def remove_member(
    tenant_id: str,
    target_user_id: str,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove member (requires Owner or Admin role, cannot remove Owner)."""
    tenant_service = get_tenant_service()
    try:
        tenant_service.remove_member(
            tenant_id=tenant_id,
            requester_user_id=current_user.id,
            target_user_id=target_user_id,
            db=db,
        )
    except TenantPermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except TenantNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    return {"message": "Member removed successfully"}
