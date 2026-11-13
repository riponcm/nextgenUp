"""Automated tests for job ownership, credit lifecycle integration, and tenant isolation."""

import pytest
from sqlalchemy.orm import Session

from demerivo.credits.wallet_service import WalletService, InsufficientCreditsError
from demerivo.db.models import (
    JobModel,
    JobStatus,
    OperationType,
    TenantModel,
    TenantRole,
    UserModel,
    WalletModel,
)
from demerivo.db.session import Base, create_db_engine
from demerivo.jobs.schemas import JobCreate
from demerivo.jobs.service import JobNotFoundError, JobService


@pytest.fixture
def test_db():
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def two_tenants(test_db: Session):
    wallet_svc = WalletService()

    # Tenant 1 (Alice)
    user1 = UserModel(id="usr-1", email="alice@test.com", is_active=True)
    tenant1 = TenantModel(id="tnt-1", name="Alice Workspace", slug="alice-ws", owner_id=user1.id)
    wallet1 = WalletModel(id="wlt-1", tenant_id=tenant1.id, balance=10)

    # Tenant 2 (Bob)
    user2 = UserModel(id="usr-2", email="bob@test.com", is_active=True)
    tenant2 = TenantModel(id="tnt-2", name="Bob Workspace", slug="bob-ws", owner_id=user2.id)
    wallet2 = WalletModel(id="wlt-2", tenant_id=tenant2.id, balance=5)

    test_db.add_all([user1, tenant1, wallet1, user2, tenant2, wallet2])
    test_db.commit()

    return tenant1, user1, tenant2, user2


class TestJobOwnershipAndCreditLifecycle:
    """Test authenticated job creation, credit deduction, refunds, and cross-tenant boundaries."""

    def test_job_creation_reserves_credits(self, test_db: Session, two_tenants):
        tenant1, user1, _, _ = two_tenants
        req = JobCreate(
            operation_type=OperationType.UPSCALE_4X,
            input_key="uploads/alice_photo.jpg",
            parameters={"face_restore": True},  # 4 + 2 = 6 credits
        )

        job = JobService.create_job(
            db=test_db,
            request=req,
            tenant_id=tenant1.id,
            user_id=user1.id,
        )
        assert job.id is not None
        assert job.tenant_id == tenant1.id
        assert job.user_id == user1.id
        assert job.credit_cost == 6
        assert job.status == JobStatus.PENDING

        # Balance was 10, should now be 4
        wallet = test_db.query(WalletModel).filter_by(tenant_id=tenant1.id).first()
        assert wallet.balance == 4

    def test_job_creation_fails_when_insufficient_credits(self, test_db: Session, two_tenants):
        _, _, tenant2, user2 = two_tenants
        # Bob has 5 credits; requesting upscale 4x + face_restore requires 4 + 2 = 6 credits
        req = JobCreate(
            operation_type=OperationType.UPSCALE_4X,
            input_key="uploads/bob_huge.jpg",
            parameters={"face_restore": True},  # 6 credits
        )

        with pytest.raises(InsufficientCreditsError) as exc_info:
            JobService.create_job(
                db=test_db,
                request=req,
                tenant_id=tenant2.id,
                user_id=user2.id,
            )
        assert exc_info.value.required == 6
        assert exc_info.value.available == 5

        # Balance must remain unchanged at 5
        wallet = test_db.query(WalletModel).filter_by(tenant_id=tenant2.id).first()
        assert wallet.balance == 5

    def test_idempotent_job_creation(self, test_db: Session, two_tenants):
        tenant1, user1, _, _ = two_tenants
        req = JobCreate(
            operation_type=OperationType.BG_REMOVE,  # 1 credit
            input_key="uploads/photo.jpg",
            idempotency_key="idemp-job-12345",
        )

        job1 = JobService.create_job(test_db, req, tenant1.id, user1.id)
        assert job1.credit_cost == 1

        # Second request with identical idempotency key
        job2 = JobService.create_job(test_db, req, tenant1.id, user1.id)
        assert job2.id == job1.id

        # Balance was 10, deducted only 1 credit -> 9
        wallet = test_db.query(WalletModel).filter_by(tenant_id=tenant1.id).first()
        assert wallet.balance == 9

    def test_cross_tenant_job_isolation(self, test_db: Session, two_tenants):
        tenant1, user1, tenant2, user2 = two_tenants

        # Alice creates a job
        req = JobCreate(
            operation_type=OperationType.ENHANCE,
            input_key="uploads/alice_secret.jpg",
        )
        job_alice = JobService.create_job(test_db, req, tenant1.id, user1.id)

        # Alice can fetch her job
        fetched_by_alice = JobService.get_job(test_db, job_alice.id, tenant_id=tenant1.id)
        assert fetched_by_alice.id == job_alice.id

        # Bob (tenant 2) CANNOT fetch Alice's job -> raises JobNotFoundError
        with pytest.raises(JobNotFoundError):
            JobService.get_job(test_db, job_alice.id, tenant_id=tenant2.id)

        # Bob CANNOT cancel Alice's job -> raises JobNotFoundError
        with pytest.raises(JobNotFoundError):
            JobService.cancel_job(test_db, job_alice.id, tenant_id=tenant2.id, user_id=user2.id)

        # Bob CANNOT delete Alice's job -> raises JobNotFoundError
        with pytest.raises(JobNotFoundError):
            JobService.delete_job(test_db, job_alice.id, tenant_id=tenant2.id)

    def test_cancellation_refunds_credits(self, test_db: Session, two_tenants):
        tenant1, user1, _, _ = two_tenants
        req = JobCreate(
            operation_type=OperationType.UPSCALE_2X,  # 2 credits
            input_key="uploads/alice_photo.jpg",
        )
        job = JobService.create_job(test_db, req, tenant1.id, user1.id)

        # Wallet balance: 10 - 2 = 8
        wallet = test_db.query(WalletModel).filter_by(tenant_id=tenant1.id).first()
        assert wallet.balance == 8

        # Cancel job
        cancelled_job = JobService.cancel_job(test_db, job.id, tenant_id=tenant1.id, user_id=user1.id)
        assert cancelled_job.status == JobStatus.CANCELLED

        # Wallet balance restored to 10
        test_db.refresh(wallet)
        assert wallet.balance == 10

    def test_job_failure_refunds_credits(self, test_db: Session, two_tenants):
        tenant1, user1, _, _ = two_tenants
        req = JobCreate(
            operation_type=OperationType.UPSCALE_4X,  # 4 credits
            input_key="uploads/alice_photo.jpg",
        )
        job = JobService.create_job(test_db, req, tenant1.id, user1.id)

        # Wallet balance: 10 - 4 = 6
        wallet = test_db.query(WalletModel).filter_by(tenant_id=tenant1.id).first()
        assert wallet.balance == 6

        # Move to processing then fail
        JobService.update_status(test_db, job.id, JobStatus.PROCESSING, tenant_id=tenant1.id)
        failed_job = JobService.update_status(
            test_db,
            job.id,
            JobStatus.FAILED,
            error_message="ONNX engine execution error",
            tenant_id=tenant1.id,
        )
        assert failed_job.status == JobStatus.FAILED

        # Wallet balance restored to 10
        test_db.refresh(wallet)
        assert wallet.balance == 10

    def test_job_success_finalizes_consumption(self, test_db: Session, two_tenants):
        tenant1, user1, _, _ = two_tenants
        req = JobCreate(
            operation_type=OperationType.BG_REMOVE,  # 1 credit
            input_key="uploads/alice_photo.jpg",
        )
        job = JobService.create_job(test_db, req, tenant1.id, user1.id)

        # Move to processing then completed
        JobService.update_status(test_db, job.id, JobStatus.PROCESSING, tenant_id=tenant1.id)
        completed_job = JobService.update_status(
            test_db,
            job.id,
            JobStatus.COMPLETED,
            output_key="outputs/alice_photo_nobg.png",
            tenant_id=tenant1.id,
        )
        assert completed_job.status == JobStatus.COMPLETED

        # Balance remains 9 (no refund on success)
        wallet = test_db.query(WalletModel).filter_by(tenant_id=tenant1.id).first()
        assert wallet.balance == 9
