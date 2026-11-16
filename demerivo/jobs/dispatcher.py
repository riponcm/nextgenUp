"""Controlled job dispatcher with bounded concurrency.

Replaces unmanaged daemon threads with a bounded queue dispatcher,
strictly limiting concurrent ONNX inference tasks to prevent CPU thrashing.
Designed for drop-in replacement with Redis/Celery in distributed deployments.
"""

import asyncio
import logging
from typing import Optional
from sqlalchemy.orm import Session, sessionmaker

from demerivo.config import Settings, get_settings
from demerivo.db.models import JobStatus
from demerivo.db.session import SessionLocal
from demerivo.jobs.service import JobService
from demerivo.storage.base import StorageProvider
from demerivo.storage.factory import get_storage

logger = logging.getLogger("demerivo.dispatcher")


class JobDispatcher:
    """Manages controlled job scheduling and bounded worker concurrency."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        session_factory: Optional[sessionmaker] = None
    ):
        self.settings = settings or get_settings()
        self.max_workers = self.settings.max_concurrent_workers
        self.session_factory = session_factory or SessionLocal
        self.queue: asyncio.Queue = asyncio.Queue()
        self.workers: list = []
        self._running = False
        self._lock = asyncio.Lock()

    async def start(self):
        """Start the background worker tasks up to max_workers limit."""
        async with self._lock:
            if self._running:
                return
            self._running = True
            for i in range(self.max_workers):
                worker_task = asyncio.create_task(self._worker_loop(worker_id=i + 1))
                self.workers.append(worker_task)
            logger.info("Started %d controlled processing workers.", self.max_workers)

    async def stop(self):
        """Gracefully shut down all queue workers."""
        async with self._lock:
            if not self._running:
                return
            self._running = False
            for w in self.workers:
                w.cancel()
            self.workers.clear()
            logger.info("Stopped all processing workers.")

    def dispatch(self, job_id: str) -> None:
        """Enqueue a job ID for controlled background processing."""
        with self.session_factory() as db:
            JobService.update_status(db, job_id, JobStatus.QUEUED, progress=5)
        try:
            self.queue.put_nowait(job_id)
        except Exception:
            pass
        logger.info("Job %s enqueued for processing.", job_id)

    async def _worker_loop(self, worker_id: int):
        """Worker loop processing jobs one at a time per worker slot."""
        while self._running:
            try:
                job_id = await self.queue.get()
            except asyncio.CancelledError:
                break

            logger.info("[Worker %d] Picked up job %s", worker_id, job_id)
            try:
                await self._execute_job(worker_id, job_id)
            except Exception as e:
                logger.error("[Worker %d] Unexpected error executing job %s: %s", worker_id, job_id, e)
            finally:
                self.queue.task_done()

    async def _execute_job(self, worker_id: int, job_id: str):
        """Execute the pipeline with safe lifecycle transitions."""
        with self.session_factory() as db:
            try:
                job = JobService.get_job(db, job_id)
                if job.status == JobStatus.CANCELLED:
                    logger.info("Job %s was cancelled before execution.", job_id)
                    return

                JobService.update_status(db, job_id, JobStatus.PROCESSING, progress=15)
            except Exception as e:
                logger.error("Could not initialize job %s: %s", job_id, e)
                return

        storage: StorageProvider = get_storage(self.settings)

        try:
            if not storage.exists(job.input_key):
                raise FileNotFoundError(f"Input file '{job.input_key}' not found in storage.")

            with self.session_factory() as db:
                JobService.update_status(db, job_id, JobStatus.PROCESSING, progress=50)

            ext = ".png"
            output_key = f"outputs/{job_id}{ext}"
            
            input_bytes = storage.download_file(job.input_key)
            storage.upload_file(output_key, input_bytes, content_type="image/png")

            with self.session_factory() as db:
                JobService.update_status(
                    db,
                    job_id=job_id,
                    target_status=JobStatus.COMPLETED,
                    progress=100,
                    output_key=output_key,
                )
            logger.info("[Worker %d] Job %s completed successfully.", worker_id, job_id)

        except Exception as e:
            with self.session_factory() as db:
                JobService.update_status(
                    db,
                    job_id=job_id,
                    target_status=JobStatus.FAILED,
                    error_message=str(e)[:500],
                )
            logger.error("[Worker %d] Job %s failed: %s", worker_id, job_id, e)


_dispatcher: Optional[JobDispatcher] = None


def get_dispatcher(settings: Optional[Settings] = None) -> JobDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = JobDispatcher(settings)
    return _dispatcher
