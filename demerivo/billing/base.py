"""Payment Provider Abstraction for Demerivo PixelFix.

Defines the contract for creating orders/subscriptions, verifying signatures,
processing webhooks, and issuing refunds.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class PaymentProviderError(Exception):
    """Base exception for payment provider errors."""
    pass


class InvalidSignatureError(PaymentProviderError):
    """Raised when payment or webhook HMAC signature verification fails."""
    pass


class PaymentProvider(ABC):
    """Abstract payment gateway provider."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return provider identifier (e.g. 'razorpay', 'stripe', 'mock')."""
        pass

    @abstractmethod
    def create_customer(
        self,
        user_id: str,
        email: str,
        name: Optional[str] = None
    ) -> str:
        """Create or fetch a customer record at the payment provider."""
        pass

    @abstractmethod
    def create_order_or_subscription(
        self,
        tenant_id: str,
        plan_code: str,
        amount_paise: int,
        currency: str = "INR",
        customer_id: Optional[str] = None,
        notes: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Create a server-side order or subscription for frontend checkout.
        
        Returns a dictionary containing provider identifiers (e.g. order_id, key_id,
        amount, currency) needed by the client-side checkout SDK.
        """
        pass

    @abstractmethod
    def verify_payment_signature(
        self,
        order_id: str,
        payment_id: str,
        signature: str
    ) -> bool:
        """Verify client-submitted payment checkout signature using HMAC SHA256."""
        pass

    @abstractmethod
    def verify_webhook_signature(
        self,
        raw_body: bytes,
        signature: str
    ) -> bool:
        """Verify incoming webhook payload signature using HMAC SHA256."""
        pass

    @abstractmethod
    def cancel_subscription(
        self,
        provider_subscription_id: str,
        at_period_end: bool = True
    ) -> bool:
        """Cancel a subscription at the payment provider."""
        pass

    @abstractmethod
    def process_refund(
        self,
        payment_id: str,
        amount_paise: Optional[int] = None,
        notes: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Issue a refund for a previously captured payment."""
        pass
