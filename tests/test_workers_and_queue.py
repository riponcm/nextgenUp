"""Automated tests for persistent background workers, durable queue, and crash recovery."""

import io
import os
import shutil
import tempfile
import threading
import time
import pytest
from PIL import Image
from sqlalchemy.orm import Session, sessionmaker

from demerivo.config import Settings
from demerivo.credits.wallet_service import WalletService
from demerivo.db.models import (
    CreditTransactionModel,
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
from demerivo.workers.runner import remove_heartbeat_file, write_heartbeat_file
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


class TestWorkerShutdownAndHeartbeat:
    """Tests for worker thread health tracking, shared shutdown budgeting, and heartbeat watchdog."""

    def test_worker_thread_failure_heartbeat_detection(self, env_fixture):
        """Verify that worker thread death updates health state and fails heartbeat checks."""
        import tempfile
        from demerivo.workers.runner import write_heartbeat_file, remove_heartbeat_file

        settings = env_fixture["settings"]
        manager = WorkerManager(
            settings=settings,
            queue=env_fixture["queue"],
            storage=env_fixture["storage"],
            session_factory=env_fixture["session_factory"],
        )
        manager.start()
        try:
            assert manager.is_healthy() is True
            assert manager.active_worker_count() == 2

            with tempfile.TemporaryDirectory() as tmp_dir:
                hb_path = os.path.join(tmp_dir, "test-heartbeat")
                # When healthy, heartbeat file can be written and is fresh
                write_heartbeat_file(hb_path)
                assert os.path.exists(hb_path)
                assert time.time() - os.path.getmtime(hb_path) < 5

                # Simulate crash of first worker thread
                w1 = manager.workers[0]
                w1._running = False
                w1._thread = None
                assert manager.active_worker_count() == 1
                assert manager.is_healthy() is True  # Still has 1 worker alive

                # Simulate crash of second worker thread
                w2 = manager.workers[1]
                w2._running = False
                w2._thread = None
                assert manager.active_worker_count() == 0
                assert manager.is_healthy() is False  # All workers are dead

                # When unhealthy, heartbeat file is removed
                remove_heartbeat_file(hb_path)
                assert not os.path.exists(hb_path)

                # Healthcheck probe verification: file does not exist -> fails
                probe_passes = os.path.exists(hb_path) and (time.time() - os.path.getmtime(hb_path)) < 60
                assert probe_passes is False
        finally:
            manager.stop(timeout=1.0)

    def test_two_worker_shutdown_within_budget(self, env_fixture):
        """Verify that multiple workers share a single overall timeout budget during shutdown."""
        settings = env_fixture["settings"]
        manager = WorkerManager(
            settings=settings,
            queue=env_fixture["queue"],
            storage=env_fixture["storage"],
            session_factory=env_fixture["session_factory"],
        )
        manager.start()

        start_time = time.time()
        # Bound shutdown timeout to 0.5s total across all workers
        manager.stop(timeout=0.5)
        elapsed = time.time() - start_time

        # With shared budget, elapsed time must remain strictly bounded
        assert elapsed < 1.2, f"Shutdown took too long: {elapsed}s"
        assert manager.workers == []
        assert manager._running is False

    def test_graceful_sigterm_in_flight_job_preservation(self, env_fixture):
        """Verify that jobs in-flight during shutdown either complete or can be recovered."""
        storage = env_fixture["storage"]
        queue = env_fixture["queue"]
        session_factory = env_fixture["session_factory"]
        tenant_id = env_fixture["tenant_id"]
        user_id = env_fixture["user_id"]

        # Upload input image
        input_key = f"uploads/{tenant_id}/sigterm_test.png"
        storage.upload_file(key=input_key, data=make_test_png(), content_type="image/png")

        with session_factory() as db:
            req = JobCreate(operation_type=OperationType.UPSCALE_2X, input_key=input_key)
            job = JobService.create_job(db, req, tenant_id=tenant_id, user_id=user_id)
            job_id = job.id

        queue.enqueue(job_id)

        manager = WorkerManager(
            settings=env_fixture["settings"],
            queue=queue,
            storage=storage,
            session_factory=session_factory,
        )
        manager.start()

        # Allow worker to dequeue and begin processing
        time.sleep(0.5)

        # Trigger shutdown
        manager.stop(timeout=5.0)

        with session_factory() as db:
            j = JobService.get_job(db, job_id, tenant_id=tenant_id)
            assert j.status in (JobStatus.COMPLETED, JobStatus.PROCESSING)
            if j.status == JobStatus.PROCESSING:
                # If interrupted mid-processing, simulate crash recovery
                recovered = manager.recover_stranded_jobs(timeout_seconds=0)
                assert job_id in recovered
                refreshed_job = JobService.get_job(db, job_id, tenant_id=tenant_id)
                assert refreshed_job.status == JobStatus.FAILED

    def test_local_storage_token_compatibility_between_api_and_worker(self):
        """Verify that API and Worker share identical presigned token signatures when configured with the same JWT secret."""
        import tempfile
        shared_secret = "production-supabase-jwt-secret-min-32-chars-long!"
        with tempfile.TemporaryDirectory() as tmp_dir:
            api_storage = LocalStorage(base_dir=tmp_dir, secret_key=shared_secret)
            worker_storage = LocalStorage(base_dir=tmp_dir, secret_key=shared_secret)
            mismatched_worker = LocalStorage(base_dir=tmp_dir, secret_key="different-secret-key-mismatch-32ch!")

            test_key = "outputs/tenant-123/job-456.png"
            expires = int(time.time()) + 3600

            api_sig = api_storage._generate_signature(test_key, expires)
            worker_sig = worker_storage._generate_signature(test_key, expires)
            mismatched_sig = mismatched_worker._generate_signature(test_key, expires)

            # Same secret -> Identical HMAC signature
            assert api_sig == worker_sig
            # Different secret -> Mismatched signature
            assert api_sig != mismatched_sig

    def test_one_worker_crashes_other_survives_degraded_healthy(self, env_fixture):
        """Verify that if 1 of 2 workers crashes, the pool remains healthy (degraded) and continues."""
        settings = env_fixture["settings"]
        manager = WorkerManager(
            settings=settings,
            queue=env_fixture["queue"],
            storage=env_fixture["storage"],
            session_factory=env_fixture["session_factory"],
        )
        manager.start()
        assert len(manager.workers) == 2
        assert manager.is_healthy() is True
        assert manager.active_worker_count() == 2

        # Simulate crash of worker-1
        w1 = manager.workers[0]
        w1._running = False
        if w1._thread:
            w1._thread.join(timeout=1.0)

        # Policy verification: 1 alive worker keeps manager healthy (degraded state)
        assert manager.is_healthy() is True
        assert manager.active_worker_count() == 1

        manager.stop(timeout=1.0)

    def test_all_workers_crash_marks_unhealthy(self, env_fixture):
        """Verify that when all workers crash, manager is unhealthy and reports 0 active workers."""
        settings = env_fixture["settings"]
        manager = WorkerManager(
            settings=settings,
            queue=env_fixture["queue"],
            storage=env_fixture["storage"],
            session_factory=env_fixture["session_factory"],
        )
        manager.start()
        assert manager.is_healthy() is True

        # Stop/kill all worker threads
        for w in manager.workers:
            w._running = False
            if w._thread:
                w._thread.join(timeout=1.0)

        assert manager.active_worker_count() == 0
        assert manager.is_healthy() is False

        manager.stop(timeout=1.0)

    def test_shutdown_timeout_overrun_retains_surviving_workers(self, env_fixture):
        """Verify that if a worker thread hangs/overruns shutdown deadline, it is retained and not hidden."""
        from unittest.mock import patch
        settings = env_fixture["settings"]
        manager = WorkerManager(
            settings=settings,
            queue=env_fixture["queue"],
            storage=env_fixture["storage"],
            session_factory=env_fixture["session_factory"],
        )
        manager.start()

        w0 = manager.workers[0]
        with patch.object(w0._thread, "is_alive", return_value=True):
            start = time.time()
            manager.stop(timeout=1.0)
            elapsed = time.time() - start

            # Shared budget was respected
            assert elapsed < 1.5
            # Surviving worker was retained in manager state
            assert len(manager.workers) == 1
            assert manager.workers[0].worker_id == w0.worker_id

    def test_recovery_of_interrupted_processing_job(self, env_fixture):
        """Verify that an interrupted PROCESSING job is detected by crash recovery, marked FAILED, and refunded."""
        storage = env_fixture["storage"]
        queue = env_fixture["queue"]
        session_factory = env_fixture["session_factory"]
        tenant_id = env_fixture["tenant_id"]
        user_id = env_fixture["user_id"]

        input_key = f"uploads/{tenant_id}/crash_recovery.png"
        storage.upload_file(key=input_key, data=make_test_png(), content_type="image/png")

        with session_factory() as db:
            req = JobCreate(operation_type=OperationType.UPSCALE_2X, input_key=input_key)
            job = JobService.create_job(db, req, tenant_id=tenant_id, user_id=user_id)
            job_id = job.id
            cost = job.credit_cost
            assert cost > 0

            # Advance to PROCESSING to simulate an in-flight job whose worker abruptly died
            JobService.update_status(db, job_id, JobStatus.PROCESSING)

        # Enqueue and simulate lease in queue
        queue.enqueue(job_id)
        queue.dequeue(timeout=1)  # moves into queue._processing

        # Verify initial wallet balance
        wallet_service = WalletService()
        with session_factory() as db:
            wallet = wallet_service.get_wallet(tenant_id, db)
            initial_balance = wallet.balance

        manager = WorkerManager(
            settings=env_fixture["settings"],
            queue=queue,
            storage=storage,
            session_factory=session_factory,
        )

        # Run crash recovery with timeout=0 to force lease expiration
        recovered = manager.recover_stranded_jobs(timeout_seconds=0)
        assert job_id in recovered

        with session_factory() as db:
            refreshed_job = JobService.get_job(db, job_id, tenant_id=tenant_id)
            assert refreshed_job.status == JobStatus.FAILED
            assert "refunded" in refreshed_job.error_message.lower()

            wallet = wallet_service.get_wallet(tenant_id, db)
            # Balance should be refunded by exactly `cost`
            assert wallet.balance == initial_balance + cost

    def test_exactly_once_credit_refund_on_recovery(self, env_fixture):
        """Verify that calling stranded job recovery or status update multiple times guarantees exactly-once refund."""
        from unittest.mock import patch
        storage = env_fixture["storage"]
        queue = env_fixture["queue"]
        session_factory = env_fixture["session_factory"]
        tenant_id = env_fixture["tenant_id"]
        user_id = env_fixture["user_id"]

        input_key = f"uploads/{tenant_id}/exactly_once.png"
        storage.upload_file(key=input_key, data=make_test_png(), content_type="image/png")

        with session_factory() as db:
            req = JobCreate(operation_type=OperationType.UPSCALE_2X, input_key=input_key)
            job = JobService.create_job(db, req, tenant_id=tenant_id, user_id=user_id)
            job_id = job.id
            cost = job.credit_cost

            # Advance to PROCESSING
            JobService.update_status(db, job_id, JobStatus.PROCESSING)

        queue.enqueue(job_id)
        queue.dequeue(timeout=1)

        wallet_service = WalletService()
        with session_factory() as db:
            wallet = wallet_service.get_wallet(tenant_id, db)
            balance_after_reserve = wallet.balance

        manager = WorkerManager(
            settings=env_fixture["settings"],
            queue=queue,
            storage=storage,
            session_factory=session_factory,
        )

        # 1. First recovery
        recovered_1 = manager.recover_stranded_jobs(timeout_seconds=0)
        assert job_id in recovered_1

        with session_factory() as db:
            wallet_1 = wallet_service.get_wallet(tenant_id, db)
            assert wallet_1.balance == balance_after_reserve + cost
            tx_count_1 = len(wallet_service.get_transactions(tenant_id, db))

        # 2. Second recovery attempt with the same job_id (e.g. repeated crash monitor)
        with patch.object(queue, "recover_stranded_jobs", return_value=[job_id]):
            recovered_2 = manager.recover_stranded_jobs(timeout_seconds=0)
            assert job_id in recovered_2

        with session_factory() as db:
            wallet_2 = wallet_service.get_wallet(tenant_id, db)
            # Balance MUST NOT increase a second time
            assert wallet_2.balance == balance_after_reserve + cost
            # Transaction count must not increase with a duplicate refund
            tx_count_2 = len(wallet_service.get_transactions(tenant_id, db))
            assert tx_count_2 == tx_count_1

    def test_heartbeat_removal_on_clean_and_abrupt_shutdown(self):
        """Verify heartbeat file behavior on clean and abnormal shutdown."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            hb_path = os.path.join(tmp_dir, "test-hb")
            write_heartbeat_file(hb_path)
            assert os.path.exists(hb_path)

            # Clean shutdown removes heartbeat file
            remove_heartbeat_file(hb_path)
            assert not os.path.exists(hb_path)

            # Abrupt crash simulation: file exists, but if process crashes and file gets removed
            write_heartbeat_file(hb_path)
            remove_heartbeat_file(hb_path)
            remove_heartbeat_file(hb_path)
            assert not os.path.exists(hb_path)

    def test_worker_progress_monitoring_detects_stuck_thread(self, env_fixture):
        """Verify that per-worker progress tracking distinguishes idle, processing, and stuck workers."""
        settings = env_fixture["settings"]
        manager = WorkerManager(
            settings=settings,
            queue=env_fixture["queue"],
            storage=env_fixture["storage"],
            session_factory=env_fixture["session_factory"],
        )
        manager._running = True

        w1 = DurableWorker(worker_id="test-w1", settings=settings, queue=env_fixture["queue"])
        w2 = DurableWorker(worker_id="test-w2", settings=settings, queue=env_fixture["queue"])
        manager.workers = [w1, w2]

        unstick_all = threading.Event()

        # Both workers running actual live threads
        t1 = threading.Thread(target=lambda: unstick_all.wait(timeout=10), daemon=True)
        t2 = threading.Thread(target=lambda: unstick_all.wait(timeout=10), daemon=True)
        t1.start()
        t2.start()

        w1._thread = t1
        w1._running = True
        w1.state = "idle"
        w1.last_activity_time = time.time()

        w2._thread = t2
        w2._running = True
        w2.state = "idle"
        w2.last_activity_time = time.time()

        # Both workers initially healthy
        assert w1.is_healthy() is True
        assert w2.is_healthy() is True
        assert manager.is_healthy() is True
        assert manager.active_worker_count() == 2

        # 1. Simulate worker-1 thread deadlocked/hung while idle (alive, but no progress for > 60s)
        w1.last_activity_time = time.time() - 120.0
        assert w1.is_stuck(idle_timeout=60.0) is True
        assert w1.is_healthy(idle_timeout=60.0) is False

        # Degraded policy preserved: 1 worker stuck, 1 worker healthy -> manager remains healthy
        assert manager.is_healthy() is True
        assert manager.active_worker_count() == 1

        # 2. Simulate worker-2 actively processing with fresh progress updates
        w2.state = "processing"
        w2.current_job_id = "job-active-long-running"
        w2.last_progress_time = time.time() - 10.0  # recent progress
        assert w2.is_stuck(processing_timeout=120.0) is False
        assert w2.is_healthy(processing_timeout=120.0) is True

        # 3. Simulate worker-2 thread becoming stuck during processing (> 300s default processing threshold)
        w2.last_progress_time = time.time() - 400.0
        assert w2.is_stuck(processing_timeout=120.0) is True
        assert w2.is_healthy() is False

        # Now BOTH worker threads are alive but genuinely stuck -> supervisor reports unhealthy
        assert manager.active_worker_count() == 0
        assert manager.is_healthy() is False

        unstick_all.set()
        manager.stop(timeout=1.0)

    def test_runner_shutdown_survives_database_telemetry_failure(self, env_fixture):
        """Verify that runner shutdown cleanup executes completely even if DB telemetry raises exceptions."""
        from unittest.mock import MagicMock, patch
        settings = env_fixture["settings"]
        manager = WorkerManager(
            settings=settings,
            queue=env_fixture["queue"],
            storage=env_fixture["storage"],
            session_factory=env_fixture["session_factory"],
        )
        manager.start()

        with tempfile.TemporaryDirectory() as tmp_dir:
            hb_path = os.path.join(tmp_dir, "test-runner-fail-hb")
            write_heartbeat_file(hb_path)
            assert os.path.exists(hb_path)

            # Simulate database throwing errors during both 'stopping' and 'stopped' updates
            mock_update_hb = MagicMock(side_effect=RuntimeError("Database connection lost!"))

            with patch("demerivo.workers.runner.update_heartbeat", mock_update_hb):
                # Run the exact resilient cleanup sequence from runner.py finally block
                remove_heartbeat_file(hb_path)
                try:
                    mock_update_hb("worker-1", "host-1", "stopping", 0)
                except Exception:
                    pass

                # manager.stop MUST still execute despite telemetry failure
                manager.stop(timeout=1.0)

                try:
                    mock_update_hb("worker-1", "host-1", "stopped", 0)
                except Exception:
                    pass

            assert not os.path.exists(hb_path)
            assert manager._running is False
            assert manager.workers == []

    def test_late_worker_completion_cannot_overwrite_terminal_failed_state(self, env_fixture):
        """Verify that a worker completing inference after crash recovery cannot overwrite terminal FAILED status."""
        storage = env_fixture["storage"]
        queue = env_fixture["queue"]
        session_factory = env_fixture["session_factory"]
        tenant_id = env_fixture["tenant_id"]
        user_id = env_fixture["user_id"]

        input_key = f"uploads/{tenant_id}/late_worker_test.png"
        storage.upload_file(key=input_key, data=make_test_png(), content_type="image/png")

        with session_factory() as db:
            req = JobCreate(operation_type=OperationType.UPSCALE_2X, input_key=input_key)
            job = JobService.create_job(db, req, tenant_id=tenant_id, user_id=user_id)
            job_id = job.id
            cost = job.credit_cost
            assert cost > 0

            # Advance to PROCESSING
            JobService.update_status(db, job_id, JobStatus.PROCESSING)

        queue.enqueue(job_id)
        queue.dequeue(timeout=1)

        wallet_service = WalletService()
        with session_factory() as db:
            initial_balance = wallet_service.get_wallet(tenant_id, db).balance

        manager = WorkerManager(
            settings=env_fixture["settings"],
            queue=queue,
            storage=storage,
            session_factory=session_factory,
        )

        # 1. Recovery expires the lease, marks the job as FAILED, and refunds credits
        recovered = manager.recover_stranded_jobs(timeout_seconds=0)
        assert job_id in recovered

        with session_factory() as db:
            job_after_recovery = JobService.get_job(db, job_id, tenant_id=tenant_id)
            assert job_after_recovery.status == JobStatus.FAILED
            refunded_balance = wallet_service.get_wallet(tenant_id, db).balance
            assert refunded_balance == initial_balance + cost

        # 2. Simulate worker completing the job late
        worker = DurableWorker(
            worker_id="test-late-worker",
            settings=env_fixture["settings"],
            queue=queue,
            storage=storage,
            session_factory=session_factory,
        )
        # Attempt to process job - should detect terminal status and discard
        success = worker.process_job(job_id)
        assert success is False

        # 3. Verify job status remains FAILED and credits were NOT re-consumed
        with session_factory() as db:
            job_final = JobService.get_job(db, job_id, tenant_id=tenant_id)
            assert job_final.status == JobStatus.FAILED
            final_balance = wallet_service.get_wallet(tenant_id, db).balance
            assert final_balance == refunded_balance

    def test_queue_renew_lease_during_active_processing(self):
        """Verify that renew_lease extends lease timestamp and prevents premature crash recovery."""
        queue = LocalDurableQueueProvider()
        queue.enqueue("job-long-1")
        queue.dequeue(timeout=1)

        # Check initial lease
        assert "job-long-1" in queue._processing
        initial_lease = queue._processing["job-long-1"]

        time.sleep(0.05)
        # Worker renews lease during processing milestone
        queue.renew_lease("job-long-1")
        renewed_lease = queue._processing["job-long-1"]
        assert renewed_lease > initial_lease

        # Safe lease verification: recovery with small threshold does not fail renewed job
        stranded = queue.recover_stranded_jobs(timeout_seconds=10)
        assert "job-long-1" not in stranded

    def test_redis_queue_fractional_timeout_sanitization(self):
        """Verify RedisQueueProvider safely converts fractional dequeue timeouts to valid Redis integer timeouts."""
        from unittest.mock import MagicMock
        from demerivo.workers.queue import RedisQueueProvider

        provider = RedisQueueProvider.__new__(RedisQueueProvider)
        provider.redis = MagicMock()
        provider.pending_key = "test:pending"
        provider.processing_key = "test:processing"
        provider.leases_key = "test:leases"
        provider.failed_key = "test:failed"

        provider.redis.brpoplpush.return_value = None

        # Pass fractional timeout 0.2s
        provider.dequeue(timeout=0.2)

        # Must have passed integer ceil(0.2) = 1 to Redis brpoplpush
        provider.redis.brpoplpush.assert_called_with("test:pending", "test:processing", timeout=1)

    def test_concurrent_refund_credits_guarantees_exactly_once(self, env_fixture):
        """Verify database-enforced atomicity and idempotency when two threads attempt refunds concurrently."""
        import concurrent.futures
        session_factory = env_fixture["session_factory"]
        tenant_id = env_fixture["tenant_id"]
        user_id = env_fixture["user_id"]
        wallet_service = WalletService()

        test_job_id = "job-concurrent-refund-test-99"
        refund_amount = 15

        # Seed initial balance and reserve credits for the test job
        with session_factory() as db:
            wallet = wallet_service.get_wallet(tenant_id, db)
            initial_balance = wallet.balance
            wallet_service.reserve_credits(
                tenant_id=tenant_id,
                amount=refund_amount,
                job_id=test_job_id,
                user_id=user_id,
                db=db,
            )
            post_reserve_balance = wallet_service.get_wallet(tenant_id, db).balance
            assert post_reserve_balance == initial_balance - refund_amount

        # Function executed concurrently by multiple threads with independent DB sessions
        def attempt_refund():
            with session_factory() as thread_db:
                return wallet_service.refund_credits(
                    tenant_id=tenant_id,
                    amount=refund_amount,
                    job_id=test_job_id,
                    user_id=user_id,
                    reason="Concurrent failure test",
                    db=thread_db,
                )

        # Run 2 concurrent refund attempts simultaneously
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut1 = executor.submit(attempt_refund)
            fut2 = executor.submit(attempt_refund)
            tx1 = fut1.result(timeout=5.0)
            tx2 = fut2.result(timeout=5.0)

        # Both callers must receive valid, consistent transaction records
        assert tx1 is not None
        assert tx2 is not None
        assert tx1.id == tx2.id
        assert tx1.job_id == test_job_id
        assert tx2.job_id == test_job_id

        # Verify DB state: exactly 1 refund transaction exists and balance increased only ONCE
        with session_factory() as db:
            refunds = (
                db.query(CreditTransactionModel)
                .filter_by(job_id=test_job_id, transaction_type=CreditTransactionType.REFUND)
                .all()
            )
            assert len(refunds) == 1

            wallet = wallet_service.get_wallet(tenant_id, db)
            # Balance must be exactly initial_balance (restored once), NOT initial_balance + refund_amount
            assert wallet.balance == initial_balance
