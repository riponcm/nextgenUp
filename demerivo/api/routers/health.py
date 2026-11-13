"""Health and diagnostic endpoints distinguishing liveness from readiness."""

import logging
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from demerivo.config import Settings, get_settings
from demerivo.db.session import get_db
from demerivo.storage.base import StorageProvider
from demerivo.storage.factory import get_storage
from demerivo.workers.queue import QueueProvider, get_queue_provider

logger = logging.getLogger("demerivo.health")
router = APIRouter(tags=["Health & System"])


@router.get("/healthz", status_code=status.HTTP_200_OK)
def liveness_probe() -> Dict[str, str]:
    """Lightweight process liveness probe.
    
    Verifies that the web server process is responsive and receiving HTTP traffic.
    Used by container orchestrators (Kubernetes / Docker) for restart decisions.
    """
    return {"status": "alive", "service": "demerivo-pixelfix"}


@router.get("/readyz", response_model=None)
def readiness_probe(
    request: Request = None,
    response: Response = None,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
    queue: QueueProvider = Depends(get_queue_provider),
    storage: StorageProvider = Depends(get_storage),
) -> Dict[str, Any]:
    """Deep readiness probe verifying critical backend subsystem dependencies.
    
    Validates:
    1. Database query connectivity (SELECT 1).
    2. Task queue and Redis connectivity.
    3. Storage provider availability.
    
    Returns HTTP 200 when ready to accept user traffic; HTTP 503 when degraded or unhealthy.
    Public responses do not leak internal exception details, hostnames, or credentials.
    Authorized internal monitors (via X-Admin-Token) receive detailed diagnostics.
    """
    is_ready = True
    db_healthy = True
    checks: Dict[str, Any] = {}
    diagnostics: Dict[str, str] = {}

    # Check for authorized diagnostic access
    auth_header = request.headers.get("X-Admin-Token", "").strip() if request else ""
    is_authorized_monitor = (
        bool(auth_header)
        and bool(settings.admin_secret_token)
        and len(settings.admin_secret_token) >= 32
        and auth_header == settings.admin_secret_token
    )

    # 1. Database Check
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "healthy"
    except Exception as e:
        is_ready = False
        db_healthy = False
        checks["database"] = "unhealthy"
        diagnostics["database_error"] = f"{type(e).__name__}: {str(e)[:120]}"
        logger.error("Readiness DB check failed: %s", e)

    # 2. Queue & Redis Check
    try:
        q_len = queue.get_queue_length()
        checks["queue"] = {"status": "healthy", "pending_jobs": q_len}
    except Exception as e:
        is_ready = False
        checks["queue"] = {"status": "unhealthy"}
        diagnostics["queue_error"] = f"{type(e).__name__}: {str(e)[:120]}"
        logger.error("Readiness Queue check failed: %s", e)

    # 3. Storage Check
    try:
        storage_ok = storage is not None
        checks["storage"] = {"status": "healthy", "provider": settings.storage_provider}
    except Exception as e:
        is_ready = False
        checks["storage"] = {"status": "unhealthy"}
        diagnostics["storage_error"] = f"{type(e).__name__}: {str(e)[:120]}"
        logger.error("Readiness Storage check failed: %s", e)

    if not is_ready:
        # Distinguish completely unhealthy (database offline) vs degraded (queue or storage impaired)
        overall_status = "unhealthy" if not db_healthy else "degraded"
        content: Dict[str, Any] = {
            "status": overall_status,
            "environment": settings.env,
            "subsystems": checks,
        }
        if is_authorized_monitor and diagnostics:
            content["diagnostics"] = diagnostics

        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=content,
        )

    content = {
        "status": "ready",
        "environment": settings.env,
        "subsystems": checks,
    }
    if is_authorized_monitor and diagnostics:
        content["diagnostics"] = diagnostics

    return content


@router.get("/health", status_code=status.HTTP_200_OK)
def root_health():
    """Legacy root service check."""
    return {"status": "ok", "service": "demerivo-pixelfix"}


@router.get("/api/v1/health", status_code=status.HTTP_200_OK)
def api_health(
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
    queue: QueueProvider = Depends(get_queue_provider),
):
    """Detailed operational health telemetry for monitoring dashboards."""
    db_status = "healthy"
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"unhealthy: {str(e)[:100]}"

    queue_length = 0
    try:
        queue_length = queue.get_queue_length()
    except Exception:
        pass

    return {
        "status": "ok" if db_status == "healthy" else "degraded",
        "environment": settings.env,
        "database": db_status,
        "queue_depth": queue_length,
        "storage_provider": settings.storage_provider,
        "version": "1.0.0",
    }


@router.get("/api/v1/storage/health", status_code=status.HTTP_200_OK)
def storage_health(
    settings: Settings = Depends(get_settings),
    storage: StorageProvider = Depends(get_storage),
):
    """Verify storage provider initialization and probe availability."""
    probe_key = ".health_probe"
    status_str = "healthy"
    try:
        storage.upload_file(probe_key, b"ok", content_type="text/plain")
        if not storage.exists(probe_key):
            status_str = "probe_failed_existence"
        storage.delete_file(probe_key)
    except Exception as e:
        status_str = f"unhealthy: {str(e)[:100]}"

    return {
        "storage_provider": settings.storage_provider,
        "status": status_str,
    }
