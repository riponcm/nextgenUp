"""Tenant credit wallet management service and atomic balance operations."""

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from demerivo.db.models import (
    CreditTransactionModel,
    CreditTransactionType,
    TenantModel,
    WalletModel,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WalletError(Exception):
    """Base error for wallet operations."""
    pass


class WalletNotFoundError(WalletError):
    """Wallet does not exist for the specified tenant."""
    pass


class InsufficientCreditsError(WalletError):
    """Tenant wallet balance is lower than the required amount."""
    def __init__(self, message: str, required: int, available: int):
        super().__init__(message)
        self.required = required
        self.available = available


class DuplicateIdempotencyError(WalletError):
    """Transaction with this idempotency key already exists."""
    pass


class WalletService:
    """Manages tenant credit wallets with strict atomic updates and immutable auditing."""

    def get_wallet(self, tenant_id: str, db: Session) -> WalletModel:
        """Fetch tenant wallet or raise WalletNotFoundError."""
        wallet = db.query(WalletModel).filter_by(tenant_id=tenant_id).first()
        if not wallet:
            raise WalletNotFoundError(f"No credit wallet found for tenant '{tenant_id}'")
        return wallet

    def allocate_credits(
        self,
        tenant_id: str,
        amount: int,
        description: str,
        db: Session,
        user_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> CreditTransactionModel:
        """Add credits to a tenant wallet atomically and record an ALLOCATION audit entry."""
        if amount <= 0:
            raise ValueError("Allocation amount must be strictly positive")

        if idempotency_key:
            existing = db.query(CreditTransactionModel).filter_by(idempotency_key=idempotency_key).first()
            if existing:
                return existing

        wallet = self.get_wallet(tenant_id, db)

        # Atomic SQL update to prevent race conditions
        stmt = (
            update(WalletModel)
            .where(WalletModel.tenant_id == tenant_id)
            .values(balance=WalletModel.balance + amount, updated_at=utcnow())
        )
        db.execute(stmt)

        # Reload updated wallet balance
        db.refresh(wallet)

        tx = CreditTransactionModel(
            tenant_id=tenant_id,
            wallet_id=wallet.id,
            user_id=user_id,
            transaction_type=CreditTransactionType.ALLOCATION,
            amount=amount,
            balance_after=wallet.balance,
            idempotency_key=idempotency_key,
            description=description,
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)
        return tx

    def reserve_credits(
        self,
        tenant_id: str,
        amount: int,
        job_id: str,
        user_id: str,
        db: Session,
        description: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> CreditTransactionModel:
        """Atomically hold/reserve credits for an enqueued job.
        
        Guarantees that balance never drops below zero.
        """
        if amount <= 0:
            raise ValueError("Reservation amount must be strictly positive")

        if idempotency_key:
            existing = db.query(CreditTransactionModel).filter_by(idempotency_key=idempotency_key).first()
            if existing:
                return existing

        wallet = self.get_wallet(tenant_id, db)

        # Atomic conditional update enforcing balance >= amount
        stmt = (
            update(WalletModel)
            .where(
                WalletModel.tenant_id == tenant_id,
                WalletModel.balance >= amount
            )
            .values(
                balance=WalletModel.balance - amount,
                updated_at=utcnow()
            )
        )
        result = db.execute(stmt)

        if result.rowcount == 0:
            # Refresh to get exact current balance for error reporting
            db.refresh(wallet)
            raise InsufficientCreditsError(
                f"Insufficient credits: required {amount}, available {wallet.balance}",
                required=amount,
                available=wallet.balance,
            )

        db.refresh(wallet)

        tx = CreditTransactionModel(
            tenant_id=tenant_id,
            wallet_id=wallet.id,
            user_id=user_id,
            job_id=job_id,
            transaction_type=CreditTransactionType.RESERVATION,
            amount=-amount,
            balance_after=wallet.balance,
            idempotency_key=idempotency_key,
            description=description or f"Reserved {amount} credits for job {job_id}",
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)
        return tx

    def finalize_consumption(
        self,
        job_id: str,
        db: Session,
    ) -> Optional[CreditTransactionModel]:
        """Record final credit consumption when a job completes successfully.
        
        Credits were already deducted during reservation, so this logs the permanent consumption event.
        """
        # Find the reservation transaction for this job
        res_tx = (
            db.query(CreditTransactionModel)
            .filter_by(job_id=job_id, transaction_type=CreditTransactionType.RESERVATION)
            .first()
        )
        if not res_tx:
            return None

        # Check if already consumed
        existing_cons = (
            db.query(CreditTransactionModel)
            .filter_by(job_id=job_id, transaction_type=CreditTransactionType.CONSUMPTION)
            .first()
        )
        if existing_cons:
            return existing_cons

        wallet = db.query(WalletModel).filter_by(id=res_tx.wallet_id).first()
        if not wallet:
            return None

        tx = CreditTransactionModel(
            tenant_id=res_tx.tenant_id,
            wallet_id=wallet.id,
            user_id=res_tx.user_id,
            job_id=job_id,
            transaction_type=CreditTransactionType.CONSUMPTION,
            amount=0,  # Already deducted in reservation; balance is unchanged
            balance_after=wallet.balance,
            description=f"Consumed {abs(res_tx.amount)} credits for completed job {job_id}",
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)
        return tx

    def refund_credits(
        self,
        tenant_id: str,
        amount: int,
        job_id: str,
        user_id: str,
        reason: str,
        db: Session,
    ) -> CreditTransactionModel:
        """Atomically refund reserved credits when a job fails or is cancelled."""
        if amount <= 0:
            raise ValueError("Refund amount must be strictly positive")

        wallet = self.get_wallet(tenant_id, db)

        # Atomic SQL addition
        stmt = (
            update(WalletModel)
            .where(WalletModel.tenant_id == tenant_id)
            .values(balance=WalletModel.balance + amount, updated_at=utcnow())
        )
        db.execute(stmt)
        db.refresh(wallet)

        tx = CreditTransactionModel(
            tenant_id=tenant_id,
            wallet_id=wallet.id,
            user_id=user_id,
            job_id=job_id,
            transaction_type=CreditTransactionType.REFUND,
            amount=amount,
            balance_after=wallet.balance,
            description=f"Refunded {amount} credits: {reason}",
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)
        return tx

    def get_transactions(
        self,
        tenant_id: str,
        db: Session,
        limit: int = 50,
        offset: int = 0,
    ) -> List[CreditTransactionModel]:
        """Fetch audit transaction ledger for a tenant, sorted most recent first."""
        return (
            db.query(CreditTransactionModel)
            .filter_by(tenant_id=tenant_id)
            .order_by(CreditTransactionModel.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )


_wallet_service_instance: Optional[WalletService] = None


def get_wallet_service() -> WalletService:
    """Return singleton WalletService."""
    global _wallet_service_instance
    if _wallet_service_instance is None:
        _wallet_service_instance = WalletService()
    return _wallet_service_instance
