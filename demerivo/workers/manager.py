"""Worker supervisor and crash recovery manager."""

import logging
import threading
import time
from typing import List, Optional
from sqlalchemy.orm import sessionmaker

from demerivo.config import Settings, get_settings
from demerivo.credits.wallet_service import get_wallet_service
from demerivo.db.models import JobStatus
from demerivo.db.session import SessionLocal
from demerivo.jobs.service import JobService
from demerivo.storage.base import StorageProvider
from demerivo.storage.factory import get_storage
from demerivo.workers.queue import QueueProvider, get_queue_provider
from demerivo.workers.worker import DurableWorker

logger = logging.getLogger("demerivo.workers.manager")


class WorkerManager:
    """Manages worker concurrency pool, health checks, and crashed job recovery."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        queue: Optional[QueueProvider] = None,
        storage: Optional[StorageProvider] = None,
        session_factory: Optional[sessionmaker] = None,
    ):
        self.settings = settings or get_settings()
        self.queue = queue or get_queue_provider(self.settings)
        self.storage = storage or get_storage(self.settings)
        self.session_factory = session_factory or SessionLocal
        self.wallet_service = get_wallet_service()
        self.workers: List[DurableWorker] = []
        self._running = False
        self._recovery_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self):
        """Start worker pool and background recovery monitor."""
        if self._running:
            return
        self._running = True

        for i in range(self.settings.max_concurrent_workers):
            worker = DurableWorker(
                worker_id=f"worker-{i + 1}",
                settings=self.settings,
                queue=self.queue,
                storage=self.storage,
                session_factory=self.session_factory,
            )
            worker.start()
            self.workers.append(worker)

        # Start periodic crash recovery monitor
        self._recovery_thread = threading.Thread(
            target=self._recovery_monitor_loop,
            name="DemerivoCrashRecoveryMonitor",
            daemon=True,
        )
        self._recovery_thread.start()
        logger.info("WorkerManager started %d workers and recovery monitor.", len(self.workers))

    def is_healthy(self, now: Optional[float] = None) -> bool:
        """Verify supervisor health and worker thread availability.

        Health Policy:
        - Healthy (True): Manager is running and at least one worker is active, alive,
          and verifiably making progress (not stuck or deadlocked).
          If 1 of N workers dies or gets stuck, the manager remains functional in degraded capacity,
          allowing surviving workers to continue processing in-flight and queued jobs
          without triggering orchestrator container churn.
        - Unhealthy (False): Manager is stopped, or ALL worker threads are dead or stuck.
          Orchestrators (Docker/K8s) must mark the container unhealthy and restart it.
        """
        if not self._running or not self.workers:
            return False
        return any(w.is_healthy(now=now) for w in self.workers)

    def active_worker_count(self, now: Optional[float] = None) -> int:
        """Return count of actively running, non-stuck worker threads."""
        return sum(1 for w in self.workers if w.is_healthy(now=now))

    def stop(self, timeout: float = 15.0):
        """Gracefully shut down all workers within the shared timeout budget.

        Ensures:
        1. All workers immediately stop accepting new queue jobs.
        2. In-flight jobs are allowed to complete within the shared timeout.
        3. Total wait time across all workers is strictly bounded by `timeout`.
        4. Workers exceeding the timeout are NOT silently hidden; surviving threads
           remain tracked in `self.workers` so manager state accurately reflects runtime reality.
        5. Background crash recovery monitor is cleanly stopped.
        """
        if not self._running:
            return
        self._running = False
        self._stop_event.set()

        # 1. Immediately signal all workers to cease accepting new jobs
        for worker in self.workers:
            worker._running = False

        # 2. Wait for all workers within a shared overall timeout budget
        deadline = time.time() + timeout
        surviving = []
        for worker in self.workers:
            remaining = max(0.0, deadline - time.time())
            worker.stop(timeout=remaining)
            if worker._thread and worker._thread.is_alive():
                surviving.append(worker)

        # 3. Join background recovery thread if active
        if self._recovery_thread and self._recovery_thread.is_alive():
            self._recovery_thread.join(timeout=1.0)

        if surviving:
            logger.warning(
                "WorkerManager shutdown timed out with %d surviving worker(s): %s",
                len(surviving),
                [w.worker_id for w in surviving],
            )
            self.workers = surviving
        else:
            self.workers.clear()
            logger.info("WorkerManager stopped all workers cleanly.")

    def enqueue_job(self, job_id: str) -> str:
        """Enqueue a job into the persistent queue."""
        with self.session_factory() as db:
            JobService.update_status(db, job_id, JobStatus.QUEUED, progress=5)
        self.queue.enqueue(job_id)
        logger.info("Enqueued job %s into persistent worker queue.", job_id)
        return job_id

    def recover_stranded_jobs(self, timeout_seconds: Optional[int] = None) -> List[str]:
        """Detect jobs whose worker crashed during processing and refund reserved credits."""
        timeout = self.settings.job_timeout_seconds if timeout_seconds is None else timeout_seconds
        recovered_job_ids = self.queue.recover_stranded_jobs(timeout_seconds=timeout)

        for job_id in recovered_job_ids:
            logger.warning("Recovering stranded job %s whose worker lease expired.", job_id)
            with self.session_factory() as db:
                try:
                    job = JobService.get_job(db, job_id)
                    if job.status == JobStatus.PROCESSING:
                        JobService.update_status(
                            db=db,
                            job_id=job_id,
                            target_status=JobStatus.FAILED,
                            error_message="Worker timeout / crashed during execution. Reserved credits refunded.",
                        )
                except Exception as e:
                    logger.error("Failed to recover stranded job %s: %s", job_id, e)

        return recovered_job_ids

    def _recovery_monitor_loop(self):
        """Periodic background thread checking for orphaned tasks."""
        while self._running and not self._stop_event.is_set():
            try:
                self.recover_stranded_jobs()
            except Exception as e:
                logger.error("Error in recovery monitor loop: %s", e)
            self._stop_event.wait(timeout=30)


_worker_manager_instance: Optional[WorkerManager] = None


def get_worker_manager(settings: Optional[Settings] = None) -> WorkerManager:
    """Return singleton WorkerManager."""
    global _worker_manager_instance
    if _worker_manager_instance is None:
        _worker_manager_instance = WorkerManager(settings=settings)
    return _worker_manager_instance
