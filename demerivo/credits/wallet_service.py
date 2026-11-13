"""Tenant credit wallet management service and atomic balance operations."""

from datetime import datetime, timezone
import re
import threading
from typing import Any, List, Optional

from sqlalchemy import text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from demerivo.db.models import (
    CreditTransactionModel,
    CreditTransactionType,
    TenantModel,
    WalletModel,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


_reservation_mutex = threading.Lock()


class WalletError(Exception):
    """Base error for wallet operations."""
    pass


class InvalidJobIdError(ValueError, WalletError):
    """Raised when job_id is None, empty, whitespace-only, or not a string."""
    pass


class SchemaValidationError(WalletError):
    """Raised when database schema or index constraints do not match requirements."""
    pass


def validate_job_id(job_id: Any) -> str:
    """Validate that job_id is a non-empty, non-whitespace string."""
    if job_id is None:
        raise InvalidJobIdError("job_id cannot be None")
    if not isinstance(job_id, str):
        raise InvalidJobIdError(f"job_id must be a string, got {type(job_id).__name__}")
    cleaned = job_id.strip()
    if not cleaned:
        raise InvalidJobIdError("job_id cannot be empty or whitespace")
    return cleaned


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


class InvalidReservationError(WalletError):
    """Credit reservation does not exist, is insufficient, or belongs to another tenant/wallet."""
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
                if existing.tenant_id != tenant_id:
                    raise DuplicateIdempotencyError(f"Idempotency key '{idempotency_key}' already used by another tenant")
                return existing

        wallet = self.get_wallet(tenant_id, db)

        # Atomic SQL update to prevent race conditions
        stmt = (
            update(WalletModel)
            .where(
                WalletModel.id == wallet.id,
                WalletModel.tenant_id == tenant_id,
            )
            .values(balance=WalletModel.balance + amount, updated_at=utcnow())
        )
        result = db.execute(stmt)
        if result.rowcount != 1:
            db.rollback()
            raise WalletError(f"Expected exactly 1 wallet updated for tenant '{tenant_id}', got {result.rowcount}")

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
        wallet_id: Optional[str] = None,
    ) -> CreditTransactionModel:
        """Atomically hold/reserve credits for an enqueued job.

        Guarantees that balance never drops below zero and enforces that all
        reservations for a single job must originate from the same wallet.
        """
        # Validate job_id before database queries, advisory lock, or balance changes
        cleaned_job_id = validate_job_id(job_id)

        if amount <= 0:
            raise ValueError("Reservation amount must be strictly positive")

        if idempotency_key:
            existing = db.query(CreditTransactionModel).filter_by(idempotency_key=idempotency_key).first()
            if existing:
                if existing.tenant_id != tenant_id:
                    raise DuplicateIdempotencyError(f"Idempotency key '{idempotency_key}' already used by another tenant")
                return existing

        with _reservation_mutex:
            bind = db.get_bind()
            if bind.dialect.name == "postgresql":
                # PostgreSQL advisory xact lock per tenant+job ensures concurrent processes serialize
                db.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                    {"key": f"reserve_{tenant_id}_{cleaned_job_id}"},
                )

            # Resolve target wallet
            if wallet_id is not None:
                wallet = (
                    db.query(WalletModel)
                    .filter(
                        WalletModel.id == wallet_id,
                        WalletModel.tenant_id == tenant_id,
                    )
                    .first()
                )
                if not wallet:
                    raise WalletNotFoundError(f"Wallet '{wallet_id}' not found for tenant '{tenant_id}'")
            else:
                wallet = self.get_wallet(tenant_id, db)

            # Enforce single wallet per job: check existing reservations for this job
            existing_reservations = (
                db.query(CreditTransactionModel)
                .filter(
                    CreditTransactionModel.tenant_id == tenant_id,
                    CreditTransactionModel.job_id == cleaned_job_id,
                    CreditTransactionModel.transaction_type.in_([
                        CreditTransactionType.RESERVATION,
                        "reservation",
                        "RESERVATION",
                    ]),
                )
                .all()
            )
            if existing_reservations:
                for res in existing_reservations:
                    if res.wallet_id != wallet.id:
                        raise InvalidReservationError(
                            f"Job '{cleaned_job_id}' already has reservations associated with wallet '{res.wallet_id}'. "
                            f"Cross-wallet reservations for the same job are not permitted."
                        )

            # Atomic conditional update enforcing balance >= amount
            stmt = (
                update(WalletModel)
                .where(
                    WalletModel.id == wallet.id,
                    WalletModel.tenant_id == tenant_id,
                    WalletModel.balance >= amount,
                )
                .values(
                    balance=WalletModel.balance - amount,
                    updated_at=utcnow(),
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
                job_id=cleaned_job_id,
                transaction_type=CreditTransactionType.RESERVATION,
                amount=-amount,
                balance_after=wallet.balance,
                idempotency_key=idempotency_key,
                description=description or f"Reserved {amount} credits for job {cleaned_job_id}",
            )
            db.add(tx)
            db.commit()
            db.refresh(tx)
            return tx

    def get_job_reservation_total(self, tenant_id: str, job_id: str, db: Session) -> int:
        """Calculate the total active reserved credits for a job from the immutable ledger."""
        cleaned_job_id = validate_job_id(job_id)
        reservations = (
            db.query(CreditTransactionModel)
            .filter(
                CreditTransactionModel.tenant_id == tenant_id,
                CreditTransactionModel.job_id == cleaned_job_id,
                CreditTransactionModel.transaction_type.in_([
                    CreditTransactionType.RESERVATION,
                    "reservation",
                    "RESERVATION",
                ]),
            )
            .all()
        )
        return sum(abs(r.amount) for r in reservations)

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

        wallet = (
            db.query(WalletModel)
            .filter_by(id=res_tx.wallet_id, tenant_id=res_tx.tenant_id)
            .first()
        )
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
        amount: Optional[int],
        job_id: str,
        user_id: str,
        reason: str,
        db: Session,
        idempotency_key: Optional[str] = None,
        wallet_id: Optional[str] = None,
    ) -> CreditTransactionModel:
        """Atomically refund reserved credits when a job fails or is cancelled."""
        cleaned_job_id = validate_job_id(job_id)

        if amount is not None and amount <= 0:
            raise ValueError("Refund amount must be strictly positive")

        key = idempotency_key or f"refund_{cleaned_job_id}"

        # 1. Fetch ALL reservation transactions for this job and tenant
        # Never select an arbitrary reservation using .first()
        reservations = (
            db.query(CreditTransactionModel)
            .filter(
                CreditTransactionModel.tenant_id == tenant_id,
                CreditTransactionModel.job_id == cleaned_job_id,
                CreditTransactionModel.transaction_type.in_([
                    CreditTransactionType.RESERVATION,
                    "reservation",
                    "RESERVATION",
                ]),
            )
            .order_by(CreditTransactionModel.created_at.asc())
            .all()
        )
        if not reservations:
            raise InvalidReservationError(
                f"No credit reservation found for job '{cleaned_job_id}' and tenant '{tenant_id}'."
            )

        # Calculate total reserved across all reservations for this job
        total_reserved = sum(abs(r.amount) for r in reservations)

        # Calculate reservations grouped by wallet
        wallet_reserved: dict[str, int] = {}
        for r in reservations:
            wallet_reserved[r.wallet_id] = wallet_reserved.get(r.wallet_id, 0) + abs(r.amount)

        # 2. Determine target wallet and validate wallet ownership
        if wallet_id is not None:
            if wallet_id not in wallet_reserved:
                raise InvalidReservationError(
                    f"Specified wallet '{wallet_id}' is not associated with any reservation for job '{cleaned_job_id}'."
                )
            target_wallet_id = wallet_id
            target_reserved_amount = wallet_reserved[target_wallet_id]
        else:
            if len(wallet_reserved) == 1:
                target_wallet_id = next(iter(wallet_reserved.keys()))
                target_reserved_amount = wallet_reserved[target_wallet_id]
            else:
                # Ambiguous wallet ownership across multiple wallets
                raise WalletError(
                    f"Ambiguous wallet ownership: job '{cleaned_job_id}' has reservations across multiple wallets "
                    f"({list(wallet_reserved.keys())}). You must specify the target wallet_id."
                )

        # Verify the target wallet exists and belongs to this tenant
        wallet = (
            db.query(WalletModel)
            .filter(
                WalletModel.id == target_wallet_id,
                WalletModel.tenant_id == tenant_id,
            )
            .first()
        )
        if not wallet:
            raise WalletNotFoundError(
                f"Wallet '{target_wallet_id}' not found for tenant '{tenant_id}'."
            )

        # 3. Enforce refund amount policy
        # If amount is not specified, calculate total eligible reservation amount from ledger
        if amount is None:
            refund_amount = target_reserved_amount
        else:
            if amount < target_reserved_amount:
                raise InvalidReservationError(
                    f"Partial refunds are not permitted: requested refund of {amount} credits, "
                    f"but refund amount must equal total eligible reservation ({target_reserved_amount}) for job '{cleaned_job_id}'."
                )
            if amount > target_reserved_amount:
                raise InvalidReservationError(
                    f"Over-refund not permitted: requested refund of {amount} credits exceeds "
                    f"total eligible reservation ({target_reserved_amount}) for job '{cleaned_job_id}'."
                )
            refund_amount = amount

        # 4. Idempotency checks (tenant-scoped)
        # Check by explicit idempotency key if provided
        if idempotency_key:
            existing_key_tx = (
                db.query(CreditTransactionModel)
                .filter(CreditTransactionModel.idempotency_key == key)
                .first()
            )
            if existing_key_tx:
                if existing_key_tx.tenant_id != tenant_id:
                    raise DuplicateIdempotencyError(
                        f"Idempotency key '{key}' already used by another tenant."
                    )
                if (
                    existing_key_tx.transaction_type not in (CreditTransactionType.REFUND, "refund", "REFUND")
                    or existing_key_tx.job_id != cleaned_job_id
                    or existing_key_tx.wallet_id != target_wallet_id
                    or existing_key_tx.amount != refund_amount
                ):
                    raise WalletError(
                        "Idempotency key conflict: existing transaction parameters do not match requested refund."
                    )
                return existing_key_tx

        # Check if a refund already exists for this job and tenant
        existing_job_refund = (
            db.query(CreditTransactionModel)
            .filter(
                CreditTransactionModel.tenant_id == tenant_id,
                CreditTransactionModel.job_id == cleaned_job_id,
                CreditTransactionModel.transaction_type.in_([
                    CreditTransactionType.REFUND,
                    "refund",
                    "REFUND",
                ]),
            )
            .first()
        )
        if existing_job_refund:
            if (
                existing_job_refund.wallet_id != target_wallet_id
                or existing_job_refund.amount != refund_amount
            ):
                raise WalletError(
                    f"Existing refund for job '{cleaned_job_id}' does not match requested parameters "
                    f"(wallet: {existing_job_refund.wallet_id} vs {target_wallet_id}, "
                    f"amount: {existing_job_refund.amount} vs {refund_amount})."
                )
            return existing_job_refund

        # 5. Atomically increment wallet and insert refund ledger record in the same transaction
        stmt = (
            update(WalletModel)
            .where(
                WalletModel.id == target_wallet_id,
                WalletModel.tenant_id == tenant_id,
            )
            .values(
                balance=WalletModel.balance + refund_amount,
                updated_at=utcnow(),
            )
        )
        result = db.execute(stmt)
        if result.rowcount != 1:
            db.rollback()
            raise WalletError(
                f"Failed to update wallet: expected exactly 1 row updated for wallet '{target_wallet_id}' "
                f"and tenant '{tenant_id}', got {result.rowcount}."
            )

        db.refresh(wallet)

        tx = CreditTransactionModel(
            tenant_id=tenant_id,
            wallet_id=target_wallet_id,
            user_id=user_id,
            job_id=cleaned_job_id,
            transaction_type=CreditTransactionType.REFUND,
            amount=refund_amount,
            balance_after=wallet.balance,
            idempotency_key=key,
            description=f"Refunded {refund_amount} credits: {reason}",
        )
        db.add(tx)

        try:
            db.commit()
            db.refresh(tx)
            return tx
        except IntegrityError:
            db.rollback()
            # Concurrent race condition: another transaction committed a refund simultaneously.
            # db.rollback() discarded the uncommitted wallet increment and failed ledger insertion.
            # Perform a strict tenant-scoped recovery lookup and validate the committed transaction.
            recovery_tx = (
                db.query(CreditTransactionModel)
                .filter(
                    CreditTransactionModel.tenant_id == tenant_id,
                    CreditTransactionModel.job_id == cleaned_job_id,
                    CreditTransactionModel.transaction_type.in_([
                        CreditTransactionType.REFUND,
                        "refund",
                        "REFUND",
                    ]),
                )
                .first()
            )
            if recovery_tx:
                # Concurrent requests using different idempotency keys for the same job return the same
                # committed refund ONLY when all expected parameters (tenant, job, wallet, amount) match!
                if (
                    recovery_tx.tenant_id == tenant_id
                    and recovery_tx.job_id == cleaned_job_id
                    and recovery_tx.wallet_id == target_wallet_id
                    and recovery_tx.amount == refund_amount
                ):
                    return recovery_tx
                raise WalletError("Concurrent refund committed with conflicting parameters.")

            if idempotency_key:
                recovery_key_tx = (
                    db.query(CreditTransactionModel)
                    .filter(
                        CreditTransactionModel.tenant_id == tenant_id,
                        CreditTransactionModel.idempotency_key == key,
                    )
                    .first()
                )
                if recovery_key_tx:
                    if (
                        recovery_key_tx.tenant_id == tenant_id
                        and recovery_key_tx.job_id == cleaned_job_id
                        and recovery_key_tx.wallet_id == target_wallet_id
                        and recovery_key_tx.amount == refund_amount
                    ):
                        return recovery_key_tx
                    raise WalletError("Concurrent transaction committed with same idempotency key but conflicting parameters.")

            raise

    def validate_refund_index(self, db: Session) -> bool:
        """Inspect existing database index definition structurally without issuing DDL.

        Read-only verification:
        - Checks for duplicate refund records. If any exist, raises SchemaValidationError.
        - Checks if uq_credit_tx_job_refund exists on credit_transactions (job_id).
        - Validates unique property, schema, single key column, and partial WHERE predicate.
        - Never issues DROP, ALTER, or CREATE statements.
        - If missing, structurally invalid, or on an unsupported dialect, raises SchemaValidationError.
        - Returns True only when the index is verified valid.
        """
        dups = self.check_for_duplicate_refunds(db)
        if dups:
            raise SchemaValidationError(
                f"Refund index validation failed: duplicate refund transactions detected for job IDs: {dups}."
            )

        bind = db.get_bind()
        dialect_name = bind.dialect.name

        if dialect_name not in ("postgresql", "sqlite"):
            raise SchemaValidationError(
                f"Refund index validation failed: unsupported database dialect '{dialect_name}'."
            )

        if dialect_name == "postgresql":
            query = text("""
                SELECT
                    n.nspname AS schema_name,
                    t.relname AS table_name,
                    ix.indisunique AS is_unique,
                    ix.indnkeyatts AS num_key_cols,
                    ARRAY(
                        SELECT a.attname
                        FROM pg_attribute a
                        WHERE a.attrelid = t.oid
                          AND a.attnum = ANY(ix.indkey[0:ix.indnkeyatts-1])
                    ) AS indexed_columns,
                    pg_get_expr(ix.indpred, ix.indrelid) AS predicate_expr
                FROM pg_index ix
                JOIN pg_class i ON i.oid = ix.indexrelid
                JOIN pg_class t ON t.oid = ix.indrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE n.nspname = current_schema()
                  AND i.relname = 'uq_credit_tx_job_refund'
            """)
            row = db.execute(query).mappings().first()
            if not row:
                raise SchemaValidationError(
                    "Refund index validation failed: index 'uq_credit_tx_job_refund' is missing. "
                    "Run migration 0005_phase6_refund_idempotency.sql to create it."
                )

            current_schema = db.execute(text("SELECT current_schema()")).scalar()
            schema_ok = (row["schema_name"] == current_schema)
            tbl_ok = (row["table_name"] == "credit_transactions")
            unique_ok = bool(row["is_unique"])
            key_cols_ok = (row.get("num_key_cols") == 1 and len(row["indexed_columns"] or []) == 1)
            cols = [str(c) for c in (row["indexed_columns"] or [])]
            cols_ok = (cols == ["job_id"])

            pred = str(row["predicate_expr"] or "").lower()
            has_not_null = bool(re.search(r"\bjob_id\b\s+is\s+not\s+null", pred))
            has_tx_type = bool(re.search(r"\btransaction_type\b", pred))
            has_refund = "refund" in pred
            has_no_or = not bool(re.search(r"\bor\b", pred))
            # Strip all occurrences of 'refund' and verify no additional transaction types or literals remain
            remaining_literals = re.sub(r"'refund'", "", pred)
            has_no_other_types = ("'" not in remaining_literals)
            has_no_unrelated_cols = not bool(re.search(r"\b(id|tenant_id|wallet_id|user_id|amount|balance_after|idempotency_key|description|created_at)\b", pred))

            pred_ok = (
                bool(row["predicate_expr"])
                and has_not_null
                and has_tx_type
                and has_refund
                and has_no_or
                and has_no_other_types
                and has_no_unrelated_cols
            )

            if not (schema_ok and tbl_ok and unique_ok and key_cols_ok and cols_ok and pred_ok):
                raise SchemaValidationError(
                    f"Refund index validation failed: 'uq_credit_tx_job_refund' has incorrect definition "
                    f"(schema={row['schema_name']}, table={row['table_name']}, unique={unique_ok}, "
                    f"key_cols={key_cols_ok}, cols={cols}, pred={pred}). "
                    "Run migration 0005_phase6_refund_idempotency.sql to repair it."
                )
            return True

        elif dialect_name == "sqlite":
            row = db.execute(text("""
                SELECT sql, tbl_name FROM sqlite_master WHERE type = 'index' AND name = 'uq_credit_tx_job_refund'
            """)).mappings().first()
            if not row:
                raise SchemaValidationError(
                    "Refund index validation failed: index 'uq_credit_tx_job_refund' is missing. "
                    "Apply migration 0005 to create it."
                )

            if row["tbl_name"] != "credit_transactions":
                raise SchemaValidationError(
                    f"Refund index validation failed: index is on table '{row['tbl_name']}', expected 'credit_transactions'."
                )

            sql = str(row["sql"] or "")
            if "unique" not in sql.lower():
                raise SchemaValidationError(
                    "Refund index validation failed: index 'uq_credit_tx_job_refund' is not UNIQUE."
                )

            # Check indexed column via pragma index_info
            info = db.execute(text("PRAGMA index_info('uq_credit_tx_job_refund')")).fetchall()
            cols = [r[2] for r in info]
            if cols != ["job_id"] or len(info) != 1:
                raise SchemaValidationError(
                    f"Refund index validation failed: indexed columns are {cols}, expected exactly ['job_id']."
                )

            if "where" not in sql.lower():
                raise SchemaValidationError(
                    "Refund index validation failed: index 'uq_credit_tx_job_refund' is missing WHERE predicate."
                )

            where_clause = sql.lower().split("where", 1)[1]
            has_not_null = bool(re.search(r"\bjob_id\b\s+is\s+not\s+null", where_clause))
            has_tx_type = bool(re.search(r"\btransaction_type\b", where_clause))
            has_refund = "refund" in where_clause
            has_no_or = not bool(re.search(r"\bor\b", where_clause))
            remaining_literals = re.sub(r"'refund'", "", where_clause)
            has_no_other_types = ("'" not in remaining_literals)
            has_no_unrelated_cols = not bool(re.search(r"\b(id|tenant_id|wallet_id|user_id|amount|balance_after|idempotency_key|description|created_at)\b", where_clause))

            pred_ok = (
                has_not_null
                and has_tx_type
                and has_refund
                and has_no_or
                and has_no_other_types
                and has_no_unrelated_cols
            )

            if not pred_ok:
                raise SchemaValidationError(
                    f"Refund index validation failed: 'uq_credit_tx_job_refund' has incorrect predicate (where={where_clause.strip()}). "
                    "Apply migration 0005 to repair it."
                )
            return True

    def validate_or_repair_refund_index(self, db: Session) -> bool:
        """Deprecated alias pointing to read-only validation.

        Actual index creation and repair is managed exclusively via database migrations.
        """
        return self.validate_refund_index(db)

    def check_for_duplicate_refunds(self, db: Session) -> List[str]:
        """Check for existing duplicate refund transactions for any job in the ledger.

        Used before running migrations to prevent silent corruption of historical records.
        Returns list of job IDs with duplicate refunds, or empty list if none.
        """
        from sqlalchemy import func
        dups = (
            db.query(CreditTransactionModel.job_id)
            .filter(
                CreditTransactionModel.transaction_type.in_([CreditTransactionType.REFUND, "refund", "REFUND"]),
                CreditTransactionModel.job_id.isnot(None),
            )
            .group_by(CreditTransactionModel.job_id)
            .having(func.count(CreditTransactionModel.id) > 1)
            .all()
        )
        return [job_id for (job_id,) in dups if job_id]

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
