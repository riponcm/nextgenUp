"""Multi-tenancy service managing workspaces, memberships, and role permissions."""

import re
import uuid
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

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


class TenantError(Exception):
    """Base error for tenant operations."""
    pass


class TenantNotFoundError(TenantError):
    """Tenant workspace does not exist."""
    pass


class TenantPermissionDeniedError(TenantError):
    """User lacks required permissions within workspace."""
    pass


class MemberAlreadyExistsError(TenantError):
    """User is already a member of this workspace."""
    pass


def _slugify(text: str) -> str:
    """Generate clean URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    text = re.sub(r"^-+|-+$", "", text)
    return text or "workspace"


class TenantService:
    """Encapsulates workspace lifecycle and membership access controls."""

    def create_workspace(
        self,
        user_id: str,
        name: str,
        db: Session,
        is_personal: bool = False,
    ) -> TenantModel:
        """Create a new workspace, associate the owner, and provision an empty wallet."""
        base_slug = _slugify(name)
        slug = f"{base_slug}-{uuid.uuid4().hex[:6]}"

        tenant = TenantModel(
            name=name,
            slug=slug,
            is_personal=is_personal,
            owner_id=user_id,
        )
        db.add(tenant)
        db.flush()

        member = TenantMemberModel(
            tenant_id=tenant.id,
            user_id=user_id,
            role=TenantRole.OWNER,
        )
        db.add(member)

        # Allocate default wallet
        wallet = WalletModel(
            tenant_id=tenant.id,
            balance=0,
        )
        db.add(wallet)
        db.commit()
        db.refresh(tenant)
        return tenant

    def get_user_workspaces(
        self,
        user_id: str,
        db: Session,
    ) -> List[Tuple[TenantModel, TenantRole]]:
        """List all workspaces where the user has active membership."""
        rows = (
            db.query(TenantModel, TenantMemberModel.role)
            .join(TenantMemberModel, TenantModel.id == TenantMemberModel.tenant_id)
            .filter(TenantMemberModel.user_id == user_id)
            .order_by(TenantModel.created_at.asc())
            .all()
        )
        return [(t, role) for t, role in rows]

    def get_workspace_members(
        self,
        tenant_id: str,
        requester_user_id: str,
        db: Session,
    ) -> List[Tuple[TenantMemberModel, UserModel]]:
        """List members of a workspace if requester has access."""
        # Check membership
        membership = (
            db.query(TenantMemberModel)
            .filter_by(tenant_id=tenant_id, user_id=requester_user_id)
            .first()
        )
        if not membership:
            raise TenantPermissionDeniedError("Access denied: You are not a member of this workspace")

        members = (
            db.query(TenantMemberModel, UserModel)
            .join(UserModel, TenantMemberModel.user_id == UserModel.id)
            .filter(TenantMemberModel.tenant_id == tenant_id)
            .all()
        )
        return members

    def add_member(
        self,
        tenant_id: str,
        requester_user_id: str,
        email: str,
        role: TenantRole,
        db: Session,
    ) -> TenantMemberModel:
        """Invite/add a member to a workspace (Owner or Admin role required)."""
        requester_member = (
            db.query(TenantMemberModel)
            .filter_by(tenant_id=tenant_id, user_id=requester_user_id)
            .first()
        )
        if not requester_member or requester_member.role not in (TenantRole.OWNER, TenantRole.ADMIN):
            raise TenantPermissionDeniedError("Only workspace Owners or Admins can invite new members")

        if role == TenantRole.OWNER:
            raise TenantPermissionDeniedError("Cannot grant OWNER role via invite")

        normalized_email = email.strip().lower()
        target_user = db.query(UserModel).filter_by(email=normalized_email).first()
        if not target_user:
            # Create user stub if not yet signed up
            target_user = UserModel(
                id=str(uuid.uuid4()),
                email=normalized_email,
                display_name=normalized_email.split("@")[0],
                is_active=True,
                email_verified=False,
            )
            db.add(target_user)
            db.flush()

        existing = (
            db.query(TenantMemberModel)
            .filter_by(tenant_id=tenant_id, user_id=target_user.id)
            .first()
        )
        if existing:
            raise MemberAlreadyExistsError(f"User '{normalized_email}' is already a member of this workspace")

        new_member = TenantMemberModel(
            tenant_id=tenant_id,
            user_id=target_user.id,
            role=role,
        )
        db.add(new_member)
        db.commit()
        db.refresh(new_member)
        return new_member

    def remove_member(
        self,
        tenant_id: str,
        requester_user_id: str,
        target_user_id: str,
        db: Session,
    ) -> bool:
        """Remove a member from a workspace (cannot remove Owner)."""
        requester_member = (
            db.query(TenantMemberModel)
            .filter_by(tenant_id=tenant_id, user_id=requester_user_id)
            .first()
        )
        if not requester_member or requester_member.role not in (TenantRole.OWNER, TenantRole.ADMIN):
            raise TenantPermissionDeniedError("Only workspace Owners or Admins can remove members")

        target_member = (
            db.query(TenantMemberModel)
            .filter_by(tenant_id=tenant_id, user_id=target_user_id)
            .first()
        )
        if not target_member:
            raise TenantNotFoundError("Target member not found in this workspace")

        if target_member.role == TenantRole.OWNER:
            raise TenantPermissionDeniedError("The workspace Owner cannot be removed")

        db.delete(target_member)
        db.commit()
        return True


_tenant_service_instance: Optional[TenantService] = None


def get_tenant_service() -> TenantService:
    """Return singleton TenantService."""
    global _tenant_service_instance
    if _tenant_service_instance is None:
        _tenant_service_instance = TenantService()
    return _tenant_service_instance
