"""SQLAlchemy data models for Demerivo PixelFix.

Includes Multi-Tenancy (Users, Tenants, Memberships), Credit Accounting
(Wallets, Credit Transactions), and Processing Jobs with Tenant Scoping.
"""

import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from demerivo.db.session import Base


def utcnow() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


class TenantRole(str, enum.Enum):
    """Role within a tenant / workspace."""
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class JobStatus(str, enum.Enum):
    """Processing job lifecycle states."""
    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OperationType(str, enum.Enum):
    """AI image enhancement operations."""
    UPSCALE_2X = "upscale_2x"
    UPSCALE_4X = "upscale_4x"
    ENHANCE = "enhance"
    BG_REMOVE = "bg_remove"
    DENOISE = "denoise"
    DEBLUR = "deblur"
    COLORIZE = "colorize"
    INPAINT = "inpaint"


class CreditTransactionType(str, enum.Enum):
    """Immutable credit ledger event types."""
    ALLOCATION = "allocation"      # Credits added (grant, plan replenishment)
    RESERVATION = "reservation"    # Credits held when a job is enqueued
    CONSUMPTION = "consumption"    # Credits permanently finalized upon job success
    REFUND = "refund"              # Credits returned upon job failure/cancellation
    CANCELLATION = "cancellation"  # Direct cancellation reversal


class UserModel(Base):
    """Customer profile and authentication identity synchronized with Supabase."""
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
        index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False
    )

    # Relationships
    memberships: Mapped[List["TenantMemberModel"]] = relationship(
        "TenantMemberModel", back_populates="user", cascade="all, delete-orphan"
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "avatar_url": self.avatar_url,
            "is_active": self.is_active,
            "email_verified": self.email_verified,
            "is_platform_admin": self.is_platform_admin,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TenantModel(Base):
    """Workspace / Organization unit for resource isolation and credit accounting."""
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    is_personal: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    owner_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False
    )

    # Relationships
    members: Mapped[List["TenantMemberModel"]] = relationship(
        "TenantMemberModel", back_populates="tenant", cascade="all, delete-orphan"
    )
    wallet: Mapped[Optional["WalletModel"]] = relationship(
        "WalletModel", back_populates="tenant", uselist=False, cascade="all, delete-orphan"
    )
    jobs: Mapped[List["JobModel"]] = relationship(
        "JobModel", back_populates="tenant", cascade="all, delete-orphan"
    )
    subscription: Mapped[Optional["SubscriptionModel"]] = relationship(
        "SubscriptionModel", back_populates="tenant", uselist=False, cascade="all, delete-orphan"
    )
    payments: Mapped[List["PaymentModel"]] = relationship(
        "PaymentModel", back_populates="tenant", cascade="all, delete-orphan"
    )
    invoices: Mapped[List["InvoiceModel"]] = relationship(
        "InvoiceModel", back_populates="tenant", cascade="all, delete-orphan"
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "is_personal": self.is_personal,
            "owner_id": self.owner_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TenantMemberModel(Base):
    """Association table linking Users to Tenants with specific role permissions."""
    __tablename__ = "tenant_members"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True
    )
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    role: Mapped[TenantRole] = mapped_column(
        Enum(TenantRole, name="tenant_role_enum", native_enum=False),
        default=TenantRole.MEMBER,
        nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False
    )

    # Relationships
    tenant: Mapped["TenantModel"] = relationship("TenantModel", back_populates="members")
    user: Mapped["UserModel"] = relationship("UserModel", back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_tenant_member"),
        Index("idx_tenant_member_role", "tenant_id", "role"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "role": self.role.value,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class WalletModel(Base):
    """Tenant credit ledger holding the current integer balance."""
    __tablename__ = "wallets"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True
    )
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True
    )
    balance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False
    )

    # Relationships
    tenant: Mapped["TenantModel"] = relationship("TenantModel", back_populates="wallet")
    transactions: Mapped[List["CreditTransactionModel"]] = relationship(
        "CreditTransactionModel", back_populates="wallet", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("balance >= 0", name="chk_wallet_balance_non_negative"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "balance": self.balance,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class CreditTransactionModel(Base):
    """Immutable audit trail for all credit additions, reservations, and refunds."""
    __tablename__ = "credit_transactions"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True
    )
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    wallet_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("wallets.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    job_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    transaction_type: Mapped[CreditTransactionType] = mapped_column(
        Enum(CreditTransactionType, name="credit_tx_type_enum", native_enum=False),
        nullable=False,
        index=True
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(128),
        unique=True,
        nullable=True,
        index=True
    )
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
        index=True
    )

    # Relationships
    wallet: Mapped["WalletModel"] = relationship("WalletModel", back_populates="transactions")
    job: Mapped[Optional["JobModel"]] = relationship("JobModel", back_populates="credit_transactions")

    __table_args__ = (
        Index("idx_credit_tx_tenant_created", "tenant_id", "created_at"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "wallet_id": self.wallet_id,
            "user_id": self.user_id,
            "job_id": self.job_id,
            "transaction_type": self.transaction_type.value,
            "amount": self.amount,
            "balance_after": self.balance_after,
            "idempotency_key": self.idempotency_key,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class JobModel(Base):
    """Multi-tenant processing job record tracking asynchronous AI task states."""
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True
    )

    # Multi-tenant scoping
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status_enum", native_enum=False),
        default=JobStatus.PENDING,
        nullable=False,
        index=True,
    )

    operation_type: Mapped[OperationType] = mapped_column(
        Enum(OperationType, name="operation_type_enum", native_enum=False),
        nullable=False,
        index=True,
    )

    input_key: Mapped[str] = mapped_column(String(512), nullable=False)
    output_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    parameters: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    credit_cost: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(128),
        unique=True,
        nullable=True,
        index=True
    )

    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
        index=True,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    tenant: Mapped["TenantModel"] = relationship("TenantModel", back_populates="jobs")
    credit_transactions: Mapped[List["CreditTransactionModel"]] = relationship(
        "CreditTransactionModel", back_populates="job"
    )

    # Composite indexes for querying tenant jobs by status and creation time
    __table_args__ = (
        Index("idx_jobs_status_created", "status", "created_at"),
        Index("idx_jobs_tenant_created", "tenant_id", "created_at"),
        Index("idx_jobs_tenant_status", "tenant_id", "status"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "status": self.status.value,
            "operation_type": self.operation_type.value,
            "input_key": self.input_key,
            "output_key": self.output_key,
            "progress": self.progress,
            "parameters": self.parameters,
            "credit_cost": self.credit_cost,
            "idempotency_key": self.idempotency_key,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


# =============================================================================
# Phase 4 — Subscription Billing & Payment Ledger Models
# =============================================================================

class SubscriptionStatus(str, enum.Enum):
    """Lifecycle states for customer subscriptions."""
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    PAUSED = "paused"


class PaymentStatus(str, enum.Enum):
    """Payment transaction states."""
    CREATED = "created"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    REFUNDED = "refunded"


class InvoiceStatus(str, enum.Enum):
    """Invoice payment states."""
    PAID = "paid"
    UNPAID = "unpaid"
    VOID = "void"


class PlanModel(Base):
    """Centralized subscription plan catalog."""
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True
    )
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    price_paise: Mapped[int] = mapped_column(Integer, nullable=False)  # 19900 = ₹199
    monthly_credits: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    features: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False
    )

    subscriptions: Mapped[List["SubscriptionModel"]] = relationship(
        "SubscriptionModel", back_populates="plan"
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "description": self.description,
            "price_paise": self.price_paise,
            "price_inr": self.price_paise / 100,
            "monthly_credits": self.monthly_credits,
            "currency": self.currency,
            "is_active": self.is_active,
            "features": self.features,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SubscriptionModel(Base):
    """Tenant subscription record tracking billing period, plan, and provider status."""
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True
    )
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True
    )
    plan_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("plans.id", ondelete="RESTRICT"),
        nullable=False,
        index=True
    )
    plan_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status_enum", native_enum=False),
        default=SubscriptionStatus.ACTIVE,
        nullable=False,
        index=True
    )

    provider: Mapped[str] = mapped_column(String(32), default="mock", nullable=False)
    provider_customer_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    provider_subscription_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)

    current_period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False
    )
    current_period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False
    )

    # Relationships
    tenant: Mapped["TenantModel"] = relationship("TenantModel", back_populates="subscription")
    plan: Mapped["PlanModel"] = relationship("PlanModel", back_populates="subscriptions")
    payments: Mapped[List["PaymentModel"]] = relationship(
        "PaymentModel", back_populates="subscription"
    )
    invoices: Mapped[List["InvoiceModel"]] = relationship(
        "InvoiceModel", back_populates="subscription"
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "plan_id": self.plan_id,
            "plan_code": self.plan_code,
            "status": self.status.value,
            "provider": self.provider,
            "provider_customer_id": self.provider_customer_id,
            "provider_subscription_id": self.provider_subscription_id,
            "current_period_start": self.current_period_start.isoformat() if self.current_period_start else None,
            "current_period_end": self.current_period_end.isoformat() if self.current_period_end else None,
            "cancel_at_period_end": self.cancel_at_period_end,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class PaymentModel(Base):
    """Payment record tracking one-off payments or recurring subscription charges."""
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True
    )
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    subscription_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("subscriptions.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    provider: Mapped[str] = mapped_column(String(32), default="mock", nullable=False)
    provider_payment_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    provider_order_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)

    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)

    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status_enum", native_enum=False),
        default=PaymentStatus.CREATED,
        nullable=False,
        index=True
    )

    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(128),
        unique=True,
        nullable=True,
        index=True
    )
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
        index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False
    )

    # Relationships
    tenant: Mapped["TenantModel"] = relationship("TenantModel", back_populates="payments")
    subscription: Mapped[Optional["SubscriptionModel"]] = relationship(
        "SubscriptionModel", back_populates="payments"
    )
    invoice: Mapped[Optional["InvoiceModel"]] = relationship(
        "InvoiceModel", back_populates="payment", uselist=False
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "subscription_id": self.subscription_id,
            "provider": self.provider,
            "provider_payment_id": self.provider_payment_id,
            "provider_order_id": self.provider_order_id,
            "amount_paise": self.amount_paise,
            "amount_inr": self.amount_paise / 100,
            "currency": self.currency,
            "status": self.status.value,
            "idempotency_key": self.idempotency_key,
            "error_code": self.error_code,
            "error_description": self.error_description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class InvoiceModel(Base):
    """Customer invoice record with line item totals and receipt generation."""
    __tablename__ = "invoices"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True
    )
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    subscription_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("subscriptions.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    payment_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("payments.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    invoice_number: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)

    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, name="invoice_status_enum", native_enum=False),
        default=InvoiceStatus.PAID,
        nullable=False
    )

    billing_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    billing_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pdf_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
        index=True
    )

    # Relationships
    tenant: Mapped["TenantModel"] = relationship("TenantModel", back_populates="invoices")
    subscription: Mapped[Optional["SubscriptionModel"]] = relationship(
        "SubscriptionModel", back_populates="invoices"
    )
    payment: Mapped[Optional["PaymentModel"]] = relationship(
        "PaymentModel", back_populates="invoice"
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "subscription_id": self.subscription_id,
            "payment_id": self.payment_id,
            "invoice_number": self.invoice_number,
            "amount_paise": self.amount_paise,
            "amount_inr": self.amount_paise / 100,
            "currency": self.currency,
            "status": self.status.value,
            "billing_period_start": self.billing_period_start.isoformat() if self.billing_period_start else None,
            "billing_period_end": self.billing_period_end.isoformat() if self.billing_period_end else None,
            "pdf_url": self.pdf_url,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# =============================================================================
# Phase 5 — Platform Administration & Audit Trail Models
# =============================================================================

class AdminAuditLogModel(Base):
    """Immutable audit log for privileged administrative actions."""
    __tablename__ = "admin_audit_logs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True
    )
    actor_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    actor_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    target_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="success", nullable=False)
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
        index=True
    )

    __table_args__ = (
        Index("idx_admin_audit_action_created", "action", "created_at"),
        Index("idx_admin_audit_target", "target_type", "target_id"),
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "actor_id": self.actor_id,
            "actor_email": self.actor_email,
            "action": self.action,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "reason": self.reason,
            "status": self.status,
            "metadata": self.metadata_json,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# =============================================================================
# Phase 6 — Email Verification Security & Infrastructure Monitoring Models
# =============================================================================

class EmailVerificationTokenModel(Base):
    """Cryptographically secure, single-use, account-bound, expiring email verification tokens."""
    __tablename__ = "email_verification_tokens"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    user: Mapped["UserModel"] = relationship("UserModel")

    __table_args__ = (
        Index("idx_email_token_lookup", "email", "used_at", "expires_at"),
    )


class WorkerHeartbeatModel(Base):
    """Active background worker telemetry, heartbeat timestamp, and state monitoring."""
    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    hostname: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    active_jobs_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_heartbeat: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


