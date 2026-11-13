"""Automated tests for multi-tenancy, workspace management, roles, and tenant isolation."""

import pytest
from sqlalchemy.orm import Session

from demerivo.db.models import TenantModel, TenantRole, UserModel
from demerivo.db.session import Base, create_db_engine
from demerivo.tenants.service import (
    MemberAlreadyExistsError,
    TenantPermissionDeniedError,
    TenantService,
)


@pytest.fixture
def test_db():
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def sample_users(test_db: Session):
    user_a = UserModel(id="user-a", email="alice@corp.com", display_name="Alice", is_active=True)
    user_b = UserModel(id="user-b", email="bob@corp.com", display_name="Bob", is_active=True)
    user_c = UserModel(id="user-c", email="carol@corp.com", display_name="Carol", is_active=True)
    test_db.add_all([user_a, user_b, user_c])
    test_db.commit()
    return user_a, user_b, user_c


class TestMultiTenancyAndWorkspaces:
    """Test workspace creation, membership roles, and isolation."""

    def test_create_workspace_and_owner_assignment(self, test_db: Session, sample_users):
        user_a, _, _ = sample_users
        service = TenantService()

        tenant = service.create_workspace(
            user_id=user_a.id,
            name="Alice Studio",
            db=test_db,
            is_personal=True,
        )
        assert tenant.id is not None
        assert tenant.owner_id == user_a.id
        assert tenant.name == "Alice Studio"

        workspaces = service.get_user_workspaces(user_a.id, test_db)
        assert len(workspaces) == 1
        t, role = workspaces[0]
        assert t.id == tenant.id
        assert role == TenantRole.OWNER

    def test_owner_can_add_admin_and_member(self, test_db: Session, sample_users):
        user_a, user_b, user_c = sample_users
        service = TenantService()

        tenant = service.create_workspace(user_id=user_a.id, name="Agency", db=test_db)

        # Owner adds Bob as Admin
        m1 = service.add_member(
            tenant_id=tenant.id,
            requester_user_id=user_a.id,
            email=user_b.email,
            role=TenantRole.ADMIN,
            db=test_db,
        )
        assert m1.role == TenantRole.ADMIN
        assert m1.user_id == user_b.id

        # Admin adds Carol as Member
        m2 = service.add_member(
            tenant_id=tenant.id,
            requester_user_id=user_b.id,
            email=user_c.email,
            role=TenantRole.MEMBER,
            db=test_db,
        )
        assert m2.role == TenantRole.MEMBER
        assert m2.user_id == user_c.id

        members = service.get_workspace_members(tenant.id, user_a.id, test_db)
        assert len(members) == 3

    def test_member_cannot_add_other_members(self, test_db: Session, sample_users):
        user_a, user_b, user_c = sample_users
        service = TenantService()
        tenant = service.create_workspace(user_id=user_a.id, name="Design Team", db=test_db)

        service.add_member(
            tenant_id=tenant.id,
            requester_user_id=user_a.id,
            email=user_c.email,
            role=TenantRole.MEMBER,
            db=test_db,
        )

        # Member Carol tries to add Bob -> Forbidden
        with pytest.raises(TenantPermissionDeniedError):
            service.add_member(
                tenant_id=tenant.id,
                requester_user_id=user_c.id,
                email=user_b.email,
                role=TenantRole.MEMBER,
                db=test_db,
            )

    def test_duplicate_membership_rejected(self, test_db: Session, sample_users):
        user_a, user_b, _ = sample_users
        service = TenantService()
        tenant = service.create_workspace(user_id=user_a.id, name="HQ", db=test_db)

        service.add_member(
            tenant_id=tenant.id,
            requester_user_id=user_a.id,
            email=user_b.email,
            role=TenantRole.MEMBER,
            db=test_db,
        )

        with pytest.raises(MemberAlreadyExistsError):
            service.add_member(
                tenant_id=tenant.id,
                requester_user_id=user_a.id,
                email=user_b.email,
                role=TenantRole.MEMBER,
                db=test_db,
            )

    def test_cannot_remove_workspace_owner(self, test_db: Session, sample_users):
        user_a, user_b, _ = sample_users
        service = TenantService()
        tenant = service.create_workspace(user_id=user_a.id, name="Firm", db=test_db)

        service.add_member(
            tenant_id=tenant.id,
            requester_user_id=user_a.id,
            email=user_b.email,
            role=TenantRole.ADMIN,
            db=test_db,
        )

        # Admin tries to remove Owner -> Forbidden
        with pytest.raises(TenantPermissionDeniedError):
            service.remove_member(
                tenant_id=tenant.id,
                requester_user_id=user_b.id,
                target_user_id=user_a.id,
                db=test_db,
            )

    def test_cross_tenant_isolation(self, test_db: Session, sample_users):
        user_a, user_b, _ = sample_users
        service = TenantService()

        tenant_a = service.create_workspace(user_id=user_a.id, name="Alice Workspace", db=test_db)
        tenant_b = service.create_workspace(user_id=user_b.id, name="Bob Workspace", db=test_db)

        # Bob cannot list members of Alice's workspace
        with pytest.raises(TenantPermissionDeniedError):
            service.get_workspace_members(
                tenant_id=tenant_a.id,
                requester_user_id=user_b.id,
                db=test_db,
            )

        # Alice cannot list members of Bob's workspace
        with pytest.raises(TenantPermissionDeniedError):
            service.get_workspace_members(
                tenant_id=tenant_b.id,
                requester_user_id=user_a.id,
                db=test_db,
            )
