"""Job lifecycle API endpoints: Creation, polling, cancellation, download, capabilities, and deletion."""

from datetime import datetime, timezone
from typing import List, Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from demerivo.ai.registry import OperationCapability, get_operation_registry
from demerivo.auth.dependencies import get_current_tenant, get_current_user
from demerivo.billing import BillingService, get_billing_service
from demerivo.credits.wallet_service import InsufficientCreditsError
from demerivo.db.models import JobModel, JobStatus, OperationType, TenantModel, TenantRole, UserModel
from demerivo.db.session import get_db
from demerivo.jobs.schemas import (
    JobCancelResponse,
    JobCreate,
    JobDownloadResponse,
    JobResponse,
)
from demerivo.jobs.service import (
    InvalidJobStateTransition,
    JobNotFoundError,
    JobService,
)
from demerivo.security.rate_limiter import rate_limit_jobs
from demerivo.storage.base import StorageProvider
from demerivo.storage.factory import get_storage
from demerivo.workers.manager import WorkerManager, get_worker_manager

router = APIRouter(prefix="/api/v1/jobs", tags=["Jobs & Processing"])


def serialize_job(job: JobModel, storage: StorageProvider) -> JobResponse:
    """Serialize JobModel to JobResponse, dynamically populating presigned download result_url."""
    resp = JobResponse.model_validate(job)
    if job.status == JobStatus.COMPLETED and job.output_key and storage.exists(job.output_key):
        resp.result_url = storage.get_presigned_download_url(job.output_key, expires_in=3600)
    return resp


@router.get("/capabilities", response_model=List[OperationCapability], status_code=status.HTTP_200_OK)
def get_capabilities():
    """List all supported AI image enhancement capabilities, credit costs, and commercial licensing status."""
    registry = get_operation_registry()
    return list(registry.values())


@router.post("", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(rate_limit_jobs)])
def create_job(
    request: JobCreate,
    current_user: UserModel = Depends(get_current_user),
    tenant_context: Tuple[TenantModel, TenantRole] = Depends(get_current_tenant),
    db: Session = Depends(get_db),
    storage: StorageProvider = Depends(get_storage),
    worker_manager: WorkerManager = Depends(get_worker_manager),
    billing_service: BillingService = Depends(get_billing_service),
):
    """Submit a new AI photo processing job with atomic credit reservation and persistent queue dispatch."""
    if billing_service is None or hasattr(billing_service, "dependency"):
        billing_service = get_billing_service()
    if worker_manager is None or hasattr(worker_manager, "dependency"):
        worker_manager = get_worker_manager()

    tenant, _ = tenant_context

    # 1. Enforce tenant ownership of input object key (IDOR Protection)
    expected_prefix = f"uploads/{tenant.id}/"
    parts = [p for p in request.input_key.split("/") if p]
    if len(parts) >= 3 and parts[0] == "uploads" and parts[1] != tenant.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied: input object belongs to workspace '{parts[1]}', not active workspace ('{expected_prefix}').",
        )

    # 2. Reject 8K resolution parameter requests
    params = request.parameters or {}
    if params.get("is_8k") or params.get("target_resolution") == "8k" or params.get("scale") == 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="8K resolution is not supported on current infrastructure. Maximum supported resolution is 4K (4096x4096px).",
        )

    # 3. Plan verification & enforcement
    sub = billing_service.get_or_create_tenant_subscription(db, tenant.id)
    now = datetime.now(timezone.utc)
    period_end = sub.current_period_end
    if period_end and period_end.tzinfo is None:
        period_end = period_end.replace(tzinfo=timezone.utc)
    if sub.cancel_at_period_end and period_end and period_end < now:
        sub = billing_service.cancel_tenant_subscription(db, tenant.id, immediately=True)

    plan_code = sub.plan_code.lower()
    if plan_code == "free":
        # Free plan allows upscale_2x, upscale_4x, enhance, bg_remove. Colorize and inpaint require paid tiers.
        paid_only_ops = {OperationType.COLORIZE, OperationType.INPAINT}
        if request.operation_type in paid_only_ops:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation '{request.operation_type.value}' requires a paid subscription plan (Pro or Studio).",
            )

    # Verify input object exists in storage
    if not storage.exists(request.input_key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Input object '{request.input_key}' was not found in storage. Upload file before creating job.",
        )

    try:
        job = JobService.create_job(
            db=db,
            request=request,
            tenant_id=tenant.id,
            user_id=current_user.id,
        )
    except InsufficientCreditsError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=str(e),
        )

    # Only dispatch if pending
    if job.status == JobStatus.PENDING:
        worker_manager.enqueue_job(job.id)

    return serialize_job(job, storage)


@router.get("", response_model=List[JobResponse], status_code=status.HTTP_200_OK)
def list_jobs(
    tenant_context: Tuple[TenantModel, TenantRole] = Depends(get_current_tenant),
    status_filter: Optional[JobStatus] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    storage: StorageProvider = Depends(get_storage),
):
    """List jobs for the active workspace, enforcing strict tenant isolation."""
    tenant, _ = tenant_context
    jobs = JobService.list_jobs(
        db=db,
        tenant_id=tenant.id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    return [serialize_job(j, storage) for j in jobs]


@router.get("/{job_id}", response_model=JobResponse, status_code=status.HTTP_200_OK)
def get_job_status(
    job_id: str,
    tenant_context: Tuple[TenantModel, TenantRole] = Depends(get_current_tenant),
    db: Session = Depends(get_db),
    storage: StorageProvider = Depends(get_storage),
):
    """Retrieve the current progress and status of a processing job for the active tenant."""
    tenant, _ = tenant_context
    try:
        job = JobService.get_job(db, job_id, tenant_id=tenant.id)
        return serialize_job(job, storage)
    except JobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job with ID '{job_id}' was not found in active workspace.",
        )


@router.post("/{job_id}/cancel", response_model=JobCancelResponse, status_code=status.HTTP_200_OK)
def cancel_job(
    job_id: str,
    current_user: UserModel = Depends(get_current_user),
    tenant_context: Tuple[TenantModel, TenantRole] = Depends(get_current_tenant),
    db: Session = Depends(get_db),
):
    """Request cancellation of an active or pending processing job and refund reserved credits."""
    tenant, _ = tenant_context
    try:
        job = JobService.cancel_job(
            db=db,
            job_id=job_id,
            tenant_id=tenant.id,
            user_id=current_user.id,
        )
        return JobCancelResponse(
            id=job.id,
            status=job.status,
            credits_refunded=job.credit_cost,
            message="Job successfully cancelled and reserved credits refunded.",
        )
    except JobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job with ID '{job_id}' was not found in active workspace.",
        )
    except InvalidJobStateTransition as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/{job_id}/download", response_model=JobDownloadResponse, status_code=status.HTTP_200_OK)
def get_job_download_url(
    job_id: str,
    tenant_context: Tuple[TenantModel, TenantRole] = Depends(get_current_tenant),
    db: Session = Depends(get_db),
    storage: StorageProvider = Depends(get_storage),
):
    """Generate a secure, time-limited presigned URL to download the processed result."""
    tenant, _ = tenant_context
    try:
        job = JobService.get_job(db, job_id, tenant_id=tenant.id)
    except JobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job with ID '{job_id}' was not found in active workspace.",
        )

    if job.status != JobStatus.COMPLETED or not job.output_key:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job is not completed yet (current status: '{job.status.value}').",
        )

    if not storage.exists(job.output_key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Processed output image file was not found in storage.",
        )

    download_url = storage.get_presigned_download_url(job.output_key, expires_in=900)
    return JobDownloadResponse(
        job_id=job.id,
        download_url=download_url,
        expires_in=900,
    )


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    job_id: str,
    tenant_context: Tuple[TenantModel, TenantRole] = Depends(get_current_tenant),
    db: Session = Depends(get_db),
    storage: StorageProvider = Depends(get_storage),
):
    """Delete a job record and its associated storage assets within the active workspace."""
    tenant, _ = tenant_context
    try:
        job = JobService.get_job(db, job_id, tenant_id=tenant.id)
    except JobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job with ID '{job_id}' was not found in active workspace.",
        )

    # Clean up storage artifacts if present
    if job.input_key and storage.exists(job.input_key):
        try:
            storage.delete_file(job.input_key)
        except Exception:
            pass

    if job.output_key and storage.exists(job.output_key):
        try:
            storage.delete_file(job.output_key)
        except Exception:
            pass

    JobService.delete_job(db, job_id, tenant_id=tenant.id)
    return None
