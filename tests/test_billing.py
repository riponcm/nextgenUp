"""Comprehensive automated tests for Phase 4 Subscription Billing & Payment Gateways.

Covers plan catalog reflection, subscription checkout, payment verification,
signed webhooks, replay protection, credit allocation, idempotency, and cancellation.
"""

import json
import unittest
import uuid
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from demerivo.api.routers.billing import (
    CancelSubscriptionRequest,
    CreateCheckoutRequest,
    VerifyPaymentRequest,
    cancel_subscription,
    create_subscription_checkout,
    get_billing_history,
    get_current_subscription,
    list_available_plans,
    verify_payment_checkout,
)
from demerivo.billing import (
    BillingService,
    InvalidSignatureError,
    MockPaymentProvider,
    PLANS_CATALOG,
)
from demerivo.config import Settings
from demerivo.db.models import (
    Base,
    CreditTransactionModel,
    CreditTransactionType,
    InvoiceModel,
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
)


class TestSubscriptionBilling(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            env="testing",
            database_url="sqlite:///:memory:",
            supabase_jwt_secret="billing-test-jwt-secret-key-32chars!!",
            payment_provider="mock",
        )
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        self.TestingSessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine
        )
        Base.metadata.create_all(bind=self.engine)

        self.db = self.TestingSessionLocal()
        self.mock_provider = MockPaymentProvider(secret="mock-billing-test-secret")
        self.billing_service = BillingService(provider=self.mock_provider)
        self.billing_service.sync_plans_to_db(self.db)

        # Create Test Users & Tenants
        self.owner_user = UserModel(
            id=str(uuid.uuid4()),
            email="owner@demerivo.com",
            display_name="Studio Owner",
            is_active=True,
            email_verified=True,
        )
        self.member_user = UserModel(
            id=str(uuid.uuid4()),
            email="member@demerivo.com",
            display_name="Team Member",
            is_active=True,
            email_verified=True,
        )
        self.db.add_all([self.owner_user, self.member_user])
        self.db.commit()

        self.tenant = TenantModel(
            id=str(uuid.uuid4()),
            name="Demerivo Studio Workspace",
            slug=f"workspace-{uuid.uuid4().hex[:8]}",
            is_personal=False,
            owner_id=self.owner_user.id,
        )
        self.db.add(self.tenant)
        self.db.commit()

        self.owner_member = TenantMemberModel(
            id=str(uuid.uuid4()),
            tenant_id=self.tenant.id,
            user_id=self.owner_user.id,
            role=TenantRole.OWNER,
        )
        self.regular_member = TenantMemberModel(
            id=str(uuid.uuid4()),
            tenant_id=self.tenant.id,
            user_id=self.member_user.id,
            role=TenantRole.MEMBER,
        )
        self.wallet = WalletModel(
            id=str(uuid.uuid4()),
            tenant_id=self.tenant.id,
            balance=10,
        )
        self.db.add_all([self.owner_member, self.regular_member, self.wallet])
        self.db.commit()

        self.owner_tenant_ctx = (self.tenant, TenantRole.OWNER)
        self.member_tenant_ctx = (self.tenant, TenantRole.MEMBER)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_list_plans_endpoint(self):
        """Verify public plans endpoint returns all 4 defined subscription tiers."""
        data = list_available_plans(billing_service=self.billing_service)
        plans = data["plans"]
        self.assertEqual(len(plans), 4)

        codes = [p["code"] for p in plans]
        self.assertIn("free", codes)
        self.assertIn("starter", codes)
        self.assertIn("pro", codes)
        self.assertIn("studio", codes)

        # Check starter plan pricing & credits
        starter = next(p for p in plans if p["code"] == "starter")
        self.assertEqual(starter["price_paise"], 19900)
        self.assertEqual(starter["price_inr"], 199.0)
        self.assertEqual(starter["monthly_credits"], 40)

        # Check pro plan
        pro = next(p for p in plans if p["code"] == "pro")
        self.assertEqual(pro["price_paise"], 49900)
        self.assertEqual(pro["monthly_credits"], 150)

        # Check studio plan
        studio = next(p for p in plans if p["code"] == "studio")
        self.assertEqual(studio["price_paise"], 99900)
        self.assertEqual(studio["monthly_credits"], 400)

    def test_get_current_subscription_defaults_to_free(self):
        """A new tenant should automatically resolve to the Free Explorer subscription."""
        data = get_current_subscription(
            db=self.db,
            tenant_context=self.owner_tenant_ctx,
            billing_service=self.billing_service,
        )
        self.assertEqual(data["subscription"]["plan_code"], "free")
        self.assertEqual(data["subscription"]["status"], "active")

    def test_member_cannot_modify_subscription(self):
        """Regular members cannot initiate checkout or change subscription plans."""
        payload = CreateCheckoutRequest(plan_code="pro")
        with self.assertRaises(HTTPException) as cm:
            create_subscription_checkout(
                payload=payload,
                db=self.db,
                user=self.member_user,
                tenant_context=self.member_tenant_ctx,
                billing_service=self.billing_service,
            )
        self.assertEqual(cm.exception.status_code, 403)
        self.assertIn("Only tenant owners and admins", cm.exception.detail)

    def test_create_subscription_checkout_paid_plan(self):
        """Owner can initiate checkout for Starter plan, returning order details."""
        payload = CreateCheckoutRequest(plan_code="starter")
        data = create_subscription_checkout(
            payload=payload,
            db=self.db,
            user=self.owner_user,
            tenant_context=self.owner_tenant_ctx,
            billing_service=self.billing_service,
        )
        self.assertEqual(data["checkout_type"], "paid")
        self.assertEqual(data["plan_code"], "starter")
        self.assertEqual(data["amount_paise"], 19900)
        self.assertTrue(data["order_id"].startswith("order_mock_"))

    def test_verify_payment_checkout_success_and_credit_allocation(self):
        """Cryptographically valid checkout verification activates subscription and credits."""
        # 1. Create checkout order
        payload = CreateCheckoutRequest(plan_code="starter")
        checkout = create_subscription_checkout(
            payload=payload,
            db=self.db,
            user=self.owner_user,
            tenant_context=self.owner_tenant_ctx,
            billing_service=self.billing_service,
        )
        order_id = checkout["order_id"]
        payment_id = f"pay_{uuid.uuid4().hex[:12]}"

        # 2. Generate valid mock HMAC signature
        valid_signature = self.mock_provider.generate_mock_signature(order_id, payment_id)

        # Record balance before activation
        initial_balance = self.wallet.balance

        # 3. Submit verification
        verify_req = VerifyPaymentRequest(
            order_id=order_id,
            payment_id=payment_id,
            signature=valid_signature,
            plan_code="starter",
        )
        result = verify_payment_checkout(
            payload=verify_req,
            db=self.db,
            user=self.owner_user,
            tenant_context=self.owner_tenant_ctx,
            billing_service=self.billing_service,
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["credits_allocated"], 40)
        self.assertTrue(result["invoice_number"].startswith("INV-"))

        # Verify wallet balance was credited by exactly 40 credits
        self.db.refresh(self.wallet)
        self.assertEqual(self.wallet.balance, initial_balance + 40)

        # Verify subscription is now active on starter
        sub_data = get_current_subscription(
            db=self.db,
            tenant_context=self.owner_tenant_ctx,
            billing_service=self.billing_service,
        )
        self.assertEqual(sub_data["subscription"]["plan_code"], "starter")

    def test_verify_payment_invalid_signature_rejected(self):
        """Invalid or forged payment signature is rejected with HTTP 400."""
        verify_req = VerifyPaymentRequest(
            order_id="order_mock_test",
            payment_id="pay_test",
            signature="forged_invalid_signature_hex",
            plan_code="starter",
        )
        with self.assertRaises(HTTPException) as cm:
            verify_payment_checkout(
                payload=verify_req,
                db=self.db,
                user=self.owner_user,
                tenant_context=self.owner_tenant_ctx,
                billing_service=self.billing_service,
            )
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("invalid signature", cm.exception.detail.lower())

    def test_cancel_subscription(self):
        """Owner can cancel subscription, setting cancel_at_period_end=True."""
        # First upgrade to starter
        checkout = create_subscription_checkout(
            payload=CreateCheckoutRequest(plan_code="starter"),
            db=self.db,
            user=self.owner_user,
            tenant_context=self.owner_tenant_ctx,
            billing_service=self.billing_service,
        )
        payment_id = f"pay_{uuid.uuid4().hex[:12]}"
        sig = self.mock_provider.generate_mock_signature(checkout["order_id"], payment_id)
        verify_payment_checkout(
            payload=VerifyPaymentRequest(
                order_id=checkout["order_id"],
                payment_id=payment_id,
                signature=sig,
                plan_code="starter",
            ),
            db=self.db,
            user=self.owner_user,
            tenant_context=self.owner_tenant_ctx,
            billing_service=self.billing_service,
        )

        req = CancelSubscriptionRequest(immediately=False)
        data = cancel_subscription(
            payload=req,
            db=self.db,
            tenant_context=self.owner_tenant_ctx,
            billing_service=self.billing_service,
        )
        self.assertEqual(data["status"], "cancelled")
        self.assertTrue(data["subscription"]["cancel_at_period_end"])

    def test_billing_history_endpoint(self):
        """Billing history reflects all generated invoices for the tenant."""
        # 1. Create checkout and verify payment to generate invoice
        payload = CreateCheckoutRequest(plan_code="starter")
        checkout = create_subscription_checkout(
            payload=payload,
            db=self.db,
            user=self.owner_user,
            tenant_context=self.owner_tenant_ctx,
            billing_service=self.billing_service,
        )
        order_id = checkout["order_id"]
        payment_id = f"pay_{uuid.uuid4().hex[:12]}"
        signature = self.mock_provider.generate_mock_signature(order_id, payment_id)

        verify_payment_checkout(
            payload=VerifyPaymentRequest(
                order_id=order_id,
                payment_id=payment_id,
                signature=signature,
                plan_code="starter",
            ),
            db=self.db,
            user=self.owner_user,
            tenant_context=self.owner_tenant_ctx,
            billing_service=self.billing_service,
        )

        # Check history
        history_data = get_billing_history(
            db=self.db,
            tenant_context=self.owner_tenant_ctx,
            billing_service=self.billing_service,
        )
        history = history_data["history"]
        self.assertGreaterEqual(len(history), 1)
        self.assertEqual(history[0]["amount_paise"], 19900)
        self.assertEqual(history[0]["currency"], "INR")

    def test_razorpay_webhook_signed_verification_and_renewal_credits(self):
        """Signed Razorpay renewal webhook allocates monthly credits idempotently."""
        initial_balance = self.wallet.balance
        event_id = f"evt_{uuid.uuid4().hex[:12]}"
        webhook_payload = {
            "entity": "event",
            "account_id": "acc_mock_test",
            "event": "payment.captured",
            "id": event_id,
            "payload": {
                "payment": {
                    "entity": {
                        "id": f"pay_{uuid.uuid4().hex[:12]}",
                        "amount": 19900,
                        "currency": "INR",
                        "status": "captured",
                        "notes": {
                            "tenant_id": self.tenant.id,
                            "plan_code": "starter",
                        },
                    }
                }
            },
        }
        raw_body = json.dumps(webhook_payload).encode("utf-8")
        signature = self.mock_provider.generate_mock_webhook_signature(raw_body)

        # 1. Process first webhook event
        res1 = self.billing_service.process_webhook_event(
            db=self.db,
            raw_body=raw_body,
            signature=signature,
            event_payload=webhook_payload,
        )
        self.assertEqual(res1["status"], "processed")
        self.assertEqual(res1["credits_allocated"], 40)

        # Verify wallet credited
        self.db.refresh(self.wallet)
        self.assertEqual(self.wallet.balance, initial_balance + 40)

        # 2. Replay duplicate webhook event -> should NOT double credit
        res2 = self.billing_service.process_webhook_event(
            db=self.db,
            raw_body=raw_body,
            signature=signature,
            event_payload=webhook_payload,
        )
        # Verify idempotency prevented duplicate allocation
        self.db.refresh(self.wallet)
        self.assertEqual(self.wallet.balance, initial_balance + 40)

    def test_razorpay_webhook_invalid_signature_rejected(self):
        """Webhook with invalid signature header is rejected."""
        payload = {"event": "payment.captured"}
        raw_body = json.dumps(payload).encode("utf-8")
        with self.assertRaises(InvalidSignatureError):
            self.billing_service.process_webhook_event(
                db=self.db,
                raw_body=raw_body,
                signature="invalid_bogus_signature",
                event_payload=payload,
            )


if __name__ == "__main__":
    unittest.main()
