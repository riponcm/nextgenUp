"""Automated tests for credit accounting, wallet operations, atomic locking, and pricing rules."""

import pytest
from sqlalchemy.orm import Session

from demerivo.credits.pricing import CreditPricingPolicy
from demerivo.credits.wallet_service import (
    CreditTransactionType,
    InsufficientCreditsError,
    WalletService,
)
from demerivo.db.models import OperationType, TenantModel, UserModel, WalletModel
from demerivo.db.session import Base, create_db_engine


@pytest.fixture
def test_db():
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def sample_tenant(test_db: Session):
    user = UserModel(id="usr-credit", email="credit@test.com", is_active=True)
    tenant = TenantModel(id="tnt-credit", name="Credit Tenant", slug="credit-tenant", owner_id=user.id)
    wallet = WalletModel(id="wlt-credit", tenant_id=tenant.id, balance=10)
    test_db.add_all([user, tenant, wallet])
    test_db.commit()
    return tenant, user, wallet


class TestCreditPricingPolicy:
    """Test operation pricing rules and parameter add-on costs."""

    def test_pricing_base_operations(self):
        pricing = CreditPricingPolicy()
        assert pricing.calculate_cost(OperationType.BG_REMOVE) == 1
        assert pricing.calculate_cost(OperationType.ENHANCE) == 1
        assert pricing.calculate_cost(OperationType.UPSCALE_2X) == 2
        assert pricing.calculate_cost(OperationType.UPSCALE_4X) == 4
        assert pricing.calculate_cost(OperationType.DENOISE) == 1
        assert pricing.calculate_cost(OperationType.DEBLUR) == 1

    def test_pricing_with_8k_resolution_deprecated(self):
        pricing = CreditPricingPolicy()
        # 8K resolution is removed from the pricing policy; upscale_4x remains standard 4 credits
        cost_4x = pricing.calculate_cost(OperationType.UPSCALE_4X, {"is_8k": True})
        assert cost_4x == 4

    def test_pricing_with_face_restore(self):
        pricing = CreditPricingPolicy()
        cost = pricing.calculate_cost(OperationType.UPSCALE_4X, {"face_restore": True})
        # 4 (base) + 2 (face_restore) = 6
        assert cost == 6

        cost_bg = pricing.calculate_cost(OperationType.BG_REMOVE, {"face_restore": True})
        # 1 (base) + 2 (face_restore) = 3
        assert cost_bg == 3


class TestCreditWalletService:
    """Test atomic wallet balance adjustments, transactions, and idempotency."""

    def test_allocate_credits(self, test_db: Session, sample_tenant):
        tenant, user, wallet = sample_tenant
        service = WalletService()

        tx = service.allocate_credits(
            tenant_id=tenant.id,
            amount=50,
            description="Purchased credits",
            db=test_db,
            user_id=user.id,
        )
        assert tx.amount == 50
        assert tx.balance_after == 60
        assert tx.transaction_type == CreditTransactionType.ALLOCATION

        w = service.get_wallet(tenant.id, test_db)
        assert w.balance == 60

    def test_reserve_credits_success(self, test_db: Session, sample_tenant):
        tenant, user, wallet = sample_tenant
        service = WalletService()

        # Balance starts at 10; reserve 4
        tx = service.reserve_credits(
            tenant_id=tenant.id,
            amount=4,
            job_id="job-101",
            user_id=user.id,
            db=test_db,
        )
        assert tx.amount == -4
        assert tx.balance_after == 6
        assert tx.transaction_type == CreditTransactionType.RESERVATION

        w = service.get_wallet(tenant.id, test_db)
        assert w.balance == 6

    def test_insufficient_credits_blocks_reservation(self, test_db: Session, sample_tenant):
        tenant, user, wallet = sample_tenant
        service = WalletService()

        # Balance is 10; try to reserve 15 -> Error
        with pytest.raises(InsufficientCreditsError) as exc_info:
            service.reserve_credits(
                tenant_id=tenant.id,
                amount=15,
                job_id="job-too-expensive",
                user_id=user.id,
                db=test_db,
            )
        assert exc_info.value.required == 15
        assert exc_info.value.available == 10

        # Balance must remain unchanged at 10
        w = service.get_wallet(tenant.id, test_db)
        assert w.balance == 10

    def test_idempotent_reservation_prevents_double_charge(self, test_db: Session, sample_tenant):
        tenant, user, wallet = sample_tenant
        service = WalletService()

        # Initial reservation
        tx1 = service.reserve_credits(
            tenant_id=tenant.id,
            amount=4,
            job_id="job-idemp",
            user_id=user.id,
            db=test_db,
            idempotency_key="idemp-key-xyz",
        )
        assert tx1.balance_after == 6

        # Duplicate call with identical idempotency key
        tx2 = service.reserve_credits(
            tenant_id=tenant.id,
            amount=4,
            job_id="job-idemp",
            user_id=user.id,
            db=test_db,
            idempotency_key="idemp-key-xyz",
        )
        assert tx2.id == tx1.id
        assert tx2.balance_after == 6

        # Balance must still be 6, NOT 2!
        w = service.get_wallet(tenant.id, test_db)
        assert w.balance == 6

    def test_refund_credits(self, test_db: Session, sample_tenant):
        tenant, user, wallet = sample_tenant
        service = WalletService()

        # Reserve 4 (balance becomes 6)
        service.reserve_credits(
            tenant_id=tenant.id,
            amount=4,
            job_id="job-refund-test",
            user_id=user.id,
            db=test_db,
        )

        # Refund 4
        tx_refund = service.refund_credits(
            tenant_id=tenant.id,
            amount=4,
            job_id="job-refund-test",
            user_id=user.id,
            reason="Processing cancelled",
            db=test_db,
        )
        assert tx_refund.amount == 4
        assert tx_refund.balance_after == 10
        assert tx_refund.transaction_type == CreditTransactionType.REFUND

        w = service.get_wallet(tenant.id, test_db)
        assert w.balance == 10

    def test_finalize_consumption(self, test_db: Session, sample_tenant):
        tenant, user, wallet = sample_tenant
        service = WalletService()

        # Reserve 4
        service.reserve_credits(
            tenant_id=tenant.id,
            amount=4,
            job_id="job-consumption-test",
            user_id=user.id,
            db=test_db,
        )

        # Finalize consumption
        tx_cons = service.finalize_consumption(
            job_id="job-consumption-test",
            db=test_db,
        )
        assert tx_cons is not None
        assert tx_cons.transaction_type == CreditTransactionType.CONSUMPTION
        assert tx_cons.balance_after == 6

        # Verify transaction history
        txs = service.get_transactions(tenant.id, test_db)
        assert len(txs) == 2
