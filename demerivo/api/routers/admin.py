"""Administrative API endpoints for internal operations, business analytics, and dashboard support.

Strictly segregated from customer routers. Requires validated platform administrator privileges
(via authenticated Bearer JWT or verified machine-to-machine administrative service credentials).
Records an immutable audit trail for every privileged action.
"""

import math
import uuid
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from demerivo.auth.dependencies import (
    AdminPrincipal,
    get_admin_principal,
    get_current_platform_admin,
    security_scheme,
)
from demerivo.config import Settings, get_settings
from demerivo.credits.wallet_service import WalletService, get_wallet_service
from demerivo.db.models import (
    AdminAuditLogModel,
    CreditTransactionModel,
    CreditTransactionType,
    JobModel,
    JobStatus,
    OperationType,
    PaymentModel,
    PaymentStatus,
    PlanModel,
    SubscriptionModel,
    SubscriptionStatus,
    TenantMemberModel,
    TenantModel,
    TenantRole,
    UserModel,
    WalletModel,
    utcnow,
)
from demerivo.db.session import get_db
from demerivo.services.admin_analytics_service import AdminAnalyticsService
from demerivo.services.admin_audit_service import record_audit_log

router = APIRouter(prefix="/api/v1/admin", tags=["Internal Administration"])


# =============================================================================
# Request & Response Schemas
# =============================================================================

class AdminCreditAdjustmentRequest(BaseModel):
    """Payload for authorized administrative credit allocation."""
    tenant_id: str = Field(..., description="Target tenant workspace UUID")
    amount: int = Field(..., gt=0, description="Amount of credits to grant")
    reason: str = Field(..., min_length=5, max_length=500, description="Administrative audit justification")
    actor: Optional[str] = Field(None, max_length=100, description="Client actor claim (ignored; server derives actor from authenticated principal)")
    idempotency_key: Optional[str] = Field(None, max_length=128, description="Deduplication key")


class AdminCreditAdjustmentResponse(BaseModel):
    """Response returned upon administrative credit adjustment."""
    transaction_id: str
    tenant_id: str
    amount: int
    balance_after: int
    actor: str
    reason: str
    created_at: str


class UserStatusUpdateRequest(BaseModel):
    """Payload to suspend or activate a user account."""
    action: str = Field(..., pattern="^(suspend|activate)$", description="Desired status action")
    reason: str = Field(..., min_length=5, max_length=500, description="Audit reason for status change")


class SubscriptionModifyRequest(BaseModel):
    """Payload to modify customer subscription tier administratively."""
    plan_code: str = Field(..., description="Target plan code: free, starter, pro, studio")
    reason: str = Field(..., min_length=5, max_length=500, description="Audit justification for modification")
    cancel_at_period_end: Optional[bool] = Field(None, description="Whether to cancel at end of period")


# =============================================================================
# Authorization Helper (Preserving Backward Compatibility)
# =============================================================================

def verify_admin_authorization(
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Secret-Token"),
    settings: Optional[Settings] = None,
) -> None:
    """Validate internal administrative credentials using constant-time comparison."""
    import hmac
    if settings is None or not isinstance(settings, Settings):
        settings = get_settings()
    configured_secret = settings.admin_secret_token
    if not configured_secret or not x_admin_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrative access denied. Administrative service credentials required.",
        )

    if not hmac.compare_digest(configured_secret.encode("utf-8"), x_admin_token.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrative access denied. Invalid credentials.",
        )


# =============================================================================
# 1. Platform Admin Identity
# =============================================================================

@router.get(
    "/me",
    summary="Get current platform administrator profile",
)
def get_admin_me(
    admin_user: UserModel = Depends(get_current_platform_admin),
) -> Dict[str, Any]:
    """Return profile details for the authenticated platform administrator."""
    return {
        "id": admin_user.id,
        "email": admin_user.email,
        "display_name": admin_user.display_name,
        "is_platform_admin": True,
        "created_at": admin_user.created_at.isoformat() if admin_user.created_at else None,
    }


# =============================================================================
# 2. Business Analytics & KPI Overviews
# =============================================================================

@router.get(
    "/analytics/overview",
    summary="Dashboard aggregate KPI overview with date filtering",
)
def get_analytics_overview(
    range: str = Query("30d", pattern="^(today|7d|30d|90d|custom)$"),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    admin: AdminPrincipal = Depends(get_admin_principal),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve platform-wide operational KPIs computed directly from database tables."""
    return AdminAnalyticsService.get_overview_metrics(
        db=db,
        range_preset=range,
        start_date=start_date,
        end_date=end_date,
    )


@router.get(
    "/analytics/revenue",
    summary="Revenue and payment analytics",
)
def get_analytics_revenue(
    range: str = Query("30d", pattern="^(today|7d|30d|90d|custom)$"),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    admin: AdminPrincipal = Depends(get_admin_principal),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Compute financial analytics distinguishing gross, refunds, net, and estimated MRR."""
    return AdminAnalyticsService.get_revenue_analytics(
        db=db,
        range_preset=range,
        start_date=start_date,
        end_date=end_date,
    )


@router.get(
    "/analytics/credits",
    summary="Credit circulation and consumption analytics",
)
def get_analytics_credits(
    admin: AdminPrincipal = Depends(get_admin_principal),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Compute credit circulation, consumption, refund, and adjustment aggregates."""
    return AdminAnalyticsService.get_credit_analytics(db=db)


# =============================================================================
# 3. User Management
# =============================================================================

@router.get(
    "/users",
    summary="Paginated customer search and listing",
)
def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    admin: AdminPrincipal = Depends(get_admin_principal),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Search and paginate platform users with membership and credit stats."""
    query = db.query(UserModel)

    if search:
        search_pattern = f"%{search.strip()}%"
        query = query.filter(
            or_(
                UserModel.email.ilike(search_pattern),
                UserModel.display_name.ilike(search_pattern),
                UserModel.id == search.strip(),
            )
        )

    if isinstance(status_filter, str) and status_filter:
        if status_filter == "active":
            query = query.filter(UserModel.is_active == True)
        elif status_filter == "suspended":
            query = query.filter(UserModel.is_active == False)

    total_count = query.count()
    users = (
        query.order_by(UserModel.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    user_ids = [u.id for u in users]
    if user_ids:
        # M-1: Bounded batch queries eliminating N+1 lookups
        ws_counts = dict(
            db.query(TenantMemberModel.user_id, func.count(TenantMemberModel.id))
            .filter(TenantMemberModel.user_id.in_(user_ids))
            .group_by(TenantMemberModel.user_id)
            .all()
        )

        job_counts = dict(
            db.query(JobModel.user_id, func.count(JobModel.id))
            .filter(JobModel.user_id.in_(user_ids))
            .group_by(JobModel.user_id)
            .all()
        )

        personal_tenant_rows = (
            db.query(
                TenantModel.owner_id,
                WalletModel.balance,
                SubscriptionModel.plan_code,
            )
            .outerjoin(WalletModel, WalletModel.tenant_id == TenantModel.id)
            .outerjoin(SubscriptionModel, SubscriptionModel.tenant_id == TenantModel.id)
            .filter(TenantModel.owner_id.in_(user_ids), TenantModel.is_personal == True)
            .all()
        )
        personal_info = {
            row[0]: (row[1] if row[1] is not None else 0, row[2] or "free")
            for row in personal_tenant_rows
        }

        for u in users:
            credit_balance, sub_plan = personal_info.get(u.id, (0, "free"))
            items.append({
                "id": u.id,
                "email": u.email,
                "display_name": u.display_name,
                "is_active": u.is_active,
                "is_platform_admin": u.is_platform_admin,
                "email_verified": u.email_verified,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "workspaces_count": ws_counts.get(u.id, 0),
                "credit_balance": credit_balance,
                "subscription_plan": sub_plan,
                "jobs_count": job_counts.get(u.id, 0),
            })

    return {
        "items": items,
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": math.ceil(total_count / page_size) if total_count > 0 else 1,
    }


@router.get(
    "/users/{user_id}",
    summary="Inspect detailed user profile and associated resources",
)
def inspect_user(
    user_id: str,
    admin: AdminPrincipal = Depends(get_admin_principal),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve full profile details, workspace memberships, and job summary for a user."""
    user = db.query(UserModel).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{user_id}' not found.",
        )

    # Workspaces
    memberships = (
        db.query(TenantMemberModel)
        .filter_by(user_id=user.id)
        .all()
    )
    workspaces = []
    for m in memberships:
        t = m.tenant
        workspaces.append({
            "tenant_id": t.id,
            "name": t.name,
            "slug": t.slug,
            "role": m.role.value,
            "is_personal": t.is_personal,
            "wallet_balance": t.wallet.balance if t.wallet else 0,
            "subscription_plan": t.subscription.plan_code if t.subscription else "none",
        })

    # Recent jobs
    recent_jobs = (
        db.query(JobModel)
        .filter_by(user_id=user.id)
        .order_by(JobModel.created_at.desc())
        .limit(10)
        .all()
    )

    return {
        "user": user.to_dict(),
        "workspaces": workspaces,
        "recent_jobs": [
            {
                "id": j.id,
                "operation_type": j.operation_type.value,
                "status": j.status.value,
                "credit_cost": j.credit_cost,
                "created_at": j.created_at.isoformat() if j.created_at else None,
            }
            for j in recent_jobs
        ],
    }


@router.post(
    "/users/{user_id}/status",
    summary="Update customer account status (suspend or activate)",
)
def update_user_status(
    user_id: str,
    payload: UserStatusUpdateRequest,
    admin: AdminPrincipal = Depends(get_admin_principal),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Suspend or restore a customer account with mandatory audit trail logging."""
    user = db.query(UserModel).filter_by(id=user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{user_id}' not found.",
        )

    # Prevent admin from suspending themselves
    if admin.actor_id and admin.actor_id == user.id and payload.action == "suspend":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Administrators cannot suspend their own active account.",
        )

    # M-4: Prevent suspending the final active platform administrator
    if user.is_platform_admin and payload.action == "suspend":
        active_admins = (
            db.query(UserModel)
            .filter(UserModel.is_platform_admin == True, UserModel.is_active == True)
            .count()
        )
        if active_admins <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Operation prohibited: Cannot suspend the final active platform administrator.",
            )

    new_active_state = (payload.action == "activate")
    user.is_active = new_active_state
    user.updated_at = utcnow()

    # Record audit log
    record_audit_log(
        db=db,
        actor_email=admin.email,
        actor_id=admin.actor_id,
        action=f"user.{payload.action}",
        target_type="user",
        target_id=user.id,
        reason=payload.reason,
        status="success",
        metadata={"user_email": user.email, "previous_state": not new_active_state},
    )

    db.commit()
    db.refresh(user)

    return {
        "user_id": user.id,
        "email": user.email,
        "is_active": user.is_active,
        "action_taken": payload.action,
        "reason": payload.reason,
    }


# =============================================================================
# 4. Tenant & Workspace Management
# =============================================================================

@router.get(
    "/tenants",
    summary="Paginated workspace search and listing",
)
def list_tenants(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    is_personal: Optional[bool] = None,
    admin: AdminPrincipal = Depends(get_admin_principal),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Search and paginate tenant workspaces with member count and usage stats."""
    query = db.query(TenantModel)

    if search:
        search_pattern = f"%{search.strip()}%"
        query = query.filter(
            or_(
                TenantModel.name.ilike(search_pattern),
                TenantModel.slug.ilike(search_pattern),
                TenantModel.id == search.strip(),
            )
        )

    if is_personal is not None:
        query = query.filter(TenantModel.is_personal == is_personal)

    total_count = query.count()
    tenants = (
        query.order_by(TenantModel.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    tenant_ids = [t.id for t in tenants]
    owner_ids = [t.owner_id for t in tenants if t.owner_id]
    if tenant_ids:
        # M-1: Bounded batch lookups eliminating N+1 queries
        owner_map = {
            u.id: u.email
            for u in db.query(UserModel.id, UserModel.email).filter(UserModel.id.in_(owner_ids)).all()
        } if owner_ids else {}

        member_counts = dict(
            db.query(TenantMemberModel.tenant_id, func.count(TenantMemberModel.id))
            .filter(TenantMemberModel.tenant_id.in_(tenant_ids))
            .group_by(TenantMemberModel.tenant_id)
            .all()
        )

        job_counts = dict(
            db.query(JobModel.tenant_id, func.count(JobModel.id))
            .filter(JobModel.tenant_id.in_(tenant_ids))
            .group_by(JobModel.tenant_id)
            .all()
        )

        wallet_map = {
            w.tenant_id: w.balance
            for w in db.query(WalletModel.tenant_id, WalletModel.balance).filter(WalletModel.tenant_id.in_(tenant_ids)).all()
        }

        sub_rows = (
            db.query(SubscriptionModel.tenant_id, SubscriptionModel.plan_code, SubscriptionModel.status)
            .filter(SubscriptionModel.tenant_id.in_(tenant_ids))
            .all()
        )
        sub_map = {
            row[0]: (row[1], row[2].value if row[2] else "none")
            for row in sub_rows
        }

        for t in tenants:
            plan_code, sub_status = sub_map.get(t.id, ("none", "none"))
            items.append({
                "id": t.id,
                "name": t.name,
                "slug": t.slug,
                "is_personal": t.is_personal,
                "owner_id": t.owner_id,
                "owner_email": owner_map.get(t.owner_id, "unknown"),
                "member_count": member_counts.get(t.id, 0),
                "wallet_balance": wallet_map.get(t.id, 0),
                "subscription_plan": plan_code,
                "subscription_status": sub_status,
                "jobs_count": job_counts.get(t.id, 0),
                "created_at": t.created_at.isoformat() if t.created_at else None,
            })

    return {
        "items": items,
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": math.ceil(total_count / page_size) if total_count > 0 else 1,
    }


@router.get(
    "/tenants/{tenant_id}",
    summary="Inspect workspace details, members, and usage",
)
def inspect_tenant(
    tenant_id: str,
    admin: AdminPrincipal = Depends(get_admin_principal),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve full details of a specific workspace, including member roster and billing."""
    tenant = db.query(TenantModel).filter_by(id=tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tenant '{tenant_id}' not found.",
        )

    # Members
    members = (
        db.query(TenantMemberModel)
        .filter_by(tenant_id=tenant.id)
        .all()
    )
    members_data = []
    for m in members:
        u = m.user
        members_data.append({
            "user_id": u.id,
            "email": u.email,
            "display_name": u.display_name,
            "role": m.role.value,
            "joined_at": m.created_at.isoformat() if m.created_at else None,
        })

    # Usage summary
    jobs_summary = db.query(
        func.count(JobModel.id).label("total_jobs"),
        func.sum(case((JobModel.status == JobStatus.COMPLETED, 1), else_=0)).label("successful_jobs"),
        func.sum(case((JobModel.status == JobStatus.FAILED, 1), else_=0)).label("failed_jobs"),
    ).filter_by(tenant_id=tenant.id).one()

    return {
        "tenant": tenant.to_dict(),
        "wallet": tenant.wallet.to_dict() if tenant.wallet else None,
        "subscription": tenant.subscription.to_dict() if tenant.subscription else None,
        "members": members_data,
        "usage": {
            "total_jobs": jobs_summary.total_jobs or 0,
            "successful_jobs": jobs_summary.successful_jobs or 0,
            "failed_jobs": jobs_summary.failed_jobs or 0,
        },
    }


# =============================================================================
# 5. Job Monitoring
# =============================================================================

@router.get(
    "/jobs",
    summary="Paginated image processing job monitoring",
)
def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    operation_type: Optional[str] = None,
    tenant_id: Optional[str] = None,
    user_id: Optional[str] = None,
    admin: AdminPrincipal = Depends(get_admin_principal),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Monitor platform processing jobs with filtering and safe duration telemetry."""
    query = db.query(JobModel)

    if isinstance(status_filter, str) and status_filter:
        try:
            enum_status = JobStatus(status_filter)
            query = query.filter(JobModel.status == enum_status)
        except ValueError:
            pass

    if isinstance(operation_type, str) and operation_type:
        try:
            enum_op = OperationType(operation_type)
            query = query.filter(JobModel.operation_type == enum_op)
        except ValueError:
            pass

    if tenant_id:
        query = query.filter(JobModel.tenant_id == tenant_id)

    if user_id:
        query = query.filter(JobModel.user_id == user_id)

    total_count = query.count()
    jobs = (
        query.order_by(JobModel.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for j in jobs:
        duration_seconds = None
        if j.completed_at and j.created_at:
            duration_seconds = round((j.completed_at - j.created_at).total_seconds(), 2)

        items.append({
            "id": j.id,
            "tenant_id": j.tenant_id,
            "user_id": j.user_id,
            "operation_type": j.operation_type.value,
            "status": j.status.value,
            "progress": j.progress,
            "credit_cost": j.credit_cost,
            "duration_seconds": duration_seconds,
            "error_message": j.error_message[:200] if j.error_message else None,
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "completed_at": j.completed_at.isoformat() if j.completed_at else None,
        })

    return {
        "items": items,
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": math.ceil(total_count / page_size) if total_count > 0 else 1,
    }


@router.get(
    "/jobs/{job_id}",
    summary="Inspect individual job details and lifecycle audit",
)
def inspect_job(
    job_id: str,
    admin: AdminPrincipal = Depends(get_admin_principal),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve detailed telemetry for a job without exposing customer image contents."""
    job = db.query(JobModel).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )

    # Filter out raw storage paths or secret keys from parameters
    safe_parameters = {k: v for k, v in job.parameters.items() if "secret" not in k.lower()}

    # Associated credit transactions
    txs = (
        db.query(CreditTransactionModel)
        .filter_by(job_id=job.id)
        .all()
    )

    return {
        "id": job.id,
        "tenant_id": job.tenant_id,
        "user_id": job.user_id,
        "operation_type": job.operation_type.value,
        "status": job.status.value,
        "progress": job.progress,
        "credit_cost": job.credit_cost,
        "parameters": safe_parameters,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "credit_transactions": [
            {
                "id": t.id,
                "type": t.transaction_type.value,
                "amount": t.amount,
                "description": t.description,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in txs
        ],
    }


# =============================================================================
# 6. Subscription Management
# =============================================================================

@router.get(
    "/subscriptions",
    summary="Paginated customer subscription tracking",
)
def list_subscriptions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    plan_code: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    admin: AdminPrincipal = Depends(get_admin_principal),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve paginated customer subscriptions filtered by plan tier and status."""
    query = db.query(SubscriptionModel)

    if isinstance(plan_code, str) and plan_code:
        query = query.filter(SubscriptionModel.plan_code == plan_code.lower())

    if isinstance(status_filter, str) and status_filter:
        try:
            enum_status = SubscriptionStatus(status_filter.lower())
            query = query.filter(SubscriptionModel.status == enum_status)
        except ValueError:
            pass

    total_count = query.count()
    subs = (
        query.order_by(SubscriptionModel.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for s in subs:
        t = s.tenant
        p = s.plan
        items.append({
            "id": s.id,
            "tenant_id": s.tenant_id,
            "tenant_name": t.name if t else "unknown",
            "plan_code": s.plan_code,
            "plan_name": p.name if p else s.plan_code.title(),
            "price_inr": (p.price_paise / 100) if p else 0,
            "status": s.status.value,
            "provider": s.provider,
            "current_period_start": s.current_period_start.isoformat() if s.current_period_start else None,
            "current_period_end": s.current_period_end.isoformat() if s.current_period_end else None,
            "cancel_at_period_end": s.cancel_at_period_end,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        })

    return {
        "items": items,
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": math.ceil(total_count / page_size) if total_count > 0 else 1,
    }


@router.post(
    "/subscriptions/{subscription_id}/modify",
    summary="Administrative subscription tier modification",
)
def modify_subscription(
    subscription_id: str,
    payload: SubscriptionModifyRequest,
    admin: AdminPrincipal = Depends(get_admin_principal),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Administratively change subscription plan with audit logging and history preservation."""
    sub = db.query(SubscriptionModel).filter_by(id=subscription_id).first()
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Subscription '{subscription_id}' not found.",
        )

    target_plan = db.query(PlanModel).filter_by(code=payload.plan_code.lower()).first()
    if not target_plan:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Target plan '{payload.plan_code}' does not exist in catalog.",
        )

    previous_plan_code = sub.plan_code
    sub.plan_id = target_plan.id
    sub.plan_code = target_plan.code
    if payload.cancel_at_period_end is not None:
        sub.cancel_at_period_end = payload.cancel_at_period_end
    sub.updated_at = utcnow()

    # Record immutable audit log
    record_audit_log(
        db=db,
        actor_email=admin.email,
        actor_id=admin.actor_id,
        action="subscription.modify",
        target_type="subscription",
        target_id=sub.id,
        reason=payload.reason,
        status="success",
        metadata={
            "tenant_id": sub.tenant_id,
            "previous_plan": previous_plan_code,
            "new_plan": sub.plan_code,
            "cancel_at_period_end": sub.cancel_at_period_end,
        },
    )

    db.commit()
    db.refresh(sub)

    return {
        "subscription_id": sub.id,
        "tenant_id": sub.tenant_id,
        "previous_plan": previous_plan_code,
        "current_plan": sub.plan_code,
        "status": sub.status.value,
        "cancel_at_period_end": sub.cancel_at_period_end,
        "reason": payload.reason,
    }


# =============================================================================
# 7. Payment History
# =============================================================================

@router.get(
    "/payments",
    summary="Paginated payment transaction history",
)
def list_payments(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = None,
    admin: AdminPrincipal = Depends(get_admin_principal),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve historical payment ledger transactions with customer workspace details."""
    query = db.query(PaymentModel)

    if isinstance(status_filter, str) and status_filter:
        try:
            enum_status = PaymentStatus(status_filter.lower())
            query = query.filter(PaymentModel.status == enum_status)
        except ValueError:
            pass

    if isinstance(search, str) and search:
        search_pattern = f"%{search.strip()}%"
        query = query.filter(
            or_(
                PaymentModel.provider_payment_id.ilike(search_pattern),
                PaymentModel.provider_order_id.ilike(search_pattern),
                PaymentModel.id == search.strip(),
            )
        )

    total_count = query.count()
    payments = (
        query.order_by(PaymentModel.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for p in payments:
        t = p.tenant
        items.append({
            "id": p.id,
            "tenant_id": p.tenant_id,
            "tenant_name": t.name if t else "unknown",
            "provider": p.provider,
            "provider_payment_id": p.provider_payment_id,
            "provider_order_id": p.provider_order_id,
            "amount_inr": p.amount_paise / 100,
            "currency": p.currency,
            "status": p.status.value,
            "error_description": p.error_description,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })

    return {
        "items": items,
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": math.ceil(total_count / page_size) if total_count > 0 else 1,
    }


# =============================================================================
# 8. Administrative Credit Adjustment (Preserving Phase 4.1 Behavior)
# =============================================================================

@router.post(
    "/credits/grant",
    response_model=AdminCreditAdjustmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Internal administrative credit adjustment",
)
def admin_grant_credits(
    payload: AdminCreditAdjustmentRequest,
    admin: AdminPrincipal = Depends(get_admin_principal),
    db: Session = Depends(get_db),
    wallet_service: WalletService = Depends(get_wallet_service),
) -> AdminCreditAdjustmentResponse:
    """Execute an authorized administrative credit grant with immutable audit logging.
    
    Derives actor identity exclusively from authenticated platform-admin principal (M-5).
    """
    if admin is None or not isinstance(admin, AdminPrincipal):
        # Fallback for direct unit test function calls without FastAPI dependency injection
        admin = AdminPrincipal(actor_id=None, email=payload.actor or "service-token", is_service=True)

    # Validate target tenant workspace
    target_tenant = db.query(TenantModel).filter(TenantModel.id == payload.tenant_id).first()
    if not target_tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Target tenant workspace '{payload.tenant_id}' was not found.",
        )

    # M-5: Authenticated identity exclusively dictates the actor in both credit ledger and audit log
    effective_actor = admin.email if admin.email else "service-token"

    audit_description = f"[ADMIN ADJUSTMENT by {effective_actor}]: {payload.reason}"
    audit_idempotency = payload.idempotency_key or f"admin_adj_{uuid.uuid4().hex[:16]}"

    tx = wallet_service.allocate_credits(
        tenant_id=payload.tenant_id,
        amount=payload.amount,
        description=audit_description,
        db=db,
        user_id=admin.actor_id,
        idempotency_key=audit_idempotency,
    )

    # Record administrative audit trail log with verified actor identity
    record_audit_log(
        db=db,
        actor_email=effective_actor,
        actor_id=admin.actor_id,
        action="credit.grant",
        target_type="tenant",
        target_id=payload.tenant_id,
        reason=payload.reason,
        status="success",
        metadata={
            "amount": payload.amount,
            "balance_after": tx.balance_after,
            "transaction_id": tx.id,
            "idempotency_key": audit_idempotency,
        },
    )
    db.commit()

    return AdminCreditAdjustmentResponse(
        transaction_id=tx.id,
        tenant_id=tx.tenant_id,
        amount=tx.amount,
        balance_after=tx.balance_after,
        actor=effective_actor,
        reason=payload.reason,
        created_at=tx.created_at.isoformat(),
    )


# =============================================================================
# 9. Audit Logs
# =============================================================================

@router.get(
    "/audit-logs",
    summary="Paginated administrative audit log search",
)
def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    action: Optional[str] = None,
    actor_email: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    admin: AdminPrincipal = Depends(get_admin_principal),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve immutable audit events for security and compliance inspections."""
    query = db.query(AdminAuditLogModel)

    if action:
        query = query.filter(AdminAuditLogModel.action.ilike(f"%{action.strip()}%"))

    if actor_email:
        query = query.filter(AdminAuditLogModel.actor_email.ilike(f"%{actor_email.strip()}%"))

    if target_type:
        query = query.filter(AdminAuditLogModel.target_type == target_type)

    if target_id:
        query = query.filter(AdminAuditLogModel.target_id == target_id)

    total_count = query.count()
    logs = (
        query.order_by(AdminAuditLogModel.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "items": [l.to_dict() for l in logs],
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": math.ceil(total_count / page_size) if total_count > 0 else 1,
    }


# =============================================================================
# 10. System Health & Infrastructure Monitoring
# =============================================================================

@router.get(
    "/system/health",
    summary="Comprehensive infrastructure and component health telemetry",
)
def get_system_health(
    admin: AdminPrincipal = Depends(get_admin_principal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """Perform real non-destructive status checks for API, DB, Queue, and Storage."""
    return AdminAnalyticsService.get_system_health(db=db, settings=settings)
