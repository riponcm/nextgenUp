"""Production background worker process runner with graceful shutdown and heartbeat monitoring."""

import logging
import os
from pathlib import Path
import signal
import socket
import sys
import tempfile
import time
from typing import Optional
from sqlalchemy.orm import Session

from demerivo.config import get_settings
from demerivo.db.models import WorkerHeartbeatModel, utcnow
from demerivo.db.session import SessionLocal, init_db
from demerivo.logging_config import setup_logging
from demerivo.workers.manager import WorkerManager

logger = logging.getLogger("demerivo.worker.runner")

HEARTBEAT_FILE = os.environ.get(
    "DEMERIVO_WORKER_HEARTBEAT_FILE",
    os.path.join(tempfile.gettempdir(), "worker-heartbeat") if os.name == "nt" else "/tmp/worker-heartbeat"
)


def write_heartbeat_file(heartbeat_path: Optional[str] = None) -> None:
    """Touch local heartbeat file to signal health to container orchestrator."""
    path_str = heartbeat_path or HEARTBEAT_FILE
    try:
        p = Path(path_str)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(time.time()), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to write local heartbeat file %s: %s", path_str, e)


def remove_heartbeat_file(heartbeat_path: Optional[str] = None) -> None:
    """Remove local heartbeat file when worker is unhealthy or stopped."""
    path_str = heartbeat_path or HEARTBEAT_FILE
    try:
        p = Path(path_str)
        if p.exists():
            p.unlink()
    except Exception as e:
        logger.warning("Failed to remove local heartbeat file %s: %s", path_str, e)


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
    if manager.is_healthy():
        update_heartbeat(worker_id, hostname, "active", manager.active_worker_count())
        write_heartbeat_file()

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
            if manager.is_healthy():
                alive_count = manager.active_worker_count()
                total_workers = len(manager.workers)
                # Differentiate fully active vs partially degraded worker pool
                status_str = "active" if (total_workers > 0 and alive_count == total_workers) else "degraded"
                update_heartbeat(worker_id, hostname, status_str, alive_count)
                write_heartbeat_file()
            else:
                logger.error("Worker healthcheck failed: all worker threads are inactive or crashed!")
                update_heartbeat(worker_id, hostname, "crashed", 0)
                remove_heartbeat_file()

            # Responsive sleep loop checking shutdown_requested every second
            for _ in range(15):
                if shutdown_requested:
                    break
                time.sleep(1)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received.")
    finally:
        # Resilient graceful shutdown: telemetry failures must NEVER prevent process cleanup
        logger.info("Initiating resilient worker shutdown sequence...")

        # 1. Unlink local heartbeat file so container orchestrator knows shutdown has started
        try:
            remove_heartbeat_file()
        except Exception as e:
            logger.warning("Error removing heartbeat file during shutdown: %s", e)

        # 2. Record 'stopping' telemetry (non-blocking, failure tolerant)
        try:
            update_heartbeat(worker_id, hostname, "stopping", 0)
        except Exception as e:
            logger.warning("Telemetry update ('stopping') failed during shutdown: %s", e)

        # 3. Shutdown worker manager and all worker/recovery threads (MANDATORY)
        try:
            manager.stop(timeout=20.0)
        except Exception as e:
            logger.error("Error stopping WorkerManager: %s", e)

        # 4. Record 'stopped' telemetry (non-blocking, failure tolerant)
        try:
            update_heartbeat(worker_id, hostname, "stopped", 0)
        except Exception as e:
            logger.warning("Telemetry update ('stopped') failed during shutdown: %s", e)

        logger.info("Worker process [%s] terminated.", worker_id)
    sys.exit(0)


if __name__ == "__main__":
    main()
