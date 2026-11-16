"""Job management service handling multi-tenant persistence, state transitions, and credit lifecycle."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from demerivo.credits.pricing import get_pricing_policy
from demerivo.credits.wallet_service import get_wallet_service, InsufficientCreditsError
from demerivo.db.models import JobModel, JobStatus
from demerivo.jobs.schemas import JobCreate


class InvalidJobStateTransition(Exception):
    """Raised when an illegal status change is attempted on a job."""
    pass


class JobNotFoundError(Exception):
    """Raised when a requested job ID is not in the database or belongs to another tenant."""
    pass


VALID_TRANSITIONS = {
    JobStatus.PENDING: {JobStatus.QUEUED, JobStatus.PROCESSING, JobStatus.CANCELLED, JobStatus.FAILED},
    JobStatus.QUEUED: {JobStatus.PROCESSING, JobStatus.CANCELLED, JobStatus.FAILED},
    JobStatus.PROCESSING: {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED},
    JobStatus.COMPLETED: set(),  # Terminal state
    JobStatus.FAILED: set(),     # Terminal state
    JobStatus.CANCELLED: set(),  # Terminal state
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobService:
    """Service encapsulating database transactions for processing job lifecycles and credit sync."""

    @staticmethod
    def create_job(
        db: Session,
        request: JobCreate,
        tenant_id: str,
        user_id: str,
    ) -> JobModel:
        """Atomically calculate cost, reserve credits, and persist a new tenant processing job."""
        # Check idempotency first to avoid double charges on network retry
        if request.idempotency_key:
            existing_job = (
                db.query(JobModel)
                .filter_by(tenant_id=tenant_id, idempotency_key=request.idempotency_key)
                .first()
            )
            if existing_job:
                return existing_job

        # Cross-tenant IDOR protection: if the upload key is scoped to a workspace (uploads/<tenant_id>/...),
        # strictly enforce that it matches the caller's active tenant_id.
        parts = [p for p in request.input_key.split("/") if p]
        if len(parts) >= 3 and parts[0] == "uploads":
            upload_owner = parts[1]
            if upload_owner != tenant_id:
                from demerivo.tenants.service import TenantPermissionDeniedError
                raise TenantPermissionDeniedError(
                    f"Cross-tenant access violation: input object '{request.input_key}' does not belong to tenant '{tenant_id}'."
                )

        pricing = get_pricing_policy()
        required_credits = pricing.calculate_cost(request.operation_type, request.parameters)

        job_id = str(uuid.uuid4())
        wallet_service = get_wallet_service()

        # Reserve credits atomically before job creation
        if required_credits > 0:
            reserve_key = f"job_res_{request.idempotency_key}" if request.idempotency_key else None
            wallet_service.reserve_credits(
                tenant_id=tenant_id,
                amount=required_credits,
                job_id=job_id,
                user_id=user_id,
                db=db,
                description=f"Reserved {required_credits} credits for {request.operation_type.value}",
                idempotency_key=reserve_key,
            )

        job = JobModel(
            id=job_id,
            tenant_id=tenant_id,
            user_id=user_id,
            operation_type=request.operation_type,
            input_key=request.input_key,
            parameters=request.parameters or {},
            credit_cost=required_credits,
            idempotency_key=request.idempotency_key,
            status=JobStatus.PENDING,
            progress=0,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job

    @staticmethod
    def get_job(db: Session, job_id: str, tenant_id: Optional[str] = None) -> JobModel:
        """Fetch job enforcing strict tenant isolation."""
        query = db.query(JobModel).filter(JobModel.id == job_id)
        if tenant_id is not None:
            query = query.filter(JobModel.tenant_id == tenant_id)

        job = query.first()
        if not job:
            raise JobNotFoundError(f"Job with ID '{job_id}' was not found.")
        return job

    @staticmethod
    def list_jobs(
        db: Session,
        tenant_id: str,
        user_id: Optional[str] = None,
        status: Optional[JobStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[JobModel]:
        """List tenant jobs, optionally filtered by user or status."""
        query = db.query(JobModel).filter(JobModel.tenant_id == tenant_id)
        if user_id:
            query = query.filter(JobModel.user_id == user_id)
        if status:
            query = query.filter(JobModel.status == status)

        return query.order_by(JobModel.created_at.desc()).offset(offset).limit(limit).all()

    @staticmethod
    def update_status(
        db: Session,
        job_id: str,
        target_status: JobStatus,
        progress: Optional[int] = None,
        output_key: Optional[str] = None,
        error_message: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> JobModel:
        """Update job lifecycle status enforcing state machine and handling credit finalization/refunds."""
        job = JobService.get_job(db, job_id, tenant_id=tenant_id)
        status_changed = (target_status != job.status)

        if status_changed:
            allowed = VALID_TRANSITIONS.get(job.status, set())
            if target_status not in allowed:
                raise InvalidJobStateTransition(
                    f"Cannot transition job '{job_id}' from '{job.status.value}' to '{target_status.value}'."
                )
            job.status = target_status

        if progress is not None:
            job.progress = max(0, min(100, progress))

        if output_key is not None:
            job.output_key = output_key

        if error_message is not None:
            job.error_message = error_message

        if target_status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            job.completed_at = utcnow()

        # Handle credit accounting side-effects only when status actually changes
        if status_changed:
            wallet_service = get_wallet_service()
            if target_status == JobStatus.COMPLETED:
                wallet_service.finalize_consumption(job.id, db)
            elif target_status in (JobStatus.FAILED, JobStatus.CANCELLED):
                # Prefer ledger as authoritative source for total reserved credits (accounting for add-ons)
                total_reserved = wallet_service.get_job_reservation_total(job.tenant_id, job.id, db)
                refund_amount = total_reserved if total_reserved > 0 else job.credit_cost
                if refund_amount > 0:
                    wallet_service.refund_credits(
                        tenant_id=job.tenant_id,
                        amount=refund_amount,
                        job_id=job.id,
                        user_id=job.user_id,
                        reason=f"Refund for {target_status.value} job: {error_message or 'cancelled'}",
                        db=db,
                    )

        db.commit()
        db.refresh(job)
        return job

    @staticmethod
    def cancel_job(
        db: Session,
        job_id: str,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> JobModel:
        """Cancel a pending or queued job and refund reserved credits."""
        return JobService.update_status(
            db=db,
            job_id=job_id,
            target_status=JobStatus.CANCELLED,
            error_message="Job cancelled by user request.",
            tenant_id=tenant_id,
        )

    @staticmethod
    def delete_job(db: Session, job_id: str, tenant_id: Optional[str] = None) -> bool:
        """Delete job record enforcing tenant isolation."""
        job = JobService.get_job(db, job_id, tenant_id=tenant_id)
        db.delete(job)
        db.commit()
        return True
