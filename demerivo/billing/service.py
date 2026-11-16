"""Subscription and Billing Service for Demerivo PixelFix.

Manages plan synchronization, subscription checkout, payment verification,
signed webhook processing, monthly credit allocation, and idempotent renewals.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from demerivo.billing.base import (
    InvalidSignatureError,
    PaymentProvider,
    PaymentProviderError,
)
from demerivo.billing.plans import PLANS_CATALOG, get_plan, list_plans
from demerivo.credits.wallet_service import WalletService
from demerivo.db.models import (
    InvoiceModel,
    InvoiceStatus,
    PaymentModel,
    PaymentStatus,
    PlanModel,
    SubscriptionModel,
    SubscriptionStatus,
    TenantModel,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BillingService:
    """Core domain service for subscription management and payments."""

    def __init__(
        self,
        provider: PaymentProvider,
        wallet_service: Optional[WalletService] = None,
    ):
        self.provider = provider
        self.wallet_service = wallet_service or WalletService()

    def sync_plans_to_db(self, db: Session) -> Dict[str, PlanModel]:
        """Ensure all authoritative plans are synchronized in the database."""
        plans_by_code: Dict[str, PlanModel] = {}
        for code, plan_def in PLANS_CATALOG.items():
            record = db.query(PlanModel).filter(PlanModel.code == code).first()
            if not record:
                record = PlanModel(
                    id=f"plan_{code}",
                    code=code,
                    name=plan_def.name,
                    description=plan_def.description,
                    price_paise=plan_def.price_paise,
                    monthly_credits=plan_def.monthly_credits,
                    currency=plan_def.currency,
                    is_active=True,
                    features=plan_def.features,
                )
                db.add(record)
                db.flush()
            else:
                record.name = plan_def.name
                record.description = plan_def.description
                record.price_paise = plan_def.price_paise
                record.monthly_credits = plan_def.monthly_credits
                record.features = plan_def.features
            plans_by_code[code] = record
        db.commit()
        return plans_by_code

    def get_plans(self) -> List[Dict[str, Any]]:
        """Return public plan definitions."""
        return list_plans()

    def get_or_create_tenant_subscription(
        self,
        db: Session,
        tenant_id: str,
    ) -> SubscriptionModel:
        """Retrieve existing tenant subscription or provision a default FREE tier."""
        sub = db.query(SubscriptionModel).filter(SubscriptionModel.tenant_id == tenant_id).first()
        if sub:
            return sub

        # Ensure plans exist in DB
        plans = self.sync_plans_to_db(db)
        free_plan = plans.get("free") or db.query(PlanModel).filter(PlanModel.code == "free").first()
        if not free_plan:
            raise RuntimeError("Free plan definition missing from database.")

        now = utcnow()
        sub = SubscriptionModel(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            plan_id=free_plan.id,
            plan_code="free",
            status=SubscriptionStatus.ACTIVE,
            provider="system",
            current_period_start=now,
            current_period_end=now + timedelta(days=365 * 10),  # Indefinite free tier
            cancel_at_period_end=False,
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)
        return sub

    def create_subscription_checkout(
        self,
        db: Session,
        tenant_id: str,
        user_id: str,
        plan_code: str,
    ) -> Dict[str, Any]:
        """Create a server-side order/subscription checkout payload for client SDK."""
        target_plan = get_plan(plan_code)
        plans = self.sync_plans_to_db(db)
        plan_record = plans[target_plan.code]

        if target_plan.code == "free":
            # Free tier requires no payment gateway order
            sub = self.get_or_create_tenant_subscription(db, tenant_id)
            sub.plan_id = plan_record.id
            sub.plan_code = "free"
            sub.status = SubscriptionStatus.ACTIVE
            db.commit()
            db.refresh(sub)
            return {
                "checkout_type": "free",
                "plan_code": "free",
                "status": "active",
                "message": "Switched to Free Explorer plan.",
            }

        # Paid plan: generate order with payment provider
        order_info = self.provider.create_order_or_subscription(
            tenant_id=tenant_id,
            plan_code=target_plan.code,
            amount_paise=target_plan.price_paise,
            currency=target_plan.currency,
            notes={"tenant_id": tenant_id, "user_id": user_id, "plan_code": target_plan.code},
        )

        # Track pending payment attempt in ledger
        payment = PaymentModel(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            provider=self.provider.provider_name,
            provider_order_id=order_info.get("order_id"),
            amount_paise=target_plan.price_paise,
            currency=target_plan.currency,
            status=PaymentStatus.CREATED,
            idempotency_key=f"checkout_{tenant_id}_{order_info.get('order_id')}",
        )
        db.add(payment)
        db.commit()

        return {
            "checkout_type": "paid",
            "provider": self.provider.provider_name,
            "order_id": order_info.get("order_id"),
            "key_id": order_info.get("key_id"),
            "amount_paise": target_plan.price_paise,
            "currency": target_plan.currency,
            "plan_code": target_plan.code,
            "plan_name": target_plan.name,
        }

    def verify_and_activate_subscription(
        self,
        db: Session,
        tenant_id: str,
        user_id: str,
        order_id: str,
        payment_id: str,
        signature: str,
        plan_code: str,
    ) -> Dict[str, Any]:
        """Verify client-submitted payment signature and activate subscription with credits."""
        target_plan = get_plan(plan_code)

        # 1. Cryptographically verify signature
        is_valid = self.provider.verify_payment_signature(
            order_id=order_id,
            payment_id=payment_id,
            signature=signature,
        )
        if not is_valid:
            raise InvalidSignatureError("Payment verification failed: invalid signature.")

        # 2. Update payment status to CAPTURED
        payment = db.query(PaymentModel).filter(
            PaymentModel.tenant_id == tenant_id,
            PaymentModel.provider_order_id == order_id,
        ).first()

        now = utcnow()
        if not payment:
            payment = PaymentModel(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                provider=self.provider.provider_name,
                provider_order_id=order_id,
                provider_payment_id=payment_id,
                amount_paise=target_plan.price_paise,
                currency=target_plan.currency,
                status=PaymentStatus.CAPTURED,
                idempotency_key=f"verify_{payment_id}",
            )
            db.add(payment)
        else:
            payment.provider_payment_id = payment_id
            payment.status = PaymentStatus.CAPTURED

        # 3. Synchronize plan and activate/upgrade subscription
        plans = self.sync_plans_to_db(db)
        plan_record = plans[target_plan.code]

        sub = self.get_or_create_tenant_subscription(db, tenant_id)
        sub.plan_id = plan_record.id
        sub.plan_code = target_plan.code
        sub.status = SubscriptionStatus.ACTIVE
        sub.provider = self.provider.provider_name
        sub.provider_subscription_id = order_id
        sub.current_period_start = now
        sub.current_period_end = now + timedelta(days=30)
        sub.cancel_at_period_end = False

        # 4. Generate Invoice Record
        invoice_number = f"INV-{now.strftime('%Y%m')}-{uuid.uuid4().hex[:6].upper()}"
        invoice = InvoiceModel(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            subscription_id=sub.id,
            payment_id=payment.id,
            invoice_number=invoice_number,
            amount_paise=target_plan.price_paise,
            currency=target_plan.currency,
            status=InvoiceStatus.PAID,
            billing_period_start=now,
            billing_period_end=sub.current_period_end,
        )
        db.add(invoice)

        # 5. Allocate Monthly Credits Idempotently
        credit_idempotency_key = f"sub_activation_{tenant_id}_{payment_id}"
        self.wallet_service.allocate_credits(
            db=db,
            tenant_id=tenant_id,
            amount=target_plan.monthly_credits,
            user_id=user_id,
            idempotency_key=credit_idempotency_key,
            description=f"Monthly subscription credit allocation - {target_plan.name}",
        )

        db.commit()
        db.refresh(sub)

        return {
            "status": "success",
            "subscription": sub.to_dict(),
            "invoice_number": invoice_number,
            "credits_allocated": target_plan.monthly_credits,
        }

    def cancel_tenant_subscription(
        self,
        db: Session,
        tenant_id: str,
        immediately: bool = False,
    ) -> SubscriptionModel:
        """Cancel tenant subscription either at period end or immediately."""
        sub = self.get_or_create_tenant_subscription(db, tenant_id)
        if sub.plan_code == "free":
            return sub

        if sub.provider_subscription_id and sub.provider != "mock":
            self.provider.cancel_subscription(sub.provider_subscription_id, at_period_end=not immediately)

        if immediately:
            plans = self.sync_plans_to_db(db)
            free_plan = plans["free"]
            sub.plan_id = free_plan.id
            sub.plan_code = "free"
            sub.status = SubscriptionStatus.ACTIVE
            sub.cancel_at_period_end = False
        else:
            sub.cancel_at_period_end = True

        db.commit()
        db.refresh(sub)
        return sub

    def process_webhook_event(
        self,
        db: Session,
        raw_body: bytes,
        signature: str,
        event_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Handle cryptographically verified webhook event from payment provider."""
        # 1. Verify webhook HMAC signature
        is_valid = self.provider.verify_webhook_signature(raw_body, signature)
        if not is_valid:
            raise InvalidSignatureError("Webhook HMAC signature validation failed.")

        event_name = event_payload.get("event") or event_payload.get("type", "")
        event_id = event_payload.get("id") or str(uuid.uuid4())

        # Check for replay / idempotency
        existing_tx = db.query(PaymentModel).filter(
            PaymentModel.idempotency_key == f"webhook_{event_id}"
        ).first()
        if existing_tx:
            return {"status": "duplicate_ignored", "event_id": event_id}

        # Process specific events
        if event_name in ("payment.captured", "subscription.charged", "invoice.paid"):
            payload_entity = (
                event_payload.get("payload", {})
                .get("payment", {})
                .get("entity", {})
            ) or event_payload.get("data", {}).get("object", {})

            tenant_id = payload_entity.get("notes", {}).get("tenant_id")
            plan_code = payload_entity.get("notes", {}).get("plan_code")
            order_id = payload_entity.get("order_id")
            payment_id = payload_entity.get("id")
            amount_paise = payload_entity.get("amount", 0)
            currency = payload_entity.get("currency", "INR")

            if tenant_id:
                # Record successful payment ledger entry with idempotency key
                payment = PaymentModel(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    provider=self.provider.provider_name,
                    provider_order_id=order_id,
                    provider_payment_id=payment_id,
                    amount_paise=amount_paise,
                    currency=currency,
                    status=PaymentStatus.CAPTURED,
                    idempotency_key=f"webhook_{event_id}",
                )
                db.add(payment)

                credits_allocated = 0
                if plan_code:
                    plan = get_plan(plan_code)
                    idempotency_key = f"webhook_renewal_{event_id}"
                    self.wallet_service.allocate_credits(
                        db=db,
                        tenant_id=tenant_id,
                        amount=plan.monthly_credits,
                        idempotency_key=idempotency_key,
                        description=f"Subscription renewal credit allocation - {plan.name}",
                    )
                    credits_allocated = plan.monthly_credits

                    # Extend subscription period
                    sub = db.query(SubscriptionModel).filter(
                        SubscriptionModel.tenant_id == tenant_id
                    ).first()
                    if sub:
                        sub.status = SubscriptionStatus.ACTIVE
                        sub.current_period_end = utcnow() + timedelta(days=30)
                        sub.cancel_at_period_end = False

                db.commit()
                return {"status": "processed", "event": event_name, "credits_allocated": credits_allocated}

        elif event_name in ("payment.failed",):
            payload_entity = (
                event_payload.get("payload", {})
                .get("payment", {})
                .get("entity", {})
            ) or event_payload.get("data", {}).get("object", {})

            tenant_id = payload_entity.get("notes", {}).get("tenant_id")
            if tenant_id:
                payment = PaymentModel(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    provider=self.provider.provider_name,
                    provider_order_id=payload_entity.get("order_id"),
                    provider_payment_id=payload_entity.get("id"),
                    amount_paise=payload_entity.get("amount", 0),
                    currency=payload_entity.get("currency", "INR"),
                    status=PaymentStatus.FAILED,
                    idempotency_key=f"webhook_{event_id}",
                )
                db.add(payment)
                db.commit()
                return {"status": "payment_failed_recorded", "event": event_name}

        elif event_name in ("refund.processed", "payment.refunded"):
            payload_entity = (
                event_payload.get("payload", {})
                .get("refund", {})
                .get("entity", {})
            ) or event_payload.get("data", {}).get("object", {})

            tenant_id = payload_entity.get("notes", {}).get("tenant_id")
            if tenant_id:
                payment = PaymentModel(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    provider=self.provider.provider_name,
                    provider_order_id=payload_entity.get("order_id"),
                    provider_payment_id=payload_entity.get("payment_id") or payload_entity.get("id"),
                    amount_paise=payload_entity.get("amount", 0),
                    currency=payload_entity.get("currency", "INR"),
                    status=PaymentStatus.REFUNDED,
                    idempotency_key=f"webhook_{event_id}",
                )
                db.add(payment)
                db.commit()
                return {"status": "refund_processed", "event": event_name}

        elif event_name in ("subscription.cancelled", "customer.subscription.deleted"):
            sub_id = (
                event_payload.get("payload", {})
                .get("subscription", {})
                .get("entity", {})
                .get("id")
            )
            if sub_id:
                sub = db.query(SubscriptionModel).filter(
                    SubscriptionModel.provider_subscription_id == sub_id
                ).first()
                if sub:
                    sub.status = SubscriptionStatus.CANCELLED
                    payment = PaymentModel(
                        id=str(uuid.uuid4()),
                        tenant_id=sub.tenant_id,
                        provider=self.provider.provider_name,
                        provider_order_id=sub_id,
                        status=PaymentStatus.CANCELLED,
                        idempotency_key=f"webhook_{event_id}",
                    )
                    db.add(payment)
                    db.commit()
            return {"status": "cancelled", "subscription_id": sub_id}

        return {"status": "unhandled_event_accepted", "event": event_name}

    def get_billing_history(
        self,
        db: Session,
        tenant_id: str,
    ) -> List[Dict[str, Any]]:
        """Retrieve past invoices and payments for tenant."""
        invoices = (
            db.query(InvoiceModel)
            .filter(InvoiceModel.tenant_id == tenant_id)
            .order_by(InvoiceModel.created_at.desc())
            .all()
        )
        return [inv.to_dict() for inv in invoices]
