"""Comprehensive automated tests for Demerivo PixelFix Phase 5 Admin Dashboard & Analytics.

Tests:
1. Platform-admin authentication & role enforcement.
2. Ordinary customer access denial (HTTP 403).
3. Workspace-admin access denial (HTTP 403).
4. Machine-to-machine service token authorization.
5. Overview KPI metrics SQL aggregation & date-filtering bounds.
6. Revenue & payment analytics (gross, refunds, net, MRR calculation).
7. Credit analytics & disclosure of single-wallet architecture.
8. Customer search, status filtering, and pagination.
9. User suspension and restoration with mandatory audit trail.
10. Tenant management, member inspection, and isolation.
11. Job monitoring, duration telemetry, and filter logic.
12. Subscription tracking and administrative tier modification.
13. Payment ledger history search and pagination.
14. System health non-destructive probes & offline service reporting.
15. Audit log access control and credential redaction.
"""

import uuid
from datetime import datetime, timedelta, timezone
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from demerivo.auth.dependencies import (
    AdminPrincipal,
    get_admin_principal,
    get_current_platform_admin,
)
from demerivo.config import Settings
from demerivo.db.models import (
    AdminAuditLogModel,
    CreditTransactionModel,
    CreditTransactionType,
    JobModel,
    JobStatus,
    OperationType,
    PaymentModel,
    PaymentStatus,
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
    get_admin_me,
    get_analytics_overview,
    get_analytics_revenue,
    get_analytics_credits,
    list_users,
    inspect_user,
    update_user_status,
    list_tenants,
    inspect_tenant,
    list_jobs,
    inspect_job,
    list_subscriptions,
    modify_subscription,
    list_payments,
    list_audit_logs,
    get_system_health,
    UserStatusUpdateRequest,
    SubscriptionModifyRequest,
)
from demerivo.services.admin_analytics_service import AdminAnalyticsService
from demerivo.services.admin_audit_service import record_audit_log, sanitize_audit_metadata


@pytest.fixture
def admin_db_session(tmp_path):
    """Create an isolated SQLite database session with seed plans and admin data."""
    db_file = tmp_path / "test_admin.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)

    with Session(engine) as session:
        # Seed default plans
        free_plan = PlanModel(
            code="free",
            name="Free Explorer",
            description="Trial tier",
            price_paise=0,
            monthly_credits=5,
            currency="INR",
            features=["upscale_2x", "enhance"],
        )
        starter_plan = PlanModel(
            code="starter",
            name="Starter Creator",
            description="Casual creators",
            price_paise=19900,
            monthly_credits=40,
            currency="INR",
            features=["upscale_2x", "upscale_4x", "enhance", "bg_remove"],
        )
        pro_plan = PlanModel(
            code="pro",
            name="Pro Studio",
            description="Professional creators",
            price_paise=49900,
            monthly_credits=150,
            currency="INR",
            features=["upscale_4x", "enhance", "bg_remove", "colorize", "inpaint"],
        )
        studio_plan = PlanModel(
            code="studio",
            name="Studio Agency",
            description="High-volume agency",
            price_paise=99900,
            monthly_credits=400,
            currency="INR",
            features=["upscale_4x", "enhance", "bg_remove", "colorize", "inpaint"],
        )
        session.add_all([free_plan, starter_plan, pro_plan, studio_plan])

        # Create Platform Admin User
        admin_user = UserModel(
            id="usr_admin_1",
            email="admin@demerivo.com",
            display_name="System Administrator",
            is_active=True,
            is_platform_admin=True,
            email_verified=True,
        )
        # Create Regular Customer 1 (Workspace Owner)
        cust_alice = UserModel(
            id="usr_alice_1",
            email="alice@example.com",
            display_name="Alice User",
            is_active=True,
            is_platform_admin=False,
            email_verified=True,
        )
        # Create Regular Customer 2 (Workspace Admin)
        cust_bob = UserModel(
            id="usr_bob_1",
            email="bob@example.com",
            display_name="Bob User",
            is_active=True,
            is_platform_admin=False,
            email_verified=True,
        )
        session.add_all([admin_user, cust_alice, cust_bob])
        session.flush()

        # Workspace for Alice
        alice_tenant = TenantModel(
            id="tnt_alice_1",
            name="Alice Studio",
            slug="alice-studio",
            is_personal=True,
            owner_id=cust_alice.id,
        )
        session.add(alice_tenant)
        session.flush()

        # Membership: Alice is Owner, Bob is Workspace Admin
        session.add(TenantMemberModel(tenant_id=alice_tenant.id, user_id=cust_alice.id, role=TenantRole.OWNER))
        session.add(TenantMemberModel(tenant_id=alice_tenant.id, user_id=cust_bob.id, role=TenantRole.ADMIN))

        # Wallet for Alice's tenant
        alice_wallet = WalletModel(
            id="wlt_alice_1",
            tenant_id=alice_tenant.id,
            balance=120,
        )
        session.add(alice_wallet)
        session.flush()

        # Subscription for Alice's tenant: Pro
        alice_sub = SubscriptionModel(
            id="sub_alice_1",
            tenant_id=alice_tenant.id,
            plan_id=pro_plan.id,
            plan_code="pro",
            status=SubscriptionStatus.ACTIVE,
            provider="mock",
            current_period_start=utcnow() - timedelta(days=10),
            current_period_end=utcnow() + timedelta(days=20),
        )
        session.add(alice_sub)

        # Credit Transactions
        session.add(CreditTransactionModel(
            tenant_id=alice_tenant.id,
            wallet_id=alice_wallet.id,
            user_id=cust_alice.id,
            transaction_type=CreditTransactionType.ALLOCATION,
            amount=150,
            balance_after=150,
            description="Pro plan subscription renewal",
        ))
        session.add(CreditTransactionModel(
            tenant_id=alice_tenant.id,
            wallet_id=alice_wallet.id,
            user_id=cust_alice.id,
            transaction_type=CreditTransactionType.CONSUMPTION,
            amount=30,
            balance_after=120,
            description="Upscale jobs batch",
        ))

        # Jobs for Alice
        job1 = JobModel(
            id="job_alice_1",
            tenant_id=alice_tenant.id,
            user_id=cust_alice.id,
            status=JobStatus.COMPLETED,
            operation_type=OperationType.UPSCALE_4X,
            input_key="inputs/img1.jpg",
            output_key="outputs/img1_out.png",
            credit_cost=4,
            created_at=utcnow() - timedelta(hours=2),
            completed_at=utcnow() - timedelta(hours=2) + timedelta(seconds=12),
        )
        job2 = JobModel(
            id="job_alice_2",
            tenant_id=alice_tenant.id,
            user_id=cust_alice.id,
            status=JobStatus.FAILED,
            operation_type=OperationType.BG_REMOVE,
            input_key="inputs/img2.jpg",
            error_message="Image corrupted header signature",
            credit_cost=1,
            created_at=utcnow() - timedelta(hours=1),
            completed_at=utcnow() - timedelta(hours=1) + timedelta(seconds=4),
        )
        job3 = JobModel(
            id="job_alice_3",
            tenant_id=alice_tenant.id,
            user_id=cust_alice.id,
            status=JobStatus.PROCESSING,
            operation_type=OperationType.ENHANCE,
            input_key="inputs/img3.jpg",
            credit_cost=1,
            created_at=utcnow() - timedelta(minutes=5),
        )
        session.add_all([job1, job2, job3])

        # Payments for Alice
        pay1 = PaymentModel(
            id="pay_alice_1",
            tenant_id=alice_tenant.id,
            subscription_id=alice_sub.id,
            provider="mock",
            provider_payment_id="pay_mock_111",
            provider_order_id="order_mock_111",
            amount_paise=49900,  # ₹499
            currency="INR",
            status=PaymentStatus.CAPTURED,
        )
        pay2 = PaymentModel(
            id="pay_alice_2",
            tenant_id=alice_tenant.id,
            provider="mock",
            provider_payment_id="pay_mock_222",
            amount_paise=19900,  # ₹199
            currency="INR",
            status=PaymentStatus.REFUNDED,
        )
        session.add_all([pay1, pay2])

        session.commit()

    yield engine


# =============================================================================
# 1. Platform-Admin Authentication & Role Enforcement Tests
# =============================================================================

def test_platform_admin_authorization_success(admin_db_session):
    """Verify that a user with is_platform_admin=True is authorized."""
    with Session(admin_db_session) as db:
        admin_user = db.query(UserModel).filter_by(id="usr_admin_1").first()
        res = get_current_platform_admin(current_user=admin_user, db=db)
        assert res.id == "usr_admin_1"
        assert res.is_platform_admin is True

        profile = get_admin_me(admin_user=res)
        assert profile["email"] == "admin@demerivo.com"
        assert profile["is_platform_admin"] is True


def test_ordinary_customer_denied_platform_admin(admin_db_session):
    """Verify that an ordinary customer without platform admin privileges is denied with HTTP 403."""
    with Session(admin_db_session) as db:
        alice = db.query(UserModel).filter_by(id="usr_alice_1").first()
        with pytest.raises(HTTPException) as exc_info:
            get_current_platform_admin(current_user=alice, db=db)
        assert exc_info.value.status_code == 403
        assert "Platform administrator access denied" in exc_info.value.detail


def test_workspace_admin_denied_platform_admin(admin_db_session):
    """Verify that a tenant workspace admin (Bob) is strictly denied platform admin access."""
    with Session(admin_db_session) as db:
        bob = db.query(UserModel).filter_by(id="usr_bob_1").first()
        # Verify Bob is a Tenant ADMIN
        membership = db.query(TenantMemberModel).filter_by(user_id=bob.id).first()
        assert membership.role == TenantRole.ADMIN

        # Verify Bob cannot access platform admin dependency
        with pytest.raises(HTTPException) as exc_info:
            get_current_platform_admin(current_user=bob, db=db)
        assert exc_info.value.status_code == 403
        assert "Platform administrator access denied" in exc_info.value.detail


def test_admin_principal_via_service_token(admin_db_session, monkeypatch):
    """Verify machine-to-machine service token authorization."""
    settings = Settings(admin_secret_token="valid-secret-999")
    monkeypatch.setattr("demerivo.auth.dependencies.get_settings", lambda: settings)

    with Session(admin_db_session) as db:
        principal = get_admin_principal(
            credentials=None,
            x_admin_token="valid-secret-999",
            db=db,
        )
        assert principal.is_service is True
        assert principal.email == "service-token"


# =============================================================================
# 2. Business Analytics & KPI Overviews Tests
# =============================================================================

def test_admin_overview_metrics_aggregation(admin_db_session):
    """Verify SQL aggregate computations for dashboard overview metrics."""
    with Session(admin_db_session) as db:
        admin_actor = AdminPrincipal(actor_id="usr_admin_1", email="admin@demerivo.com")
        metrics = get_analytics_overview(range="30d", admin=admin_actor, db=db)

        # Assert correct record counts
        assert metrics["users"]["total"] == 3
        assert metrics["users"]["active"] == 3
        assert metrics["workspaces"]["total"] == 1
        assert metrics["subscriptions"]["active"] == 1

        # Assert job counts
        assert metrics["jobs"]["total"] == 3
        assert metrics["jobs"]["successful"] == 1
        assert metrics["jobs"]["failed"] == 1
        assert metrics["jobs"]["pending_and_processing"] == 1
        assert metrics["jobs"]["success_rate_percent"] == 33.3

        # Assert credit consumption in window
        assert metrics["credits"]["consumed_in_window"] == 30

        # Assert payment totals in window
        assert metrics["financials"]["captured_amount_paise"] == 49900
        assert metrics["financials"]["captured_amount_inr"] == 499.0
        assert metrics["financials"]["refunded_amount_paise"] == 19900
        assert metrics["financials"]["refunded_amount_inr"] == 199.0
        assert metrics["financials"]["net_amount_inr"] == 300.0


def test_admin_revenue_analytics_and_mrr(admin_db_session):
    """Verify revenue breakdown, refunds, and MRR calculations."""
    with Session(admin_db_session) as db:
        admin_actor = AdminPrincipal(actor_id="usr_admin_1", email="admin@demerivo.com")
        rev = get_analytics_revenue(range="30d", admin=admin_actor, db=db)

        summary = rev["financial_summary"]
        assert summary["total_transactions"] == 2
        assert summary["gross_collected_inr"] == 499.0
        assert summary["refunds_inr"] == 199.0
        assert summary["net_collected_inr"] == 300.0

        # Check MRR from active Pro subscription (₹499)
        mrr = rev["mrr"]
        assert mrr["estimated_monthly_recurring_revenue_inr"] == 499.0
        assert "pro" in mrr["breakdown_by_plan"]
        assert mrr["breakdown_by_plan"]["pro"]["active_subscriptions"] == 1

        # Verify revenue disclaimer is present
        assert "Payment volume represents total transactions processed and is not net business profit" in rev["disclaimer"]


def test_admin_credit_analytics_and_architecture_disclosure(admin_db_session):
    """Verify credit circulation metrics and single-wallet accounting disclosure."""
    with Session(admin_db_session) as db:
        admin_actor = AdminPrincipal(actor_id="usr_admin_1", email="admin@demerivo.com")
        cred = get_analytics_credits(admin=admin_actor, db=db)

        circ = cred["circulation"]
        assert circ["total_wallets"] == 1
        assert circ["credits_in_circulation"] == 120

        cumul = cred["cumulative_totals"]
        assert cumul["total_credits_granted"] == 150
        assert cumul["total_credits_consumed"] == 30

        # Verify accounting disclosure
        assert "single unified integer balance" in cred["accounting_architecture_disclosure"]


# =============================================================================
# 3. User Management Tests
# =============================================================================

def test_user_management_search_and_pagination(admin_db_session):
    """Verify customer search, status filtering, and pagination."""
    with Session(admin_db_session) as db:
        admin_actor = AdminPrincipal(actor_id="usr_admin_1", email="admin@demerivo.com")

        # 1. Search by email
        res1 = list_users(page=1, page_size=10, search="alice", status_filter=None, admin=admin_actor, db=db)
        assert res1["total"] == 1
        assert res1["items"][0]["email"] == "alice@example.com"
        assert res1["items"][0]["credit_balance"] == 120
        assert res1["items"][0]["subscription_plan"] == "pro"

        # 2. Search non-matching
        res2 = list_users(page=1, page_size=10, search="nonexistent", status_filter=None, admin=admin_actor, db=db)
        assert res2["total"] == 0

        # 3. Status filter active
        res3 = list_users(page=1, page_size=10, search=None, status_filter="active", admin=admin_actor, db=db)
        assert res3["total"] == 3


def test_user_inspection_and_status_update_with_audit_trail(admin_db_session):
    """Verify detailed profile inspection and account suspension with audit logging."""
    with Session(admin_db_session) as db:
        admin_actor = AdminPrincipal(actor_id="usr_admin_1", email="admin@demerivo.com")

        # Inspect Alice
        detail = inspect_user(user_id="usr_alice_1", admin=admin_actor, db=db)
        assert detail["user"]["email"] == "alice@example.com"
        assert len(detail["workspaces"]) == 1
        assert len(detail["recent_jobs"]) == 3

        # Suspend Alice
        suspend_req = UserStatusUpdateRequest(
            action="suspend",
            reason="Violation of terms regarding rate abuse",
        )
        suspend_res = update_user_status(
            user_id="usr_alice_1",
            payload=suspend_req,
            admin=admin_actor,
            db=db,
        )
        assert suspend_res["is_active"] is False
        assert suspend_res["action_taken"] == "suspend"

        # Verify DB record updated
        alice = db.query(UserModel).filter_by(id="usr_alice_1").first()
        assert alice.is_active is False

        # Verify Audit Log entry created
        audit_log = (
            db.query(AdminAuditLogModel)
            .filter_by(action="user.suspend", target_id="usr_alice_1")
            .first()
        )
        assert audit_log is not None
        assert audit_log.actor_email == "admin@demerivo.com"
        assert audit_log.reason == "Violation of terms regarding rate abuse"

        # Reactivate Alice
        activate_req = UserStatusUpdateRequest(
            action="activate",
            reason="Compliance review completed and account restored",
        )
        activate_res = update_user_status(
            user_id="usr_alice_1",
            payload=activate_req,
            admin=admin_actor,
            db=db,
        )
        assert activate_res["is_active"] is True
        assert alice.is_active is True


def test_admin_cannot_suspend_themselves(admin_db_session):
    """Verify administrator cannot suspend their own active account."""
    with Session(admin_db_session) as db:
        admin_actor = AdminPrincipal(actor_id="usr_admin_1", email="admin@demerivo.com")
        req = UserStatusUpdateRequest(action="suspend", reason="Accidental self-lockout")

        with pytest.raises(HTTPException) as exc_info:
            update_user_status(user_id="usr_admin_1", payload=req, admin=admin_actor, db=db)
        assert exc_info.value.status_code == 400
        assert "Administrators cannot suspend their own" in exc_info.value.detail


# =============================================================================
# 4. Tenant & Workspace Management Tests
# =============================================================================

def test_tenant_search_and_inspection(admin_db_session):
    """Verify workspace search, member roster, and usage stats inspection."""
    with Session(admin_db_session) as db:
        admin_actor = AdminPrincipal(actor_id="usr_admin_1", email="admin@demerivo.com")

        # List workspaces
        t_list = list_tenants(page=1, page_size=10, search="Alice", is_personal=None, admin=admin_actor, db=db)
        assert t_list["total"] == 1
        item = t_list["items"][0]
        assert item["name"] == "Alice Studio"
        assert item["member_count"] == 2
        assert item["wallet_balance"] == 120
        assert item["subscription_plan"] == "pro"

        # Inspect workspace
        t_detail = inspect_tenant(tenant_id="tnt_alice_1", admin=admin_actor, db=db)
        assert len(t_detail["members"]) == 2
        assert t_detail["usage"]["total_jobs"] == 3
        assert t_detail["usage"]["successful_jobs"] == 1
        assert t_detail["usage"]["failed_jobs"] == 1


# =============================================================================
# 5. Job Monitoring Tests
# =============================================================================

def test_job_monitoring_filters_and_duration_telemetry(admin_db_session):
    """Verify job listing filters and safe duration measurement."""
    with Session(admin_db_session) as db:
        admin_actor = AdminPrincipal(actor_id="usr_admin_1", email="admin@demerivo.com")

        # 1. Filter by status: completed
        completed = list_jobs(page=1, page_size=10, status_filter="completed", admin=admin_actor, db=db)
        assert completed["total"] == 1
        job_item = completed["items"][0]
        assert job_item["operation_type"] == "upscale_4x"
        assert job_item["duration_seconds"] == 12.0

        # 2. Filter by operation: bg_remove
        bg_jobs = list_jobs(page=1, page_size=10, operation_type="bg_remove", admin=admin_actor, db=db)
        assert bg_jobs["total"] == 1
        assert bg_jobs["items"][0]["status"] == "failed"
        assert "corrupted header" in bg_jobs["items"][0]["error_message"]

        # 3. Inspect job details
        detail = inspect_job(job_id="job_alice_1", admin=admin_actor, db=db)
        assert detail["id"] == "job_alice_1"
        assert detail["credit_cost"] == 4
        # Verify private S3 keys or secrets are not returned in parameters
        assert "secret" not in str(detail["parameters"]).lower()


# =============================================================================
# 6. Subscription Management Tests
# =============================================================================

def test_subscription_management_and_tier_modification(admin_db_session):
    """Verify subscription search, plan modifications, and audit log generation."""
    with Session(admin_db_session) as db:
        admin_actor = AdminPrincipal(actor_id="usr_admin_1", email="admin@demerivo.com")

        # List subscriptions
        subs = list_subscriptions(page=1, page_size=10, plan_code="pro", admin=admin_actor, db=db)
        assert subs["total"] == 1
        assert subs["items"][0]["plan_code"] == "pro"

        # Modify subscription from pro to studio
        req = SubscriptionModifyRequest(
            plan_code="studio",
            reason="Special customer upgrade promotion",
            cancel_at_period_end=False,
        )
        mod_res = modify_subscription(
            subscription_id="sub_alice_1",
            payload=req,
            admin=admin_actor,
            db=db,
        )
        assert mod_res["previous_plan"] == "pro"
        assert mod_res["current_plan"] == "studio"

        # Verify DB updated
        sub = db.query(SubscriptionModel).filter_by(id="sub_alice_1").first()
        assert sub.plan_code == "studio"

        # Verify Audit Log
        audit_entry = (
            db.query(AdminAuditLogModel)
            .filter_by(action="subscription.modify", target_id="sub_alice_1")
            .first()
        )
        assert audit_entry is not None
        assert audit_entry.reason == "Special customer upgrade promotion"


# =============================================================================
# 7. Payment Ledger History Tests
# =============================================================================

def test_payment_history_pagination(admin_db_session):
    """Verify payment ledger retrieval and filtering."""
    with Session(admin_db_session) as db:
        admin_actor = AdminPrincipal(actor_id="usr_admin_1", email="admin@demerivo.com")

        payments = list_payments(page=1, page_size=10, status_filter="captured", admin=admin_actor, db=db)
        assert payments["total"] == 1
        item = payments["items"][0]
        assert item["amount_inr"] == 499.0
        assert item["status"] == "captured"
        assert item["provider_payment_id"] == "pay_mock_111"


# =============================================================================
# 8. System Health Probes Tests
# =============================================================================

def test_system_health_probes(admin_db_session):
    """Verify non-destructive health checks for FastAPI, DB, Redis, Queue, and Storage."""
    settings = Settings()
    with Session(admin_db_session) as db:
        admin_actor = AdminPrincipal(actor_id="usr_admin_1", email="admin@demerivo.com")
        health = get_system_health(admin=admin_actor, db=db, settings=settings)

        assert "components" in health
        comps = health["components"]

        # FastAPI
        assert comps["fastapi"]["status"] == "healthy"

        # Database
        assert comps["database"]["status"] == "healthy"
        assert comps["database"]["latency_ms"] >= 0

        # Redis (expected offline in local test, should safely report in_memory_fallback)
        assert comps["redis"]["status"] in ("connected", "offline")
        if comps["redis"]["status"] == "offline":
            assert comps["redis"]["rate_limiter_mode"] == "in_memory_fallback"

        # Queue depth
        assert comps["queue"]["status"] == "healthy"
        assert comps["queue"]["processing_jobs"] == 1

        # Failure telemetry
        assert "job_failure_telemetry_24h" in comps


# =============================================================================
# 9. Audit Log Security & Redaction Tests
# =============================================================================

def test_audit_log_redaction_and_listing(admin_db_session):
    """Verify audit log metadata redaction and paginated query."""
    # Test metadata sanitization
    sensitive_data = {
        "user_email": "test@example.com",
        "api_key": "secret-12345",
        "nested": {
            "password": "plain-text-pwd",
            "safe_field": "public_value",
        },
    }
    scrubbed = sanitize_audit_metadata(sensitive_data)
    assert scrubbed["api_key"] == "[REDACTED]"
    assert scrubbed["nested"]["password"] == "[REDACTED]"
    assert scrubbed["nested"]["safe_field"] == "public_value"

    with Session(admin_db_session) as db:
        admin_actor = AdminPrincipal(actor_id="usr_admin_1", email="admin@demerivo.com")

        # Record a test audit log
        record_audit_log(
            db=db,
            actor_email="admin@demerivo.com",
            action="config.update",
            target_type="system",
            target_id="settings",
            reason="Updated concurrency settings",
            status="success",
            metadata={"max_workers": 4, "secret_key": "hidden"},
        )
        db.commit()

        # Query audit logs
        logs = list_audit_logs(page=1, page_size=10, action="config.update", admin=admin_actor, db=db)
        assert logs["total"] >= 1
        entry = logs["items"][0]
        assert entry["action"] == "config.update"
        assert entry["metadata"]["secret_key"] == "[REDACTED]"
        assert entry["metadata"]["max_workers"] == 4
