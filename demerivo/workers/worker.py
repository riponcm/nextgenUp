"""Durable background worker processing jobs with credit reconciliation and storage integration."""

import logging
import threading
import time
from typing import Optional
from sqlalchemy.orm import sessionmaker

from demerivo.ai.engine import get_ai_engine
from demerivo.config import Settings, get_settings
from demerivo.credits.wallet_service import get_wallet_service
from demerivo.db.models import JobStatus
from demerivo.db.session import SessionLocal
from demerivo.jobs.service import InvalidJobStateTransition, JobService
from demerivo.storage.base import StorageProvider
from demerivo.storage.factory import get_storage
from demerivo.workers.queue import QueueProvider, get_queue_provider

logger = logging.getLogger("demerivo.worker")


class DurableWorker:
    """Persistent background task consumer managing end-to-end AI execution and credit lifecycle."""

    def __init__(
        self,
        worker_id: str = "worker-1",
        settings: Optional[Settings] = None,
        queue: Optional[QueueProvider] = None,
        storage: Optional[StorageProvider] = None,
        session_factory: Optional[sessionmaker] = None,
    ):
        self.worker_id = worker_id
        self.settings = settings or get_settings()
        self.queue = queue or get_queue_provider(self.settings)
        self.storage = storage or get_storage(self.settings)
        self.session_factory = session_factory or SessionLocal
        self.ai_engine = get_ai_engine()
        self.wallet_service = get_wallet_service()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.state: str = "stopped"  # stopped, idle, processing
        self.last_activity_time: float = time.time()
        self.last_progress_time: float = time.time()
        self.current_job_id: Optional[str] = None
        self.current_stage: Optional[str] = None
        self.current_progress_percent: int = 0

    def start(self):
        """Start the worker thread."""
        self._running = True
        self.state = "idle"
        self.last_activity_time = time.time()
        self.last_progress_time = time.time()
        self._thread = threading.Thread(target=self._run_loop, name=f"DemerivoWorker-{self.worker_id}", daemon=True)
        self._thread.start()
        logger.info("[%s] Worker started.", self.worker_id)

    def stop(self, timeout: float = 5.0):
        """Stop worker and wait for current task to complete."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self.state = "stopped"
        logger.info("[%s] Worker stopped.", self.worker_id)

    def _run_loop(self):
        """Main polling loop."""
        self.state = "idle"
        self.last_activity_time = time.time()
        self.last_progress_time = time.time()

        while self._running:
            try:
                self.state = "idle"
                self.last_activity_time = time.time()
                job_id = self.queue.dequeue(timeout=0.2)
                if not job_id:
                    continue

                self.state = "processing"
                self.current_job_id = job_id
                self.last_activity_time = time.time()
                self.last_progress_time = time.time()
                logger.info("[%s] Picked up job %s", self.worker_id, job_id)
                self.process_job(job_id)
            except Exception as e:
                logger.error("[%s] Unexpected loop error: %s", self.worker_id, e)
                time.sleep(1)
            finally:
                self.current_job_id = None
                self.current_stage = None
                self.current_progress_percent = 0
                self.state = "idle"
                self.last_activity_time = time.time()
                self.last_progress_time = time.time()

    def is_stuck(
        self,
        idle_timeout: float = 60.0,
        processing_timeout: float = 300.0,
        now: Optional[float] = None,
    ) -> bool:
        """Determine if worker thread is deadlocked or has ceased making forward progress."""
        if not self._running:
            return False
        if not self._thread or not self._thread.is_alive():
            return False

        curr = now if now is not None else time.time()

        if self.state == "idle":
            return (curr - self.last_activity_time) > idle_timeout
        elif self.state == "processing":
            return (curr - self.last_progress_time) > processing_timeout

        return False

    def is_healthy(
        self,
        idle_timeout: float = 60.0,
        processing_timeout: float = 300.0,
        now: Optional[float] = None,
    ) -> bool:
        """Worker is healthy if running, alive, and making verifiable forward progress."""
        if not self._running:
            return False
        if self._thread is None or not self._thread.is_alive():
            return False
        if self.is_stuck(idle_timeout=idle_timeout, processing_timeout=processing_timeout, now=now):
            return False
        return True

    def process_job(self, job_id: str) -> bool:
        """Process a single job through the complete pipeline with credit safety."""
        # 1. Fetch job metadata in short-lived database session
        self.current_stage = "initializing"
        self.current_progress_percent = 5
        self.last_progress_time = time.time()
        self.queue.renew_lease(job_id)

        with self.session_factory() as db:
            try:
                job = JobService.get_job(db, job_id)
            except Exception as e:
                logger.error("Job %s not found: %s", job_id, e)
                self.queue.fail(job_id, f"Job not found: {e}")
                return False

            if job.status == JobStatus.CANCELLED:
                logger.info("Job %s was cancelled before processing started.", job_id)
                self.queue.acknowledge(job_id)
                return True

            if job.status in (JobStatus.FAILED, JobStatus.COMPLETED):
                logger.info("Job %s already in terminal state %s before processing started.", job_id, job.status.value)
                self.queue.acknowledge(job_id)
                return False

            tenant_id = job.tenant_id
            user_id = job.user_id
            operation_type = job.operation_type
            input_key = job.input_key
            parameters = dict(job.parameters)
            credit_cost = job.credit_cost

            # Mark as processing
            JobService.update_status(db, job_id, JobStatus.PROCESSING, progress=10)

        # 2. Progress callback updating database without holding long transactions
        def _on_progress(stage: str, percent: int):
            self.current_stage = stage
            self.current_progress_percent = percent
            self.last_progress_time = time.time()
            self.queue.renew_lease(job_id)
            try:
                with self.session_factory() as db:
                    JobService.update_status(db, job_id, JobStatus.PROCESSING, progress=percent)
            except Exception:
                pass

        # 3. Retrieve input image from storage
        self.current_stage = "downloading"
        self.current_progress_percent = 15
        self.last_progress_time = time.time()
        self.queue.renew_lease(job_id)
        try:
            input_bytes = self.storage.download_file(input_key)
        except Exception as e:
            logger.error("Failed to download input image %s: %s", input_key, e)
            self._handle_failure(job_id, tenant_id, user_id, credit_cost, f"Storage download error: {e}")
            return False

        # 4. Execute AI pipeline
        self.current_stage = "inference"
        self.current_progress_percent = 30
        self.last_progress_time = time.time()
        self.queue.renew_lease(job_id)
        try:
            output_bytes, dims = self.ai_engine.process(
                image_bytes=input_bytes,
                operation_type=operation_type,
                parameters=parameters,
                progress_cb=_on_progress,
            )
        except Exception as e:
            logger.error("Inference failed for job %s: %s", job_id, e)
            self._handle_failure(job_id, tenant_id, user_id, credit_cost, f"AI processing error: {e}")
            return False

        # 5. Upload output image to storage
        self.current_stage = "uploading"
        self.current_progress_percent = 90
        self.last_progress_time = time.time()
        self.queue.renew_lease(job_id)
        output_key = f"outputs/{tenant_id}/{job_id}.png"
        try:
            self.storage.upload_file(
                key=output_key,
                data=output_bytes,
                content_type="image/png",
            )
        except Exception as e:
            logger.error("Failed to upload output image %s: %s", output_key, e)
            self._handle_failure(job_id, tenant_id, user_id, credit_cost, f"Storage upload error: {e}")
            return False

        # 6. Finalize job success and credit consumption
        with self.session_factory() as db:
            try:
                JobService.update_status(
                    db=db,
                    job_id=job_id,
                    target_status=JobStatus.COMPLETED,
                    progress=100,
                    output_key=output_key,
                )
                self.wallet_service.finalize_consumption(job_id, db)
            except InvalidJobStateTransition as e:
                logger.warning(
                    "[%s] Job %s cannot be transitioned to COMPLETED (%s). Discarding late completion.",
                    self.worker_id,
                    job_id,
                    e,
                )
                self.queue.acknowledge(job_id)
                return False

        self.current_stage = "completed"
        self.current_progress_percent = 100
        self.last_progress_time = time.time()
        self.queue.acknowledge(job_id)
        logger.info("[%s] Successfully completed job %s (size: %s)", self.worker_id, job_id, dims)
        return True

    def _handle_failure(
        self,
        job_id: str,
        tenant_id: str,
        user_id: str,
        credit_cost: int,
        error_message: str,
    ):
        """Atomically mark job as failed (JobService automatically executes refund)."""
        with self.session_factory() as db:
            try:
                JobService.update_status(
                    db=db,
                    job_id=job_id,
                    target_status=JobStatus.FAILED,
                    error_message=error_message,
                )
            except Exception as e:
                logger.error("Error during failure refund for job %s: %s", job_id, e)

        self.queue.fail(job_id, error_message)
