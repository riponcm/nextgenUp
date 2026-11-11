"""Automated tests for persistent background workers, durable queue, and crash recovery."""

import io
import os
import shutil
import tempfile
import time
import pytest
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker

from demerivo.config import Settings
from demerivo.credits.wallet_service import WalletService
from demerivo.db.models import (
    CreditTransactionType,
    JobModel,
    JobStatus,
    OperationType,
    TenantModel,
    UserModel,
    WalletModel,
)
from demerivo.db.session import Base, create_db_engine
from demerivo.jobs.schemas import JobCreate
from demerivo.jobs.service import JobService
from demerivo.storage.local import LocalStorage
from demerivo.workers.manager import WorkerManager
from demerivo.workers.queue import LocalDurableQueueProvider
from demerivo.workers.worker import DurableWorker


def make_test_png() -> bytes:
    img = Image.new("RGB", (64, 64), color="green")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def env_fixture():
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_workers.db")
    storage_dir = os.path.join(temp_dir, "storage")
    os.makedirs(storage_dir, exist_ok=True)

    settings = Settings(
        env="testing",
        debug=True,
        storage_provider="local",
        local_storage_dir=storage_dir,
        database_url=f"sqlite:///{db_path}",
        max_concurrent_workers=2,
    )

    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    storage = LocalStorage(base_dir=storage_dir)
    queue = LocalDurableQueueProvider()

    # Seed test tenant & wallet
    with TestingSession() as db:
        user = UserModel(id="usr-worker-test", email="worker@demerivo.com", is_active=True)
        tenant = TenantModel(id="tnt-worker-test", name="Worker WS", slug="worker-ws", owner_id=user.id)
        wallet = WalletModel(id="wlt-worker-test", tenant_id=tenant.id, balance=30)
        db.add_all([user, tenant, wallet])
        db.commit()

    yield {
        "settings": settings,
        "engine": engine,
        "session_factory": TestingSession,
        "storage": storage,
        "queue": queue,
        "tenant_id": "tnt-worker-test",
        "user_id": "usr-worker-test",
    }

    engine.dispose()
    shutil.rmtree(temp_dir, ignore_errors=True)


class TestQueueProvider:
    """Test durable queue contracts, leases, and operations."""

    def test_queue_lifecycle(self):
        queue = LocalDurableQueueProvider()
        assert queue.get_queue_length() == 0

        queue.enqueue("job-1")
        queue.enqueue("job-2")
        assert queue.get_queue_length() == 2

        # Dequeue job-1
        item = queue.dequeue(timeout=1)
        assert item == "job-1"
        assert queue.get_queue_length() == 1
        assert "job-1" in queue._processing

        # Ack job-1
        queue.acknowledge("job-1")
        assert "job-1" not in queue._processing

        # Fail job-2
        item2 = queue.dequeue(timeout=1)
        assert item2 == "job-2"
        queue.fail("job-2", "Inference error")
        assert "job-2" not in queue._processing
        assert queue._failed["job-2"] == "Inference error"


class TestWorkerExecutionAndRecovery:
    """Test full asynchronous worker execution, credit reconciliation, and crash recovery."""

    def test_worker_end_to_end_job_processing(self, env_fixture):
        storage = env_fixture["storage"]
        queue = env_fixture["queue"]
        session_factory = env_fixture["session_factory"]
        tenant_id = env_fixture["tenant_id"]
        user_id = env_fixture["user_id"]

        # 1. Upload sample image into storage
        input_key = f"uploads/{tenant_id}/sample.png"
        sample_bytes = make_test_png()
        storage.upload_file(key=input_key, data=sample_bytes, content_type="image/png")

        # 2. Create Job with credit reservation (UPSCALE_2X = 2 credits)
        with session_factory() as db:
            req = JobCreate(operation_type=OperationType.UPSCALE_2X, input_key=input_key)
            job = JobService.create_job(db, req, tenant_id=tenant_id, user_id=user_id)
            job_id = job.id

            # Verify balance: 30 - 2 = 28
            wallet = db.query(WalletModel).filter_by(tenant_id=tenant_id).first()
            assert wallet.balance == 28

        # Enqueue job
        queue.enqueue(job_id)

        # 3. Worker executes the job
        worker = DurableWorker(
            worker_id="test-worker",
            settings=env_fixture["settings"],
            queue=queue,
            storage=storage,
            session_factory=session_factory,
        )
        success = worker.process_job(job_id)
        assert success is True

        # 4. Verify job state in database
        with session_factory() as db:
            completed_job = JobService.get_job(db, job_id, tenant_id=tenant_id)
            assert completed_job.status == JobStatus.COMPLETED
            assert completed_job.progress == 100
            assert completed_job.output_key is not None

            # Verify output file exists in storage and is 2x resolution (128x128)
            assert storage.exists(completed_job.output_key)
            out_bytes = storage.download_file(completed_job.output_key)
            out_img = Image.open(io.BytesIO(out_bytes))
            assert out_img.size == (128, 128)

            # Verify credit balance remains 28 (consumed, not refunded)
            wallet = db.query(WalletModel).filter_by(tenant_id=tenant_id).first()
            assert wallet.balance == 28

            # Verify consumption audit transaction exists
            txs = WalletService().get_transactions(tenant_id, db)
            tx_types = [t.transaction_type for t in txs]
            assert CreditTransactionType.RESERVATION in tx_types
            assert CreditTransactionType.CONSUMPTION in tx_types

    def test_worker_failure_triggers_automatic_refund(self, env_fixture):
        storage = env_fixture["storage"]
        queue = env_fixture["queue"]
        session_factory = env_fixture["session_factory"]
        tenant_id = env_fixture["tenant_id"]
        user_id = env_fixture["user_id"]

        # Job referencing non-existent input key to force storage download failure
        with session_factory() as db:
            req = JobCreate(operation_type=OperationType.UPSCALE_4X, input_key="missing.png")
            # 4 credits reserved
            job = JobService.create_job(db, req, tenant_id=tenant_id, user_id=user_id)
            job_id = job.id

            wallet = db.query(WalletModel).filter_by(tenant_id=tenant_id).first()
            balance_after_res = wallet.balance

        # Worker processes failing job
        worker = DurableWorker(
            worker_id="test-worker-fail",
            settings=env_fixture["settings"],
            queue=queue,
            storage=storage,
            session_factory=session_factory,
        )
        success = worker.process_job(job_id)
        assert success is False

        # Verify job is FAILED and credits were automatically refunded
        with session_factory() as db:
            failed_job = JobService.get_job(db, job_id, tenant_id=tenant_id)
            assert failed_job.status == JobStatus.FAILED
            assert "error" in failed_job.error_message.lower()

            wallet = db.query(WalletModel).filter_by(tenant_id=tenant_id).first()
            # Balance should be restored (+4 credits)
            assert wallet.balance == balance_after_res + 4

            # Verify refund transaction in ledger
            txs = WalletService().get_transactions(tenant_id, db)
            assert any(t.transaction_type == CreditTransactionType.REFUND for t in txs)

    def test_worker_crash_recovery_monitor(self, env_fixture):
        """Simulate a worker crashing mid-processing; verify recovery monitor refunds credits."""
        storage = env_fixture["storage"]
        queue = env_fixture["queue"]
        session_factory = env_fixture["session_factory"]
        tenant_id = env_fixture["tenant_id"]
        user_id = env_fixture["user_id"]

        with session_factory() as db:
            req = JobCreate(operation_type=OperationType.UPSCALE_4X, input_key="crash_test.png")
            job = JobService.create_job(db, req, tenant_id=tenant_id, user_id=user_id)
            job_id = job.id

            # Simulate worker picking it up and transitioning to PROCESSING, then dying
            JobService.update_status(db, job_id, JobStatus.PROCESSING, progress=30)
            wallet = db.query(WalletModel).filter_by(tenant_id=tenant_id).first()
            balance_reserved = wallet.balance

        # Simulate queue lease assigned in the past
        queue._processing[job_id] = time.time() - 1000

        manager = WorkerManager(
            settings=env_fixture["settings"],
            queue=queue,
            storage=storage,
            session_factory=session_factory,
        )

        # Run recovery with 600s threshold
        recovered = manager.recover_stranded_jobs(timeout_seconds=600)
        assert job_id in recovered

        # Verify job was marked FAILED and credits were automatically refunded
        with session_factory() as db:
            recovered_job = JobService.get_job(db, job_id, tenant_id=tenant_id)
            assert recovered_job.status == JobStatus.FAILED
            assert "timed out" in recovered_job.error_message.lower() or "crashed" in recovered_job.error_message.lower()

            wallet = db.query(WalletModel).filter_by(tenant_id=tenant_id).first()
            assert wallet.balance == balance_reserved + 4
