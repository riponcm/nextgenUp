"""Production background worker process runner with graceful shutdown and heartbeat monitoring."""

import logging
import os
import signal
import socket
import sys
import time
from typing import Optional
from sqlalchemy.orm import Session

from demerivo.config import get_settings
from demerivo.db.models import WorkerHeartbeatModel, utcnow
from demerivo.db.session import SessionLocal, init_db
from demerivo.logging_config import setup_logging
from demerivo.workers.manager import WorkerManager

logger = logging.getLogger("demerivo.worker.runner")


def update_heartbeat(worker_id: str, hostname: str, status_str: str, active_jobs: int):
    """Upsert worker heartbeat record into database."""
    try:
        with SessionLocal() as db:
            record = db.query(WorkerHeartbeatModel).filter_by(worker_id=worker_id).first()
            if not record:
                record = WorkerHeartbeatModel(
                    worker_id=worker_id,
                    hostname=hostname,
                    status=status_str,
                    active_jobs_count=active_jobs,
                    started_at=utcnow(),
                    last_heartbeat=utcnow(),
                )
                db.add(record)
            else:
                record.status = status_str
                record.active_jobs_count = active_jobs
                record.last_heartbeat = utcnow()
            db.commit()
    except Exception as e:
        logger.warning("Failed to record worker heartbeat: %s", e)


def main():
    """Main entrypoint for standalone background worker container."""
    settings = get_settings()
    setup_logging(level="INFO", json_format=settings.is_production)

    worker_id = os.environ.get("DEMERIVO_WORKER_ID", f"worker-{socket.gethostname()}-{os.getpid()}")
    hostname = socket.gethostname()

    logger.info("Initializing Demerivo PixelFix Background Worker [%s] on host %s...", worker_id, hostname)

    # Initialize database if needed
    init_db()

    manager = WorkerManager(settings=settings)

    # On startup, immediately recover any stranded jobs from previous crashes
    recovered = manager.recover_stranded_jobs(timeout_seconds=60)
    if recovered:
        logger.info("Startup crash recovery processed %d stranded jobs: %s", len(recovered), recovered)

    # Start worker pool
    manager.start()
    update_heartbeat(worker_id, hostname, "active", 0)

    # Signal handlers for graceful container termination (SIGTERM / SIGINT)
    shutdown_requested = False

    def handle_shutdown(signum, frame):
        nonlocal shutdown_requested
        sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        logger.info("Received signal %s. Initiating graceful worker shutdown...", sig_name)
        shutdown_requested = True

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    logger.info("Worker process [%s] is active and consuming tasks from queue.", worker_id)

    # Main supervisor loop: heartbeats every 15 seconds
    try:
        while not shutdown_requested:
            active_count = sum(1 for w in manager.workers if w._running)
            update_heartbeat(worker_id, hostname, "active", active_count)
            time.sleep(15)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received.")

    # Graceful shutdown
    logger.info("Stopping worker manager pool...")
    update_heartbeat(worker_id, hostname, "stopping", 0)
    manager.stop(timeout=10.0)
    update_heartbeat(worker_id, hostname, "stopped", 0)
    logger.info("Worker process [%s] terminated cleanly.", worker_id)
    sys.exit(0)


if __name__ == "__main__":
    main()
