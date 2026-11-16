"""Deterministic Mock Payment Provider for Demerivo PixelFix.

Used in local development, test environments, and CI/CD pipelines to guarantee
100% reproducible payment lifecycles without external network dependencies.
"""

import hashlib
import hmac
import uuid
from typing import Any, Dict, Optional

from demerivo.billing.base import PaymentProvider


class MockPaymentProvider(PaymentProvider):
    """Deterministic mock payment provider."""

    def __init__(self, secret: str = "mock-payment-secret-key-12345"):
        self.secret = secret
        self.customers: Dict[str, str] = {}
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.cancelled_subscriptions: set = set()

    @property
    def provider_name(self) -> str:
        return "mock"

    def create_customer(
        self,
        user_id: str,
        email: str,
        name: Optional[str] = None
    ) -> str:
        cust_id = f"cust_mock_{user_id[:8]}"
        self.customers[user_id] = cust_id
        return cust_id

    def create_order_or_subscription(
        self,
        tenant_id: str,
        plan_code: str,
        amount_paise: int,
        currency: str = "INR",
        customer_id: Optional[str] = None,
        notes: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        order_id = f"order_mock_{uuid.uuid4().hex[:12]}"
        order_data = {
            "id": order_id,
            "tenant_id": tenant_id,
            "plan_code": plan_code,
            "amount_paise": amount_paise,
            "currency": currency,
            "customer_id": customer_id,
            "status": "created",
            "notes": notes or {},
        }
        self.orders[order_id] = order_data
        return {
            "order_id": order_id,
            "provider": self.provider_name,
            "key_id": "mock_key_id_live_test",
            "amount_paise": amount_paise,
            "currency": currency,
            "plan_code": plan_code,
        }

    def generate_mock_signature(self, order_id: str, payment_id: str) -> str:
        """Generate a valid HMAC SHA256 signature for mock test payloads."""
        msg = f"{order_id}|{payment_id}".encode("utf-8")
        return hmac.new(self.secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()

    def generate_mock_webhook_signature(self, raw_body: bytes) -> str:
        """Generate a valid HMAC SHA256 signature for mock webhook payloads."""
        return hmac.new(self.secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    def verify_payment_signature(
        self,
        order_id: str,
        payment_id: str,
        signature: str
    ) -> bool:
        expected = self.generate_mock_signature(order_id, payment_id)
        return hmac.compare_digest(expected, signature)

    def verify_webhook_signature(
        self,
        raw_body: bytes,
        signature: str
    ) -> bool:
        expected = self.generate_mock_webhook_signature(raw_body)
        return hmac.compare_digest(expected, signature)

    def cancel_subscription(
        self,
        provider_subscription_id: str,
        at_period_end: bool = True
    ) -> bool:
        self.cancelled_subscriptions.add(provider_subscription_id)
        return True

    def process_refund(
        self,
        payment_id: str,
        amount_paise: Optional[int] = None,
        notes: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        return {
            "refund_id": f"rfnd_mock_{uuid.uuid4().hex[:12]}",
            "payment_id": payment_id,
            "amount_paise": amount_paise,
            "status": "processed",
        }
