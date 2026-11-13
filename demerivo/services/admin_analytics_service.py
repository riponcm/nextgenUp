"""Business analytics and SQL aggregation service for the Demerivo Admin Dashboard.

Executes efficient database-level aggregate queries across Users, Tenants, Jobs,
Subscriptions, Payments, and Wallets without in-memory loading.
"""

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import func, case
from sqlalchemy.orm import Session

from demerivo.config import Settings
from demerivo.db.models import (
    CreditTransactionModel,
    CreditTransactionType,
    JobModel,
    JobStatus,
    PaymentModel,
    PaymentStatus,
    PlanModel,
    SubscriptionModel,
    SubscriptionStatus,
    TenantMemberModel,
    TenantModel,
    UserModel,
    WalletModel,
    utcnow,
)


def resolve_date_range(
    range_preset: Optional[str] = "30d",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Tuple[datetime, datetime, str]:
    """Parse and normalize date range presets into timezone-aware UTC datetime bounds."""
    now = utcnow()
    if range_preset == "today":
        start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        return start, now, "today"
    elif range_preset == "7d":
        return now - timedelta(days=7), now, "7d"
    elif range_preset == "90d":
        return now - timedelta(days=90), now, "90d"
    elif range_preset == "custom" and start_date and end_date:
        try:
            s = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
            e = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            if s.tzinfo is None:
                s = s.replace(tzinfo=timezone.utc)
            if e.tzinfo is None:
                e = e.replace(tzinfo=timezone.utc)
            return s, e, "custom"
        except Exception:
            pass  # Fall back to 30d
    return now - timedelta(days=30), now, "30d"


class AdminAnalyticsService:
    """Encapsulates aggregate metrics computation for the administration dashboard."""

    @staticmethod
    def get_overview_metrics(
        db: Session,
        range_preset: str = "30d",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compute top-level KPI metrics within the specified date window."""
        start_dt, end_dt, resolved_preset = resolve_date_range(range_preset, start_date, end_date)

        # 1. Total & Active Users
        total_users = db.query(func.count(UserModel.id)).scalar() or 0
        active_users = db.query(func.count(UserModel.id)).filter(UserModel.is_active == True).scalar() or 0

        # 2. Total Workspaces & Active Subscriptions
        total_workspaces = db.query(func.count(TenantModel.id)).scalar() or 0
        active_subscriptions = db.query(func.count(SubscriptionModel.id)).filter(
            SubscriptionModel.status == SubscriptionStatus.ACTIVE
        ).scalar() or 0

        # 3. Processing Jobs Breakdown in Window
        job_stats = db.query(
            func.count(JobModel.id).label("total_jobs"),
            func.sum(case((JobModel.status == JobStatus.COMPLETED, 1), else_=0)).label("successful_jobs"),
            func.sum(case((JobModel.status == JobStatus.FAILED, 1), else_=0)).label("failed_jobs"),
            func.sum(
                case(
                    (JobModel.status.in_([JobStatus.PENDING, JobStatus.QUEUED, JobStatus.PROCESSING]), 1),
                    else_=0
                )
            ).label("pending_jobs"),
        ).filter(JobModel.created_at >= start_dt, JobModel.created_at <= end_dt).one()

        total_jobs = job_stats.total_jobs or 0
        successful_jobs = job_stats.successful_jobs or 0
        failed_jobs = job_stats.failed_jobs or 0
        pending_jobs = job_stats.pending_jobs or 0

        # 4. Credit Consumption in Window
        credit_consumed = db.query(func.coalesce(func.sum(CreditTransactionModel.amount), 0)).filter(
            CreditTransactionModel.transaction_type == CreditTransactionType.CONSUMPTION,
            CreditTransactionModel.created_at >= start_dt,
            CreditTransactionModel.created_at <= end_dt,
        ).scalar() or 0

        # 5. Financial Totals in Window
        payment_stats = db.query(
            func.sum(case((PaymentModel.status == PaymentStatus.CAPTURED, PaymentModel.amount_paise), else_=0)).label("captured_paise"),
            func.sum(case((PaymentModel.status == PaymentStatus.REFUNDED, PaymentModel.amount_paise), else_=0)).label("refunded_paise"),
        ).filter(PaymentModel.created_at >= start_dt, PaymentModel.created_at <= end_dt).one()

        captured_paise = payment_stats.captured_paise or 0
        refunded_paise = payment_stats.refunded_paise or 0

        return {
            "date_range": {
                "preset": resolved_preset,
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
            },
            "users": {
                "total": total_users,
                "active": active_users,
                "suspended": total_users - active_users,
            },
            "workspaces": {
                "total": total_workspaces,
            },
            "subscriptions": {
                "active": active_subscriptions,
            },
            "jobs": {
                "total": total_jobs,
                "successful": successful_jobs,
                "failed": failed_jobs,
                "pending_and_processing": pending_jobs,
                "success_rate_percent": round((successful_jobs / total_jobs * 100), 1) if total_jobs > 0 else 0.0,
            },
            "credits": {
                "consumed_in_window": credit_consumed,
            },
            "financials": {
                "captured_amount_paise": captured_paise,
                "captured_amount_inr": captured_paise / 100,
                "refunded_amount_paise": refunded_paise,
                "refunded_amount_inr": refunded_paise / 100,
                "net_amount_paise": captured_paise - refunded_paise,
                "net_amount_inr": (captured_paise - refunded_paise) / 100,
                "currency": "INR",
            },
        }

    @staticmethod
    def get_revenue_analytics(
        db: Session,
        range_preset: str = "30d",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compute financial analytics distinguishing volume, gross collected, refunds, and MRR."""
        start_dt, end_dt, resolved_preset = resolve_date_range(range_preset, start_date, end_date)

        # Payment metrics within date range
        payment_query = db.query(
            func.count(PaymentModel.id).label("total_transactions"),
            func.sum(case((PaymentModel.status == PaymentStatus.CAPTURED, PaymentModel.amount_paise), else_=0)).label("gross_paise"),
            func.sum(case((PaymentModel.status == PaymentStatus.REFUNDED, PaymentModel.amount_paise), else_=0)).label("refund_paise"),
            func.sum(case((PaymentModel.status == PaymentStatus.FAILED, 1), else_=0)).label("failed_count"),
            func.sum(case((PaymentModel.status == PaymentStatus.CAPTURED, 1), else_=0)).label("captured_count"),
        ).filter(PaymentModel.created_at >= start_dt, PaymentModel.created_at <= end_dt).one()

        total_tx = payment_query.total_transactions or 0
        gross_paise = payment_query.gross_paise or 0
        refund_paise = payment_query.refund_paise or 0
        net_paise = gross_paise - refund_paise
        failed_count = payment_query.failed_count or 0
        captured_count = payment_query.captured_count or 0

        # Calculate Monthly Recurring Revenue (MRR) from currently active subscriptions
        active_sub_records = (
            db.query(SubscriptionModel.plan_code, PlanModel.price_paise, func.count(SubscriptionModel.id))
            .join(PlanModel, SubscriptionModel.plan_id == PlanModel.id)
            .filter(SubscriptionModel.status == SubscriptionStatus.ACTIVE)
            .group_by(SubscriptionModel.plan_code, PlanModel.price_paise)
            .all()
        )

        mrr_paise = 0
        revenue_by_plan: Dict[str, Dict[str, Any]] = {}
        for plan_code, price_paise, sub_count in active_sub_records:
            plan_mrr_paise = price_paise * sub_count
            mrr_paise += plan_mrr_paise
            revenue_by_plan[plan_code] = {
                "active_subscriptions": sub_count,
                "price_inr": price_paise / 100,
                "estimated_monthly_revenue_inr": plan_mrr_paise / 100,
            }

        return {
            "date_range": {
                "preset": resolved_preset,
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
            },
            "financial_summary": {
                "total_transactions": total_tx,
                "successful_transactions": captured_count,
                "failed_transactions": failed_count,
                "gross_collected_paise": gross_paise,
                "gross_collected_inr": gross_paise / 100,
                "refunds_paise": refund_paise,
                "refunds_inr": refund_paise / 100,
                "net_collected_paise": net_paise,
                "net_collected_inr": net_paise / 100,
                "currency": "INR",
            },
            "mrr": {
                "estimated_monthly_recurring_revenue_paise": mrr_paise,
                "estimated_monthly_recurring_revenue_inr": mrr_paise / 100,
                "currency": "INR",
                "breakdown_by_plan": revenue_by_plan,
            },
            "disclaimer": (
                "Gross and Net collected amounts represent actual processed payment transactions. "
                "Payment volume represents total transactions processed and is not net business profit. "
                "MRR is estimated based on current active paid subscriptions."
            ),
        }

    @staticmethod
    def get_credit_analytics(db: Session) -> Dict[str, Any]:
        """Compute aggregate credit circulation, consumption, and grant metrics."""
        # Circulation in wallets
        wallet_stats = db.query(
            func.count(WalletModel.id).label("wallet_count"),
            func.coalesce(func.sum(WalletModel.balance), 0).label("total_in_circulation"),
            func.coalesce(func.avg(WalletModel.balance), 0).label("avg_balance"),
            func.coalesce(func.max(WalletModel.balance), 0).label("max_balance"),
        ).one()

        # Cumulative transaction aggregates
        tx_stats = (
            db.query(
                CreditTransactionModel.transaction_type,
                func.count(CreditTransactionModel.id).label("tx_count"),
                func.coalesce(func.sum(CreditTransactionModel.amount), 0).label("total_credits"),
            )
            .group_by(CreditTransactionModel.transaction_type)
            .all()
        )

        breakdown: Dict[str, Dict[str, Any]] = {}
        total_granted = 0
        total_consumed = 0
        total_refunded = 0

        for tx_type, count, credits in tx_stats:
            type_val = tx_type.value if hasattr(tx_type, "value") else str(tx_type)
            breakdown[type_val] = {
                "transaction_count": count,
                "total_credits": credits,
            }
            if type_val == CreditTransactionType.ALLOCATION.value:
                total_granted = credits
            elif type_val == CreditTransactionType.CONSUMPTION.value:
                total_consumed = credits
            elif type_val in (CreditTransactionType.REFUND.value, CreditTransactionType.CANCELLATION.value):
                total_refunded += credits

        # Administrative adjustments specifically
        admin_adjustments = db.query(
            func.count(CreditTransactionModel.id).label("adj_count"),
            func.coalesce(func.sum(CreditTransactionModel.amount), 0).label("adj_credits"),
        ).filter(CreditTransactionModel.description.like("[ADMIN ADJUSTMENT%")).one()

        return {
            "circulation": {
                "total_wallets": wallet_stats.wallet_count or 0,
                "credits_in_circulation": wallet_stats.total_in_circulation or 0,
                "average_wallet_balance": round(float(wallet_stats.avg_balance or 0), 1),
                "maximum_wallet_balance": wallet_stats.max_balance or 0,
            },
            "cumulative_totals": {
                "total_credits_granted": total_granted,
                "total_credits_consumed": total_consumed,
                "total_credits_refunded": total_refunded,
            },
            "administrative_adjustments": {
                "adjustment_count": admin_adjustments.adj_count or 0,
                "total_adjusted_credits": admin_adjustments.adj_credits or 0,
            },
            "transactions_by_type": breakdown,
            "accounting_architecture_disclosure": (
                "Demerivo PixelFix currently utilizes a single unified integer balance per tenant wallet. "
                "Monthly subscription allocations, top-up purchases, and promotional grants pool into the same balance "
                "and do not segregate into separate expiration buckets."
            ),
        }

    @staticmethod
    def get_system_health(db: Session, settings: Settings) -> Dict[str, Any]:
        """Perform non-destructive connectivity and status probes for core infrastructure components."""
        now = utcnow()
        components: Dict[str, Any] = {}

        # 1. FastAPI Application
        components["fastapi"] = {
            "status": "healthy",
            "environment": settings.env,
            "debug": settings.debug,
            "api_version": "0.2.0",
        }

        # 2. Database Connectivity
        db_start = time.perf_counter()
        try:
            db.execute(func.now())
            db_latency_ms = round((time.perf_counter() - db_start) * 1000, 2)
            components["database"] = {
                "status": "healthy",
                "engine": "postgresql" if "postgresql" in settings.database_url else "sqlite",
                "latency_ms": db_latency_ms,
            }
        except Exception as e:
            components["database"] = {
                "status": "unhealthy",
                "error": str(e),
            }

        # 3. Redis / Rate Limiter Connectivity
        try:
            import redis
            r = redis.Redis.from_url(settings.redis_url, socket_timeout=1.0)
            r.ping()
            components["redis"] = {
                "status": "connected",
                "queue_backend": settings.queue_backend,
                "rate_limiter_mode": "distributed_redis",
            }
        except Exception:
            components["redis"] = {
                "status": "offline",
                "queue_backend": settings.queue_backend,
                "rate_limiter_mode": "in_memory_fallback",
                "note": "Redis service is unreachable. Application is running safely on thread-safe local in-memory fallback.",
            }

        # 4. Storage Backend
        if settings.storage_provider == "local":
            import os
            dir_exists = os.path.isdir(settings.local_storage_dir)
            components["storage"] = {
                "status": "healthy" if dir_exists else "degraded",
                "provider": "local",
                "directory": settings.local_storage_dir,
            }
        else:
            components["storage"] = {
                "status": "configured" if settings.s3_endpoint_url else "unconfigured",
                "provider": "s3_compatible",
                "bucket": settings.s3_bucket_name,
            }

        # 5. Workers and Queue Depth
        pending_jobs = db.query(func.count(JobModel.id)).filter(
            JobModel.status.in_([JobStatus.PENDING, JobStatus.QUEUED])
        ).scalar() or 0

        processing_jobs = db.query(func.count(JobModel.id)).filter(
            JobModel.status == JobStatus.PROCESSING
        ).scalar() or 0

        components["queue"] = {
            "status": "healthy" if pending_jobs < 50 else "congested",
            "pending_jobs": pending_jobs,
            "processing_jobs": processing_jobs,
            "max_concurrent_workers": settings.max_concurrent_workers,
        }

        # 6. Recent Failures (Last 24h)
        one_day_ago = now - timedelta(days=1)
        recent_total = db.query(func.count(JobModel.id)).filter(JobModel.created_at >= one_day_ago).scalar() or 0
        recent_failures = db.query(func.count(JobModel.id)).filter(
            JobModel.status == JobStatus.FAILED,
            JobModel.created_at >= one_day_ago,
        ).scalar() or 0

        failure_rate = round((recent_failures / recent_total * 100), 2) if recent_total > 0 else 0.0

        components["job_failure_telemetry_24h"] = {
            "total_jobs_24h": recent_total,
            "failed_jobs_24h": recent_failures,
            "failure_rate_percent": failure_rate,
            "status": "healthy" if failure_rate < 5.0 else ("warning" if failure_rate < 20.0 else "critical"),
        }

        return {
            "timestamp": now.isoformat(),
            "overall_health": "degraded" if components["redis"]["status"] == "offline" else "healthy",
            "components": components,
        }
