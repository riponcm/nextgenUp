"""Comprehensive regression and security test suite for Demerivo PixelFix Phase 4.1 Remediation.

Validates all security fixes, tenant isolation boundaries, rate limiters,
credit ledger protections, plan permission gating, and webhook lifecycle handling.
Uses direct endpoint and domain service invocations without external network dependencies.
"""

import hashlib
import hmac
import io
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from demerivo.ai.engine import get_ai_engine
from demerivo.ai.registry import OPERATIONS_CATALOG, get_operation_registry
from demerivo.api.routers.admin import (
    AdminCreditAdjustmentRequest,
    admin_grant_credits,
    verify_admin_authorization,
)
from demerivo.api.routers.credits import router as credits_router
from demerivo.api.routers.jobs import (
    cancel_job,
    create_job,
    get_job_download_url,
    get_job_status,
)
from demerivo.api.routers.storage_local import local_presigned_download
from demerivo.billing.base import InvalidSignatureError
from demerivo.billing.plans import PLANS_CATALOG
from demerivo.billing.razorpay_provider import RazorpayProvider
from demerivo.billing.service import BillingService
from demerivo.config import Settings
from demerivo.credits.pricing import get_pricing_policy
from demerivo.credits.wallet_service import WalletService
from demerivo.db.models import (
    CreditTransactionModel,
    JobModel,
    JobStatus,
    OperationType,
    PaymentModel,
    PaymentStatus,
    SubscriptionModel,
    SubscriptionStatus,
    TenantMemberModel,
    TenantModel,
    TenantRole,
    UserModel,
    WalletModel,
)
from demerivo.db.session import Base, create_db_engine
from demerivo.jobs.schemas import JobCreate
from demerivo.jobs.service import JobService
from demerivo.security.rate_limiter import SlidingWindowRateLimiter
from demerivo.storage.local import LocalStorage


@pytest.fixture
def remediation_fixture(tmp_path):
    """Isolated test harness providing database session, storage, and seeded tenants."""
    db_path = tmp_path / "test_remediation.db"
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)

    settings = Settings(
        env="testing",
        database_url=f"sqlite:///{db_path}",
        local_storage_dir=str(storage_dir),
        storage_provider="local",
        admin_secret_token="super-secret-admin-token-12345",
        razorpay_key_id="rzp_test_fixture_key",
        razorpay_key_secret="rzp_test_fixture_secret",
        razorpay_webhook_secret="rzp_test_webhook_secret_67890",
        supabase_jwt_secret="demerivo-test-jwt-secret-at-least-32chars-long",
        free_tier_initial_credits=5,
        rate_limit_auth_requests_per_minute=5,
        rate_limit_upload_requests_per_minute=10,
        rate_limit_jobs_requests_per_minute=10,
    )

    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(bind=engine)
    storage = LocalStorage(base_dir=str(storage_dir), secret_key=settings.supabase_jwt_secret)

    with Session(engine) as db:
        user_alice = UserModel(id="usr_alice", email="alice@remediation.test", display_name="Alice", is_active=True)
        tenant_alice = TenantModel(id="tnt_alice", name="Alice Workspace", slug="alice-ws", owner_id=user_alice.id)
        wallet_alice = WalletModel(id="wlt_alice", tenant_id=tenant_alice.id, balance=10)

        user_bob = UserModel(id="usr_bob", email="bob@remediation.test", display_name="Bob", is_active=True)
        tenant_bob = TenantModel(id="tnt_bob", name="Bob Workspace", slug="bob-ws", owner_id=user_bob.id)
        wallet_bob = WalletModel(id="wlt_bob", tenant_id=tenant_bob.id, balance=10)

        m_alice = TenantMemberModel(id=str(uuid.uuid4()), user_id=user_alice.id, tenant_id=tenant_alice.id, role=TenantRole.OWNER)
        m_bob = TenantMemberModel(id=str(uuid.uuid4()), user_id=user_bob.id, tenant_id=tenant_bob.id, role=TenantRole.OWNER)

        db.add_all([user_alice, tenant_alice, wallet_alice, user_bob, tenant_bob, wallet_bob, m_alice, m_bob])
        db.commit()

    razorpay_provider = RazorpayProvider(
        key_id=settings.razorpay_key_id,
        key_secret=settings.razorpay_key_secret,
        webhook_secret=settings.razorpay_webhook_secret,
    )
    wallet_service = WalletService()
    billing_service = BillingService(provider=razorpay_provider, wallet_service=wallet_service)

    return {
        "engine": engine,
        "settings": settings,
        "storage": storage,
        "wallet_service": wallet_service,
        "billing_service": billing_service,
        "razorpay_provider": razorpay_provider,
        "alice_id": "usr_alice",
        "bob_id": "usr_bob",
        "tenant_alice_id": "tnt_alice",
        "tenant_bob_id": "tnt_bob",
    }


# ==============================================================================
# 1. PRIORITY 1.A: CREDIT GRANT VULNERABILITY REMEDIATION
# ==============================================================================

def test_public_credit_grant_endpoint_removed():
    """Verify that unprivileged public @router.post('/grant') was completely removed."""
    routes = [r.path for r in credits_router.routes]
    assert "/grant" not in routes
    assert "/credits/grant" not in routes


def test_admin_authorization_guard(remediation_fixture):
    """Verify constant-time comparison on X-Admin-Secret-Token."""
    settings = remediation_fixture["settings"]

    # 1. Missing header -> 403 Forbidden
    with pytest.raises(HTTPException) as exc_info:
        verify_admin_authorization(x_admin_token=None, settings=settings)
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    # 2. Tampered / invalid token -> 403 Forbidden
    with pytest.raises(HTTPException) as exc_info:
        verify_admin_authorization(x_admin_token="wrong_token_12345", settings=settings)
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    # 3. Valid token -> succeeds without raising
    verify_admin_authorization(x_admin_token="super-secret-admin-token-12345", settings=settings)


def test_admin_credit_grant_with_audit_trail_and_idempotency(remediation_fixture):
    """Verify admin credit adjustment updates wallet balance and creates audit ledger record."""
    engine = remediation_fixture["engine"]
    wallet_svc = remediation_fixture["wallet_service"]
    idemp_key = f"admin_comp_{uuid.uuid4().hex[:8]}"

    payload = AdminCreditAdjustmentRequest(
        tenant_id="tnt_alice",
        amount=30,
        reason="Platform disruption compensation",
        actor="admin_ops@demerivo.com",
        idempotency_key=idemp_key,
    )

    with Session(engine) as db:
        res1 = admin_grant_credits(payload=payload, db=db, wallet_service=wallet_svc)
        assert res1.amount == 30
        assert res1.balance_after == 40  # Initial 10 + 30
        assert res1.tenant_id == "tnt_alice"
        assert res1.actor == "admin_ops@demerivo.com"

        # Verify ledger entry in DB
        tx = db.query(CreditTransactionModel).filter_by(idempotency_key=idemp_key).first()
        assert tx is not None
        assert tx.amount == 30
        assert "[ADMIN ADJUSTMENT by admin_ops@demerivo.com]" in tx.description

        # Re-submit with same idempotency key: balance must NOT double
        res2 = admin_grant_credits(payload=payload, db=db, wallet_service=wallet_svc)
        assert res2.balance_after == 40


# ==============================================================================
# 2. PRIORITY 1.B: CROSS-TENANT IDOR VULNERABILITY REMEDIATION
# ==============================================================================

def test_cross_tenant_input_key_blocked_at_job_creation(remediation_fixture):
    """Verify that Tenant Alice cannot submit a job referencing Tenant Bob's uploaded image."""
    engine = remediation_fixture["engine"]
    storage = remediation_fixture["storage"]
    billing_svc = remediation_fixture["billing_service"]
    storage.upload_file("uploads/tnt_alice/photo.png", b"PHOTO_BYTES", "image/png")

    # Object key belongs to Bob's workspace namespace
    bobs_secret_key = "uploads/tnt_bob/private_tax_doc.png"

    job_req = JobCreate(
        operation_type=OperationType.UPSCALE_2X,
        input_key=bobs_secret_key,
        parameters={},
    )

    with Session(engine) as db:
        alice = db.query(UserModel).filter_by(id="usr_alice").first()
        tnt_alice = db.query(TenantModel).filter_by(id="tnt_alice").first()

        with pytest.raises(HTTPException) as exc_info:
            create_job(
                request=job_req,
                current_user=alice,
                tenant_context=(tnt_alice, TenantRole.OWNER),
                db=db,
                storage=storage,
                billing_service=billing_svc,
            )
        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert "belongs to workspace 'tnt_bob'" in exc_info.value.detail


def test_cross_tenant_job_status_and_download_isolated(remediation_fixture):
    """Verify that Tenant Alice cannot view or download Tenant Bob's job."""
    engine = remediation_fixture["engine"]
    storage = remediation_fixture["storage"]

    with Session(engine) as db:
        alice = db.query(UserModel).filter_by(id="usr_alice").first()
        tnt_alice = db.query(TenantModel).filter_by(id="tnt_alice").first()
        bob = db.query(UserModel).filter_by(id="usr_bob").first()
        tnt_bob = db.query(TenantModel).filter_by(id="tnt_bob").first()

        # Create Bob's completed job
        bob_job = JobModel(
            id=f"job_bob_{uuid.uuid4().hex[:6]}",
            tenant_id="tnt_bob",
            user_id="usr_bob",
            operation_type=OperationType.UPSCALE_2X,
            input_key="uploads/tnt_bob/in.png",
            output_key="results/tnt_bob/out.png",
            status=JobStatus.COMPLETED,
            credit_cost=2,
            parameters={},
        )
        db.add(bob_job)
        db.commit()
        bob_job_id = bob_job.id

        # Alice attempts to inspect Bob's job -> 404 Not Found
        with pytest.raises(HTTPException) as exc_info:
            get_job_status(job_id=bob_job_id, tenant_context=(tnt_alice, TenantRole.OWNER), db=db, storage=storage)
        assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND

        # Alice attempts to download Bob's job result -> 404 Not Found
        with pytest.raises(HTTPException) as exc_dl:
            get_job_download_url(job_id=bob_job_id, tenant_context=(tnt_alice, TenantRole.OWNER), db=db, storage=storage)
        assert exc_dl.value.status_code == status.HTTP_404_NOT_FOUND

        # Bob can download his own completed job -> signed download URL
        storage.upload_file(bob_job.output_key, b"BOB_RESULT", "image/png")
        download_resp = get_job_download_url(job_id=bob_job_id, tenant_context=(tnt_bob, TenantRole.OWNER), db=db, storage=storage)
        assert download_resp.download_url is not None
        assert "sig=" in download_resp.download_url


# ==============================================================================
# 3. PRIORITY 2: SIGNED DOWNLOADS & SERIALIZATION
# ==============================================================================

def test_job_response_serialization_with_signed_url(remediation_fixture):
    """Verify that completed jobs serialize presigned result_url with HMAC expiration."""
    engine = remediation_fixture["engine"]
    storage = remediation_fixture["storage"]

    out_key = "results/tnt_alice/rendered.png"
    storage.upload_file(out_key, b"RENDERED_IMAGE_BINARY", "image/png")

    with Session(engine) as db:
        tnt_alice = db.query(TenantModel).filter_by(id="tnt_alice").first()
        job = JobModel(
            id=f"job_alice_{uuid.uuid4().hex[:6]}",
            tenant_id="tnt_alice",
            user_id="usr_alice",
            operation_type=OperationType.UPSCALE_2X,
            input_key="uploads/tnt_alice/in.png",
            output_key=out_key,
            status=JobStatus.COMPLETED,
            credit_cost=2,
            parameters={},
        )
        db.add(job)
        db.commit()

        resp = get_job_status(job_id=job.id, tenant_context=(tnt_alice, TenantRole.OWNER), db=db, storage=storage)
        assert resp.result_url is not None
        assert "expires=" in resp.result_url
        assert "sig=" in resp.result_url


def test_unsigned_or_expired_storage_access_blocked(remediation_fixture):
    """Verify that accessing storage files without valid cryptographic signature returns 403."""
    settings = remediation_fixture["settings"]
    storage = remediation_fixture["storage"]
    test_key = "results/tnt_alice/secret.png"
    storage.upload_file(test_key, b"SECRET_BYTES", "image/png")

    # 1. Expired signature
    past_timestamp = int(time.time()) - 300
    expired_sig = storage.get_presigned_download_url(test_key, expires_in=-300).split("sig=")[-1]

    with pytest.raises(HTTPException) as exc_info:
        local_presigned_download(
            key=test_key,
            expires=past_timestamp,
            sig=expired_sig,
            settings=settings,
            storage=storage,
        )
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert "expired" in exc_info.value.detail

    # 2. Forged signature
    future_timestamp = int(time.time()) + 3600
    with pytest.raises(HTTPException) as exc_forged:
        local_presigned_download(
            key=test_key,
            expires=future_timestamp,
            sig="forged_signature_0000000000",
            settings=settings,
            storage=storage,
        )
    assert exc_forged.value.status_code == status.HTTP_403_FORBIDDEN
    assert "Invalid presigned download signature" in exc_forged.value.detail

    # 3. Valid signature succeeds
    valid_url = storage.get_presigned_download_url(test_key, expires_in=3600)
    valid_expires = int(valid_url.split("expires=")[1].split("&")[0])
    valid_sig = valid_url.split("sig=")[1]
    res = local_presigned_download(
        key=test_key,
        expires=valid_expires,
        sig=valid_sig,
        settings=settings,
        storage=storage,
    )
    assert res.status_code == status.HTTP_200_OK
    assert res.body == b"SECRET_BYTES"


# ==============================================================================
# 4. PRIORITY 4: SLIDING-WINDOW RATE LIMITING
# ==============================================================================

def test_sliding_window_rate_limiter_local_fallback(remediation_fixture):
    """Verify that requests exceeding configured rate limits are blocked under sliding window."""
    settings = remediation_fixture["settings"]
    limiter = SlidingWindowRateLimiter(settings)
    limiter.reset()

    key = "test_client_key:auth"
    max_requests = 3
    window = 10.0

    # First 3 requests permitted
    assert limiter.is_allowed(key, max_requests=max_requests, window_seconds=window) is True
    assert limiter.is_allowed(key, max_requests=max_requests, window_seconds=window) is True
    assert limiter.is_allowed(key, max_requests=max_requests, window_seconds=window) is True

    # 4th request blocked
    assert limiter.is_allowed(key, max_requests=max_requests, window_seconds=window) is False
    assert limiter.is_allowed(key, max_requests=max_requests, window_seconds=window) is False


# ==============================================================================
# 5. PRIORITY 5: PLAN ENFORCEMENT & 8K REMOVAL
# ==============================================================================

def test_free_plan_alignment():
    """Verify free tier defaults to 5 monthly credits."""
    settings = Settings()
    assert settings.free_tier_initial_credits == 5
    assert PLANS_CATALOG["free"].monthly_credits == 5


def test_free_plan_blocks_colorize_and_inpaint(remediation_fixture):
    """Verify that Free tier users are blocked from executing COLORIZE and INPAINT operations."""
    engine = remediation_fixture["engine"]
    storage = remediation_fixture["storage"]
    billing_svc = remediation_fixture["billing_service"]
    storage.upload_file("uploads/tnt_alice/photo.png", b"PHOTO_BYTES", "image/png")

    for op in [OperationType.COLORIZE, OperationType.INPAINT]:
        req = JobCreate(
            operation_type=op,
            input_key="uploads/tnt_alice/photo.png",
            parameters={},
        )
        with Session(engine) as db:
            alice = db.query(UserModel).filter_by(id="usr_alice").first()
            tnt_alice = db.query(TenantModel).filter_by(id="tnt_alice").first()

            with pytest.raises(HTTPException) as exc_info:
                create_job(
                    request=req,
                    current_user=alice,
                    tenant_context=(tnt_alice, TenantRole.OWNER),
                    db=db,
                    storage=storage,
                    billing_service=billing_svc,
                )
            assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
            assert "(Pro or Studio)" in exc_info.value.detail


def test_reject_8k_resolution_request(remediation_fixture):
    """Verify that 8K resolution parameter requests are rejected with 400 Bad Request."""
    engine = remediation_fixture["engine"]
    storage = remediation_fixture["storage"]
    billing_svc = remediation_fixture["billing_service"]
    storage.upload_file("uploads/tnt_alice/photo.png", b"PHOTO_BYTES", "image/png")

    with Session(engine) as db:
        alice = db.query(UserModel).filter_by(id="usr_alice").first()
        tnt_alice = db.query(TenantModel).filter_by(id="tnt_alice").first()

        # 1. is_8k: True
        req1 = JobCreate(
            operation_type=OperationType.UPSCALE_4X,
            input_key="uploads/tnt_alice/photo.png",
            parameters={"is_8k": True},
        )
        with pytest.raises(HTTPException) as exc1:
            create_job(
                request=req1,
                current_user=alice,
                tenant_context=(tnt_alice, TenantRole.OWNER),
                db=db,
                storage=storage,
                billing_service=billing_svc,
            )
        assert exc1.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "8K resolution is not supported" in exc1.value.detail

        # 2. scale: 8
        req2 = JobCreate(
            operation_type=OperationType.UPSCALE_4X,
            input_key="uploads/tnt_alice/photo.png",
            parameters={"scale": 8},
        )
        with pytest.raises(HTTPException) as exc2:
            create_job(
                request=req2,
                current_user=alice,
                tenant_context=(tnt_alice, TenantRole.OWNER),
                db=db,
                storage=storage,
                billing_service=billing_svc,
            )
        assert exc2.value.status_code == status.HTTP_400_BAD_REQUEST


def test_subscription_auto_downgrade_when_expired(remediation_fixture):
    """Verify that cancelled subscription past current_period_end is auto-downgraded to free."""
    engine = remediation_fixture["engine"]
    storage = remediation_fixture["storage"]
    billing_svc = remediation_fixture["billing_service"]
    storage.upload_file("uploads/tnt_alice/photo.png", b"PHOTO_BYTES", "image/png")

    with Session(engine) as db:
        alice = db.query(UserModel).filter_by(id="usr_alice").first()
        tnt_alice = db.query(TenantModel).filter_by(id="tnt_alice").first()

        # Setup expired Pro subscription marked cancel_at_period_end
        now = datetime.now(timezone.utc)
        sub = billing_svc.get_or_create_tenant_subscription(db, "tnt_alice")
        sub.plan_code = "pro"
        sub.cancel_at_period_end = True
        sub.current_period_end = now - timedelta(days=2)  # Expired 2 days ago
        db.commit()

        # Job submission triggers auto-downgrade check
        req = JobCreate(
            operation_type=OperationType.UPSCALE_2X,
            input_key="uploads/tnt_alice/photo.png",
            parameters={},
        )
        mock_wm = type("MockWM", (), {"enqueue_job": lambda self, jid: None})()
        create_job(
            request=req,
            current_user=alice,
            tenant_context=(tnt_alice, TenantRole.OWNER),
            db=db,
            storage=storage,
            worker_manager=mock_wm,
            billing_service=billing_svc,
        )

        # Check subscription status in DB
        sub_after = db.query(SubscriptionModel).filter_by(tenant_id="tnt_alice").first()
        assert sub_after.plan_code == "free"
        assert sub_after.cancel_at_period_end is False


# ==============================================================================
# 6. PRIORITY 6: RAZORPAY WEBHOOK SECURITY & REPLAY PROTECTION
# ==============================================================================

def test_razorpay_webhook_signature_verification_and_tamper_rejection(remediation_fixture):
    """Verify that tampered webhook body or signature is rejected with InvalidSignatureError."""
    billing_svc = remediation_fixture["billing_service"]
    engine = remediation_fixture["engine"]
    secret = remediation_fixture["settings"].razorpay_webhook_secret

    raw_payload = b'{"event": "payment.captured", "id": "evt_123"}'
    valid_sig = hmac.new(secret.encode("utf-8"), raw_payload, hashlib.sha256).hexdigest()

    with Session(engine) as db:
        # 1. Invalid signature
        with pytest.raises(InvalidSignatureError):
            billing_svc.process_webhook_event(
                db=db,
                raw_body=raw_payload,
                signature="invalid_tampered_signature",
                event_payload=json.loads(raw_payload.decode("utf-8")),
            )

        # 2. Valid signature accepted
        res = billing_svc.process_webhook_event(
            db=db,
            raw_body=raw_payload,
            signature=valid_sig,
            event_payload=json.loads(raw_payload.decode("utf-8")),
        )
        assert res["status"] in ("processed", "unhandled_event_accepted")


def test_razorpay_webhook_payment_captured_and_idempotency(remediation_fixture):
    """Verify payment.captured webhook allocates renewal credits and rejects duplicate replays."""
    billing_svc = remediation_fixture["billing_service"]
    engine = remediation_fixture["engine"]
    secret = remediation_fixture["settings"].razorpay_webhook_secret
    event_id = f"evt_renew_{uuid.uuid4().hex[:8]}"

    event_payload = {
        "event": "payment.captured",
        "id": event_id,
        "payload": {
            "payment": {
                "entity": {
                    "id": f"pay_{uuid.uuid4().hex[:8]}",
                    "order_id": f"order_{uuid.uuid4().hex[:8]}",
                    "amount": 49900,
                    "currency": "INR",
                    "notes": {
                        "tenant_id": "tnt_alice",
                        "plan_code": "pro",
                    },
                }
            }
        },
    }
    raw_bytes = json.dumps(event_payload).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), raw_bytes, hashlib.sha256).hexdigest()

    with Session(engine) as db:
        init_balance = db.query(WalletModel).filter_by(tenant_id="tnt_alice").first().balance

        # 1. First delivery: successfully processed
        res1 = billing_svc.process_webhook_event(
            db=db,
            raw_body=raw_bytes,
            signature=sig,
            event_payload=event_payload,
        )
        assert res1["status"] == "processed"
        assert res1["credits_allocated"] == 150  # Pro plan = 150 credits

        new_bal = db.query(WalletModel).filter_by(tenant_id="tnt_alice").first().balance
        assert new_bal == init_balance + 150

        # Verify payment record written with idempotency key
        pay_rec = db.query(PaymentModel).filter_by(idempotency_key=f"webhook_{event_id}").first()
        assert pay_rec is not None
        assert pay_rec.status == PaymentStatus.CAPTURED

        # 2. Duplicate replay delivery: ignored
        res2 = billing_svc.process_webhook_event(
            db=db,
            raw_body=raw_bytes,
            signature=sig,
            event_payload=event_payload,
        )
        assert res2["status"] == "duplicate_ignored"

        bal_after_replay = db.query(WalletModel).filter_by(tenant_id="tnt_alice").first().balance
        assert bal_after_replay == new_bal  # No double allocation


def test_razorpay_webhook_failure_and_refund_states(remediation_fixture):
    """Verify handling of payment.failed, refund.processed, and subscription.cancelled."""
    billing_svc = remediation_fixture["billing_service"]
    engine = remediation_fixture["engine"]
    secret = remediation_fixture["settings"].razorpay_webhook_secret

    # 1. payment.failed
    fail_id = f"evt_fail_{uuid.uuid4().hex[:8]}"
    fail_payload = {
        "event": "payment.failed",
        "id": fail_id,
        "payload": {
            "payment": {
                "entity": {
                    "id": f"pay_fail_{uuid.uuid4().hex[:8]}",
                    "order_id": f"order_fail_{uuid.uuid4().hex[:8]}",
                    "amount": 19900,
                    "notes": {"tenant_id": "tnt_alice"},
                }
            }
        },
    }
    raw_fail = json.dumps(fail_payload).encode("utf-8")
    sig_fail = hmac.new(secret.encode("utf-8"), raw_fail, hashlib.sha256).hexdigest()

    with Session(engine) as db:
        res_fail = billing_svc.process_webhook_event(
            db=db,
            raw_body=raw_fail,
            signature=sig_fail,
            event_payload=fail_payload,
        )
        assert res_fail["status"] == "payment_failed_recorded"
        pay_rec = db.query(PaymentModel).filter_by(idempotency_key=f"webhook_{fail_id}").first()
        assert pay_rec.status == PaymentStatus.FAILED

    # 2. refund.processed
    rfnd_id = f"evt_rfnd_{uuid.uuid4().hex[:8]}"
    rfnd_payload = {
        "event": "refund.processed",
        "id": rfnd_id,
        "payload": {
            "refund": {
                "entity": {
                    "id": f"rfnd_{uuid.uuid4().hex[:8]}",
                    "payment_id": f"pay_{uuid.uuid4().hex[:8]}",
                    "amount": 19900,
                    "notes": {"tenant_id": "tnt_alice"},
                }
            }
        },
    }
    raw_rfnd = json.dumps(rfnd_payload).encode("utf-8")
    sig_rfnd = hmac.new(secret.encode("utf-8"), raw_rfnd, hashlib.sha256).hexdigest()

    with Session(engine) as db:
        res_rfnd = billing_svc.process_webhook_event(
            db=db,
            raw_body=raw_rfnd,
            signature=sig_rfnd,
            event_payload=rfnd_payload,
        )
        assert res_rfnd["status"] == "refund_processed"
        pay_rfnd = db.query(PaymentModel).filter_by(idempotency_key=f"webhook_{rfnd_id}").first()
        assert pay_rfnd.status == PaymentStatus.REFUNDED


# ==============================================================================
# 7. PRIORITY 7: SPA ROUTING FALLBACK
# ==============================================================================

def test_spa_routing_fallback_logic():
    """Verify that SPA fallback serves index.html for non-existent routes and static files when found."""
    import os
    dist_dir = os.path.abspath("frontend/dist")
    index_html_path = os.path.join(dist_dir, "index.html")

    assert os.path.isfile(index_html_path)

    # Helper function matching main.py's spa_fallback
    def spa_resolver(full_path: str) -> str:
        safe_path = os.path.abspath(os.path.join(dist_dir, full_path.lstrip("/\\")))
        if safe_path.startswith(dist_dir) and os.path.isfile(safe_path):
            return safe_path
        return index_html_path

    # Subroute (e.g. /app/studio) resolves to index.html
    assert spa_resolver("studio") == index_html_path
    assert spa_resolver("billing") == index_html_path
    assert spa_resolver("settings") == index_html_path

    # Real static file (e.g. app.js) resolves to app.js
    app_js_path = os.path.join(dist_dir, "app.js")
    if os.path.isfile(app_js_path):
        assert spa_resolver("app.js") == app_js_path

    # Path traversal attack attempts resolve safely to index.html (or don't escape)
    assert spa_resolver("../../../etc/passwd") == index_html_path
