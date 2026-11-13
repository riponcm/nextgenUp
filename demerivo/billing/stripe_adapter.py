"""Stripe International Payment Adapter for Demerivo PixelFix.

Provides a pluggable adapter interface for future international expansion.
In accordance with merchant compliance rules, this provider remains in an inactive,
unconfigured state until merchant overseas eligibility is confirmed.
"""

from typing import Any, Dict, Optional

from demerivo.billing.base import PaymentProvider, PaymentProviderError


class StripeMerchantNotConfiguredError(PaymentProviderError):
    """Raised when Stripe processing is invoked prior to merchant eligibility confirmation."""
    pass


class StripePaymentAdapter(PaymentProvider):
    """International payment gateway adapter for Stripe."""

    def __init__(self, secret_key: str = "", webhook_secret: str = ""):
        self.secret_key = secret_key
        self.webhook_secret = webhook_secret

    @property
    def provider_name(self) -> str:
        return "stripe"

    def _ensure_active(self) -> None:
        if not self.secret_key:
            raise StripeMerchantNotConfiguredError(
                "Stripe international payments are not currently active. "
                "Overseas merchant verification and credentials required."
            )

    def create_customer(
        self,
        user_id: str,
        email: str,
        name: Optional[str] = None
    ) -> str:
        self._ensure_active()
        return f"cus_{user_id[:14]}"

    def create_order_or_subscription(
        self,
        tenant_id: str,
        plan_code: str,
        amount_paise: int,
        currency: str = "INR",
        customer_id: Optional[str] = None,
        notes: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        self._ensure_active()
        return {
            "session_id": f"cs_test_{tenant_id[:8]}",
            "provider": self.provider_name,
            "plan_code": plan_code,
        }

    def verify_payment_signature(
        self,
        order_id: str,
        payment_id: str,
        signature: str
    ) -> bool:
        self._ensure_active()
        return False

    def verify_webhook_signature(
        self,
        raw_body: bytes,
        signature: str
    ) -> bool:
        self._ensure_active()
        return False

    def cancel_subscription(
        self,
        provider_subscription_id: str,
        at_period_end: bool = True
    ) -> bool:
        self._ensure_active()
        return True

    def process_refund(
        self,
        payment_id: str,
        amount_paise: Optional[int] = None,
        notes: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        self._ensure_active()
        return {}
