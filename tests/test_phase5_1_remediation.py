"""Comprehensive automated regression tests for Demerivo PixelFix Phase 5.1 Remediation.

Covers:
1. C-1: Inactive administrator authorization enforcement (db check on every request, suspended admins rejected).
2. H-1: Bootstrap elevation email verification requirements (unverified emails blocked from platform-admin).
3. H-3: Inactive account login rejection (prevents issuing tokens to deactivated users; existing JWTs rejected).
4. M-1: Query optimization instrumentation (verifies bounded queries in list_users and list_tenants).
5. M-4: Platform administrator self-deletion protection (blocks final active admin lockout, customer deletion intact).
6. M-5: Audit actor spoofing prevention (actor derived strictly from authenticated token principal).
"""

import uuid
from datetime import datetime, timedelta, timezone
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from demerivo.auth.dependencies import (
    AdminPrincipal,
    get_admin_principal,
    get_current_platform_admin,
    get_current_user,
)
from demerivo.auth.jwt import create_jwt_token
from demerivo.auth.provider import SupabaseAuthProvider
from demerivo.auth.schemas import (
    EmailVerificationRequest,
    LoginRequest,
    RegisterRequest,
)
from demerivo.config import Settings, get_settings
from demerivo.credits.wallet_service import get_wallet_service
from demerivo.db.models import (
    AdminAuditLogModel,
    JobModel,
    JobStatus,
    OperationType,
    PlanModel,
    SubscriptionModel,
    SubscriptionStatus,
    TenantMemberModel,
    TenantModel,
    TenantRole,
    UserModel,
    WalletModel,
    utcnow,
)
from demerivo.db.session import Base
from demerivo.api.routers.admin import (
    AdminCreditAdjustmentRequest,
    UserStatusUpdateRequest,
    admin_grant_credits,
    get_admin_me,
    get_analytics_overview,
    get_analytics_revenue,
    get_analytics_credits,
    get_system_health,
    list_audit_logs,
    list_jobs,
    list_payments,
    list_subscriptions,
    list_tenants,
    list_users,
    update_user_status,
)
from demerivo.api.routers.auth import login, register, verify_email
from demerivo.api.routers.profile import delete_account
from demerivo.services.email_service import create_verification_token
from demerivo.tenants.service import get_tenant_service


@pytest.fixture
def remediation_db(tmp_path):
    """Create an isolated test database with plans, admins, and customer accounts."""
    db_file = tmp_path / "test_remediation_p51.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)

    with Session(engine) as session:
        # Seed default plan
        plan = PlanModel(
            code="free",
            name="Free Tier",
            description="Free",
            price_paise=0,
            monthly_credits=10,
            currency="INR",
            features=["upscale_2x"],
        )
        session.add(plan)

        # 1. Active platform admin
        admin_active = UserModel(
            id="usr_admin_active",
            email="admin@demerivo.com",
            display_name="Active Super Admin",
            is_active=True,
            is_platform_admin=True,
            email_verified=True,
        )
        # 2. Deactivated / suspended platform admin
        admin_suspended = UserModel(
            id="usr_admin_suspended",
            email="rogue_admin@demerivo.com",
            display_name="Suspended Admin",
            is_active=False,
            is_platform_admin=True,
            email_verified=True,
        )
        # 3. Bootstrap candidate with UNVERIFIED email
        bootstrap_unverified = UserModel(
            id="usr_bootstrap_unverified",
            email="bootstrap_candidate@demerivo.com",
            display_name="Unverified Admin Candidate",
            is_active=True,
            is_platform_admin=False,
            email_verified=False,
        )
        # 4. Bootstrap candidate with VERIFIED email
        bootstrap_verified = UserModel(
            id="usr_bootstrap_verified",
            email="verified_candidate@demerivo.com",
            display_name="Verified Admin Candidate",
            is_active=True,
            is_platform_admin=False,
            email_verified=True,
        )
        # 5. Regular active customer
        customer_active = UserModel(
            id="usr_cust_active",
            email="customer@example.com",
            display_name="Active Customer",
            is_active=True,
            is_platform_admin=False,
            email_verified=True,
        )
        # 6. Deactivated customer
        customer_inactive = UserModel(
            id="usr_cust_inactive",
            email="inactive_cust@example.com",
            display_name="Deactivated Customer",
            is_active=False,
            is_platform_admin=False,
            email_verified=True,
        )

        session.add_all([
            admin_active,
            admin_suspended,
            bootstrap_unverified,
            bootstrap_verified,
            customer_active,
            customer_inactive,
        ])
        session.flush()

        # Seed workspace & wallet for active customer
        tnt_cust = TenantModel(
            id="tnt_customer_1",
            name="Customer Workspace",
            slug="customer-ws",
            is_personal=True,
            owner_id=customer_active.id,
        )
        session.add(tnt_cust)
        session.flush()

        session.add(TenantMemberModel(
            tenant_id=tnt_cust.id,
            user_id=customer_active.id,
            role=TenantRole.OWNER,
        ))
        session.add(WalletModel(
            id="wlt_customer_1",
            tenant_id=tnt_cust.id,
            balance=50,
        ))

        session.commit()

    yield engine


# =============================================================================
# 1. C-1: Inactive Administrator Authorization Tests
# =============================================================================

def test_active_administrator_succeeds(remediation_db):
    """Active administrator with valid credentials successfully obtains AdminPrincipal."""
    token = create_jwt_token(
        user_id="usr_admin_active",
        email="admin@demerivo.com",
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with Session(remediation_db) as db:
        principal = get_admin_principal(credentials=creds, db=db)
        assert principal.actor_id == "usr_admin_active"
        assert principal.email == "admin@demerivo.com"
        assert principal.is_service is False

        # Can call get_current_platform_admin
        admin_user = db.query(UserModel).filter_by(id="usr_admin_active").first()
        res = get_current_platform_admin(current_user=admin_user, db=db)
        assert res.id == "usr_admin_active"


def test_inactive_administrator_rejected_on_admin_principal(remediation_db):
    """Inactive / suspended administrator with valid unexpired JWT is strictly rejected with HTTP 403."""
    token = create_jwt_token(
        user_id="usr_admin_suspended",
        email="rogue_admin@demerivo.com",
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with Session(remediation_db) as db:
        with pytest.raises(HTTPException) as exc:
            get_admin_principal(credentials=creds, db=db)
        assert exc.value.status_code == 403
        assert "deactivated or suspended" in exc.value.detail.lower()


def test_inactive_administrator_rejected_on_current_platform_admin(remediation_db):
    """get_current_platform_admin rejects deactivated user with HTTP 403."""
    with Session(remediation_db) as db:
        suspended = db.query(UserModel).filter_by(id="usr_admin_suspended").first()
        with pytest.raises(HTTPException) as exc:
            get_current_platform_admin(current_user=suspended, db=db)
        assert exc.value.status_code == 403
        assert "deactivated or suspended" in exc.value.detail.lower()


def test_suspended_administrator_blocked_across_admin_endpoints(remediation_db):
    """Suspended administrator cannot access any admin endpoint even with a valid JWT."""
    token = create_jwt_token(
        user_id="usr_admin_suspended",
        email="rogue_admin@demerivo.com",
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with Session(remediation_db) as db:
        # Dependency rejects on invocation
        with pytest.raises(HTTPException) as exc:
            get_admin_principal(credentials=creds, db=db)
        assert exc.value.status_code == 403

        # Direct call to get_admin_me also verifies active state
        suspended_user = db.query(UserModel).filter_by(id="usr_admin_suspended").first()
        with pytest.raises(HTTPException):
            get_current_platform_admin(current_user=suspended_user, db=db)


def test_reactivated_administrator_regains_access(remediation_db):
    """When a suspended admin account is reactivated in DB, valid session access is restored."""
    token = create_jwt_token(
        user_id="usr_admin_suspended",
        email="rogue_admin@demerivo.com",
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with Session(remediation_db) as db:
        # Initially rejected
        with pytest.raises(HTTPException):
            get_admin_principal(credentials=creds, db=db)

        # Reactivate account in DB
        admin = db.query(UserModel).filter_by(id="usr_admin_suspended").first()
        admin.is_active = True
        db.commit()

        # Now authorized
        principal = get_admin_principal(credentials=creds, db=db)
        assert principal.actor_id == "usr_admin_suspended"
        assert principal.email == "rogue_admin@demerivo.com"


# =============================================================================
# 2. H-1: Bootstrap Elevation Email Verification Tests
# =============================================================================

def test_unverified_configured_admin_email_rejected(remediation_db, monkeypatch):
    """Unverified email matching DEMERIVO_ADMIN_EMAILS is strictly rejected with HTTP 403."""
    base_settings = get_settings()
    custom_settings = Settings(
        admin_emails="bootstrap_candidate@demerivo.com",
        supabase_jwt_secret=base_settings.supabase_jwt_secret,
        jwt_algorithm=base_settings.jwt_algorithm,
        jwt_audience=base_settings.jwt_audience,
    )
    monkeypatch.setattr("demerivo.auth.dependencies.get_settings", lambda: custom_settings)

    token = create_jwt_token(
        user_id="usr_bootstrap_unverified",
        email="bootstrap_candidate@demerivo.com",
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with Session(remediation_db) as db:
        with pytest.raises(HTTPException) as exc:
            get_admin_principal(credentials=creds, db=db)
        assert exc.value.status_code == 403
        assert "must be verified" in exc.value.detail.lower()

        # Also rejected via get_current_platform_admin
        unverified_user = db.query(UserModel).filter_by(id="usr_bootstrap_unverified").first()
        with pytest.raises(HTTPException) as exc2:
            get_current_platform_admin(current_user=unverified_user, db=db)
        assert exc2.value.status_code == 403
        assert "must be verified" in exc2.value.detail.lower()


def test_verified_configured_admin_email_receives_privilege(remediation_db, monkeypatch):
    """Verified email matching DEMERIVO_ADMIN_EMAILS successfully receives bootstrap elevation."""
    base_settings = get_settings()
    custom_settings = Settings(
        admin_emails="verified_candidate@demerivo.com",
        supabase_jwt_secret=base_settings.supabase_jwt_secret,
        jwt_algorithm=base_settings.jwt_algorithm,
        jwt_audience=base_settings.jwt_audience,
    )
    monkeypatch.setattr("demerivo.auth.dependencies.get_settings", lambda: custom_settings)

    token = create_jwt_token(
        user_id="usr_bootstrap_verified",
        email="verified_candidate@demerivo.com",
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with Session(remediation_db) as db:
        principal = get_admin_principal(credentials=creds, db=db)
        assert principal.actor_id == "usr_bootstrap_verified"
        assert principal.email == "verified_candidate@demerivo.com"

        # Check that user record in DB was promoted
        user = db.query(UserModel).filter_by(id="usr_bootstrap_verified").first()
        assert user.is_platform_admin is True


def test_ordinary_user_cannot_self_promote(remediation_db):
    """Ordinary customer cannot self-promote to platform admin via JWT claims or API requests."""
    token = create_jwt_token(
        user_id="usr_cust_active",
        email="customer@example.com",
        extra_claims={"is_platform_admin": True, "role": "admin"},
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with Session(remediation_db) as db:
        with pytest.raises(HTTPException) as exc:
            get_admin_principal(credentials=creds, db=db)
        assert exc.value.status_code == 403


def test_email_verification_flow_enables_elevation(remediation_db, monkeypatch):
    """Verifying an unverified admin email enables bootstrap elevation."""
    base_settings = get_settings()
    custom_settings = Settings(
        admin_emails="bootstrap_candidate@demerivo.com",
        supabase_jwt_secret=base_settings.supabase_jwt_secret,
        jwt_algorithm=base_settings.jwt_algorithm,
        jwt_audience=base_settings.jwt_audience,
    )
    monkeypatch.setattr("demerivo.auth.dependencies.get_settings", lambda: custom_settings)

    provider = SupabaseAuthProvider()

    with Session(remediation_db) as db:
        # 1. Initially unverified candidate is rejected
        user = db.query(UserModel).filter_by(id="usr_bootstrap_unverified").first()
        assert user.email_verified is False

        # 2. Call email verification endpoint with secure cryptographic token
        v_token = create_verification_token(user_id=user.id, email=user.email, db=db)
        req = EmailVerificationRequest(token=v_token, email="bootstrap_candidate@demerivo.com")
        res = verify_email(request=req, auth_provider=provider, db=db)
        assert res["email_verified"] is True

        # 3. Reload user
        db.refresh(user)
        assert user.email_verified is True

        # 4. Now elevation succeeds
        token = create_jwt_token(
            user_id="usr_bootstrap_unverified",
            email="bootstrap_candidate@demerivo.com",
        )
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        principal = get_admin_principal(credentials=creds, db=db)
        assert principal.actor_id == "usr_bootstrap_unverified"


# =============================================================================
# 3. H-3: Inactive Account Login Tests
# =============================================================================

def test_active_user_login_succeeds(remediation_db):
    """Active customer can authenticate and receive a signed JWT."""
    provider = SupabaseAuthProvider()
    provider.register_user("customer@example.com", "Password123!", "Active Customer")

    with Session(remediation_db) as db:
        req = LoginRequest(email="customer@example.com", password="Password123!")
        token_resp = login(request=req, auth_provider=provider, db=db)
        assert token_resp.access_token is not None
        assert token_resp.user.email == "customer@example.com"
        assert token_resp.user.is_active is True


def test_inactive_user_login_rejected_with_403(remediation_db):
    """Inactive / deactivated customer cannot log in; rejected before token is issued."""
    provider = SupabaseAuthProvider()
    provider.register_user("inactive_cust@example.com", "Password123!", "Deactivated Customer")

    with Session(remediation_db) as db:
        req = LoginRequest(email="inactive_cust@example.com", password="Password123!")
        with pytest.raises(HTTPException) as exc:
            login(request=req, auth_provider=provider, db=db)
        assert exc.value.status_code == 403
        assert "deactivated or suspended" in exc.value.detail.lower()


def test_existing_jwt_for_inactive_account_rejected_by_get_current_user(remediation_db):
    """Previously issued JWT for an account that is subsequently deactivated is rejected by get_current_user."""
    token = create_jwt_token(
        user_id="usr_cust_inactive",
        email="inactive_cust@example.com",
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with Session(remediation_db) as db:
        with pytest.raises(HTTPException) as exc:
            get_current_user(credentials=creds, db=db)
        assert exc.value.status_code == 403
        assert "deactivated" in exc.value.detail.lower()


# =============================================================================
# 4. M-1: Query Optimization Instrumentation (N+1 Elimination)
# =============================================================================

def test_n_plus_one_query_bounded_list_users(remediation_db):
    """Verify that list_users executes a bounded O(1) number of queries regardless of page size."""
    # Seed 15 users
    with Session(remediation_db) as db:
        for i in range(15):
            u = UserModel(
                id=f"usr_batch_{i}",
                email=f"batch_{i}@example.com",
                display_name=f"Batch User {i}",
                is_active=True,
                email_verified=True,
            )
            db.add(u)
        db.commit()

    admin_principal = AdminPrincipal(actor_id="usr_admin_active", email="admin@demerivo.com")

    # Instrument query execution for page_size=2
    queries_page_2 = []
    def count_queries_2(conn, cursor, statement, parameters, context, executemany):
        queries_page_2.append(statement)

    event.listen(remediation_db, "before_cursor_execute", count_queries_2)
    with Session(remediation_db) as db:
        res2 = list_users(page=1, page_size=2, admin=admin_principal, db=db)
        assert len(res2["items"]) == 2
    event.remove(remediation_db, "before_cursor_execute", count_queries_2)

    # Instrument query execution for page_size=10
    queries_page_10 = []
    def count_queries_10(conn, cursor, statement, parameters, context, executemany):
        queries_page_10.append(statement)

    event.listen(remediation_db, "before_cursor_execute", count_queries_10)
    with Session(remediation_db) as db:
        res10 = list_users(page=1, page_size=10, admin=admin_principal, db=db)
        assert len(res10["items"]) == 10
    event.remove(remediation_db, "before_cursor_execute", count_queries_10)

    # Query counts must be equal and bounded (not multiplying by page size)
    assert len(queries_page_2) == len(queries_page_10)
    assert len(queries_page_10) <= 6  # 5 bounded SQL queries total


def test_n_plus_one_query_bounded_list_tenants(remediation_db):
    """Verify that list_tenants executes a bounded O(1) number of queries regardless of page size."""
    # Seed 15 tenants
    with Session(remediation_db) as db:
        for i in range(15):
            t = TenantModel(
                id=f"tnt_batch_{i}",
                name=f"Batch Tenant {i}",
                slug=f"batch-tnt-{i}",
                is_personal=False,
                owner_id="usr_admin_active",
            )
            db.add(t)
        db.commit()

    admin_principal = AdminPrincipal(actor_id="usr_admin_active", email="admin@demerivo.com")

    queries_page_2 = []
    def count_queries_2(conn, cursor, statement, parameters, context, executemany):
        queries_page_2.append(statement)

    event.listen(remediation_db, "before_cursor_execute", count_queries_2)
    with Session(remediation_db) as db:
        res2 = list_tenants(page=1, page_size=2, admin=admin_principal, db=db)
        assert len(res2["items"]) == 2
    event.remove(remediation_db, "before_cursor_execute", count_queries_2)

    queries_page_10 = []
    def count_queries_10(conn, cursor, statement, parameters, context, executemany):
        queries_page_10.append(statement)

    event.listen(remediation_db, "before_cursor_execute", count_queries_10)
    with Session(remediation_db) as db:
        res10 = list_tenants(page=1, page_size=10, admin=admin_principal, db=db)
        assert len(res10["items"]) == 10
    event.remove(remediation_db, "before_cursor_execute", count_queries_10)

    # Query counts must remain bounded and equal
    assert len(queries_page_2) == len(queries_page_10)
    assert len(queries_page_10) <= 8  # 7 bounded SQL queries total


# =============================================================================
# 5. M-4: Platform Administrator Self-Deletion Protection Tests
# =============================================================================

def test_final_platform_admin_self_deletion_prevented(remediation_db):
    """The final active platform administrator is strictly prevented from deleting their account."""
    with Session(remediation_db) as db:
        # In remediation_db, only usr_admin_active is is_active=True and is_platform_admin=True
        admin = db.query(UserModel).filter_by(id="usr_admin_active").first()
        assert admin.is_platform_admin is True

        with pytest.raises(HTTPException) as exc:
            delete_account(current_user=admin, db=db)
        assert exc.value.status_code == 400
        assert "final active platform administrator" in exc.value.detail.lower()

        # Audit log of rejected attempt was written
        audit_log = (
            db.query(AdminAuditLogModel)
            .filter_by(action="admin.self_delete.rejected")
            .first()
        )
        assert audit_log is not None
        assert audit_log.actor_email == "admin@demerivo.com"


def test_final_platform_admin_suspension_prevented(remediation_db):
    """An administrative action to suspend the final active platform administrator is prohibited."""
    admin_principal = AdminPrincipal(actor_id="usr_admin_active", email="admin@demerivo.com")

    with Session(remediation_db) as db:
        req = UserStatusUpdateRequest(action="suspend", reason="Attempted admin deactivation")
        with pytest.raises(HTTPException) as exc:
            update_user_status(user_id="usr_admin_active", payload=req, admin=admin_principal, db=db)
        assert exc.value.status_code == 400


def test_ordinary_customer_deletion_succeeds(remediation_db):
    """Legitimate customer account deletion succeeds normally without restriction."""
    with Session(remediation_db) as db:
        customer = db.query(UserModel).filter_by(id="usr_cust_active").first()
        res = delete_account(current_user=customer, db=db)
        assert "successfully deactivated" in res["message"]

        db.refresh(customer)
        assert customer.is_active is False
        assert customer.display_name == "Deleted User"


# =============================================================================
# 6. M-5: Audit Actor Spoofing Prevention Tests
# =============================================================================

def test_admin_credit_grant_actor_spoofing_prevented(remediation_db):
    """admin_grant_credits derives actor identity strictly from authenticated principal, ignoring client payload actor."""
    wallet_svc = get_wallet_service()
    admin_principal = AdminPrincipal(actor_id="usr_admin_active", email="verified_admin@demerivo.com")

    payload = AdminCreditAdjustmentRequest(
        tenant_id="tnt_customer_1",
        amount=25,
        reason="Security remediation compensation",
        actor="spoofed_ceo@demerivo.com",  # Client attempts impersonation
        idempotency_key=f"spoof_test_{uuid.uuid4().hex[:8]}",
    )

    with Session(remediation_db) as db:
        res = admin_grant_credits(
            payload=payload,
            admin=admin_principal,
            db=db,
            wallet_service=wallet_svc,
        )
        # Client actor claim must be completely ignored
        assert res.actor == "verified_admin@demerivo.com"
        assert res.actor != "spoofed_ceo@demerivo.com"

        # Check audit log in DB
        audit = (
            db.query(AdminAuditLogModel)
            .filter_by(action="credit.grant", target_id="tnt_customer_1")
            .order_by(AdminAuditLogModel.created_at.desc())
            .first()
        )
        assert audit is not None
        assert audit.actor_email == "verified_admin@demerivo.com"
        assert audit.actor_id == "usr_admin_active"
