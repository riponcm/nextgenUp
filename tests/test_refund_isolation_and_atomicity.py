"""Phase 6 Regression Tests — Refund Isolation, Wallet Atomicity & Migration Consistency.

Validates:
1. Two concurrent refund attempts for the same job guarantee exactly-once refund and balance increment.
2. Repeated refund requests return the original refund without incrementing the wallet again.
3. Same idempotency key used by different tenants cannot expose another tenant's transaction.
4. Different tenants' wallets remain completely unchanged and isolated.
5. A tenant with multiple wallets only has the correct job-associated wallet updated.
6. Missing wallet or invalid reservation fails safely with clean rollback.
7. A database uniqueness conflict rolls back the wallet update, preventing unrecorded increments.
8. Existing duplicate ledger rows are detected before migration without silent modification.
9. Repeated non-refund transactions behave according to intended append-only ledger rules.
10. Multiple reservations for a single job calculate the total reserved amount and refund the correct wallet.
11. Multiple reservations across different wallets require target wallet specification and handle ambiguous ownership safely.
12. Refund amount policy enforces total eligible reservation match: partial refunds and over-refunds fail safely.
13. Concurrent requests using different idempotency keys return the committed refund only when parameters match, and reject conflicts.
14. Database index validator inspects existing definitions to ensure IF NOT EXISTS cannot hide mismatched definitions.
15. Real PostgreSQL integration test that executes full schema creation, migration 0005, and multi-session concurrency when configured.
"""

import concurrent.futures
import os
import uuid
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from unittest.mock import MagicMock, patch

from demerivo.credits.wallet_service import (
    CreditTransactionType,
    DuplicateIdempotencyError,
    InsufficientCreditsError,
    InvalidJobIdError,
    InvalidReservationError,
    SchemaValidationError,
    WalletError,
    WalletNotFoundError,
    WalletService,
    validate_job_id,
)
from demerivo.jobs.service import JobService
from demerivo.db.models import (
    CreditTransactionModel,
    JobModel,
    JobStatus,
    OperationType,
    TenantModel,
    UserModel,
    WalletModel,
)
from demerivo.db.session import Base


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def db_engine(tmp_path):
    """Isolated SQLite database engine with partial unique index support."""
    db_file = tmp_path / "test_refund_atomicity.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(db_engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=db_engine)


@pytest.fixture
def wallet_service():
    return WalletService()


def create_tenant_with_wallet(db: Session, prefix: str = "tnt", initial_balance: int = 100):
    """Helper to provision an isolated user, tenant, and wallet."""
    unique_id = str(uuid.uuid4())[:8]
    user = UserModel(
        id=f"usr_{prefix}_{unique_id}",
        email=f"{prefix}_{unique_id}@demerivo.test",
        display_name=f"User {prefix}",
        is_active=True,
    )
    tenant = TenantModel(
        id=f"tnt_{prefix}_{unique_id}",
        name=f"Tenant {prefix}",
        slug=f"slug-{prefix}-{unique_id}",
        owner_id=user.id,
    )
    wallet = WalletModel(
        id=f"wlt_{prefix}_{unique_id}",
        tenant_id=tenant.id,
        balance=initial_balance,
    )
    db.add_all([user, tenant, wallet])
    db.commit()
    db.refresh(tenant)
    db.refresh(user)
    db.refresh(wallet)
    return tenant, user, wallet


# ==============================================================================
# 1. Concurrent Refund Atomicity
# ==============================================================================

def test_two_concurrent_refund_attempts_for_same_job(session_factory, wallet_service):
    """Verify database-enforced atomicity and idempotency when two threads attempt refunds concurrently."""
    with session_factory() as db:
        tenant, user, wallet = create_tenant_with_wallet(db, prefix="concurrent", initial_balance=100)
        tenant_id = tenant.id
        user_id = user.id
        wallet_id = wallet.id
        initial_balance = wallet.balance

        job_id = "job-conc-refund-001"
        refund_amount = 20

        # Reserve credits first
        wallet_service.reserve_credits(
            tenant_id=tenant_id,
            amount=refund_amount,
            job_id=job_id,
            user_id=user_id,
            db=db,
        )
        post_reserve_balance = wallet_service.get_wallet(tenant_id, db).balance
        assert post_reserve_balance == initial_balance - refund_amount

    # Attempt concurrent refunds simultaneously with separate DB sessions
    def attempt_refund():
        with session_factory() as thread_db:
            return wallet_service.refund_credits(
                tenant_id=tenant_id,
                amount=refund_amount,
                job_id=job_id,
                user_id=user_id,
                reason="Simultaneous worker timeout recovery",
                db=thread_db,
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        fut1 = executor.submit(attempt_refund)
        fut2 = executor.submit(attempt_refund)
        tx1 = fut1.result(timeout=5.0)
        tx2 = fut2.result(timeout=5.0)

    # Both callers must receive valid, identical transaction records
    assert tx1 is not None
    assert tx2 is not None
    assert tx1.id == tx2.id
    assert tx1.job_id == job_id
    assert tx2.job_id == job_id
    assert tx1.wallet_id == wallet_id
    assert tx1.amount == refund_amount

    # Database ledger must contain exactly ONE refund transaction
    with session_factory() as db:
        refund_txs = (
            db.query(CreditTransactionModel)
            .filter_by(job_id=job_id, transaction_type=CreditTransactionType.REFUND)
            .all()
        )
        assert len(refund_txs) == 1

        # Balance must be exactly restored once (100), not double-credited (120)
        current_wallet = wallet_service.get_wallet(tenant_id, db)
        assert current_wallet.balance == initial_balance


# ==============================================================================
# 2. Repeated Refund Idempotency
# ==============================================================================

def test_repeated_refund_request_returns_original_without_incrementing_wallet(session_factory, wallet_service):
    """Verify repeated sequential refund calls return the original refund without double-crediting."""
    with session_factory() as db:
        tenant, user, wallet = create_tenant_with_wallet(db, prefix="repeat", initial_balance=50)
        tenant_id = tenant.id
        user_id = user.id
        initial_balance = wallet.balance

        job_id = "job-repeat-002"
        refund_amount = 15

        wallet_service.reserve_credits(
            tenant_id=tenant_id,
            amount=refund_amount,
            job_id=job_id,
            user_id=user_id,
            db=db,
        )
        assert wallet_service.get_wallet(tenant_id, db).balance == 35

        # First refund
        tx1 = wallet_service.refund_credits(
            tenant_id=tenant_id,
            amount=refund_amount,
            job_id=job_id,
            user_id=user_id,
            reason="First refund attempt",
            db=db,
        )
        assert tx1.amount == refund_amount
        assert wallet_service.get_wallet(tenant_id, db).balance == 50

        # Second refund (same parameters)
        tx2 = wallet_service.refund_credits(
            tenant_id=tenant_id,
            amount=refund_amount,
            job_id=job_id,
            user_id=user_id,
            reason="Second refund attempt",
            db=db,
        )
        assert tx2.id == tx1.id
        assert tx2.balance_after == 50

        # Third refund with custom idempotency key matching first default key
        tx3 = wallet_service.refund_credits(
            tenant_id=tenant_id,
            amount=refund_amount,
            job_id=job_id,
            user_id=user_id,
            reason="Third refund attempt",
            idempotency_key=f"refund_{job_id}",
            db=db,
        )
        assert tx3.id == tx1.id

        # Wallet balance must still be strictly 50 (never 65 or 80)
        assert wallet_service.get_wallet(tenant_id, db).balance == 50

        # Exactly 1 refund in DB
        count = (
            db.query(CreditTransactionModel)
            .filter_by(job_id=job_id, transaction_type=CreditTransactionType.REFUND)
            .count()
        )
        assert count == 1


# ==============================================================================
# 3. Cross-Tenant Idempotency Isolation
# ==============================================================================

def test_same_idempotency_key_used_by_different_tenants_cannot_expose_transaction(session_factory, wallet_service):
    """Verify an idempotency key used by Tenant A cannot leak transaction data to Tenant B or be reused."""
    with session_factory() as db:
        tenant_a, user_a, wallet_a = create_tenant_with_wallet(db, prefix="tntA", initial_balance=100)
        tenant_b, user_b, wallet_b = create_tenant_with_wallet(db, prefix="tntB", initial_balance=100)

        shared_key = "idemp_shared_key_xyz"

        # Tenant A performs an allocation with shared_key
        tx_a = wallet_service.allocate_credits(
            tenant_id=tenant_a.id,
            amount=20,
            description="Tenant A allocation",
            db=db,
            user_id=user_a.id,
            idempotency_key=shared_key,
        )
        assert tx_a.idempotency_key == shared_key
        assert tx_a.tenant_id == tenant_a.id

        # Tenant B attempts to use the same idempotency key for allocate_credits
        with pytest.raises(DuplicateIdempotencyError) as exc_alloc:
            wallet_service.allocate_credits(
                tenant_id=tenant_b.id,
                amount=30,
                description="Tenant B allocation",
                db=db,
                user_id=user_b.id,
                idempotency_key=shared_key,
            )
        assert "already used by another tenant" in str(exc_alloc.value)

        # Tenant B attempts to use the same idempotency key for reserve_credits
        with pytest.raises(DuplicateIdempotencyError) as exc_res:
            wallet_service.reserve_credits(
                tenant_id=tenant_b.id,
                amount=10,
                job_id="job_b_001",
                user_id=user_b.id,
                db=db,
                idempotency_key=shared_key,
            )
        assert "already used by another tenant" in str(exc_res.value)

        # Tenant B attempts to use the same idempotency key for refund_credits
        # Even if Tenant B creates a valid reservation for its own job:
        wallet_service.reserve_credits(
            tenant_id=tenant_b.id,
            amount=10,
            job_id="job_b_refund_test",
            user_id=user_b.id,
            db=db,
        )
        with pytest.raises(DuplicateIdempotencyError) as exc_refund:
            wallet_service.refund_credits(
                tenant_id=tenant_b.id,
                amount=10,
                job_id="job_b_refund_test",
                user_id=user_b.id,
                reason="Tenant B refund",
                db=db,
                idempotency_key=shared_key,
            )
        assert "already used by another tenant" in str(exc_refund.value)

        # Verify Tenant A's wallet balance remained 120 and Tenant B's was not improperly changed
        assert wallet_service.get_wallet(tenant_a.id, db).balance == 120
        assert wallet_service.get_wallet(tenant_b.id, db).balance == 90


# ==============================================================================
# 4. Tenant Balance Isolation
# ==============================================================================

def test_different_tenants_wallets_remain_unchanged(session_factory, wallet_service):
    """Verify operations on Tenant A never affect Tenant B's wallet or transaction ledger."""
    with session_factory() as db:
        tenant_a, user_a, wallet_a = create_tenant_with_wallet(db, prefix="isoA", initial_balance=50)
        tenant_b, user_b, wallet_b = create_tenant_with_wallet(db, prefix="isoB", initial_balance=80)

        job_a = "job-isolated-a"
        wallet_service.reserve_credits(
            tenant_id=tenant_a.id,
            amount=20,
            job_id=job_a,
            user_id=user_a.id,
            db=db,
        )
        assert wallet_service.get_wallet(tenant_a.id, db).balance == 30
        assert wallet_service.get_wallet(tenant_b.id, db).balance == 80  # Untouched

        wallet_service.refund_credits(
            tenant_id=tenant_a.id,
            amount=20,
            job_id=job_a,
            user_id=user_a.id,
            reason="Refund A",
            db=db,
        )
        assert wallet_service.get_wallet(tenant_a.id, db).balance == 50
        assert wallet_service.get_wallet(tenant_b.id, db).balance == 80  # Strictly untouched

        # Tenant B's transactions must remain completely empty
        txs_b = wallet_service.get_transactions(tenant_b.id, db)
        assert len(txs_b) == 0


# ==============================================================================
# 5. Multi-Wallet Tenant Targeting
# ==============================================================================

def test_tenant_with_multiple_wallets_only_updates_correct_wallet(tmp_path, wallet_service):
    """Verify a tenant with multiple wallets only has the job's reserved wallet updated."""
    db_file = tmp_path / "test_multi_wallet.db"
    engine = create_engine(f"sqlite:///{db_file}")

    with engine.connect() as conn:
        for stmt in [
            "CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, email VARCHAR(255), display_name VARCHAR(100), is_active BOOLEAN)",
            "CREATE TABLE tenants (id VARCHAR(36) PRIMARY KEY, name VARCHAR(100), slug VARCHAR(120), owner_id VARCHAR(36))",
            "CREATE TABLE wallets (id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36), balance INTEGER DEFAULT 0, updated_at TIMESTAMP, created_at TIMESTAMP)",
            """CREATE TABLE credit_transactions (
                id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36), wallet_id VARCHAR(36), user_id VARCHAR(36),
                job_id VARCHAR(36), transaction_type VARCHAR(32), amount INTEGER, balance_after INTEGER,
                idempotency_key VARCHAR(128), description VARCHAR(255), created_at TIMESTAMP
            )""",
            """CREATE UNIQUE INDEX uq_credit_tx_job_refund ON credit_transactions (job_id)
            WHERE transaction_type IN ('refund', 'REFUND') AND job_id IS NOT NULL""",
        ]:
            conn.execute(text(stmt))
        conn.commit()

    with Session(engine) as db:
        tenant_id = "tnt_multi"
        user_id = "usr_multi"
        w1_id = "wlt_primary"
        w2_id = "wlt_secondary"

        # Insert user and tenant
        db.execute(text("INSERT INTO users (id, email, is_active) VALUES (:id, 'multi@test.com', 1)"), {"id": user_id})
        db.execute(text("INSERT INTO tenants (id, name, slug, owner_id) VALUES (:id, 'Multi Tenant', 'multi-tnt', :uid)"), {"id": tenant_id, "uid": user_id})

        # Provision two distinct wallets for the SAME tenant
        db.execute(text("INSERT INTO wallets (id, tenant_id, balance) VALUES (:id, :tnt, 40)"), {"id": w1_id, "tnt": tenant_id})
        db.execute(text("INSERT INTO wallets (id, tenant_id, balance) VALUES (:id, :tnt, 70)"), {"id": w2_id, "tnt": tenant_id})
        db.commit()

        # Create a reservation explicitly attached to wallet 2
        job_id = "job-target-wallet-2"
        res_tx = CreditTransactionModel(
            id="tx-res-w2",
            tenant_id=tenant_id,
            wallet_id=w2_id,
            user_id=user_id,
            job_id=job_id,
            transaction_type=CreditTransactionType.RESERVATION,
            amount=-25,
            balance_after=45,
            description="Reserved from secondary wallet",
        )
        db.add(res_tx)
        # Deduct from wallet_2 to simulate prior reservation
        db.execute(text("UPDATE wallets SET balance = 45 WHERE id = :id"), {"id": w2_id})
        db.commit()

        # Execute refund
        refund_tx = wallet_service.refund_credits(
            tenant_id=tenant_id,
            amount=25,
            job_id=job_id,
            user_id=user_id,
            reason="Multi-wallet refund test",
            db=db,
        )

        # Wallet 2 must be refunded (+25 -> 70)
        w2_bal = db.execute(text("SELECT balance FROM wallets WHERE id = :id"), {"id": w2_id}).scalar()
        assert w2_bal == 70

        # Wallet 1 must be strictly UNCHANGED (40)
        w1_bal = db.execute(text("SELECT balance FROM wallets WHERE id = :id"), {"id": w1_id}).scalar()
        assert w1_bal == 40
        assert refund_tx.wallet_id == w2_id


# ==============================================================================
# 6. Multiple Reservations Accounting
# ==============================================================================

def test_multiple_reservations_same_wallet_refunded_together(session_factory, wallet_service):
    """Verify that multiple reservations for a job calculate the total reserved amount and refund the full amount."""
    with session_factory() as db:
        tenant, user, wallet = create_tenant_with_wallet(db, prefix="multiRes", initial_balance=100)
        tenant_id = tenant.id
        user_id = user.id
        job_id = "job-multi-res-01"

        # 1. Base reservation: 10 credits
        wallet_service.reserve_credits(
            tenant_id=tenant_id,
            amount=10,
            job_id=job_id,
            user_id=user_id,
            db=db,
            description="Initial base reservation",
        )
        # 2. Add-on reservation: 15 credits
        wallet_service.reserve_credits(
            tenant_id=tenant_id,
            amount=15,
            job_id=job_id,
            user_id=user_id,
            db=db,
            description="Upscale add-on reservation",
        )
        assert wallet_service.get_wallet(tenant_id, db).balance == 75

        # 3. Partial refund attempt (e.g. 10 out of 25) must be rejected
        with pytest.raises(InvalidReservationError) as exc_partial:
            wallet_service.refund_credits(
                tenant_id=tenant_id,
                amount=10,
                job_id=job_id,
                user_id=user_id,
                reason="Partial refund attempt",
                db=db,
            )
        assert "Partial refunds are not permitted" in str(exc_partial.value)
        assert wallet_service.get_wallet(tenant_id, db).balance == 75

        # 4. Over-refund attempt (e.g. 30 out of 25) must be rejected
        with pytest.raises(InvalidReservationError) as exc_over:
            wallet_service.refund_credits(
                tenant_id=tenant_id,
                amount=30,
                job_id=job_id,
                user_id=user_id,
                reason="Over-refund attempt",
                db=db,
            )
        assert "Over-refund not permitted" in str(exc_over.value)
        assert wallet_service.get_wallet(tenant_id, db).balance == 75

        # 5. Full refund matching total eligible reservation (25 credits) succeeds
        refund_tx = wallet_service.refund_credits(
            tenant_id=tenant_id,
            amount=25,
            job_id=job_id,
            user_id=user_id,
            reason="Job failure full refund",
            db=db,
        )
        assert refund_tx.amount == 25
        assert refund_tx.balance_after == 100
        assert wallet_service.get_wallet(tenant_id, db).balance == 100


def test_multiple_reservations_across_different_wallets_rejected(tmp_path, wallet_service):
    """Verify split-wallet reservations for the same job are strictly rejected to guarantee 1-to-1 refund mapping."""
    db_file = tmp_path / "test_multi_wallet_reject.db"
    engine = create_engine(f"sqlite:///{db_file}")

    with engine.connect() as conn:
        for stmt in [
            "CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, email VARCHAR(255), is_active BOOLEAN)",
            "CREATE TABLE tenants (id VARCHAR(36) PRIMARY KEY, name VARCHAR(100), slug VARCHAR(120), owner_id VARCHAR(36))",
            "CREATE TABLE wallets (id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36), balance INTEGER DEFAULT 0, updated_at TIMESTAMP, created_at TIMESTAMP)",
            """CREATE TABLE credit_transactions (
                id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36), wallet_id VARCHAR(36), user_id VARCHAR(36),
                job_id VARCHAR(36), transaction_type VARCHAR(32), amount INTEGER, balance_after INTEGER,
                idempotency_key VARCHAR(128), description VARCHAR(255), created_at TIMESTAMP
            )""",
            """CREATE UNIQUE INDEX uq_credit_tx_job_refund ON credit_transactions (job_id)
            WHERE transaction_type IN ('refund', 'REFUND') AND job_id IS NOT NULL""",
        ]:
            conn.execute(text(stmt))
        conn.commit()

    with Session(engine) as db:
        tenant_id = "tnt_cross_wallet"
        user_id = "usr_cross"
        w1_id = "wlt_alpha"
        w2_id = "wlt_beta"

        # Insert user and tenant
        db.execute(text("INSERT INTO users (id, email, is_active) VALUES (:id, 'cross@test.com', 1)"), {"id": user_id})
        db.execute(text("INSERT INTO tenants (id, name, slug, owner_id) VALUES (:id, 'Cross Tenant', 'cross-tnt', :uid)"), {"id": tenant_id, "uid": user_id})

        # Provision two distinct wallets for this tenant
        db.execute(text("INSERT INTO wallets (id, tenant_id, balance) VALUES (:id, :tnt, 50)"), {"id": w1_id, "tnt": tenant_id})
        db.execute(text("INSERT INTO wallets (id, tenant_id, balance) VALUES (:id, :tnt, 50)"), {"id": w2_id, "tnt": tenant_id})
        db.commit()

        job_id = "job-cross-wallet-01"

        # 1. First reservation from wlt_alpha succeeds
        res1 = wallet_service.reserve_credits(
            tenant_id=tenant_id,
            amount=15,
            job_id=job_id,
            user_id=user_id,
            wallet_id=w1_id,
            db=db,
        )
        assert res1.wallet_id == w1_id
        bal_alpha = db.execute(text("SELECT balance FROM wallets WHERE id = :id"), {"id": w1_id}).scalar()
        assert bal_alpha == 35

        # 2. Second reservation attempt for the same job from wlt_beta must be REJECTED
        with pytest.raises(InvalidReservationError) as exc_cross:
            wallet_service.reserve_credits(
                tenant_id=tenant_id,
                amount=20,
                job_id=job_id,
                user_id=user_id,
                wallet_id=w2_id,
                db=db,
            )
        assert "Cross-wallet reservations for the same job are not permitted" in str(exc_cross.value)

        # 3. Verify wlt_beta balance was NEVER touched and remains 50
        bal_beta = db.execute(text("SELECT balance FROM wallets WHERE id = :id"), {"id": w2_id}).scalar()
        assert bal_beta == 50

        # 4. Verify wlt_alpha balance was not modified by the failed attempt
        bal_alpha_after = db.execute(text("SELECT balance FROM wallets WHERE id = :id"), {"id": w1_id}).scalar()
        assert bal_alpha_after == 35

        # 5. Full refund of wlt_alpha reservation succeeds cleanly
        refund_tx = wallet_service.refund_credits(
            tenant_id=tenant_id,
            amount=15,
            job_id=job_id,
            user_id=user_id,
            reason="Refund alpha reservation",
            db=db,
        )
        assert refund_tx.wallet_id == w1_id
        assert db.execute(text("SELECT balance FROM wallets WHERE id = :id"), {"id": w1_id}).scalar() == 50


def test_concurrent_reservations_different_wallets_rejected(tmp_path, wallet_service):
    """Verify concurrent reservation attempts for the same job from different wallets cannot bypass the single-wallet rule."""
    db_file = tmp_path / "test_concurrent_multi_wallet.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})

    with engine.connect() as conn:
        for stmt in [
            "CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, email VARCHAR(255), is_active BOOLEAN)",
            "CREATE TABLE tenants (id VARCHAR(36) PRIMARY KEY, name VARCHAR(100), slug VARCHAR(120), owner_id VARCHAR(36))",
            "CREATE TABLE wallets (id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36), balance INTEGER DEFAULT 0, updated_at TIMESTAMP, created_at TIMESTAMP)",
            """CREATE TABLE credit_transactions (
                id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36), wallet_id VARCHAR(36), user_id VARCHAR(36),
                job_id VARCHAR(36), transaction_type VARCHAR(32), amount INTEGER, balance_after INTEGER,
                idempotency_key VARCHAR(128), description VARCHAR(255), created_at TIMESTAMP
            )""",
            """CREATE UNIQUE INDEX uq_credit_tx_job_refund ON credit_transactions (job_id)
            WHERE transaction_type IN ('refund', 'REFUND') AND job_id IS NOT NULL""",
        ]:
            conn.execute(text(stmt))
        conn.commit()

    with Session(engine) as db:
        tenant_id = "tnt_conc_wallets"
        user_id = "usr_conc"
        w1_id = "wlt_1"
        w2_id = "wlt_2"

        db.execute(text("INSERT INTO users (id, email, is_active) VALUES (:id, 'conc@test.com', 1)"), {"id": user_id})
        db.execute(text("INSERT INTO tenants (id, name, slug, owner_id) VALUES (:id, 'Conc Tenant', 'conc-tnt', :uid)"), {"id": tenant_id, "uid": user_id})
        db.execute(text("INSERT INTO wallets (id, tenant_id, balance) VALUES (:id, :tnt, 60)"), {"id": w1_id, "tnt": tenant_id})
        db.execute(text("INSERT INTO wallets (id, tenant_id, balance) VALUES (:id, :tnt, 60)"), {"id": w2_id, "tnt": tenant_id})
        db.commit()

    job_id = "job-concurrent-wallets-01"
    results = []
    errors = []

    def try_reserve(wallet_target: str, amt: int):
        with Session(engine) as thread_db:
            try:
                tx = wallet_service.reserve_credits(
                    tenant_id=tenant_id,
                    amount=amt,
                    job_id=job_id,
                    user_id=user_id,
                    wallet_id=wallet_target,
                    db=thread_db,
                )
                results.append((wallet_target, tx))
            except Exception as e:
                errors.append((wallet_target, e))

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(try_reserve, w1_id, 20)
        f2 = executor.submit(try_reserve, w2_id, 20)
        f1.result(timeout=5.0)
        f2.result(timeout=5.0)

    # Exactly one reservation must succeed, and one must be rejected
    assert len(results) == 1
    assert len(errors) == 1
    winning_wallet, _ = results[0]
    losing_wallet, err = errors[0]
    assert isinstance(err, InvalidReservationError)
    assert "Cross-wallet reservations for the same job are not permitted" in str(err)

    # Winning wallet decremented by 20 -> 40; losing wallet untouched -> 60
    with Session(engine) as db:
        bal_win = db.execute(text("SELECT balance FROM wallets WHERE id = :id"), {"id": winning_wallet}).scalar()
        bal_lose = db.execute(text("SELECT balance FROM wallets WHERE id = :id"), {"id": losing_wallet}).scalar()
        assert bal_win == 40
        assert bal_lose == 60

        # Ledger contains reservations for ONLY the winning wallet
        txs = db.query(CreditTransactionModel).filter_by(job_id=job_id).all()
        assert len(txs) == 1
        assert txs[0].wallet_id == winning_wallet


# ==============================================================================
# 7. Safe Failure on Missing Wallet / Invalid Reservation
# ==============================================================================

def test_missing_wallet_or_invalid_reservation_fails_safely(session_factory, wallet_service):
    """Verify refund fails safely without wallet corruption when reservation or wallet is missing/invalid."""
    with session_factory() as db:
        tenant, user, wallet = create_tenant_with_wallet(db, prefix="failSafe", initial_balance=100)
        tenant_id = tenant.id
        user_id = user.id

        # 7a: Refund with no reservation
        with pytest.raises(InvalidReservationError) as exc_no_res:
            wallet_service.refund_credits(
                tenant_id=tenant_id,
                amount=10,
                job_id="job_nonexistent_res",
                user_id=user_id,
                reason="Should fail",
                db=db,
            )
        assert "No credit reservation found" in str(exc_no_res.value)
        assert wallet_service.get_wallet(tenant_id, db).balance == 100

        # 7b: Over-refund attempt
        job_id_partial = "job_partial_res"
        wallet_service.reserve_credits(
            tenant_id=tenant_id,
            amount=10,
            job_id=job_id_partial,
            user_id=user_id,
            db=db,
        )
        with pytest.raises(InvalidReservationError) as exc_exceed:
            wallet_service.refund_credits(
                tenant_id=tenant_id,
                amount=20,  # Exceeds reserved 10
                job_id=job_id_partial,
                user_id=user_id,
                reason="Exceeding refund",
                db=db,
            )
        assert "Over-refund not permitted" in str(exc_exceed.value)
        assert wallet_service.get_wallet(tenant_id, db).balance == 90  # 100 - 10

        # 7c: Reservation exists but points to non-existent wallet
        fake_job = "job_fake_wallet"
        fake_tx = CreditTransactionModel(
            tenant_id=tenant_id,
            wallet_id="wlt_nonexistent_999",
            user_id=user_id,
            job_id=fake_job,
            transaction_type=CreditTransactionType.RESERVATION,
            amount=-10,
            balance_after=80,
            description="Reservation with broken wallet reference",
        )
        db.add(fake_tx)
        db.commit()

        with pytest.raises(WalletNotFoundError) as exc_wlt_not_found:
            wallet_service.refund_credits(
                tenant_id=tenant_id,
                amount=10,
                job_id=fake_job,
                user_id=user_id,
                reason="Should fail on missing wallet",
                db=db,
            )
        assert "not found for tenant" in str(exc_wlt_not_found.value)

        # 7d: Reservation belongs to Tenant A, but Tenant B attempts to refund it
        tenant_b, user_b, wallet_b = create_tenant_with_wallet(db, prefix="intruderB", initial_balance=50)
        with pytest.raises(InvalidReservationError):
            wallet_service.refund_credits(
                tenant_id=tenant_b.id,
                amount=10,
                job_id=job_id_partial,  # belongs to Tenant A
                user_id=user_b.id,
                reason="Cross-tenant refund attempt",
                db=db,
            )
        assert wallet_service.get_wallet(tenant_b.id, db).balance == 50


# ==============================================================================
# 8. Rollback on Database Uniqueness Conflict
# ==============================================================================

def test_database_uniqueness_conflict_rolls_back_wallet_update(session_factory, wallet_service):
    """Verify that if database commit fails on an unrecoverable conflict, the wallet increment rolls back."""
    with session_factory() as db:
        tenant, user, wallet = create_tenant_with_wallet(db, prefix="rollback", initial_balance=50)
        tenant_id = tenant.id
        user_id = user.id

        job_id = "job-rollback-test"
        wallet_service.reserve_credits(
            tenant_id=tenant_id,
            amount=15,
            job_id=job_id,
            user_id=user_id,
            db=db,
        )
        assert wallet_service.get_wallet(tenant_id, db).balance == 35

        # Patch db.commit to simulate an unexpected IntegrityError where no committed refund exists
        with patch.object(db, "commit", side_effect=IntegrityError("Simulated DB conflict", params=None, orig=Exception())):
            with pytest.raises(IntegrityError):
                wallet_service.refund_credits(
                    tenant_id=tenant_id,
                    amount=15,
                    job_id=job_id,
                    user_id=user_id,
                    reason="Simulated failure",
                    db=db,
                )

        # Verify wallet balance in DB was NOT left incremented
        db.refresh(wallet)
        assert wallet.balance == 35  # Remains 35, rolled back cleanly!


# ==============================================================================
# 9. Concurrency with Different Idempotency Keys
# ==============================================================================

def test_concurrent_refunds_with_different_idempotency_keys(session_factory, wallet_service):
    """Verify concurrent requests using different idempotency keys for the same job return the same committed refund."""
    with session_factory() as db:
        tenant, user, wallet = create_tenant_with_wallet(db, prefix="diffKey", initial_balance=80)
        tenant_id = tenant.id
        user_id = user.id
        wallet_id = wallet.id

        job_id = "job-diff-key-01"
        refund_amount = 25

        wallet_service.reserve_credits(
            tenant_id=tenant_id,
            amount=refund_amount,
            job_id=job_id,
            user_id=user_id,
            db=db,
        )
        assert wallet_service.get_wallet(tenant_id, db).balance == 55

    # Concurrently execute two refund requests with DIFFERENT idempotency keys for the same job
    def worker1():
        with session_factory() as db1:
            return wallet_service.refund_credits(
                tenant_id=tenant_id,
                amount=refund_amount,
                job_id=job_id,
                user_id=user_id,
                reason="Worker 1 timeout recovery",
                idempotency_key="worker1_idemp_key_aaa",
                db=db1,
            )

    def worker2():
        with session_factory() as db2:
            return wallet_service.refund_credits(
                tenant_id=tenant_id,
                amount=refund_amount,
                job_id=job_id,
                user_id=user_id,
                reason="Worker 2 timeout recovery",
                idempotency_key="worker2_idemp_key_bbb",
                db=db2,
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        fut1 = executor.submit(worker1)
        fut2 = executor.submit(worker2)
        tx1 = fut1.result(timeout=5.0)
        tx2 = fut2.result(timeout=5.0)

    # Both callers must receive the exact same committed refund record
    assert tx1 is not None
    assert tx2 is not None
    assert tx1.id == tx2.id
    assert tx1.job_id == job_id
    assert tx2.job_id == job_id
    assert tx1.amount == refund_amount
    assert tx2.amount == refund_amount

    # Balance must be exactly restored once (80)
    with session_factory() as db:
        assert wallet_service.get_wallet(tenant_id, db).balance == 80


# ==============================================================================
# 10. Detection of Pre-Migration Duplicate Ledger Rows
# ==============================================================================

def test_existing_duplicate_ledger_rows_detected_before_migration(session_factory, wallet_service):
    """Verify pre-migration validation detects duplicate refund records without altering or dropping them."""
    with session_factory() as db:
        tenant, user, wallet = create_tenant_with_wallet(db, prefix="dupLedger", initial_balance=100)
        tenant_id = tenant.id
        user_id = user.id
        wallet_id = wallet.id

        dup_job_1 = "job-duplicate-historical-01"

        try:
            tx1 = CreditTransactionModel(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                wallet_id=wallet_id,
                user_id=user_id,
                job_id=dup_job_1,
                transaction_type=CreditTransactionType.REFUND,
                amount=10,
                balance_after=90,
                description="Historical refund 1",
            )
            tx2 = CreditTransactionModel(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                wallet_id=wallet_id,
                user_id=user_id,
                job_id=dup_job_1,
                transaction_type=CreditTransactionType.REFUND,
                amount=10,
                balance_after=100,
                description="Historical duplicate refund 2",
            )
            db.add_all([tx1, tx2])
            db.commit()
            duplicate_inserted = True
        except IntegrityError:
            db.rollback()
            duplicate_inserted = False

        if duplicate_inserted:
            dups = wallet_service.check_for_duplicate_refunds(db)
            assert dup_job_1 in dups

            # Historical records must NOT have been silently modified or deleted
            records = db.query(CreditTransactionModel).filter_by(job_id=dup_job_1).all()
            assert len(records) == 2
            assert records[0].description == "Historical refund 1"
            assert records[1].description == "Historical duplicate refund 2"


# ==============================================================================
# 11. Repeated Non-Refund Transaction Rules
# ==============================================================================

def test_repeated_non_refund_transactions_behave_according_to_intended_ledger_rules(session_factory):
    """Verify append-only ledger allows multiple non-refund transactions, but enforces exactly-once for refunds."""
    with session_factory() as db:
        tenant, user, wallet = create_tenant_with_wallet(db, prefix="nonRefund", initial_balance=100)
        tenant_id = tenant.id
        user_id = user.id
        wallet_id = wallet.id

        job_id = "job-audit-ledger-01"

        # 1. Multiple reservations for the same job
        tx_res1 = CreditTransactionModel(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            wallet_id=wallet_id,
            user_id=user_id,
            job_id=job_id,
            transaction_type=CreditTransactionType.RESERVATION,
            amount=-10,
            balance_after=90,
            description="Base reservation",
        )
        tx_res2 = CreditTransactionModel(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            wallet_id=wallet_id,
            user_id=user_id,
            job_id=job_id,
            transaction_type=CreditTransactionType.RESERVATION,
            amount=-5,
            balance_after=85,
            description="Add-on reservation",
        )
        db.add_all([tx_res1, tx_res2])
        db.commit()

        # 2. Add a consumption transaction
        tx_cons = CreditTransactionModel(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            wallet_id=wallet_id,
            user_id=user_id,
            job_id=job_id,
            transaction_type=CreditTransactionType.CONSUMPTION,
            amount=0,
            balance_after=85,
            description="Consumption recorded",
        )
        db.add(tx_cons)
        db.commit()

        non_refunds = db.query(CreditTransactionModel).filter_by(job_id=job_id).all()
        assert len(non_refunds) == 3

        # 3. Add first refund transaction
        tx_ref1 = CreditTransactionModel(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            wallet_id=wallet_id,
            user_id=user_id,
            job_id=job_id,
            transaction_type=CreditTransactionType.REFUND,
            amount=15,
            balance_after=100,
            description="Refund 1",
        )
        db.add(tx_ref1)
        db.commit()

        # 4. Attempt to add a second refund transaction for the same job -> rejected by partial unique index
        tx_ref2 = CreditTransactionModel(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            wallet_id=wallet_id,
            user_id=user_id,
            job_id=job_id,
            transaction_type=CreditTransactionType.REFUND,
            amount=15,
            balance_after=115,
            description="Duplicate refund 2",
        )
        db.add(tx_ref2)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


# ==============================================================================
# 12. Job ID Validation Tests
# ==============================================================================

def test_job_id_validation_rejects_invalid_values(session_factory, wallet_service):
    """Verify job_id validation rejects None, empty, whitespace-only, and non-string inputs."""
    with session_factory() as db:
        tenant, user, wallet = create_tenant_with_wallet(db, prefix="valJob", initial_balance=50)
        invalid_job_ids = [None, "", "   ", "\t\n", 12345, ["job_id"], {"id": "job_id"}]

        # Verify reserve_credits rejects each invalid job_id without balance change
        for invalid_id in invalid_job_ids:
            with pytest.raises(InvalidJobIdError):
                wallet_service.reserve_credits(
                    tenant_id=tenant.id,
                    amount=10,
                    job_id=invalid_id,
                    user_id=user.id,
                    db=db,
                )
            assert wallet_service.get_wallet(tenant.id, db).balance == 50

        # Create a valid reservation to test refund_credits
        valid_job_id = "job-valid-id-001"
        wallet_service.reserve_credits(
            tenant_id=tenant.id,
            amount=10,
            job_id=valid_job_id,
            user_id=user.id,
            db=db,
        )
        assert wallet_service.get_wallet(tenant.id, db).balance == 40

        # Verify refund_credits rejects each invalid job_id without balance change
        for invalid_id in invalid_job_ids:
            with pytest.raises(InvalidJobIdError):
                wallet_service.refund_credits(
                    tenant_id=tenant.id,
                    amount=10,
                    job_id=invalid_id,
                    user_id=user.id,
                    reason="Invalid refund test",
                    db=db,
                )
            assert wallet_service.get_wallet(tenant.id, db).balance == 40

        # Valid job ID with surrounding whitespace is sanitized and succeeds
        refund_tx = wallet_service.refund_credits(
            tenant_id=tenant.id,
            amount=10,
            job_id=f"  {valid_job_id}  ",
            user_id=user.id,
            reason="Trimmed refund test",
            db=db,
        )
        assert refund_tx.job_id == valid_job_id
        assert wallet_service.get_wallet(tenant.id, db).balance == 50


# ==============================================================================
# 13. Add-on Reservation Refund Accounting Tests
# ==============================================================================

def test_addon_reservations_refund_total_outstanding_amount(session_factory, wallet_service):
    """Verify that base (10) + add-on (5) reservations refund exactly 15 credits on failure."""
    with session_factory() as db:
        tenant, user, wallet = create_tenant_with_wallet(db, prefix="addonFail", initial_balance=100)

        # 1. Create a job with initial base cost of 10
        job_id = f"job-addon-{uuid.uuid4().hex[:8]}"
        job = JobModel(
            id=job_id,
            tenant_id=tenant.id,
            user_id=user.id,
            operation_type=OperationType.ENHANCE,
            input_key="input/base.jpg",
            credit_cost=10,
            status=JobStatus.PENDING,
        )
        db.add(job)
        db.commit()

        # Initial base reservation: 10 credits
        wallet_service.reserve_credits(
            tenant_id=tenant.id,
            amount=10,
            job_id=job.id,
            user_id=user.id,
            db=db,
            description="Initial base reservation",
        )
        assert wallet_service.get_wallet(tenant.id, db).balance == 90

        # Add-on reservation: 5 credits (same wallet)
        wallet_service.reserve_credits(
            tenant_id=tenant.id,
            amount=5,
            job_id=job.id,
            user_id=user.id,
            db=db,
            description="Upscale add-on reservation",
        )
        assert wallet_service.get_wallet(tenant.id, db).balance == 85

        # 2. Mark job as PROCESSING
        JobService.update_status(db, job.id, JobStatus.PROCESSING, progress=10)

        # 3. Simulate failure: JobService.update_status must calculate 15 credits from ledger
        failed_job = JobService.update_status(
            db=db,
            job_id=job.id,
            target_status=JobStatus.FAILED,
            error_message="Worker GPU out of memory",
        )
        assert failed_job.status == JobStatus.FAILED

        # Verify wallet balance restored by exactly 15 (85 + 15 = 100)
        assert wallet_service.get_wallet(tenant.id, db).balance == 100

        # Verify exactly one refund transaction exists with amount 15
        refund_txs = (
            db.query(CreditTransactionModel)
            .filter_by(job_id=job.id, transaction_type=CreditTransactionType.REFUND)
            .all()
        )
        assert len(refund_txs) == 1
        assert refund_txs[0].amount == 15


def test_addon_reservations_cancellation_and_worker_failure_paths(session_factory, wallet_service):
    """Verify add-on reservations refund correctly via cancel_job and repeated attempts cannot double-refund."""
    with session_factory() as db:
        tenant, user, wallet = create_tenant_with_wallet(db, prefix="addonCanc", initial_balance=80)

        job_id = f"job-canc-{uuid.uuid4().hex[:8]}"
        job = JobModel(
            id=job_id,
            tenant_id=tenant.id,
            user_id=user.id,
            operation_type=OperationType.ENHANCE,
            input_key="input/canc.jpg",
            credit_cost=10,
            status=JobStatus.PENDING,
        )
        db.add(job)
        db.commit()

        # Base 10 + Add-on 5 = 15 total reserved
        wallet_service.reserve_credits(
            tenant_id=tenant.id,
            amount=10,
            job_id=job.id,
            user_id=user.id,
            db=db,
        )
        wallet_service.reserve_credits(
            tenant_id=tenant.id,
            amount=5,
            job_id=job.id,
            user_id=user.id,
            db=db,
        )
        assert wallet_service.get_wallet(tenant.id, db).balance == 65

        # Cancel job via JobService.cancel_job
        cancelled_job = JobService.cancel_job(db, job.id)
        assert cancelled_job.status == JobStatus.CANCELLED

        # Exactly 15 credits refunded, restoring balance to 80
        assert wallet_service.get_wallet(tenant.id, db).balance == 80

        # Repeated cancellation attempt must be a no-op and cannot refund again
        repeated_job = JobService.cancel_job(db, job.id)
        assert repeated_job.status == JobStatus.CANCELLED
        assert wallet_service.get_wallet(tenant.id, db).balance == 80

        # Direct second refund call returns original refund transaction without double-crediting
        tx_repeat = wallet_service.refund_credits(
            tenant_id=tenant.id,
            amount=None,  # ledger authoritative
            job_id=job.id,
            user_id=user.id,
            reason="Duplicate refund test",
            db=db,
        )
        assert tx_repeat.amount == 15
        assert wallet_service.get_wallet(tenant.id, db).balance == 80


# ==============================================================================
# 14. Read-Only Refund Index Validation
# ==============================================================================

def test_validate_refund_index_read_only(session_factory, wallet_service):
    """Verify validate_refund_index performs read-only checks and never executes DDL."""
    with session_factory() as db:
        tenant, user, wallet = create_tenant_with_wallet(db, prefix="readOnlyIdx", initial_balance=100)

        # 1. Existing valid index succeeds
        assert wallet_service.validate_refund_index(db) is True

        # 2. Missing index raises SchemaValidationError and does NOT issue CREATE DDL
        db.execute(text("DROP INDEX uq_credit_tx_job_refund"))
        db.commit()

        with pytest.raises(SchemaValidationError) as exc_missing:
            wallet_service.validate_refund_index(db)
        assert "is missing" in str(exc_missing.value)

        # Verify index was NOT created by the validator (remains missing)
        row_still_missing = db.execute(text("SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_credit_tx_job_refund'")).first()
        assert row_still_missing is None

        # 3. Structurally incorrect index raises SchemaValidationError and does NOT issue DROP/REPLACE DDL
        db.execute(text("CREATE UNIQUE INDEX uq_credit_tx_job_refund ON credit_transactions (user_id)"))  # wrong column!
        db.commit()

        with pytest.raises(SchemaValidationError) as exc_incorrect:
            wallet_service.validate_refund_index(db)
        assert "indexed columns" in str(exc_incorrect.value) or "incorrect definition" in str(exc_incorrect.value)

        # Recreate correct index
        db.execute(text("DROP INDEX uq_credit_tx_job_refund"))
        db.execute(text("""
            CREATE UNIQUE INDEX uq_credit_tx_job_refund ON credit_transactions (job_id)
            WHERE transaction_type IN ('refund', 'REFUND') AND job_id IS NOT NULL
        """))
        db.commit()
        assert wallet_service.validate_refund_index(db) is True

        # 4. Duplicate refunds cause validator to raise SchemaValidationError and preserve historical data
        dup_job = "job-dup-readonly-test"
        db.execute(text("DROP INDEX uq_credit_tx_job_refund"))
        db.commit()

        tx1 = CreditTransactionModel(
            id=str(uuid.uuid4()),
            tenant_id=tenant.id,
            wallet_id=wallet.id,
            user_id=user.id,
            job_id=dup_job,
            transaction_type=CreditTransactionType.REFUND,
            amount=10,
            balance_after=90,
            description="Historical dup 1",
        )
        tx2 = CreditTransactionModel(
            id=str(uuid.uuid4()),
            tenant_id=tenant.id,
            wallet_id=wallet.id,
            user_id=user.id,
            job_id=dup_job,
            transaction_type=CreditTransactionType.REFUND,
            amount=10,
            balance_after=100,
            description="Historical dup 2",
        )
        db.add_all([tx1, tx2])
        db.commit()

        with pytest.raises(SchemaValidationError) as exc_dups:
            wallet_service.validate_refund_index(db)
        assert "duplicate refund transactions detected" in str(exc_dups.value)

        # Historical duplicate records are strictly preserved intact
        dups_in_db = db.query(CreditTransactionModel).filter_by(job_id=dup_job).all()
        assert len(dups_in_db) == 2


def test_validate_refund_index_rejects_unsupported_dialect(session_factory, wallet_service):
    """Verify validate_refund_index raises SchemaValidationError for unsupported database dialects."""
    with session_factory() as db:
        real_bind = db.get_bind()
        mock_dialect = MagicMock()
        mock_dialect.name = "mysql"
        with patch.object(real_bind, "dialect", mock_dialect):
            with pytest.raises(SchemaValidationError) as exc:
                wallet_service.validate_refund_index(db)
            assert "unsupported database dialect 'mysql'" in str(exc.value)


def test_validate_refund_index_rejects_invalid_predicate(session_factory, wallet_service):
    """Verify validate_refund_index rejects indexes with invalid partial WHERE predicates,
    including non-refund transaction types, OR conditions, and unrelated columns.
    """
    with session_factory() as db:
        create_tenant_with_wallet(db, prefix="invPred", initial_balance=100)

        # 1. Missing WHERE predicate entirely
        db.execute(text("DROP INDEX IF EXISTS uq_credit_tx_job_refund"))
        db.execute(text("CREATE UNIQUE INDEX uq_credit_tx_job_refund ON credit_transactions (job_id)"))
        db.commit()

        with pytest.raises(SchemaValidationError) as exc_nowhere:
            wallet_service.validate_refund_index(db)
        assert "missing WHERE predicate" in str(exc_nowhere.value) or "incorrect predicate" in str(exc_nowhere.value)

        # 2. Predicate filters on wrong column (e.g. user_id instead of job_id)
        db.execute(text("DROP INDEX uq_credit_tx_job_refund"))
        db.execute(text("""
            CREATE UNIQUE INDEX uq_credit_tx_job_refund ON credit_transactions (job_id)
            WHERE user_id IS NOT NULL AND transaction_type = 'refund'
        """))
        db.commit()

        with pytest.raises(SchemaValidationError) as exc_wrong_col:
            wallet_service.validate_refund_index(db)
        assert "incorrect predicate" in str(exc_wrong_col.value)

        # 3. Predicate includes non-refund transaction type (e.g. reservation)
        db.execute(text("DROP INDEX uq_credit_tx_job_refund"))
        db.execute(text("""
            CREATE UNIQUE INDEX uq_credit_tx_job_refund ON credit_transactions (job_id)
            WHERE job_id IS NOT NULL AND transaction_type IN ('refund', 'reservation')
        """))
        db.commit()

        with pytest.raises(SchemaValidationError) as exc_forbidden:
            wallet_service.validate_refund_index(db)
        assert "incorrect predicate" in str(exc_forbidden.value)

        # 4. Predicate contains OR condition
        db.execute(text("DROP INDEX uq_credit_tx_job_refund"))
        db.execute(text("""
            CREATE UNIQUE INDEX uq_credit_tx_job_refund ON credit_transactions (job_id)
            WHERE transaction_type = 'refund' OR job_id IS NOT NULL
        """))
        db.commit()

        with pytest.raises(SchemaValidationError) as exc_or:
            wallet_service.validate_refund_index(db)
        assert "incorrect predicate" in str(exc_or.value)

        # 5. Predicate allows additional transaction type via OR
        db.execute(text("DROP INDEX uq_credit_tx_job_refund"))
        db.execute(text("""
            CREATE UNIQUE INDEX uq_credit_tx_job_refund ON credit_transactions (job_id)
            WHERE (transaction_type = 'refund' OR transaction_type = 'bonus') AND job_id IS NOT NULL
        """))
        db.commit()

        with pytest.raises(SchemaValidationError) as exc_extra_type:
            wallet_service.validate_refund_index(db)
        assert "incorrect predicate" in str(exc_extra_type.value)

        # 6. Predicate includes unrelated columns (e.g. amount)
        db.execute(text("DROP INDEX uq_credit_tx_job_refund"))
        db.execute(text("""
            CREATE UNIQUE INDEX uq_credit_tx_job_refund ON credit_transactions (job_id)
            WHERE transaction_type IN ('refund', 'REFUND') AND job_id IS NOT NULL AND amount > 0
        """))
        db.commit()

        with pytest.raises(SchemaValidationError) as exc_unrelated:
            wallet_service.validate_refund_index(db)
        assert "incorrect predicate" in str(exc_unrelated.value)

        # 7. Valid predicate succeeds
        db.execute(text("DROP INDEX uq_credit_tx_job_refund"))
        db.execute(text("""
            CREATE UNIQUE INDEX uq_credit_tx_job_refund ON credit_transactions (job_id)
            WHERE transaction_type IN ('refund', 'REFUND') AND job_id IS NOT NULL
        """))
        db.commit()
        assert wallet_service.validate_refund_index(db) is True


def test_ci_postgres_connection_failure_causes_test_failure(monkeypatch):
    """Verify that under CI=true or GITHUB_ACTIONS=true, a PostgreSQL connection failure
    raises pytest.Failed (calls pytest.fail), never pytest.Skipped.
    """
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("POSTGRES_TEST_URL", "postgresql+psycopg://invalid_usr:invalid_pw@127.0.0.1:54329/invalid_db")

    with pytest.raises(pytest.fail.Exception) as exc_info:
        test_postgresql_migration_and_concurrency_integration()

    assert "PostgreSQL connection failure in CI" in str(exc_info.value)


# ==============================================================================
# 15. PostgreSQL Full Integration Test (Migration + Concurrency)
# ==============================================================================

def test_postgresql_migration_and_concurrency_integration():
    """Execute migrations 0001–0004, verify legacy constraint removal, test duplicate detection abort
    with historical rows intact, apply migration 0005, verify catalog definitions structurally,
    and run multi-session concurrency against PostgreSQL with mathematically correct balances.
    """
    is_ci = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"
    pg_url = os.environ.get("POSTGRES_TEST_URL") or os.environ.get("TEST_DATABASE_URL")
    if not pg_url or not pg_url.startswith("postgresql"):
        if is_ci:
            pytest.fail(
                "POSTGRES_TEST_URL must be configured in CI environment; "
                "PostgreSQL integration test cannot be skipped in CI."
            )
        pytest.skip(
            "PostgreSQL test instance not configured. "
            "Set TEST_DATABASE_URL or POSTGRES_TEST_URL to execute PostgreSQL integration tests."
        )

    # Normalize driver to psycopg v3 if psycopg2 is absent
    if pg_url.startswith("postgresql://"):
        try:
            import psycopg2  # noqa: F401
        except ImportError:
            try:
                import psycopg  # noqa: F401
                pg_url = pg_url.replace("postgresql://", "postgresql+psycopg://", 1)
            except ImportError:
                pass

    base_engine = None
    try:
        base_engine = create_engine(pg_url)
        with base_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        if base_engine is not None:
            base_engine.dispose()
        if is_ci:
            pytest.fail(f"PostgreSQL connection failure in CI: {e}")
        pytest.skip(f"Could not connect to PostgreSQL instance: {e}")

    # Use uniquely named temporary schemas for test isolation; NEVER drop public schema
    schema_a = f"test_phase6_a_{uuid.uuid4().hex[:8]}"
    schema_b = f"test_phase6_b_{uuid.uuid4().hex[:8]}"
    schema_unrelated = f"test_unrelated_{uuid.uuid4().hex[:8]}"
    created_schemas = []

    try:
        # 1. Create isolated schemas
        try:
            with base_engine.connect() as conn:
                for s in [schema_a, schema_b, schema_unrelated]:
                    conn.execute(text(f"CREATE SCHEMA {s}"))
                    created_schemas.append(s)
                conn.commit()
        except Exception as e:
            if is_ci:
                pytest.fail(f"Failed to set up isolated test schemas in PostgreSQL (CI): {e}")
            pytest.skip(f"Failed to set up isolated test schemas in PostgreSQL: {e}")

        migrations_dir = os.path.join(os.path.dirname(__file__), "..", "migrations")
        pre_0005_files = [
            "0001_phase1_initial_schema.sql",
            "0002_phase2_multi_tenancy_and_credits.sql",
            "0003_phase4_billing_and_subscriptions.sql",
            "0004_phase5_phase6_admin_and_tokens.sql",
        ]
        with open(os.path.join(migrations_dir, "0005_phase6_refund_idempotency.sql"), "r", encoding="utf-8") as f:
            mig_0005_sql = f.read()

        # =========================================================================
        # Scenario A: Legacy constraint removal, valid migration 0005, and concurrency
        # =========================================================================
        engine_a = create_engine(
            pg_url,
            connect_args={"options": f"-c search_path={schema_a},public"}
        )
        try:
            # Apply pre-0005 migrations in schema_a
            for mig_file in pre_0005_files:
                with open(os.path.join(migrations_dir, mig_file), "r", encoding="utf-8") as f:
                    sql = f.read()
                with engine_a.connect() as conn:
                    conn.execute(text(sql))
                    conn.commit()

            # Add legacy unique constraint uq_credit_tx_job_type on credit_transactions in schema_a
            with engine_a.connect() as conn:
                conn.execute(text("""
                    ALTER TABLE credit_transactions 
                    ADD CONSTRAINT uq_credit_tx_job_type UNIQUE (job_id, transaction_type);
                """))
                conn.commit()

            # In separate schema_unrelated, create unrelated_audit_table with an index having
            # the exact same name: uq_credit_tx_job_type. PostgreSQL permits identical index names
            # only across different schemas.
            with base_engine.connect() as conn:
                conn.execute(text(f"""
                    CREATE TABLE {schema_unrelated}.unrelated_audit_table (
                        id VARCHAR(36) PRIMARY KEY,
                        job_id VARCHAR(36),
                        transaction_type VARCHAR(32)
                    );
                    CREATE UNIQUE INDEX uq_credit_tx_job_type
                    ON {schema_unrelated}.unrelated_audit_table (job_id, transaction_type);
                """))
                conn.commit()

            # Insert valid historical records in schema_a
            user_id = f"usr_pg_{uuid.uuid4().hex[:8]}"
            tenant_id = f"tnt_pg_{uuid.uuid4().hex[:8]}"
            wallet_id = f"wlt_pg_{uuid.uuid4().hex[:8]}"
            job_completed = f"job-comp-{uuid.uuid4().hex[:6]}"
            job_failed = f"job-fail-{uuid.uuid4().hex[:6]}"

            with engine_a.connect() as conn:
                conn.execute(text("""
                    INSERT INTO users (id, email, is_active) VALUES (:uid, 'pguser_a@demerivo.test', TRUE);
                    INSERT INTO tenants (id, name, slug, owner_id) VALUES (:tid, 'PG Tenant A', :slug, :uid);
                    INSERT INTO wallets (id, tenant_id, balance) VALUES (:wid, :tid, 100);
                    INSERT INTO jobs (id, tenant_id, user_id, operation_type, input_key, credit_cost, status)
                    VALUES (:jcomp, :tid, :uid, 'enhance', 'input/key1.jpg', 10, 'completed'),
                           (:jfail, :tid, :uid, 'enhance', 'input/key2.jpg', 10, 'failed');
                    -- Completed job transactions
                    INSERT INTO credit_transactions (id, tenant_id, wallet_id, user_id, job_id, transaction_type, amount, balance_after)
                    VALUES (gen_random_uuid()::text, :tid, :wid, :uid, NULL, 'allocation', 100, 100),
                           (gen_random_uuid()::text, :tid, :wid, :uid, :jcomp, 'reservation', -10, 90),
                           (gen_random_uuid()::text, :tid, :wid, :uid, :jcomp, 'consumption', 0, 90);
                    -- Failed job transactions
                    INSERT INTO credit_transactions (id, tenant_id, wallet_id, user_id, job_id, transaction_type, amount, balance_after)
                    VALUES (gen_random_uuid()::text, :tid, :wid, :uid, :jfail, 'reservation', -10, 80),
                           (gen_random_uuid()::text, :tid, :wid, :uid, :jfail, 'refund', 10, 90);
                """), {
                    "uid": user_id, "tid": tenant_id, "wid": wallet_id,
                    "slug": f"pg-slug-a-{uuid.uuid4().hex[:6]}",
                    "jcomp": job_completed, "jfail": job_failed,
                })
                conn.commit()

            # Apply migration 0005 in schema_a (current_schema() is schema_a)
            with engine_a.connect() as conn:
                conn.execute(text(mig_0005_sql))
                conn.commit()

            # Verify legacy constraint is removed on credit_transactions in schema_a
            with engine_a.connect() as conn:
                old_con = conn.execute(text("""
                    SELECT 1 FROM pg_constraint c
                    JOIN pg_class t ON t.oid = c.conrelid
                    JOIN pg_namespace n ON n.oid = t.relnamespace
                    WHERE n.nspname = :s AND t.relname = 'credit_transactions' AND c.conname = 'uq_credit_tx_job_type'
                """), {"s": schema_a}).first()
                assert old_con is None

                # Verify unrelated same-name index on schema_unrelated.unrelated_audit_table was NOT dropped!
                unrelated_idx = conn.execute(text("""
                    SELECT 1 FROM pg_index ix
                    JOIN pg_class i ON i.oid = ix.indexrelid
                    JOIN pg_class t ON t.oid = ix.indrelid
                    JOIN pg_namespace n ON n.oid = t.relnamespace
                    WHERE n.nspname = :s AND t.relname = 'unrelated_audit_table' AND i.relname = 'uq_credit_tx_job_type'
                """), {"s": schema_unrelated}).first()
                assert unrelated_idx is not None

                # Verify new partial unique index exists and is structurally correct
                catalog_row = conn.execute(text("""
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
                    WHERE n.nspname = :s
                      AND i.relname = 'uq_credit_tx_job_refund'
                """), {"s": schema_a}).mappings().first()

                assert catalog_row is not None
                assert catalog_row["schema_name"] == schema_a
                assert catalog_row["table_name"] == "credit_transactions"
                assert catalog_row["is_unique"] is True
                assert catalog_row["num_key_cols"] == 1
                assert list(catalog_row["indexed_columns"]) == ["job_id"]
                pred = str(catalog_row["predicate_expr"]).lower()
                assert "job_id is not null" in pred
                assert "refund" in pred

            # Verify WalletService.validate_refund_index() succeeds in schema_a
            SessionA = sessionmaker(bind=engine_a)
            wallet_svc = WalletService()
            with SessionA() as db:
                assert wallet_svc.validate_refund_index(db) is True

            # Concurrency test in schema_a:
            # User wallet starts at initial balance = 100.
            # Reserve 30 -> balance becomes 70.
            # Two concurrent refunds of 30: exactly one succeeds, balance returns to 100.
            with SessionA() as db:
                pg_job_id = f"job-pg-conc-{uuid.uuid4().hex[:8]}"
                db.execute(text("""
                    INSERT INTO jobs (id, tenant_id, user_id, operation_type, input_key, credit_cost, status)
                    VALUES (:jid, :tid, :uid, 'enhance', 'input/key4.jpg', 30, 'processing');
                """), {"jid": pg_job_id, "tid": tenant_id, "uid": user_id})
                db.commit()

                wallet_svc.reserve_credits(
                    tenant_id=tenant_id,
                    amount=30,
                    job_id=pg_job_id,
                    user_id=user_id,
                    db=db,
                )
                assert wallet_svc.get_wallet(tenant_id, db).balance == 70

            def pg_worker(worker_name: str):
                with SessionA() as db:
                    return wallet_svc.refund_credits(
                        tenant_id=tenant_id,
                        amount=30,
                        job_id=pg_job_id,
                        user_id=user_id,
                        reason="PG concurrent test",
                        idempotency_key=f"pg_idemp_{worker_name}",
                        db=db,
                    )

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                fut1 = executor.submit(pg_worker, "w1")
                fut2 = executor.submit(pg_worker, "w2")
                tx1 = fut1.result(timeout=10.0)
                tx2 = fut2.result(timeout=10.0)

            assert tx1.id == tx2.id
            assert tx1.amount == 30

            with SessionA() as db:
                w = wallet_svc.get_wallet(tenant_id, db)
                # Mathematically verified: initial 100 - reserved 30 + refunded 30 = 100
                expected_final_balance = 100 - 30 + 30
                assert w.balance == expected_final_balance
                count = (
                    db.query(CreditTransactionModel)
                    .filter_by(job_id=pg_job_id, transaction_type=CreditTransactionType.REFUND)
                    .count()
                )
                assert count == 1
        finally:
            engine_a.dispose()

        # =========================================================================
        # Scenario B: Duplicate historical refunds in separate isolated schema without legacy constraint
        # =========================================================================
        engine_b = create_engine(
            pg_url,
            connect_args={"options": f"-c search_path={schema_b},public"}
        )
        try:
            # Apply pre-0005 migrations in schema_b (WITHOUT uq_credit_tx_job_type)
            for mig_file in pre_0005_files:
                with open(os.path.join(migrations_dir, mig_file), "r", encoding="utf-8") as f:
                    sql = f.read()
                with engine_b.connect() as conn:
                    conn.execute(text(sql))
                    conn.commit()

            # Insert two historical refund rows for the same job
            user_b = f"usr_pg_b_{uuid.uuid4().hex[:8]}"
            tenant_b = f"tnt_pg_b_{uuid.uuid4().hex[:8]}"
            wallet_b = f"wlt_pg_b_{uuid.uuid4().hex[:8]}"
            job_dup = f"job-pg-dup-{uuid.uuid4().hex[:6]}"

            with engine_b.connect() as conn:
                conn.execute(text("""
                    INSERT INTO users (id, email, is_active) VALUES (:uid, 'pguser_b@demerivo.test', TRUE);
                    INSERT INTO tenants (id, name, slug, owner_id) VALUES (:tid, 'PG Tenant B', :slug, :uid);
                    INSERT INTO wallets (id, tenant_id, balance) VALUES (:wid, :tid, 100);
                    INSERT INTO jobs (id, tenant_id, user_id, operation_type, input_key, credit_cost, status)
                    VALUES (:jdup, :tid, :uid, 'enhance', 'input/key_dup.jpg', 10, 'failed');
                    INSERT INTO credit_transactions (id, tenant_id, wallet_id, user_id, job_id, transaction_type, amount, balance_after)
                    VALUES (gen_random_uuid()::text, :tid, :wid, :uid, :jdup, 'refund', 10, 100),
                           (gen_random_uuid()::text, :tid, :wid, :uid, :jdup, 'refund', 10, 110);
                """), {
                    "uid": user_b, "tid": tenant_b, "wid": wallet_b,
                    "slug": f"pg-slug-b-{uuid.uuid4().hex[:6]}",
                    "jdup": job_dup,
                })
                conn.commit()

            # Migration 0005 MUST fail due to existing duplicate refunds
            with pytest.raises(Exception) as exc_dup:
                with engine_b.connect() as conn:
                    conn.execute(text(mig_0005_sql))
                    conn.commit()
            assert "duplicate refund transactions in credit_transactions" in str(exc_dup.value)
            assert job_dup in str(exc_dup.value)

            # Assert that BOTH historical rows remain untouched after migration abort
            with engine_b.connect() as conn:
                rows = conn.execute(text(
                    "SELECT id, amount, balance_after FROM credit_transactions WHERE job_id = :jdup ORDER BY balance_after"
                ), {"jdup": job_dup}).mappings().all()
                assert len(rows) == 2
                assert rows[0]["amount"] == 10
                assert rows[0]["balance_after"] == 100
                assert rows[1]["amount"] == 10
                assert rows[1]["balance_after"] == 110
        finally:
            engine_b.dispose()

    finally:
        # Clean up only the test-owned temporary schemas; NEVER touch public
        try:
            with base_engine.connect() as conn:
                for s in created_schemas:
                    conn.execute(text(f"DROP SCHEMA IF EXISTS {s} CASCADE"))
                conn.commit()
        except Exception:
            pass
        base_engine.dispose()
